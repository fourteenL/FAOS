"""触发链路诊断（无头）：真实 CTP + 合成 tick 点燃触发，定位"下单无反应"断点。

用法（交易登录窗口内：交易日 8:30–16:30）：
  .venv/Scripts/python scripts/diag_trigger.py [--lots N]
链路：place_trigger(open_long@400) → 注入两腿合成 tick → LocalSpreadFeed 合成价差
→ TriggerMonitor 点燃 → SpreadTaker 拆腿 → CTP 真实报单（收盘后柜台拒单=链路通的证明）
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src-tauri" / "python"))

from datetime import datetime

from anyio.from_thread import start_blocking_portal

from vnpy.trader.constant import Direction, Exchange
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import TickData

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

PAIR_A, PAIR_B = "ag2611", "ag2612"
LOTS = float(sys.argv[sys.argv.index("--lots") + 1]) if "--lots" in sys.argv else 1.0


def make_tick(vt_symbol, bid, ask):
    sym, _, ex = vt_symbol.partition(".")
    t = TickData(symbol=sym, exchange=Exchange(ex or "SHFE"), datetime=datetime.now(),
                 gateway_name="CTP")
    t.bid_price_1, t.ask_price_1 = bid, ask
    t.last_price = (bid + ask) / 2
    t.volume, t.open_interest = 1000, 100000
    return t


class ConsolePush:
    def __getattr__(self, name):
        def _noop(*a, **k):
            pass
        return _noop

    def strategy(self, s):
        print(f"[STRATEGY] #{s.id} {s.pair} {s.status} held={s.held_units} "
              f"triggers={[(t.action.value, t.spread) for t in s.triggers]} algos={s.active_algos}")

    def order(self, o):
        print(f"[ORDER] {o.get('vt_symbol')} {o.get('direction')} {o.get('volume')}@{o.get('price')} "
              f"{o.get('status_zh')}")

    def trade(self, t):
        print(f"[TRADE] {t.get('vt_symbol')} {t.get('volume')}@{t.get('price')}")

    def algo(self, d):
        print(f"[ALGO] {d.get('algoid')} traded={d.get('traded_volume')}/{d.get('volume')} "
              f"A跳={d.get('chase_a')} B跳={d.get('chase_b')} {d.get('status')}")

    def notice(self, level, msg):
        print(f"[NOTICE:{level}] {msg}")

    def conn_state(self, kind, state, msg=None):
        print(f"[CONN] {kind} -> {state} {msg or ''}")


def wait_for(pred, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    print(f"[TIMEOUT] {what}")
    return False


def main() -> int:
    settings = load_effective_settings(settings_path())
    push = ConsolePush()

    with start_blocking_portal("asyncio") as portal:
        store = StrategyStore(Path(__file__).parent / "_diag_strategies.json")
        store_path = store._path
        store_path.unlink(missing_ok=True)
        store = StrategyStore(store_path)

        ctp = CtpService(push=push)
        manager = StrategyManager(store, push,
                                  legs_subscriber=ctp.subscribe_symbols,
                                  contracts_provider=ctp.get_contract_symbols)
        monitor = TriggerMonitor(manager)
        state = AppState(settings=settings, store=store, manager=manager,
                         monitor=monitor, pusher=push, ctp=ctp)

        print("=== 1. 连接 CTP（等就绪，1~3 分钟）===")
        ctp.start(settings.ctp, portal, connect=False)
        ctp.connect(settings.ctp)
        if not wait_for(lambda: ctp.state == "ready", 600, "CTP 就绪"):
            return 2
        n = ctp.refresh_contract_seed()
        print(f"[OK] ready，合约 {n} 个；OI 快照 {len(ctp.get_open_interest())} 条")

        from vnpy_spreadtrading import SpreadTradingApp

        engine = ctp.main_engine.add_app(SpreadTradingApp)
        engine.start()
        svc = SpreadService(ctp=ctp, push=push, algo_settings=settings.algo,
                            engine=engine, event_engine=ctp.event_engine,
                            monitor=monitor, store=store, portal=portal)
        monitor.set_executor(svc)
        manager.on_stop_hooks.append(svc.stop_algos)
        feed = LocalSpreadFeed(ctp, manager, monitor, portal=portal)

        print(f"=== 2. 建策略 {PAIR_A}&{PAIR_B} ===")
        s = manager.create_pair(PAIR_A, PAIR_B, LOTS, LOTS * 10)
        print(f"[OK] #{s.id} active，两腿行情已订阅")

        print("=== 3. 挂做多触发单 @400（必触发价）===")
        t = manager.place_trigger(s.id, TriggerAction.open_long, spread=400.0, lots=LOTS)
        print(f"[OK] 触发单 {t.trigger_id} open_long@{t.spread}")

        print("=== 4. 等真实行情合成价差（夜盘已开盘，无需合成）===")
        time.sleep(8)  # 真实 tick → LocalSpreadFeed 合成 → 400 必触发
        s2 = store.get(s.id)
        print(f"[CHECK] 状态={s2.status} 触发单剩余={len(s2.triggers)} 算法={s2.active_algos} "
              f"held={s2.held_units}")
        print("[CHECK] 撤单计数:", s2.cancel_count_a)

        print("=== 4.5 引擎内部探针 ===")
        de = engine.data_engine
        sp = de.spreads.get(f"{PAIR_A}&{PAIR_B}")
        if sp:
            print(f"[PROBE] spread bid={sp.bid_price} ask={sp.ask_price} "
                  f"bid_vol={sp.bid_volume} ask_vol={sp.ask_volume}")
        else:
            print("[PROBE] spread 不在 data_engine.spreads！")
        for vt, leg in de.legs.items():
            if vt.startswith(("ag2610", "ag2612")):
                print(f"[PROBE] leg {vt}: bid={leg.bid_price} ask={leg.ask_price} "
                      f"bid_vol={leg.bid_volume} ask_vol={leg.ask_volume}")
        ae = engine.algo_engine
        for vt in (f"{PAIR_A}.SHFE", f"{PAIR_B}.SHFE"):
            print(f"[PROBE] algo_map[{vt}] = {[a.algoid for a in ae.symbol_algo_map.get(vt, [])]}")
        for aid, algo in ae.algos.items():
            print(f"[PROBE] {aid}: active={algo.is_active()} dir={algo.direction} "
                  f"price={algo.price} spread.ask={algo.spread.ask_price} "
                  f"spread.bid_vol={algo.spread.bid_volume}")

        print("=== 5. 观察期 20 秒（委托回报/柜台响应）===")
        time.sleep(20)
        s3 = store.get(s.id)
        print(f"[FINAL] 状态={s3.status} held={s3.held_units}")
        print("DIAG_DONE")
        import os

        os._exit(0)  # 收盘后 close 可能挂起，硬退（证据已打印）


if __name__ == "__main__":
    sys.exit(main())
