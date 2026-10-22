"""FAOS 定制价差吃价算法：每腿独立滑点 + 报单价格类型 + 平仓显式今昨拆分。

vnpy_spreadtrading.SpreadTakerAlgo 的两腿共用 send_leg_order 出口，此处重写它，
按 vt_symbol 区分 A 腿（主动）用 extra["slip_a"]、B 腿（被动）用 extra["slip_b"]。
报单类型：对手价（默认）/排队价/最新价/市价（以对手价+滑点限价实现）。

平仓今昨拆分（2026-10-19 定稿，修平仓归属错位）：extra["close_split"] 携带
SpreadService 按策略账本算好的 {vt_symbol: {"yd": n, "td": m}}，发送时先平昨
（上期所平今费率高）后期、显式指定 Offset 绕过账户级净仓转换；撤单追价重发时
按本算法已成交的今/昨量（on_trade 累计）重算剩余；计划外的量回落账户级。
"""

from dataclasses import asdict, dataclass

from vnpy.trader.constant import Direction, Offset, OrderType, Status
from vnpy.trader.object import OrderData, OrderRequest
from vnpy.trader.utility import round_to
from vnpy_spreadtrading import APP_NAME
from vnpy_spreadtrading.algo import SpreadTakerAlgo
from vnpy_spreadtrading.base import AlgoItem

_FINAL_STATUS = {Status.ALLTRADED, Status.CANCELLED, Status.REJECTED}


def leg_order_price(tick, pricetick: float, slip: int, order_type: str, side: str) -> float:
    """腿委托定价。side: "buy"|"sell"；未知类型回落对手价。挂价=排队价。"""
    if order_type in ("挂价", "排队价"):
        return tick.bid_price_1 if side == "buy" else tick.ask_price_1
    if order_type == "最新价":
        base = tick.last_price or (tick.ask_price_1 if side == "buy" else tick.bid_price_1)
        return base + pricetick * slip if side == "buy" else base - pricetick * slip
    # 对手价 / 市价（市价以对手价+滑点限价实现）
    return tick.ask_price_1 + pricetick * slip if side == "buy" else tick.bid_price_1 - pricetick * slip


@dataclass
class FaosAlgoItem(AlgoItem):
    """带逐跳计数的算法快照：chase_a/chase_b = 该腿委托已发出次数（首报+每次追价）。"""

    chase_a: int = 0
    chase_b: int = 0


