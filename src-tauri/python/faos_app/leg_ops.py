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
            long_pos = sum(p.volume for p in positions if p.vt_symbol == sym and "long" in _dir(p))
            short_pos = sum(p.volume for p in positions if p.vt_symbol == sym and "short" in _dir(p))
            out[f"leg_{tag.lower()}"] = {
                "symbol": sym,
                "orders": leg_orders,
                "trades_recent": leg_trades,
                "long_pos": float(long_pos),
                "short_pos": float(short_pos),
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


def _dir(p) -> str:
    v = getattr(p.direction, "value", str(p.direction))
    return {"多": "long", "空": "short"}.get(v, str(v).lower())
