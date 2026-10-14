"""beta.9 真机验收（无头，SimNow 夜盘实柜台）：策略隔离（开仓锁仓）+ 反向平仓。

验证链路：
1. 登录就绪 → 订阅三腿行情（ag2708/ag2709/ag2612，今晚均有成交记录、流动性可用）
2. 快照账户持仓（合约×方向）
3. 建两个共享主力策略 #1 ag2708&ag2612、#2 ag2709&ag2612（对手价快速成交参数）
4. #1 做空、#2 做多（必触发价）→ 等全部成交
5. 【隔离断言】ag2612 多头 +1 且 空头 +1 同时存在（旧净仓行为会互相抵消=FAIL）
6. 反向平仓 #1 → ag2708 空头回补、ag2612 多头 -1，#2 持仓不动
7. 反向平仓 #2 → 账户恢复初始快照（方向 delta 归零）
8. 清理策略（临时 store，不动用户数据）
"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # os._exit 前所有打印必须已落盘
sys.path.insert(0, str(Path(__file__).parent.parent / "src-tauri" / "python"))

from anyio.from_thread import start_blocking_portal

from faos_app.config import load_effective_settings, settings_path
from faos_app.ctp import CtpService
from faos_app.local_spread import LocalSpreadFeed
from faos_app.log import log
from faos_app.models import TriggerAction
from faos_app.spread_service import SpreadService
from faos_app.state import AppState
from faos_app.store import StrategyStore
from faos_app.strategy_manager import StrategyManager
from faos_app.trigger_monitor import TriggerMonitor

LEGS = ["ag2708", "ag2709", "ag2612"]


class ConsolePush:
    def __getattr__(self, name):
        def _noop(*a, **k):
            pass
        return _noop

    def notice(self, level, msg):
        print(f"[NOTICE:{level}] {msg}")

    def conn_state(self, kind, state, msg=None):
        print(f"[CONN] {kind} -> {state} {msg or ''}")

    def strategy(self, s):
        print(f"[STRATEGY] #{s.id} {s.pair} {s.status} held={s.held_units} algos={len(s.active_algos)}")


def wait_for(pred, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.3)
    print(f"[TIMEOUT] {what}")
    return False


def pos_snapshot(ctp):
    """账户持仓快照 {(vt_symbol, long/short): volume}。"""
    snap = {}
    for p in ctp.get_positions():
        v = getattr(p.direction, "value", str(p.direction))
        d = {"多": "long", "空": "short"}.get(v, str(v).lower())
        snap[(p.vt_symbol, d)] = snap.get((p.vt_symbol, d), 0) + p.volume
    return snap


def delta(after, before):
    keys = set(before) | set(after)
    return {k: after.get(k, 0) - before.get(k, 0) for k in keys if after.get(k, 0) != before.get(k, 0)}


def spread_of(ctp, a, b):
    ta, tb = ctp.get_tick(f"{a}.SHFE"), ctp.get_tick(f"{b}.SHFE")
    if not (ta and tb and ta.bid_price_1 and ta.ask_price_1 and tb.bid_price_1 and tb.ask_price_1):
        return None
    return ta.bid_price_1 - tb.ask_price_1, ta.ask_price_1 - tb.bid_price_1  # 空开, 多开


def wait_positions(ctp, expect, timeout=30, what="持仓回报"):
    """轮询等持仓快照满足预期（柜台持仓回报约 4s 一轮，固定 sleep 会读到滞后缓存）。"""
    def done():
        return expect(pos_snapshot(ctp))
    return wait_for(done, timeout, what)


def wait_held(store, sid, target, timeout=240):
    """等策略持仓到达目标且算法全部结束（held 在算法完成时更新）。"""
    def done():
        s = store.get(sid)
        return (s is not None and s.held_units == target
                and not s.active_algos and s.status in ("active", "armed"))
    return wait_for(done, timeout, f"#{sid} held→{target}")


def main() -> int:
    settings = load_effective_settings(settings_path())
    push = ConsolePush()
    results = []

    def check(name, ok):
        results.append((name, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    with start_blocking_portal("asyncio") as portal:
        store_path = Path(__file__).parent / "_accept_strategies.json"
        store_path.unlink(missing_ok=True)
        store = StrategyStore(store_path)

        ctp = CtpService(push=push)
        manager = StrategyManager(store, push,
                                  legs_subscriber=ctp.subscribe_symbols,
                                  contracts_provider=ctp.get_contract_symbols)
        monitor = TriggerMonitor(manager)
        state = AppState(settings=settings, store=store, manager=manager,
                         monitor=monitor, pusher=push, ctp=ctp)

        print("=== 1. 连接 CTP（夜盘就绪 1~6 分钟）===")
        ctp.start(settings.ctp, portal, connect=False)
        ctp.connect(settings.ctp)
        if not wait_for(lambda: ctp.state == "ready", 600, "CTP 就绪"):
            return 2
        print("[OK] ready")

        from vnpy_spreadtrading import SpreadTradingApp

        engine = ctp.main_engine.add_app(SpreadTradingApp)
        engine.start()
        svc = SpreadService(ctp=ctp, push=push, algo_settings=settings.algo,
                            engine=engine, event_engine=ctp.event_engine,
                            monitor=monitor, store=store, portal=portal)
        monitor.set_executor(svc)
        manager.on_stop_hooks.append(svc.stop_algos)
        feed = LocalSpreadFeed(ctp, manager, monitor, portal=portal)

        print("=== 2. 等三腿行情 ===")
        ctp.subscribe_symbols([f"{l}.SHFE" for l in LEGS])
        if not wait_for(lambda: all(
            (t := ctp.get_tick(f"{l}.SHFE")) and t.bid_price_1 for l in LEGS), 120, "三腿 tick"):
            return 2
        sp1 = spread_of(ctp, "ag2708", "ag2612")
        sp2 = spread_of(ctp, "ag2709", "ag2612")
        print(f"[OK] #1 空开/多开={sp1}；#2 空开/多开={sp2}")
        if not (sp1 and sp2):
            return 2

        before = pos_snapshot(ctp)
        print(f"[OK] 持仓快照 {before or '（空）'}")

        print("=== 3. 建两策略（对手价快速成交）===")
        s1 = manager.create_pair("ag2708", "ag2612", 1, 5)
        s2 = manager.create_pair("ag2709", "ag2612", 1, 5)
        for s in (s1, s2):
            manager.set_config(s.id, slip_a=5, slip_b=100,
                               order_type_a="对手价", order_type_b="对手价")

        print("=== 4. #1 做空 + #2 做多（必触发价）===")
        manager.place_trigger(s1.id, TriggerAction.open_short, spread=round(sp1[0] - 50, 2))
        manager.place_trigger(s2.id, TriggerAction.open_long, spread=round(sp2[1] + 50, 2))
        ok1 = wait_held(store, s1.id, -1)
        ok2 = wait_held(store, s2.id, 1)
        check("#1 做空成交 held=-1", ok1)
        check("#2 做多成交 held=+1", ok2)
        if not (ok1 and ok2):
            print("[ABORT] 开仓未完成，跳过后续断言")
            _cleanup(manager, store)
            _report(results)
            _hard_exit()

        wait_positions(ctp, lambda snap: (
            snap.get(("ag2612.SHFE", "long"), 0) - before.get(("ag2612.SHFE", "long"), 0) == 1
            and snap.get(("ag2612.SHFE", "short"), 0) - before.get(("ag2612.SHFE", "short"), 0) == 1),
            30, "开仓双腿持仓回报")
        after_open = pos_snapshot(ctp)
        d = delta(after_open, before)
        print(f"[CHECK] 开仓后持仓变化 {d}")
        check("隔离：ag2612 多头 +1（#1 的 B 腿）", d.get(("ag2612.SHFE", "long"), 0) == 1)
        check("隔离：ag2612 空头 +1（#2 的 B 腿，未被净掉）", d.get(("ag2612.SHFE", "short"), 0) == 1)
        check("#1 A 腿 ag2708 空头 +1", d.get(("ag2708.SHFE", "short"), 0) >= 1)
        check("#2 A 腿 ag2709 多头 +1", d.get(("ag2709.SHFE", "long"), 0) >= 1)

        print("=== 5. 反向平仓 #1（做多区语义 open_long）===")
        sp1b = spread_of(ctp, "ag2708", "ag2612")
        manager.place_trigger(s1.id, TriggerAction.open_long, spread=round(sp1b[1] + 50, 2))
        okc = wait_held(store, s1.id, 0)
        check("#1 反向平仓 held=0", okc)
        wait_positions(ctp, lambda snap: (
            snap.get(("ag2612.SHFE", "long"), 0) == 0), 30, "平#1 后 ag2612 多头清零")
        after_close1 = pos_snapshot(ctp)
        d1 = delta(after_close1, after_open)
        print(f"[CHECK] 平 #1 后变化 {d1}")
        check("平 #1：ag2612 多头 -1", d1.get(("ag2612.SHFE", "long"), 0) == -1)
        check("平 #1 不动 #2：ag2709 多头不变", d1.get(("ag2709.SHFE", "long"), 0) == 0)

        print("=== 6. 反向平仓 #2 → 恢复快照 ===")
        sp2b = spread_of(ctp, "ag2709", "ag2612")
        manager.place_trigger(s2.id, TriggerAction.open_short, spread=round(sp2b[0] - 50, 2))
        okc2 = wait_held(store, s2.id, 0)
        check("#2 反向平仓 held=0", okc2)
        wait_positions(ctp, lambda snap: (
            snap.get(("ag2709.SHFE", "long"), 0) == 0
            and snap.get(("ag2612.SHFE", "short"), 0) == 0), 30, "平#2 后持仓清零")
        final = pos_snapshot(ctp)
        df = delta(final, before)
        print(f"[CHECK] 最终相对初始快照变化 {df or '（无，已还原）'}")
        check("账户恢复初始状态（方向 delta 全归零）", not df)

        _cleanup(manager, store)
        _report(results)
        _hard_exit()


def _cleanup(manager, store):
    print("=== 清理：删除临时策略 ===")
    for s in list(store.list()):
        manager.delete(s.id)
    print("[OK] 临时策略已删（临时 store 文件不涉及用户数据）")


def _report(results):
    print("\n===== 验收结果 =====")
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    passed = sum(1 for _, ok in results if ok)
    print(f"ACCEPT: {passed}/{len(results)} PASS")
    print("ACCEPT_DONE")


def _hard_exit():
    import os

    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
