"""CTP 服务：vnpy MainEngine + CtpGateway 封装。

事件（vnpy EventEngine 线程回调）→ 转换 → Pusher 推前端：
- EVENT_POSITION 经 PositionCoalescer 节流合并（200ms 窗口）
- EVENT_ORDER / EVENT_TRADE 即时透传
- EVENT_LOG 关键标记 → 连接状态机（connected / ready / disconnected）

vnpy 对象可经 engine_factory 注入（测试用 fake）。
"""

import time
from dataclasses import asdict
from typing import Callable, Optional

from threading import Lock

from vnpy.trader.constant import Exchange

from faos_app.config import CtpSettings


_STEP_LABELS = [
    ("交易服务器连接成功", "① 连接交易服务器"),
    ("行情服务器连接成功", "① 连接行情服务器"),
    ("交易服务器授权验证成功", "② 授权验证"),
    ("交易服务器登录成功", "③ 登录"),
    ("结算信息确认成功", "④ 结算确认"),
    ("合约信息查询成功", "⑤ 合约加载完成"),
]


def _step_of(msg: str) -> str:
    """vnpy 日志 → 顶栏进度步骤文案（原文下发兜底）。"""
    for marker, label in _STEP_LABELS:
        if marker in msg:
            return label
    return msg


def build_ctp_setting(s: CtpSettings) -> dict:
    """CtpSettings → vnpy_ctp 网关中文键 setting dict。"""
    return {
        "用户名": s.用户名,
        "密码": s.密码,
        "经纪商代码": s.经纪商代码,
        "交易服务器": s.交易服务器,
        "行情服务器": s.行情服务器,
        "产品名称": s.产品名称,
        "授权编码": s.授权编码,
        "柜台环境": s.柜台环境,
    }


def _dir_str(direction) -> str:
    name = getattr(direction, "value", str(direction))
    return {"多": "long", "空": "short"}.get(name, str(name).lower())


def position_to_dict(p) -> dict:
    return {
        "vt_symbol": p.vt_symbol,
        "direction": _dir_str(p.direction),
        "volume": float(p.volume),
        "frozen": float(getattr(p, "frozen", 0)),
        "price": float(getattr(p, "price", 0)),
        "pnl": float(getattr(p, "pnl", 0)),
    }


_STATUS_ZH = {
    "SUBMITTING": "提交中", "NOTTRADED": "未成交", "PARTTRADED": "部分成交",
    "ALLTRADED": "全部成交", "CANCELLED": "已撤销", "REJECTED": "拒单",
}


def order_to_dict(o) -> dict:
    d = asdict(o)
    d["vt_orderid"] = o.vt_orderid
    d["vt_symbol"] = o.vt_symbol
    d["direction"] = _dir_str(o.direction)
    status = getattr(o.status, "value", str(o.status))
    d["status"] = status
    d["status_zh"] = _STATUS_ZH.get(str(status).upper(), str(status))
    d["offset"] = getattr(o.offset, "value", str(getattr(o, "offset", "")))
    return d


def trade_to_dict(t) -> dict:
    d = asdict(t)
    d["vt_tradeid"] = t.vt_tradeid
    d["vt_orderid"] = t.vt_orderid
    d["vt_symbol"] = t.vt_symbol
    d["direction"] = _dir_str(t.direction)
    d["offset"] = getattr(t.offset, "value", str(getattr(t, "offset", "")))
    return d


class PositionCoalescer:
    """持仓推送节流：窗口内缓冲，flush 时同 (vt_symbol, direction) last-wins 合并。

    生产端由 portal 定时任务按 interval 驱动 flush；测试可手动驱动。
    """

    def __init__(self, interval: float, sink: Callable[[list], None]):
        self._interval = interval
        self._sink = sink
        self._pending: dict = {}
        self._last_flush = 0.0
        self._lock = Lock()  # vnpy 线程 push 与 portal 线程 flush 并发

    def push(self, p, now: Optional[float] = None) -> None:
        d = position_to_dict(p) if not isinstance(p, dict) else p
        with self._lock:
            self._pending[(d["vt_symbol"], d["direction"])] = d

    def flush(self, now: Optional[float] = None) -> None:
        with self._lock:
            if self._pending:
                self._sink(list(self._pending.values()))
                self._pending = {}
            if now is not None:
                self._last_flush = now