class FaosTakerAlgo(SpreadTakerAlgo):
    """按腿滑点与报单类型定价的吃价算法；平仓按策略账本显式拆平今/平昨。"""

    algo_name: str = "FaosTaker"

    def __init__(self, algo_engine, algoid, spread, direction, price, volume,
                 payup, interval, lock, extra):
        # 基类 __init__（vnpy_spreadtrading 1.4.0）不保存 extra，此处自存
        super().__init__(algo_engine, algoid, spread, direction, price, volume,
                         payup, interval, lock, extra)
        self.extra = extra or {}
        self.leg_sends: dict[str, int] = {}
        self.leg_offset_done: dict[str, dict] = {}  # vt_symbol -> {"td","yd"} 本算法已成交今/昨量

    def _slip_for(self, vt_symbol: str) -> int:
        extra = self.extra or {}
        slip = extra.get("slip_a") if vt_symbol == self.spread.active_leg.vt_symbol else extra.get("slip_b")
        return slip if slip is not None else self.payup

    def _order_type_for(self, vt_symbol: str) -> str:
        extra = self.extra or {}
        ot = extra.get("order_type_a") if vt_symbol == self.spread.active_leg.vt_symbol else extra.get("order_type_b")
        return ot or "对手价"

    def get_item(self) -> AlgoItem:
        base = super().get_item()
        active = self.spread.active_leg.vt_symbol
        passive_total = sum(n for sym, n in self.leg_sends.items() if sym != active)
        return FaosAlgoItem(**asdict(base), chase_a=self.leg_sends.get(active, 0), chase_b=passive_total)

    def take_active_leg(self) -> None:
        """主动腿一次报全量（2026-10-20 夜盘实证：基类按盘口量分轮——单份 2 被
        拆成 1+1 两轮报单凑）。限价追价模式下超盘口量可排队/追价，无需分轮；
        B 腿对冲按 A 成交量一次全量发。"""
        active_symbol = self.spread.active_leg.vt_symbol
        spread_volume_left = self.target - self.traded
        leg_order_volume = self.spread.calculate_leg_volume(active_symbol, spread_volume_left)
        if leg_order_volume:
            self.send_leg_order(active_symbol, leg_order_volume)

    def on_tick(self, tick) -> None:
        """停止=真停（2026-10-20 用户实证「停不下来」）：基类停止后仍继续补被动腿
        直到对冲完成（无限易挂单不断），此处停止后不再发任何新单——残留单腿敞口
        由 SpreadService 部分成交提醒点名，人工处理。"""
        if self.stopped:
            return
        super().on_tick(tick)

    def on_order(self, order) -> None:
        if self.stopped:
            return  # 停止后撤单回报不再触发补腿
        super().on_order(order)

    def check_algo_cancelled(self) -> None:
        """停止即终结（不等对冲完成——基类条件含 is_hedge_finished 会永远停不下来）。"""
        from vnpy.trader.constant import Status as _Status

        if self.stopped and self.is_order_finished():
            self.status = _Status.CANCELLED
            self.write_log("算法已停止（停止即终结，不再补腿；未对冲敞口请人工核对）")
            self.put_event()

    def update_trade(self, trade) -> None:
        """成交早于委托回报时补占位单（2026-10-22 加固）。

        基类 update_trade 直接 self.orders[vt_orderid] 取值，委托回报晚到会
        KeyError → 移出在途/算法结算全部中断。补一个同量占位单让基类正常推进。
        注意：**不能在 update_order 里提前移除 ALLTRADED 委托**——旧实现那样做
        会在「委托已成交但成交回报未到」的间隙让 is_order_finished() 变真，
        对冲腿被重复下单（22 日夜盘实证：A 卖 1、B 买 2，凭空多出裸腿）。"""
        if trade.vt_orderid not in self.orders:
            self.orders[trade.vt_orderid] = OrderData(
                gateway_name=trade.gateway_name, symbol=trade.symbol,
                exchange=trade.exchange, orderid=trade.orderid,
                direction=trade.direction, offset=trade.offset,
                price=trade.price, volume=trade.volume, traded=trade.volume,
                datetime=None,
            )
        super().update_trade(trade)

    def cancel_leg_order(self, vt_symbol: str) -> None:
        """撤在途委托（已终结的跳过：CTP 错误 26「报单已全成交或已撤销，不能再撤」）。"""
        for vt_orderid in list(self.leg_orders.get(vt_symbol, [])):
            order = self.orders.get(vt_orderid)
            if order is not None and order.status in _FINAL_STATUS:
                ids = self.leg_orders.get(vt_symbol)
                if ids and vt_orderid in ids:
                    ids.remove(vt_orderid)
                continue
            self.algo_engine.cancel_order(self, vt_orderid)

    def on_trade(self, trade) -> None:
        """平仓拆分算法：按成交 Offset 累计本算法已成交的今/昨量（重发时重算剩余）。"""
        plan = (self.extra or {}).get("close_split", {})
        if trade.vt_symbol not in plan:
            return
        off = str(getattr(trade.offset, "value", trade.offset))
        done = self.leg_offset_done.setdefault(trade.vt_symbol, {"td": 0.0, "yd": 0.0})
        if "昨" in off or "yesterday" in off.lower():
            done["yd"] += trade.volume
        elif "今" in off or "today" in off.lower():
            done["td"] += trade.volume

    def send_leg_order(self, vt_symbol: str, leg_volume: float) -> None:
        leg = self.spread.legs[vt_symbol]
        leg_tick = self.get_tick(vt_symbol)
        leg_contract = self.get_contract(vt_symbol)
        if not (leg_tick and leg_contract):
            return
        if leg_volume == 0:
            return
        slip = self._slip_for(vt_symbol)
        order_type = self._order_type_for(vt_symbol)
        self.leg_sends[vt_symbol] = self.leg_sends.get(vt_symbol, 0) + 1
        if leg_volume > 0:
            price = leg_order_price(leg_tick, leg_contract.pricetick, slip, order_type, "buy")
            direction = Direction.LONG
        else:
            price = leg_order_price(leg_tick, leg_contract.pricetick, slip, order_type, "sell")
            direction = Direction.SHORT
        extra = self.extra or {}
        vol = abs(leg_volume)
        if not extra.get("closing"):
            # 开仓一律显式 OPEN（2026-10-22 定稿）：vnpy 的 lock 转换在上期所会
            # 「先平昨再开」——开仓单会把别的策略的昨仓平掉（22 日实证：策略账本
            # 转负、柜台仓位错乱）。绕开账户级转换，策略腿真正物理隔离。
            self._send_offset_order(vt_symbol, price, vol, direction, Offset.OPEN)
            return
        plan = extra.get("close_split", {}).get(vt_symbol)
        if plan:
            # 剩余计划 = 初始拆分 − 本算法已成交今/昨；先昨后今
            done = self.leg_offset_done.get(vt_symbol, {"td": 0.0, "yd": 0.0})
            yd_left = max(0.0, plan["yd"] - done["yd"])
            td_left = max(0.0, plan["td"] - done["td"])
            yd_use = min(yd_left, vol)
            td_use = min(td_left, vol - yd_use)
            overflow = vol - yd_use - td_use
            if yd_use > 0:
                self._send_offset_order(vt_symbol, price, yd_use, direction, Offset.CLOSEYESTERDAY)
            if td_use > 0:
                self._send_offset_order(vt_symbol, price, td_use, direction, Offset.CLOSETODAY)
            if overflow > 1e-9:  # 计划外量按柜台实际可平处理（不再净仓转换开新仓）
                self._send_account_close(vt_symbol, price, overflow, direction)
            return
        self._send_account_close(vt_symbol, price, vol, direction)

    def _account_split(self, vt_symbol: str):
        """柜台该合约今昨持仓（多空各今/昨）；OMS 不可用返回 None。"""
        try:
            me = self.algo_engine.main_engine
            oms = me.get_engine("oms") if me is not None else None
            positions = oms.get_all_positions() if oms is not None else None
        except Exception:
            return None
        if positions is None:
            return None
        out = {"long_td": 0.0, "long_yd": 0.0, "short_td": 0.0, "short_yd": 0.0}
        for p in positions:
            if getattr(p, "vt_symbol", "") != vt_symbol:
                continue
            vol = float(getattr(p, "volume", 0) or 0)
            yd = float(getattr(p, "yd_volume", 0) or 0)
            d = str(getattr(getattr(p, "direction", ""), "value", getattr(p, "direction", "")))
            if d in ("多", "long"):
                out["long_yd"] += yd
                out["long_td"] += vol - yd
            elif d in ("空", "short"):
                out["short_yd"] += yd
                out["short_td"] += vol - yd
        return out

    def _send_account_close(self, vt_symbol: str, price: float, volume: float,
                            direction) -> None:
        """按柜台实际持仓显式平仓：先昨后今、只平不开。

        2026-10-22 定稿：净仓转换（send_order + lock=False）在柜台无可平量时会把
        溢出量变成 OPEN 反向建仓——平仓单开出新仓（用户实证「胡乱下单」）。"""
        split = self._account_split(vt_symbol)
        if split is None:
            # 拿不到柜台持仓时宁可不发：净仓转换在无仓可平时会把量变成开新仓
            # （10-22 用户实证「平仓单开出新仓」），宁可平不掉也不能乱开
            self.write_log(f"{vt_symbol} 无法读取柜台持仓，平仓委托未发（避免反向建仓）")
            return
        closing_short = direction == Direction.LONG  # 买入=平空；卖出=平多
        yd = split["short_yd"] if closing_short else split["long_yd"]
        td = split["short_td"] if closing_short else split["long_td"]
        use_yd = min(volume, max(0.0, yd))
        use_td = min(volume - use_yd, max(0.0, td))
        if use_yd > 0:
            self._send_offset_order(vt_symbol, price, use_yd, direction, Offset.CLOSEYESTERDAY)
        if use_td > 0:
            self._send_offset_order(vt_symbol, price, use_td, direction, Offset.CLOSETODAY)
        left = volume - use_yd - use_td
        if left > 1e-9:
            self.write_log(f"{vt_symbol} 柜台可平不足 {left:g} 手，未开新仓（避免反向建仓）")

    def _send_offset_order(self, vt_symbol: str, price: float, volume: float,
                           direction, offset) -> None:
        """显式今昨平仓委托：绕过账户级净仓转换直接指定 Offset（策略账本拆分）。

        复刻 AlgoEngine.send_order 的登记（order_algo_map / 价差委托映射 / oms），
        保证算法收到该委托的回报事件（update_order/update_trade）与撤单路由正常。"""
        if self.stopped and vt_symbol == self.spread.active_leg.vt_symbol:
            return  # 停止中禁主动腿发新单（对齐基类 send_order）
        contract = self.get_contract(vt_symbol)
        if contract is None:
            return
        if getattr(contract, "min_volume", 0):
            volume = round_to(volume, contract.min_volume)
        price = round_to(price, contract.pricetick)
        tick = self.get_tick(vt_symbol)
        if tick:
            limit_up = getattr(tick, "limit_up", None)
            limit_down = getattr(tick, "limit_down", None)
            if direction == Direction.LONG and limit_up:
                price = min(price, limit_up)
            elif direction == Direction.SHORT and limit_down:
                price = max(price, limit_down)
        req = OrderRequest(
            symbol=contract.symbol, exchange=contract.exchange,
            direction=direction, offset=offset, type=OrderType.LIMIT,
            price=price, volume=volume,
            reference=f"{APP_NAME}_{self.spread_name}",
        )
        engine = self.algo_engine
        main_engine = getattr(engine, "main_engine", None)
        if main_engine is None:  # 极端情况（引擎未装配）：跳过，不让事件线程崩
            self.write_log("柜台通道不可用，跳过腿委托")
            return
        vt_orderid = main_engine.send_order(req, contract.gateway_name)
        if not vt_orderid:
            return
        main_engine.update_order_request(req, vt_orderid, contract.gateway_name)
        engine.order_algo_map[vt_orderid] = self
        engine.data_engine.update_order_spread_map(vt_orderid, self.spread)
        self.leg_orders[vt_symbol].append(vt_orderid)
        off_zh = {Offset.OPEN: "开", Offset.CLOSETODAY: "平今",
                  Offset.CLOSEYESTERDAY: "平昨"}.get(offset, str(offset))
        self.write_log(f"发出委托[{vt_orderid}]，{vt_symbol}，"
                       f"{getattr(direction, 'value', direction)}，{off_zh}，{volume}@{price}")
