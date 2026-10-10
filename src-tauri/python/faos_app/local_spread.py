"""本地价差合成：CTP 两腿 tick → 四向价差 → TriggerMonitor（行情推送退役后的触发数据源）。

合成规则（与原服务端推送语义一致，空开 ≤ 多开，差值为交叉成本）：
- short_open = bid_A - ask_B（卖A买B 立即成交）≡ long_cover
- long_open  = ask_A - bid_B（买A卖B 立即成交）≡ short_cover

CTP 行情事件（vnpy 线程）→ 有 portal 时串行化到 portal 线程再触发监控。
"""

from typing import Optional

from faos_app.models import SpreadQuote


def compose_quote(tick_a, tick_b) -> Optional[SpreadQuote]:
    """由 A/B 两腿最新 tick 合成四向价差；价格无效（0/None）返回 None。"""
    a_bid, a_ask = tick_a.bid_price_1, tick_a.ask_price_1
    b_bid, b_ask = tick_b.bid_price_1, tick_b.ask_price_1
    if not (a_bid and a_ask and b_bid and b_ask):  # 未开盘/无效价
        return None
    return SpreadQuote(
        short_open=a_bid - b_ask,
        long_open=a_ask - b_bid,
        short_cover=a_ask - b_bid,
        long_cover=a_bid - b_ask,
    )


class LocalSpreadFeed:
    """订阅策略两腿行情，tick 到达即合成价差并驱动触发监控。"""

    def __init__(self, ctp, manager, monitor, portal=None):
        self._ctp = ctp
        self._manager = manager
        self._monitor = monitor
        self._portal = portal
        self._ticks: dict = {}
        from vnpy.trader.event import EVENT_TICK

        ctp.event_engine.register(EVENT_TICK, self._wrap)

    def _wrap(self, event) -> None:
        tick = getattr(event, "data", event)
        try:
            if self._portal is not None:
                self._portal.start_task_soon(self.on_tick, tick)
            else:
                self.on_tick(tick)
        except Exception as e:
            from faos_app.log import log

            log(f"FAOS_LOCAL_SPREAD_FAIL {e}")

    def on_tick(self, tick) -> None:
        self._ticks[tick.vt_symbol] = tick
        from faos_app.spread_service import vt_symbol_of

        symbol = tick.vt_symbol
        for s in self._manager.store.list():
            a, b = vt_symbol_of(s.leg_a), vt_symbol_of(s.leg_b)
            if symbol != a and symbol != b:
                continue
            ta, tb = self._ticks.get(a), self._ticks.get(b)
            if ta is None or tb is None:
                continue
            q = compose_quote(ta, tb)
            if q is None:
                continue
            self._monitor.on_spread(s.pair, q)