class CtpService:
    def __init__(self, push, engine_factory: Optional[Callable] = None,
                 position_interval: float = 0.2):
        self._push = push
        self._engine_factory = engine_factory
        self._position_interval = position_interval
        self.main_engine = None
        self.event_engine = None
        self._coalescer = PositionCoalescer(position_interval, push.position_batch)
        self.state = "disconnected"
        self._position_timer_task = None
        self._portal = None
        self.on_ready: Optional[Callable[[], None]] = None  # 合约查询完成回调（含持仓校正）
        self._ready_fired = False
        self._connected = False
        self.oi_map: dict = {}  # vt_symbol -> 最新持仓量（市场主力判定）

    # -- 生命周期 -----------------------------------------------------------

    def start(self, settings: CtpSettings, portal=None, connect: bool = False) -> None:
        if self._engine_factory is not None:
            self.main_engine, self.event_engine = self._engine_factory()
        else:
            from vnpy.event import EventEngine
            from vnpy.trader.engine import MainEngine

            ee = EventEngine()
            me = MainEngine(ee)
            self.event_engine, self.main_engine = ee, me
        self._portal = portal

        from vnpy_ctp.gateway.ctp_gateway import CtpGateway

        self.main_engine.add_gateway(CtpGateway)

        from vnpy.trader.event import EVENT_LOG, EVENT_ORDER, EVENT_POSITION, EVENT_TICK, EVENT_TRADE

        self.event_engine.register(EVENT_POSITION, self._wrap(self._on_position))
        self.event_engine.register(EVENT_ORDER, self._wrap(self._on_order))
        self.event_engine.register(EVENT_TRADE, self._wrap(self._on_trade))
        self.event_engine.register(EVENT_LOG, self._wrap(self._on_log))
        self.event_engine.register(EVENT_TICK, self._wrap(self._on_tick_oi))
        # MainEngine.__init__ 已 start EventEngine，不再重复启动
        self._start_flush_loop()

    def _start_flush_loop(self) -> None:
        if self._portal is None:
            return

        async def loop():
            while True:
                await __import__("anyio").sleep(self._position_interval)
                self._coalescer.flush(time.monotonic())

        self._portal.start_task_soon(loop)

    def connect(self, settings: CtpSettings) -> None:
        """手动拨号入口（幂等）：SimNow 仅交易时段可登录。"""
        if self.main_engine is None:
            raise ValueError("CTP 服务未初始化")
        if self._connected:
            return
        self._connected = True
        self.main_engine.connect(build_ctp_setting(settings), "CTP")

    def shutdown(self) -> None:
        if self.main_engine is not None:
            try:
                self.main_engine.close()
            except Exception:
                pass

    # -- 状态 ----------------------------------------------------------------

    def is_ready(self) -> bool:
        return self.state == "ready"

    # -- 事件回调（vnpy EventEngine 线程） --------------------------------------

    def _wrap(self, fn):
        """vnpy 事件引擎传入 Event 对象（payload 在 .data）；handler 异常不得杀死分发线程。"""

        def handler(event) -> None:
            try:
                fn(getattr(event, "data", event))
            except Exception as e:
                from faos_app.log import log

                log(f"FAOS_EVENT_HANDLER_FAIL {getattr(fn, '__name__', fn)}: {e}")

        return handler

    def _on_position(self, data) -> None:
        self._coalescer.push(data, time.monotonic())

    def _on_tick_oi(self, tick) -> None:
        """tick 持仓量 → 主力判定数据（品种订阅时实测最大持仓=市场主力）。"""
        oi = getattr(tick, "open_interest", 0) or 0
        if oi > 0:
            self.oi_map[tick.vt_symbol] = float(oi)

    def _on_order(self, data) -> None:
        self._push.order(order_to_dict(data))

    def _on_trade(self, data) -> None:
        self._push.trade(trade_to_dict(data))

    def _on_log(self, data) -> None:
        msg = data if isinstance(data, str) else getattr(data, "msg", None) or data.get("msg", "")
        if "交易服务器连接成功" in msg or "行情服务器连接成功" in msg:
            self._set_state("connected", _step_of(msg))
        elif "交易服务器授权验证成功" in msg:
            self._set_state("connected", _step_of(msg))
        elif "交易服务器登录成功" in msg:
            self._set_state("logining", _step_of(msg))
        elif "结算信息确认成功" in msg:
            self._set_state("logining", _step_of(msg))
        elif "合约信息查询成功" in msg:
            self._set_state("ready", _step_of(msg))
            if not self._ready_fired:
                self._ready_fired = True
                self._push.notice("success", "CTP 登录成功，交易已就绪")
                if self.on_ready is not None:
                    self.on_ready()
        elif "连接断开" in msg:
            self._set_state("disconnected", msg)
            self._ready_fired = False  # 重连成功后再次就绪时重放回调
            self._connected = False  # 允许重新手动拨号
            self._push.notice("warn", f"CTP 连接断开: {msg}")
        elif "失败" in msg or "错误" in msg:
            self._push.notice("error", f"CTP: {msg}")

    def _set_state(self, state: str, msg: Optional[str] = None) -> None:
        self.state = state
        self._push.conn_state("ctp", state, msg)  # 前端监听 py://conn-state（kind=ctp；msg=进度步骤）

    # -- 测试/驱动 ------------------------------------------------------------

    def flush_positions(self, now: Optional[float] = None) -> None:
        self._coalescer.flush(now if now is not None else time.monotonic())

    # -- 交易相关 ------------------------------------------------------------

    def subscribe_symbols(self, vt_symbols: list) -> None:
        if self.main_engine is None:
            return
        from vnpy.trader.object import SubscribeRequest

        for sym in vt_symbols:
            symbol, _, exchange = sym.partition(".")
            req = SubscribeRequest(symbol=symbol, exchange=Exchange(exchange or "SHFE"))
            self.main_engine.subscribe(req, "CTP")

    def get_open_interest(self) -> dict:
        """各合约最新持仓量快照（主力判定用）。"""
        return dict(self.oi_map)

    def get_contract_symbols(self) -> list:
        """vnpy OMS 已加载的全部 SHFE 合约号（品种本地展开用）。"""
        oms = self._oms()
        if not oms:
            return []
        return [vt for vt in oms.contracts if vt.endswith(".SHFE")]

    def refresh_contract_seed(self) -> int:
        """以 OMS 合约覆盖刷新种子文件（CTP 就绪回调调用，供离线测试兜底）。"""
        symbols = self.get_contract_symbols()
        if symbols:
            from faos_app.contract_seed import save_seed

            save_seed(symbols, oi_map=self.get_open_interest())
        return len(symbols)

    def get_positions(self) -> list:
        oms = self._oms()
        return list(oms.get_all_positions()) if oms else []

    def get_trades_recent(self, limit: int = 20) -> list:
        oms = self._oms()
        if not oms:
            return []
        return list(oms.get_all_trades())[-limit:]

    def get_all_active_orders(self) -> list:
        oms = self._oms()
        return list(oms.get_all_active_orders()) if oms else []

    def cancel_order(self, vt_orderid: str) -> None:
        """按 vt_orderid 撤单：查 OMS 活动订单 → CancelRequest → 网关。"""
        from vnpy.trader.object import CancelRequest

        if self.main_engine is None:
            return
        oms = self._oms()
        order = oms.active_orders.get(vt_orderid) if oms else None
        if order is None:
            return
        req = CancelRequest(
            orderid=order.orderid,
            symbol=order.symbol,
            exchange=order.exchange,
        )
        self.main_engine.cancel_order(req, "CTP")

    def _oms(self):
        if self.main_engine is None:
            return None
        try:
            return self.main_engine.get_engine("oms")
        except Exception:
            return None

    def get_tick(self, vt_symbol: str):
        """OMS 最新 tick（单腿平仓定价用）。"""
        oms = self._oms()
        return oms.get_tick(vt_symbol) if oms else None

    def send_leg_order(self, vt_symbol: str, direction, offset,
                       price: float, volume: float) -> str:
        """单腿直接限价委托（今昨仓管理平仓用，绕过价差算法）。返回 vt_orderid。"""
        from vnpy.trader.constant import OrderType
        from vnpy.trader.object import OrderRequest

        if self.main_engine is None:
            raise ValueError("CTP 未连接，无法下单")
        symbol, _, exchange = vt_symbol.partition(".")
        req = OrderRequest(
            symbol=symbol,
            exchange=Exchange(exchange or "SHFE"),
            direction=direction,
            offset=offset,
            type=OrderType.LIMIT,
            price=price,
            volume=volume,
        )
        return self.main_engine.send_order(req, "CTP")
