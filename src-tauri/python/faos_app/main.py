"""FAOS 差价套利 - pytauri 后端入口与启动装配。

pytauri 相关导入集中在本模块与 commands/push —— 纯 Python 进程（pytest）
中 pytauri 需要 Rust 侧先注册 ext_mod，服务模块不得导入它们。
"""

from faos_app.log import banner, log

import os

from anyio.from_thread import start_blocking_portal
from pytauri import (
    Manager,
    builder_factory,
    context_factory,
)
from pytauri_utils.async_tools import AsyncTools
from vnpy_spreadtrading import SpreadTradingApp

from faos_app.commands import commands
from faos_app.config import (
    Settings,
    default_data_dir,
    load_effective_settings,
    settings_path,
)
from faos_app.local_spread import LocalSpreadFeed
from faos_app.push import Pusher
from faos_app.state import AppState
from faos_app.store import StrategyStore
from faos_app.strategy_manager import StrategyManager
from faos_app.trigger_monitor import TriggerMonitor


def _build_services(settings: Settings, portal, pusher: Pusher) -> AppState:
    """构造服务并接线（本地价差触发：CTP 两腿 tick → 合成 → 监控；行情推送链路已退役）。"""
    store = StrategyStore(default_data_dir() / "strategies.json")
    from faos_app.ctp import CtpService

    ctp = CtpService(push=pusher)

    manager = StrategyManager(
        store, pusher,
        legs_subscriber=ctp.subscribe_symbols,          # 策略创建 → CTP 订阅两腿行情
        contracts_provider=ctp.get_contract_symbols,    # 品种本地展开（vnpy 合约列表）
    )
    monitor = TriggerMonitor(manager)  # 执行器在 CTP/价差引擎就绪后注入
    state = AppState(settings=settings, store=store,
                     manager=manager, monitor=monitor, pusher=pusher)
    state.ctp = ctp

    # 启动恢复：清除运行中算法状态、触发单重挂
    manager.recover()

    try:
        ctp.start(settings.ctp, portal, connect=False)  # CTP 由用户手动连接（SimNow 仅交易时段可登录）
        spread_engine = ctp.main_engine.add_app(SpreadTradingApp)
        spread_engine.start()  # 注册 tick/委托/成交/timer 事件路由——不调 start 算法收不到行情、撤单重报不运转
        from faos_app.spread_service import SpreadService

        spread_service = SpreadService(
            ctp=ctp, push=pusher, algo_settings=settings.algo,
            engine=spread_engine, event_engine=ctp.event_engine,
            monitor=monitor, store=store, portal=portal,
        )
        monitor.set_executor(spread_service)
        state.spread_service = spread_service
        manager.on_stop_hooks.append(spread_service.stop_algos)
        spread_service.start_watchdog(portal)  # 追价超时看门狗

        # 本地价差合成：两腿 tick → 四向价差 → 触发监控（替代原 MQTT 行情推送）
        LocalSpreadFeed(ctp, manager, monitor, portal=portal)

        from faos_app.leg_ops import LegOps

        state.leg_ops = LegOps(ctp, store, chasing_provider=spread_service.chasing_texts)
        # CTP 就绪（合约查询完成）后经 portal 串行化做持仓校正
        ctp.on_ready = lambda: portal.start_task_soon(manager.reconcile_holdings, ctp)
    except Exception as e:  # CTP 初始化失败不阻塞应用启动
        pusher.notice("error", f"CTP 初始化失败: {e}")
        log(f"FAOS_CTP_INIT_FAIL {e}")

    return state


def main() -> int:
    settings = load_effective_settings(settings_path())  # 默认 → repo/exe 本地配置 → APPDATA
    from faos_app.log import cleanup_old_logs, configure as log_configure

    log_configure(settings.log.days)  # 日志保留天数（设置面板可改）
    banner()
    log("FAOS_SETTINGS_LOADED")
    cleanup_old_logs()  # 启动清理过期日志
    with (
        start_blocking_portal("asyncio") as portal,
        AsyncTools(portal) as async_tools,
    ):
        app = builder_factory().build(
            context=context_factory(),
            invoke_handler=commands.generate_handler(portal),
        )
        pusher = Pusher(app.handle())
        state = _build_services(settings, portal, pusher)
        Manager.manage(app, state)

        # 连接状态同步到 AppState（get_app_status 初始拉取用）
        pusher.on_conn_state = lambda kind, st: state.set_ctp_state(st)
        # CTP 由用户在界面手动连接（避免启动期事件竞态；SimNow 仅交易时段）
        log("FAOS_STARTUP_OK")
        try:
            exit_code = app.run_return()  # 点击 X / 所有窗口关闭后返回
        finally:
            # 优雅断开（柜台可见的正常登出），随后硬退出：
            # portal join 与 vnpy/CTP 非守护线程可能挂住 with 退出，点击 X 后必须保证进程终结
            try:
                if state.ctp is not None:
                    state.ctp.shutdown()  # MainEngine.close 正常登出
            except Exception as e:
                log(f"FAOS_CLEANUP_CTP_FAIL {e}")
            log("FAOS_CLEAN_EXIT")
            os._exit(exit_code)  # 终结一切后台线程/任务，防止进程残留
