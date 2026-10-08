"""价差执行服务：SpreadData 定义 + SpreadTaker 算法执行器（SpreadExecutor 实现）。

- 每策略一个 SpreadData：price_formula "A-B"，A 腿主动（吃价追价），B 腿（主力）被动对冲
- execute()：CTP 未就绪返回 None（触发单挂起）；否则 start_algo
- EVENT_SPREAD_ALGO 事件 → 推前端；算法结束 → held_units 累计 + monitor.on_algo_finished
"""

from dataclasses import asdict
from typing import Optional

from vnpy.trader.constant import Direction, Status
from vnpy_spreadtrading.base import EVENT_SPREAD_ALGO, AlgoItem, LegData, SpreadData

from faos_app.models import Strategy, TriggerAction

_FINISHED_STATUS = {Status.ALLTRADED, Status.CANCELLED, Status.REJECTED}
_ACTION_DIRECTION = {
    TriggerAction.open_long: Direction.LONG,
    TriggerAction.open_short: Direction.SHORT,
    TriggerAction.close_short: Direction.LONG,
    TriggerAction.close_long: Direction.SHORT,
}

_ACTION_DONE_NOTICE = {
    TriggerAction.open_short: "做空成功（成交 {v:.0f} 份）",
    TriggerAction.open_long: "做多成功（成交 {v:.0f} 份）",
    TriggerAction.close_short: "空平成功（买回 {v:.0f} 份）",
    TriggerAction.close_long: "多平成功（卖出 {v:.0f} 份）",
}


def vt_symbol_of(symbol: str) -> str:
    """裸合约号 → vnpy vt_symbol（上期所）。"""
    return symbol if "." in symbol else f"{symbol}.SHFE"


