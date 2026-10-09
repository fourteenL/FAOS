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
from faos_app.mqtt_feed import MqttFeedService, MqStatus, SpreadQuote
from faos_app.push import Pusher
from faos_app.state import AppState
from faos_app.store import StrategyStore
from faos_app.strategy_manager import StrategyManager
from faos_app.trigger_monitor import TriggerMonitor


def _build_services(settings: Settings, portal, pusher: Pusher) -> AppState:
    """构造服务并接线（feed 回调 → manager/monitor → pusher）。"""
    store = StrategyStore(default_data_dir() / "strategies.json")
    state_ref: dict = {}

    # 行情合并批量推送：66 组合 ~170 帧/秒逐帧推事件会打满事件循环/WebView
    # （下单/停止命令排队分钟级），200ms 窗口合并后事件量降至 ~5/秒；
    # 触发判断（manager/monitor）仍走逐帧原始行情，不受影响
    from faos_app.quote_batch import SpreadBatcher

    spread_batcher = SpreadBatcher(pusher.spread_batch)

    async def flush_spread_loop():
        import anyio

        while True:
            await anyio.sleep(0.2)
            spread_batcher.flush()

    portal.start_task_soon(flush_spread_loop)

    trace_spread = bool(os.environ.get("FAOS_TRACE_SPREAD"))  # 逐帧行情日志默认关（170 行/秒 I/O）

    def on_spread(pair: str, q: SpreadQuote) -> None:
        if trace_spread:
            log(f"FAOS_SPREAD {pair} {q.model_dump_json()}")
        mgr = state_ref.get("manager")
        if mgr:
            mgr.on_spread(pair, q)
            state_ref["monitor"].on_spread(pair, q)
        spread_batcher.push(pair, q)

    def on_status(status: MqStatus) -> None:
        log(f"FAOS_MQ_STATUS {status.model_dump_json()}")
        mgr = state_ref.get("manager")
        if mgr:
            mgr.on_mq_status(status)
        pusher.mq_status(status)

    def on_state(st: str) -> None:
        pusher.conn_state("mqtt", st)

    feed = MqttFeedService(settings.mqtt, portal, on_spread, on_status, on_state)
    manager = StrategyManager(store, feed, pusher)
    monitor = TriggerMonitor(manager)  # 执行器在 CTP/价差引擎就绪后注入
    state_ref["manager"] = manager
    state_ref["monitor"] = monitor

    state = AppState(settings=settings, store=store, feed=feed,
                     manager=manager, monitor=monitor, pusher=pusher)

    # 启动恢复：清除运行中算法状态、触发单重挂
    manager.recover()

    # CTP + vnpy 价差引擎
    from faos_app.ctp import CtpService
    from faos_app.spread_service import SpreadService

    ctp = CtpService(push=pusher)
    state.ctp = ctp
    try:
        ctp.start(settings.ctp, portal, connect=False)  # CTP 由用户手动连接（SimNow 仅交易时段可登录）
        spread_engine = ctp.main_engine.add_app(SpreadTradingApp)
        spread_engine.start()  # 注册 tick/委托/成交/timer 事件路由——不调 start 算法收不到行情、撤单重报不运转
        spread_service = SpreadService(
            ctp=ctp, push=pusher, algo_settings=settings.algo,
            engine=spread_engine, event_engine=ctp.event_engine,
            monitor=monitor, store=store, portal=portal,
        )
        monitor.set_executor(spread_service)
        state.spread_service = spread_service
        manager.on_stop_hooks.append(spread_service.stop_algos)
        spread_service.start_watchdog(portal)  # 追价超时看门狗

        from faos_app.leg_ops import LegOps

        state.leg_ops = LegOps(ctp, store, chasing_provider=spread_service.chasing_texts)
        # CTP 就绪（合约查询完成）后经 portal 串行化做持仓校正
        ctp.on_ready = lambda: portal.start_task_soon(manager.reconcile_holdings, ctp)
    except Exception as e:  # CTP 初始化失败不阻塞行情功能
        pusher.notice("error", f"CTP 初始化失败: {e}")
        log(f"FAOS_CTP_INIT_FAIL {e}")

    return state


def main() -> int:
    banner()
    settings = load_effective_settings(settings_path())  # 默认 → repo/exe 本地配置 → APPDATA
    log(f"FAOS_SETTINGS_LOADED {settings.mqtt.host}:{settings.mqtt.port}")
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
        pusher.on_conn_state = lambda kind, st: (
            state.set_mqtt_state(st) if kind == "mqtt" else state.set_ctp_state(st)
        )
        # MQTT/CTP 均由用户在界面手动连接（避免启动期事件竞态；MQTT 7×24，SimNow 仅交易时段）
        log("FAOS_STARTUP_OK")
        try:
            exit_code = app.run_return()  # 点击 X / 所有窗口关闭后返回
        finally:
            # 优雅断开（broker/柜台可见的正常登出），随后硬退出：
            # portal join 与 vnpy/CTP 非守护线程可能挂住 with 退出，点击 X 后必须保证进程终结
            try:
                state.feed.stop()
            except Exception as e:
                log(f"FAOS_CLEANUP_MQTT_FAIL {e}")
            try:
                if state.ctp is not None:
                    state.ctp.shutdown()  # MainEngine.close 正常登出
            except Exception as e:
                log(f"FAOS_CLEANUP_CTP_FAIL {e}")
            log("FAOS_CLEAN_EXIT")
            os._exit(exit_code)  # 终结一切后台线程/任务，防止进程残留
