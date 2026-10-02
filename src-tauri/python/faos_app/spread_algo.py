"""FAOS 定制价差吃价算法：每腿独立滑点 + 报单价格类型。

vnpy_spreadtrading.SpreadTakerAlgo 的两腿共用 send_leg_order 出口，此处重写它，
按 vt_symbol 区分 A 腿（主动）用 extra["slip_a"]、B 腿（被动）用 extra["slip_b"]。
报单类型：对手价（默认）/排队价/最新价/市价（以对手价+滑点限价实现）。
"""

from vnpy.trader.constant import Direction
from vnpy_spreadtrading.algo import SpreadTakerAlgo


def leg_order_price(tick, pricetick: float, slip: int, order_type: str, side: str) -> float:
    """腿委托定价。side: "buy"|"sell"；未知类型回落对手价。"""
    if order_type == "排队价":
        return tick.bid_price_1 if side == "buy" else tick.ask_price_1
    if order_type == "最新价":
        base = tick.last_price or (tick.ask_price_1 if side == "buy" else tick.bid_price_1)
        return base + pricetick * slip if side == "buy" else base - pricetick * slip
    # 对手价 / 市价（市价以对手价+滑点限价实现）
    return tick.ask_price_1 + pricetick * slip if side == "buy" else tick.bid_price_1 - pricetick * slip


class FaosTakerAlgo(SpreadTakerAlgo):
    """按腿滑点与报单类型定价的吃价算法。"""

    algo_name: str = "FaosTaker"

    def _slip_for(self, vt_symbol: str) -> int:
        extra = self.extra or {}
        slip = extra.get("slip_a") if vt_symbol == self.spread.active_leg.vt_symbol else extra.get("slip_b")
        return slip if slip is not None else self.payup

    def send_leg_order(self, vt_symbol: str, leg_volume: float) -> None:
        leg = self.spread.legs[vt_symbol]
        leg_tick = self.get_tick(vt_symbol)
        leg_contract = self.get_contract(vt_symbol)
        if not (leg_tick and leg_contract):
            return
        slip = self._slip_for(vt_symbol)
        order_type = (self.extra or {}).get("order_type") or "对手价"
        if leg_volume > 0:
            price = leg_order_price(leg_tick, leg_contract.pricetick, slip, order_type, "buy")
            self.send_order(leg.vt_symbol, price, abs(leg_volume), Direction.LONG)
        elif leg_volume < 0:
            price = leg_order_price(leg_tick, leg_contract.pricetick, slip, order_type, "sell")
            self.send_order(leg.vt_symbol, price, abs(leg_volume), Direction.SHORT)