class SpreadService:
    def __init__(self, ctp, push, algo_settings, engine=None, event_engine=None,
                 monitor=None, store=None, portal=None):
        self._ctp = ctp
        self._push = push
        self._algo = algo_settings
        self._engine = engine
        self._store = store
        self._monitor = monitor
        self._portal = portal
        self._spreads: dict[str, SpreadData] = {}
        self._algo_started: dict[str, float] = {}  # I8 看门狗: algoid -> 启动时刻
        self._algo_direction: dict[str, object] = {}  # algoid -> Direction（方向停止用）
        self._algo_action: dict[str, object] = {}  # algoid -> TriggerAction（成功通知文案用）
        self._algo_latest: dict[str, "AlgoItem"] = {}  # pair -> 最新算法快照（追价文案用）
        if event_engine is not None:
            self._install_algo_class()
            event_engine.register(EVENT_SPREAD_ALGO, self._wrap_algo_handler)

    def attach(self, engine, event_engine) -> None:
        """main.py 装配时注入真实 SpreadEngine（add_app 返回值）。"""
        self._engine = engine
        self._install_algo_class()
        event_engine.register(EVENT_SPREAD_ALGO, self._wrap_algo_handler)

    def _install_algo_class(self) -> None:
        """用 FAOS 定制算法（每腿滑点/报单类型）替换默认 SpreadTaker。"""
        from faos_app.spread_algo import FaosTakerAlgo

        if self._engine is None:
            return
        if hasattr(self._engine, "algo_engine"):
            self._engine.algo_engine.algo_class = FaosTakerAlgo  # 真实 SpreadEngine
        elif hasattr(self._engine, "algo_class"):
            self._engine.algo_class = FaosTakerAlgo  # 测试 FakeEngine

    def _wrap_algo_handler(self, event) -> None:
        """vnpy 事件线程：解包 Event.data；有 portal 时串行化到 portal 线程处理。"""
        try:
            item = getattr(event, "data", event)
            if self._portal is not None:
                self._portal.start_task_soon(self._process_algo_event, item)
            else:
                self._process_algo_event(item)
        except Exception as e:
            from faos_app.log import log

            log(f"FAOS_ALGO_HANDLER_FAIL {e}")

    # -- SpreadData ---------------------------------------------------------

    def ensure_spread(self, strategy: Strategy) -> SpreadData:
        spread = self._spreads.get(strategy.pair)
        if spread is not None:
            return spread
        leg_a, leg_b = vt_symbol_of(strategy.leg_a), vt_symbol_of(strategy.leg_b)
        if self._engine is not None:
            # 真实 SpreadDataEngine.add_spread 以设置字典入参并自行构造/注册 SpreadData
            # （save=False：策略唯一事实源是 strategies.json，不让引擎再持久化一份）
            self._install_algo_class()
            self._engine.add_spread(
                strategy.pair,
                [
                    {"vt_symbol": leg_a, "variable": "A", "trading_direction": 1, "trading_multiplier": 1},
                    {"vt_symbol": leg_b, "variable": "B", "trading_direction": -1, "trading_multiplier": -1},
                ],
                "A-B", leg_a, 1, save=False,
            )
            # 真实 SpreadEngine 的注册表在其 data_engine 上（Fake 直挂自身）
            data_engine = getattr(self._engine, "data_engine", self._engine)
            spread = data_engine.spreads[strategy.pair]
        else:
            leg_a_data, leg_b_data = LegData(leg_a), LegData(leg_b)
            spread = SpreadData(
                name=strategy.pair,
                legs=[leg_a_data, leg_b_data],
                variable_symbols={"A": leg_a, "B": leg_b},
                variable_directions={"A": 1, "B": -1},
                price_formula="A-B",
                trading_multipliers={leg_a: 1, leg_b: -1},
                active_symbol=leg_a,
                min_volume=1,
            )
        self._spreads[strategy.pair] = spread
        self._ctp.subscribe_symbols([leg_a, leg_b])
        return spread

    # -- SpreadExecutor -------------------------------------------------------

    def execute(self, strategy: Strategy, trigger) -> Optional[str]:
        if not self._ctp.is_ready():
            return None
        self.ensure_spread(strategy)
        if self._engine is None:
            return None
        import time as _time

        algoid: str = self._engine.start_algo(
            spread_name=strategy.pair,
            direction=_ACTION_DIRECTION[trigger.action],
            price=trigger.spread,
            volume=trigger.lots,
            payup=self._algo.payup,
            interval=self._algo.interval,
            lock=self._algo.lock,
            extra={
                "slip_a": strategy.slip_a,
                "slip_b": strategy.slip_b,
                "order_type_a": strategy.order_type_a,
                "order_type_b": strategy.order_type_b,
            },
        )
        if algoid:
            self._algo_started[algoid] = _time.monotonic()
            self._algo_direction[algoid] = _ACTION_DIRECTION[trigger.action]
            self._algo_action[algoid] = trigger.action
        return algoid or None

    def stop_algos(self, strategy: Strategy) -> None:
        for algoid in list(strategy.active_algos):
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            self._algo_direction.pop(algoid, None)
            self._algo_action.pop(algoid, None)
        strategy.active_algos = []

    def stop_algos_direction(self, strategy: Strategy, side: str) -> None:
        """方向级停止：仅停止 side（"short"|"long"）方向的运行中算法。"""
        from vnpy.trader.constant import Direction

        want = Direction.LONG if side == "long" else Direction.SHORT
        for algoid in list(strategy.active_algos):
            if self._algo_direction.get(algoid) != want:
                continue
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            self._algo_direction.pop(algoid, None)
            self._algo_action.pop(algoid, None)
            strategy.active_algos.remove(algoid)

    # -- 算法事件 ---------------------------------------------------------------

    def _watchdog_tick(self, now: float) -> None:
        """I8: 停止超时未完成的算法并告警（SpreadTaker 追价不会自止）。"""
        import time as _time

        timeout = getattr(self._algo, "timeout", 300)
        for algoid, started in list(self._algo_started.items()):
            if now - started <= timeout:
                continue
            self._algo_started.pop(algoid, None)
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            self._push.notice(
                "warn", f"价差算法 {algoid} 追价超时（{timeout} 秒未完成），已自动停止，请检查腿部持仓"
            )

    def start_watchdog(self, portal) -> None:
        """portal 线程上每 10 秒跑一次看门狗。"""

        async def loop():
            import anyio

            while True:
                await anyio.sleep(10)
                self._watchdog_tick(_time.monotonic())

        import time as _time  # noqa: F401  (loop 内使用)

        portal.start_task_soon(loop)

    def chasing_texts(self) -> dict:
        """每策略的追价文案（腿部卡片显示）：逐跳 = 各腿委托已发出次数。"""
        out = {}
        for pair, it in self._algo_latest.items():
            hops = ""
            if hasattr(it, "chase_a"):
                hops = f"A 已报 {it.chase_a} 跳、B 已报 {it.chase_b} 跳，"
            out[pair] = f"追价中：{hops}成交 {it.traded_volume:.0f}/{it.volume:.0f}"
        return out

    def _process_algo_event(self, item: AlgoItem) -> None:
        self._algo_latest[item.spread_name] = item  # 未完成算法的最新快照
        d = asdict(item)
        d["direction"] = getattr(item.direction, "value", str(item.direction))
        d["status"] = getattr(item.status, "value", str(item.status))
        self._push.algo(d)
        if item.status not in _FINISHED_STATUS:
            return
        self._algo_latest.pop(item.spread_name, None)  # 完成/停止 → 清除追价文案
        s = self._store.get_by_pair(item.spread_name) if self._store else None
        if s is None:
            return
        self._algo_started.pop(item.algoid, None)
        self._algo_direction.pop(item.algoid, None)
        action = self._algo_action.pop(item.algoid, None)
        if item.traded_volume > 0:
            tpl = _ACTION_DONE_NOTICE.get(action, "价差算法完成（成交 {v:.0f} 份）")
            self._push.notice("success", tpl.format(v=item.traded_volume))
        signed = item.traded_volume if item.direction == Direction.LONG else -item.traded_volume
        s.held_units = round(s.held_units + signed, 6)
        self._store.update(s)
        self._push.strategy(s)
        if self._monitor is not None:
            self._monitor.on_algo_finished(s.id, item.algoid)
