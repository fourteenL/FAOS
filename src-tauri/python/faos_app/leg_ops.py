"""腿部操作：策略两腿的委托/成交/持仓快照 + 单腿撤单（A撤单/B撤单）。

追逐状态（chasing）由 spread_service 的算法事件缓存，此处仅透出。
"""

from typing import Optional

from faos_app.ctp import order_to_dict, trade_to_dict
from faos_app.models import Strategy
from faos_app.spread_service import vt_symbol_of

_LEGS = {"A": "leg_a", "B": "leg_b"}


class LegOps:
    def __init__(self, ctp, store, chasing_provider=None):
        self._ctp = ctp
        self._store = store
        self._chasing_provider = chasing_provider  # () -> {pair: str}

    def leg_snapshot(self, strategy_id: int) -> dict:
        s: Strategy = self._store.get(strategy_id)
        if s is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        orders = self._ctp.get_all_active_orders()
        trades = self._ctp.get_trades_recent() if hasattr(self._ctp, "get_trades_recent") else []
        positions = self._ctp.get_positions()
        chasing = (self._chasing_provider or dict)().get(s.pair)

        out = {}
        for tag in ("A", "B"):
            sym = vt_symbol_of(getattr(s, _LEGS[tag]))
            leg_orders = [order_to_dict(o) for o in orders if o.vt_symbol == sym]
            leg_trades = [trade_to_dict(t) for t in trades if t.vt_symbol == sym]
            long_pos = short_pos = long_yd = short_yd = 0
            for p in positions:
                if p.vt_symbol != sym:
                    continue
                yd = getattr(p, "yd_volume", 0) or 0
                d = _dir(p)
                if "long" in d:
                    long_pos += p.volume
                    long_yd += yd
                elif "short" in d:
                    short_pos += p.volume
                    short_yd += yd
            out[f"leg_{tag.lower()}"] = {
                "symbol": sym,
                "orders": leg_orders,
                "trades_recent": leg_trades,
                "long_pos": float(long_pos),
                "short_pos": float(short_pos),
                # 今昨拆分（上期所平仓需区分平今/平昨；yd_volume 来自 CTP 持仓日期）
                "long_today": float(long_pos - long_yd),
                "long_yd": float(long_yd),
                "short_today": float(short_pos - short_yd),
                "short_yd": float(short_yd),
                "chasing": chasing if tag == "A" else None,  # 主动腿 A 才有追价
            }
        return out

    def cancel_leg(self, strategy_id: int, leg: str) -> int:
        if leg not in _LEGS:
            raise ValueError(f"未知腿: {leg}（应为 A 或 B）")
        s: Strategy = self._store.get(strategy_id)
        if s is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        sym = vt_symbol_of(getattr(s, _LEGS[leg]))
        active = [o for o in self._ctp.get_all_active_orders() if o.vt_symbol == sym]
        for o in active:
            self._ctp.cancel_order(o.vt_orderid)
        return len(active)

    def close_leg_to_target(self, strategy_id: int, leg: str, targets: dict) -> dict:
        """单腿平仓到目标数（今昨仓管理，2026-10-14 用户定稿：填目标点按钮即平差额）。

        只平不开：目标 < 当前的差额下对手价限价单（卖=bid_1、买=ask_1，立即成交取向），
        今仓用平今、昨仓用平昨（上期所要求）；绕过价差算法——手动管理单腿敞口用。
        targets: {long_today, long_yd, short_today, short_yd} 目标持仓（未填的项不动）。"""
        if leg not in _LEGS:
            raise ValueError(f"未知腿: {leg}（应为 A 或 B）")
        s: Strategy = self._store.get(strategy_id)
        if s is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        from vnpy.trader.constant import Direction, Offset

        sym = vt_symbol_of(getattr(s, _LEGS[leg]))
        cur = self._leg_position_split(sym)
        tick = self._ctp.get_tick(sym)
        if tick is None or not (tick.bid_price_1 and tick.ask_price_1):
            raise ValueError(f"{sym} 无有效行情，无法定价平仓（稍候重试）")
        plans = [  # (字段, 方向, 开平, 委托价)
            ("long_today", Direction.SHORT, Offset.CLOSETODAY, tick.bid_price_1),
            ("long_yd", Direction.SHORT, Offset.CLOSEYESTERDAY, tick.bid_price_1),
            ("short_today", Direction.LONG, Offset.CLOSETODAY, tick.ask_price_1),
            ("short_yd", Direction.LONG, Offset.CLOSEYESTERDAY, tick.ask_price_1),
        ]
        sent = []
        for key, direction, offset, price in plans:
            want = targets.get(key)
            if want is None:
                continue  # 未填写的项不动
            want = float(want)
            if want < 0:
                raise ValueError("目标持仓不能为负数")
            delta = cur[key] - want
            if delta < -1e-9:
                raise ValueError(f"目标大于当前持仓（{key} 当前 {cur[key]:g}）——只平不开")
            if delta <= 1e-9:
                continue
            if abs(delta - round(delta)) > 1e-6:
                raise ValueError(f"平仓数量必须为整数手（{key} 差额 {delta:g}）")
            volume = int(round(delta))
            vt_orderid = self._ctp.send_leg_order(sym, direction, offset, price, volume)
            sent.append({"key": key, "vt_orderid": vt_orderid, "direction": "卖" if direction == Direction.SHORT else "买",
                         "offset": "平今" if offset == Offset.CLOSETODAY else "平昨",
                         "price": price, "volume": volume})
        if not sent:
            raise ValueError("无平仓数量（目标与当前一致或未填写）")
        return {"symbol": sym, "sent": sent}

    def _leg_position_split(self, sym: str) -> dict:
        """该合约当前持仓的今昨拆分 {long_today, long_yd, short_today, short_yd}。"""
        long_pos = short_pos = long_yd = short_yd = 0
        for p in self._ctp.get_positions():
            if p.vt_symbol != sym:
                continue
            yd = getattr(p, "yd_volume", 0) or 0
            d = _dir(p)
            if "long" in d:
                long_pos += p.volume
                long_yd += yd
            elif "short" in d:
                short_pos += p.volume
                short_yd += yd
        return {"long_today": long_pos - long_yd, "long_yd": long_yd,
                "short_today": short_pos - short_yd, "short_yd": short_yd}


def _dir(p) -> str:
    v = getattr(p.direction, "value", str(p.direction))
    return {"多": "long", "空": "short"}.get(v, str(v).lower())
