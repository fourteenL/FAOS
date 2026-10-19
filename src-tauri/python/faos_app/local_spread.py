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

    _logged_pairs: set = set()  # 每对仅记一次首帧（诊断：证明行情链路活着）
    _PUSH_MIN_SEC = 0.5  # 实时价差显示节流：同一组合至多每 0.5s 推一次

    def __init__(self, ctp, manager, monitor, portal=None, quote_pusher=None, settings=None):
        self._ctp = ctp
        self._manager = manager
        self._monitor = monitor
        self._portal = portal
        self._quote_pusher = quote_pusher  # (payload) 实时价差推送（可缺省）
        self._settings = settings
        self._ticks: dict = {}
        self._last_valid: dict = {}  # pair -> 最近一次成功合成时刻
        self._noquote_warned: set = set()
        self._push_at: dict = {}  # pair -> 上次推送时刻（节流）
        self._stale_push_at: dict = {}  # pair -> 上次停滞推送时刻（节流）
        self._timer_count = 0
        from vnpy.trader.event import EVENT_TICK, EVENT_TIMER

        ctp.event_engine.register(EVENT_TICK, self._wrap)
        ctp.event_engine.register(EVENT_TIMER, self._wrap_timer)

    def _stale_sec(self) -> float:
        """停滞阈值（algo.stale_seconds 可配，默认 30）：任一腿超时即不合成不触发。"""
        algo = getattr(self._settings, "algo", None)
        v = getattr(algo, "stale_seconds", None) if algo else None
        return float(v) if v else self._STALE_SEC

    def _leg_stale(self, vt_symbol: str, now: float):
        """腿行情停滞秒数；从未收到 tick 返回 None（远月无行情）。"""
        entry = self._ticks.get(vt_symbol)
        if entry is None:
            return None
        return int(now - entry[1])

    def _push_quote(self, pair: str, q) -> None:
        """实时价差推送（节流 0.5s/组合）：填价参考显示用，与触发链路无关。"""
        if self._quote_pusher is None:
            return
        import time as _time

        now = _time.monotonic()
        if now - self._push_at.get(pair, 0.0) < self._PUSH_MIN_SEC:
            return
        self._push_at[pair] = now
        from faos_app.spread_service import vt_symbol_of

        leg_a, leg_b = pair.split("&")
        try:
            self._quote_pusher({"pair": pair, "short_open": q.short_open,
                                "long_open": q.long_open, "live": True,
                                "stale_a": self._leg_stale(vt_symbol_of(leg_a), now),
                                "stale_b": self._leg_stale(vt_symbol_of(leg_b), now)})
        except Exception as e:
            from faos_app.log import log

            log(f"FAOS_SPREAD_PUSH_FAIL {e}")

    def _wrap_timer(self, event) -> None:
        try:
            if self._portal is not None:
                self._portal.start_task_soon(self.on_timer)
            else:
                self.on_timer()
        except Exception:
            pass

    def on_timer(self) -> None:
        """每秒触发：停滞组合推 live:false+各腿停滞秒数；每 20 秒巡查死腿可见性告警。"""
        import time as _time

        now = _time.monotonic()
        self._push_stale_quotes(now)
        self._timer_count += 1
        if self._timer_count % 20 != 0:
            return
        for s in self._manager.store.list():
            if not s.triggers or s.pair in self._noquote_warned:
                continue
            last = self._last_valid.get(s.pair)
            if last is not None and now - last < self._stale_sec():
                continue  # 近阈值秒数内合成过，正常
            from faos_app.spread_service import vt_symbol_of

            dead = [leg for leg in (s.leg_a, s.leg_b)
                    if self._leg_book_invalid(vt_symbol_of(leg))]
            if dead:
                self._noquote_warned.add(s.pair)
                self._manager.push.notice(
                    "warn",
                    f"策略 #{s.id} {s.pair} 有挂单但价差无法计算：{','.join(dead)} "
                    "无有效双边报价（远月/临交割合约夜盘常无行情），触发单将一直等待")
            elif last is None:
                self._noquote_warned.add(s.pair)
                self._manager.push.notice(
                    "warn", f"策略 #{s.id} {s.pair} 尚未收到两腿行情，触发单等待中")

    def _push_stale_quotes(self, now: float) -> None:
        """停滞/无行情组合每秒推 live:false + 各腿停滞秒数（前端显示递增秒数）。"""
        if self._quote_pusher is None:
            return
        from faos_app.spread_service import vt_symbol_of

        for s in self._manager.store.list():
            last = self._last_valid.get(s.pair)
            if last is not None and now - last <= self._stale_sec():
                continue  # 活跃：由 tick 驱动的 _push_quote 推送（节流 0.5s）
            if now - self._stale_push_at.get(s.pair, 0.0) < 1.0:
                continue  # 停滞推送节流 1s（秒数每秒更新）
            self._stale_push_at[s.pair] = now
            try:
                self._quote_pusher({
                    "pair": s.pair, "live": False,
                    "stale_a": self._leg_stale(vt_symbol_of(s.leg_a), now),
                    "stale_b": self._leg_stale(vt_symbol_of(s.leg_b), now),
                })
            except Exception:
                pass

    def _leg_book_invalid(self, vt_symbol: str) -> bool:
        """该腿盘口无效（无 tick 或买/卖价为 0）。"""
        entry = self._ticks.get(vt_symbol)
        if entry is None:
            return True
        tick = entry[0]
        return not (tick.bid_price_1 and tick.ask_price_1)

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

    _STALE_SEC = 30.0  # 停滞阈值默认值（algo.stale_seconds 可配）：任一腿超时即不合成不触发

    def on_tick(self, tick) -> None:
        import time as _time

        self._ticks[tick.vt_symbol] = (tick, _time.monotonic())
        from faos_app.spread_service import vt_symbol_of

        symbol = tick.vt_symbol
        now = _time.monotonic()
        for s in self._manager.store.list():
            a, b = vt_symbol_of(s.leg_a), vt_symbol_of(s.leg_b)
            if symbol != a and symbol != b:
                continue
            ea, eb = self._ticks.get(a), self._ticks.get(b)
            if ea is None or eb is None:
                continue
            if now - ea[1] > self._stale_sec() or now - eb[1] > self._stale_sec():
                continue  # 陈旧 tick 不点火（死腿残留价不算数）
            ta, tb = ea[0], eb[0]
            q = compose_quote(ta, tb)
            if q is None:
                continue
            if s.pair not in LocalSpreadFeed._logged_pairs:
                LocalSpreadFeed._logged_pairs.add(s.pair)
                from faos_app.log import log

                log(f"FAOS_SPREAD_COMPOSED {s.pair} 首帧 空开={q.short_open} 多开={q.long_open}")
            import time as _t

            self._last_valid[s.pair] = _t.monotonic()
            if s.pair in self._noquote_warned:
                self._noquote_warned.discard(s.pair)
                self._manager.push.notice("info", f"{s.pair} 行情恢复，触发监控继续")
            self._push_quote(s.pair, q)  # 实时价差显示（节流，不影响触发）
            self._monitor.on_spread(s.pair, q)
