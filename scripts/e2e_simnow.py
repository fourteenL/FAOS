r"""SimNow 全链路无头验收：登录→策略→1手触发单→算法拆腿→补腿→平仓。

镜像 main.py 装配（真实 MQTT + 真实 CTP + FaosTaker 算法），差异：
- 策略库用临时文件（不碰 %APPDATA%\faos\strategies.json）
- 结束时不退订（服务端订阅列表与用户真实数据保持原样）
- 事件推送到控制台而非 Tauri 前端

用法（交易时段 8:30–16:30）：
  .venv/Scripts/python scripts/e2e_simnow.py          # 预检（不下单）
  .venv/Scripts/python scripts/e2e_simnow.py --yes    # 真实下 1 手 ag 并平仓
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src-tauri" / "python"))

from anyio.from_thread import start_blocking_portal

from vnpy_spreadtrading import SpreadTradingApp

from faos_app.config import load_effective_settings, settings_path
from faos_app.ctp import CtpService
from faos_app.mqtt_feed import MqttFeedService, MqStatus, SpreadQuote
from faos_app.spread_service import SpreadService
from faos_app.state import AppState
from faos_app.store import StrategyStore
from faos_app.strategy_manager import StrategyManager
from faos_app.trigger_monitor import TriggerMonitor

PAIR = "ag2612&ag2702"  # A 腿主动=ag2612，B 腿对冲=ag2702


class ConsolePush:
    """Pusher 接口的控制台实现（方法集与 push.py 对齐）。"""

    def strategy(self, s):
        print(f"[STRATEGY] #{s.id} {s.pair} status={s.status} held={s.held_units} "
              f"triggers={[(t.action.value, t.spread) for t in s.triggers]} algos={s.active_algos}")

    def strategy_deleted(self, sid):
        print(f"[STRATEGY_DELETED] #{sid}")

    def spread(self, pair, q):
        pass  # 每秒数十帧，静默

    def mq_status(self, st: MqStatus):
        print(f"[MQ_STATUS] subscribed={len(st.subscribed)} pending={st.pending}")

    def conn_state(self, kind, st):
        print(f"[CONN] {kind} -> {st}")

    def notice(self, level, msg):
        print(f"[NOTICE:{level}] {msg}")

    def order(self, o):
        print(f"[ORDER] {o.get('vt_symbol')} {o.get('direction')} {o.get('volume')}@{o.get('price')} "
              f"{o.get('offset', '')} {o.get('status_zh')} ({o.get('vt_orderid')})")

    def trade(self, t):
        print(f"[TRADE] {t.get('vt_symbol')} {t.get('direction')} {t.get('volume')}@{t.get('price')} "
              f"{t.get('offset', '')}")

    def position_batch(self, ps):
        for p in ps:
            if p.get("volume"):
                print(f"[POSITION] {p['vt_symbol']} {p['direction']} {p['volume']}")

    def ctp_state(self, state, msg=None):
        print(f"[CTP] {state} {msg or ''}")

    def algo(self, d):
        print(f"[ALGO] {d.get('algoid')} {d.get('direction')} traded={d.get('traded_volume')}/"
              f"{d.get('volume')} A跳={d.get('chase_a')} B跳={d.get('chase_b')} {d.get('status')}")


def wait_for(pred, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    print(f"[TIMEOUT] {what}（{timeout}s）")
    return False


def run(live: bool) -> int:
    settings = load_effective_settings(settings_path())
    push = ConsolePush()
    latest: dict[str, SpreadQuote] = {}
    ok = {"open": False, "close": False}

    with start_blocking_portal("asyncio") as portal:
        store_path = Path(tempfile.gettempdir()) / "faos_e2e_strategies.json"
        store_path.unlink(missing_ok=True)  # 每次全新（残留旧触发单会拒挂新单）
        store = StrategyStore(store_path)
        state_ref: dict = {}

        def on_spread(pair, q):
            latest[pair] = q
            mgr = state_ref.get("manager")
            if mgr:
                mgr.on_spread(pair, q)
                state_ref["monitor"].on_spread(pair, q)

        def on_status(status):
            state_ref["status_seen"] = True
            mgr = state_ref.get("manager")
            if mgr:
                mgr.on_mq_status(status)

        feed = MqttFeedService(settings.mqtt, portal, on_spread, on_status,
                               lambda st: push.conn_state("mqtt", st))
        manager = StrategyManager(store, feed, push)
        monitor = TriggerMonitor(manager)
        state_ref["manager"] = manager
        state_ref["monitor"] = monitor
        manager.recover()

        ctp = CtpService(push=push)
        ctp.start(settings.ctp, portal, connect=False)
        spread_engine = ctp.main_engine.add_app(SpreadTradingApp)
        spread_engine.start()  # 事件路由注册（与 main.py 装配一致）
        spread_service = SpreadService(
            ctp=ctp, push=push, algo_settings=settings.algo,
            engine=spread_engine, event_engine=ctp.event_engine,
            monitor=monitor, store=store, portal=portal,
        )
        monitor.set_executor(spread_service)
        manager.on_stop_hooks.append(spread_service.stop_algos)
        spread_service.start_watchdog(portal)
        ctp.on_ready = lambda: portal.start_task_soon(manager.reconcile_holdings, ctp)

        print("=== 1. 连接 MQTT + CTP ===")
        feed.start()
        ctp.connect(settings.ctp)
        if not wait_for(lambda: ctp.state == "ready", 360, "CTP 就绪（含全量合约查询，SimNow 较慢，实测 2m50s~3m45s）"):
            _cleanup(feed, ctp, 2)
        print(f"[OK] CTP ready；当前持仓: {[(p.vt_symbol, p.direction.value, p.volume) for p in ctp.get_positions() if p.volume]}")

        print(f"=== 2. 等 tq/status 回执（服务端订阅列表）→ 定位 {PAIR} ===")
        leg_a, _, leg_b = PAIR.partition("&")
        if not wait_for(lambda: state_ref.get("status_seen"), 60, "tq/status 回执"):
            _cleanup(feed, ctp, 2)
        s = None
        for cand in store.list():
            if cand.pair == PAIR:
                s = cand
                break
        if s is None:  # 服务端未订阅过该组合 → 显式订阅（会改变服务端订阅列表）
            print("[WARN] 服务端无此组合，显式创建订阅")
            s = manager.create_pair(leg_a, leg_b, 1, 10)
        manager.set_lots(s.id, total_lots=10)
        if not wait_for(lambda: PAIR in latest, 60, "MQTT 首帧"):
            _cleanup(feed, ctp, 2)
        q0 = latest[PAIR]
        print(f"[OK] {PAIR} 实时帧: 空开={q0.short_open} 多开={q0.long_open} "
              f"空平={q0.short_cover} 多平={q0.long_cover}")

        if not live:
            print("[DRY] 预检通过（未下单）。加 --yes 执行真实 1 手开平闭环。")
            _cleanup(feed, ctp, 0)

        print("=== 3. 挂做空触发单（X=空开-1，下帧即触发）===")
        manager.set_config(s.id, slip_a=2, slip_b=5, order_type_a="对手价", order_type_b="对手价")
        t_open = manager.place_trigger(s.id, "open_short", round(q0.short_open - 1, 2), 1)
        print(f"[OK] 触发单 {t_open.trigger_id} open_short @ {t_open.spread}")

        def open_done():
            cur = store.get(s.id)
            return cur.held_units <= -1 and not cur.active_algos

        if not wait_for(open_done, 120, "开仓算法完成（A卖1手 + B买1手）"):
            _cleanup(feed, ctp, 3)

        def pos_map():
            return {p.vt_symbol + p.direction.value: p.volume for p in ctp.get_positions() if p.volume}

        # 网关每 2 秒交替查持仓/账户，成交后等 OMS 刷新（约 4 秒一轮）
        if not wait_for(lambda: pos_map().get(f"{leg_a}.SHFE空", 0) >= 1
                        and pos_map().get(f"{leg_b}.SHFE多", 0) >= 1, 30, "持仓回报 A空1/B多1"):
            _cleanup(feed, ctp, 3)
        print(f"[OK] held_units={store.get(s.id).held_units} CTP持仓={pos_map()}")
        ok["open"] = True

        print("=== 4. 挂空平触发单（X=空平+1，下帧即触发）===")
        q1 = latest[PAIR]
        t_close = manager.place_trigger(s.id, "close_short", round(q1.short_cover + 1, 2), 1)
        print(f"[OK] 触发单 {t_close.trigger_id} close_short @ {t_close.spread}")

        def close_done():
            cur = store.get(s.id)
            return cur.held_units >= 0 and not cur.active_algos

        if not wait_for(close_done, 120, "平仓算法完成（A买回 + B卖出）"):
            _cleanup(feed, ctp, 3)

        def ag_flat():
            pm = pos_map()
            return not any(k.startswith((f"{leg_a}.SHFE", f"{leg_b}.SHFE")) for k in pm)

        if not wait_for(ag_flat, 30, "持仓回报归零（ag 两腿平净）"):
            _cleanup(feed, ctp, 3)
        print(f"[OK] held_units={store.get(s.id).held_units} CTP持仓={pos_map()}")
        ok["close"] = True

        code = 0 if all(ok.values()) else 1
        print(f"\n===== 结果: 开仓={'PASS' if ok['open'] else 'FAIL'} "
              f"平仓={'PASS' if ok['close'] else 'FAIL'} =====")
        _cleanup(feed, ctp, code)


def _cleanup(feed, ctp, code):
    try:
        feed.stop()
    except Exception as e:
        print(f"[CLEANUP_MQTT_FAIL] {e}")
    try:
        ctp.shutdown()
    except Exception as e:
        print(f"[CLEANUP_CTP_FAIL] {e}")
    os._exit(code)  # CTP 非守护线程会挂住解释器，硬退出（与 main.py 同策略）


if __name__ == "__main__":
    code = run(live="--yes" in sys.argv)
    os._exit(code)
