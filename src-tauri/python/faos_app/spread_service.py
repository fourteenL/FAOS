"""价差执行服务：SpreadData 定义 + SpreadTaker 算法执行器（SpreadExecutor 实现）。

- 每策略一个 SpreadData：price_formula "A-B"，A 腿主动（吃价追价），B 腿（主力）被动对冲
- execute()：CTP 未就绪返回 None（触发单挂起）；否则 start_algo
- EVENT_SPREAD_ALGO 事件 → 推前端；算法结束 → held_units 累计 + monitor.on_algo_finished
"""

from dataclasses import asdict
from typing import Optional

from vnpy.trader.constant import Direction, Status
from vnpy.trader.event import EVENT_ORDER, EVENT_TRADE
from vnpy_spreadtrading import APP_NAME
from vnpy_spreadtrading.base import EVENT_SPREAD_ALGO, AlgoItem, LegData, SpreadData

from faos_app.models import Strategy, StrategyStatus, TriggerAction

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
        self._strategy_stopper = None  # 触限急停回调（main 注入 manager.stop）
        self._cap_halted: set = set()  # 已触限急停的策略（新算法启动成功后复位）
        self._algo_direction: dict[str, object] = {}  # algoid -> Direction（方向停止用）
        self._algo_action: dict[str, object] = {}  # algoid -> TriggerAction（成功通知文案用）
        self._algo_close_mark: dict[str, bool] = {}  # algoid -> 是否平仓单（反向开仓，通知文案用）
        self._algo_rearm: dict = {}   # algoid -> (strategy_id, action, spread, lots) 看门狗超时重挂用
        self._algo_traded: dict[str, float] = {}  # algoid -> 已成交量（重挂判定）
        self._rearm_fn = None  # 超时重挂回调（main 注入 manager.place_trigger）
        self._algo_latest: dict[str, "AlgoItem"] = {}  # pair -> 最新算法快照（追价文案用）
        self._algo_wait_warned: set = set()  # pair -> 已告警（盘口停滞提醒一次性）
        # 策略级今昨账本（2026-10-19 定稿，修平仓归属错位）：本策略算法成交入账。
        # 归属：算法委托 reference="SpreadTrading_{pair}" → 委托号→pair 映射 → 成交入账。
        self._order_pair: dict[str, str] = {}  # vt_orderid -> pair（算法委托）
        self._booked_tradeids: set = set()  # 已入账成交号（事件重放去重）
        # 主动撤（停止/超时/急停/退出）的委托号：撤单回报不计入 A 腿撤单数
        # （2026-10-22 实证：停止策略撤单数也涨；手动撤单被算进所有 A 腿同符号策略）
        self._stop_cancelled: set = set()
        if event_engine is not None:
            self._install_algo_class()
            event_engine.register(EVENT_SPREAD_ALGO, self._wrap_algo_handler)
            event_engine.register(EVENT_ORDER, self._wrap_algo_handler)
            event_engine.register(EVENT_TRADE, self._wrap_algo_handler)

    def attach(self, engine, event_engine) -> None:
        """main.py 装配时注入真实 SpreadEngine（add_app 返回值）。"""
        self._engine = engine
        self._install_algo_class()
        event_engine.register(EVENT_SPREAD_ALGO, self._wrap_algo_handler)
        event_engine.register(EVENT_ORDER, self._wrap_algo_handler)
        event_engine.register(EVENT_TRADE, self._wrap_algo_handler)

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

    def set_strategy_stopper(self, fn) -> None:
        """触限急停回调（main 注入 manager.stop：撤触发单+停算法+状态复位）。"""
        self._strategy_stopper = fn

    def _halt_on_cap(self, strategy: Strategy) -> None:
        """A 腿撤单触限急停（2026-10-14 用户定稿）：只弹窗拒新单不够——追价挂单
        还留在柜台；须马上停止策略并撤销两腿全部活动委托（他策略的挂单不动）。"""
        if strategy.id in self._cap_halted:
            return
        self._cap_halted.add(strategy.id)
        from faos_app.log import log

        log(f"FAOS_CANCEL_CAP_HALT #{strategy.id} {strategy.pair} "
            f"撤单 {strategy.cancel_count_a}/{strategy.max_cancels_a}")
        if self._strategy_stopper is not None:
            try:
                self._strategy_stopper(strategy.id)
            except Exception as e:
                log(f"FAOS_CANCEL_CAP_HALT_FAIL #{strategy.id} {e}")
        cancelled = 0
        for leg in (strategy.leg_a, strategy.leg_b):
            sym = vt_symbol_of(leg)
            for o in list(self._ctp.get_all_active_orders()):
                if o.vt_symbol != sym:
                    continue
                try:
                    self._ctp.cancel_order(o.vt_orderid)
                    cancelled += 1
                except Exception:
                    pass
        self._push.notice(
            "error",
            f"策略 #{strategy.id} A 腿撤单达上限 {strategy.max_cancels_a}，"
            f"已停止策略并撤销 {cancelled} 笔挂单（持仓保留）；"
            "请检查行情/追价参数后调大上限再重新挂单")

    # -- SpreadExecutor -------------------------------------------------------

    def execute(self, strategy: Strategy, trigger) -> Optional[str]:
        if strategy.cancel_count_a >= strategy.max_cancels_a:
            raise ValueError(
                f"A 腿撤单已达上限 {strategy.max_cancels_a}（当前 {strategy.cancel_count_a}），"
                "暂停下单，请检查行情/参数后在策略管理调整上限")
        if not self._ctp.is_ready():
            return None
        self.ensure_spread(strategy)
        if self._engine is None:
            return None
        import time as _time

        # 平仓通道=按钮专用（2026-10-16 定稿修订）：按挂单 is_close 标记判定，
        # 不再看方向与持仓的关系——手动反向挂单=正常开仓（锁仓，不净任何持仓）
        closing = bool(getattr(trigger, "is_close", False))
        # 平仓显式今昨拆分（2026-10-19 定稿，修平仓归属错位）：共享主力腿的两策略
        # 各自平仓时，账户级净仓转换会错吃对方的今昨仓——按本策略账本显式发
        # 平今/平昨单（先平昨省费）；账本不足的量在算法内回落账户级
        extra = {
            "slip_a": strategy.slip_a,
            "slip_b": strategy.slip_b,
            "order_type_a": strategy.order_type_a,
            "order_type_b": strategy.order_type_b,
            "closing": closing,  # 开仓显式 OPEN / 平仓显式平今平昨（算法内分流，2026-10-22）
        }
        if closing:
            split = self._plan_close_split(strategy, trigger)
            if split:
                extra["close_split"] = split
        # 策略隔离（2026-10-14 用户实证三问题同根：共享主力腿+净仓转换互相吃腿）：
        # 开仓锁仓（OPEN 永不净转换→上期所双向持仓，各策略腿物理隔离，也避免
        # 平今/平昨错配被柜台拒单）；反向平仓保持净仓（实际对冲本策略两腿）
        algoid: str = self._engine.start_algo(
            spread_name=strategy.pair,
            direction=_ACTION_DIRECTION[trigger.action],
            price=trigger.spread,
            volume=trigger.lots,
            payup=self._algo.payup,
            interval=self._algo.interval,
            lock=self._algo.lock or not closing,
            extra=extra,
        )
        if algoid:
            self._algo_started[algoid] = _time.monotonic()
            self._algo_direction[algoid] = _ACTION_DIRECTION[trigger.action]
            self._algo_action[algoid] = trigger.action
            self._cap_halted.discard(strategy.id)  # 上限调大后恢复交易：急停标记复位
            self._algo_close_mark[algoid] = closing
            # 重挂要保留平仓通道身份（2026-10-21 修正：旧实现超时重挂把平仓单变成
            # 普通开仓单，会反向开新仓）
            self._algo_rearm[algoid] = (strategy.id, trigger.action, trigger.spread,
                                        trigger.lots, closing)
            self._algo_traded[algoid] = 0
        return algoid or None

    def account_close_pool(self, strategy: Strategy, action) -> float:
        """账户实际可平份数（按策略两腿取两侧最小，2026-10-21）：策略账本为 0 时
        平仓按钮的兜底池——柜台有仓但 FAOS 账本未记录（老策略/手动交易/账本漂移）
        时仍可平，避免「无限易明明有仓、FAOS 报无持仓」。"""
        from faos_app.leg_ops import account_position_book

        try:
            if not self._ctp.is_ready():
                return None  # 柜台数据未知（未连接）：由策略账本独自判断
            book = account_position_book(self._ctp.get_positions())
        except Exception:
            return None
        a = book.get(vt_symbol_of(strategy.leg_a)) or {}
        b = book.get(vt_symbol_of(strategy.leg_b)) or {}
        tot = lambda row, d: row.get(f"{d}_today", 0) + row.get(f"{d}_yd", 0)
        if action == TriggerAction.open_long:  # 平空：A 买回（需 A 空）、B 卖出（需 B 多）
            return min(tot(a, "short"), tot(b, "long"))
        return min(tot(a, "long"), tot(b, "short"))  # 平多：A 卖（需 A 多）、B 买（需 B 空）

    def _mark_stopping(self, algoid: str) -> None:
        """记录该算法在途委托号：随后到达的撤单回报属主动撤，不计撤单数。

        真实引擎 algo_engine.algos[algoid].leg_orders 可取到在途单号；
        Fake/异常时静默（撤单回报计数按 reference 归属仍生效）。"""
        eng = self._engine
        if eng is None:
            return
        ae = getattr(eng, "algo_engine", eng)
        algos = getattr(ae, "algos", None)
        if not isinstance(algos, dict):
            return
        algo = algos.get(algoid)
        if algo is None:
            return
        for ids in getattr(algo, "leg_orders", {}).values():
            self._stop_cancelled.update(ids)
        if len(self._stop_cancelled) > 20000:  # 防御：无撤单回报时也有限
            self._stop_cancelled.clear()

    def stop_algos(self, strategy: Strategy) -> None:
        for algoid in list(strategy.active_algos):
            self._mark_stopping(algoid)
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            self._algo_direction.pop(algoid, None)
            self._algo_action.pop(algoid, None)
            self._algo_close_mark.pop(algoid, None)
        strategy.active_algos = []

    def stop_algos_direction(self, strategy: Strategy, side: str) -> None:
        """方向级停止：仅停止归属 side（"short"|"long"）的运行中算法。

        归属（2026-10-20 夜盘实证修订）：平仓通道算法反向归属——平空仓的
        open_long(LONG) 算法属做空侧（用户在做空区操作平仓），平多仓的
        open_short(SHORT) 算法属做多侧；普通算法按 direction 本侧。
        （旧逻辑按 direction 直判：平空算法被归做多侧 → 做空区停止无效。）"""
        from vnpy.trader.constant import Direction

        def algo_on_side(algoid) -> bool:
            action = self._algo_action.get(algoid)
            closing = self._algo_close_mark.get(algoid, False)
            d = self._algo_direction.get(algoid)
            base = "long" if d == Direction.LONG else "short"
            if action is None or not closing:
                return base == side
            action_base = "long" if action == TriggerAction.open_long else "short"
            if action in (TriggerAction.open_long, TriggerAction.open_short):
                return action_base != side  # 平仓通道反向单：反转归属
            return base == side

        for algoid in list(strategy.active_algos):
            if not algo_on_side(algoid):
                continue
            self._mark_stopping(algoid)
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            self._algo_direction.pop(algoid, None)
            self._algo_action.pop(algoid, None)
            self._algo_close_mark.pop(algoid, None)
            strategy.active_algos.remove(algoid)

    # -- 算法事件 ---------------------------------------------------------------

    def set_rearm_fn(self, fn) -> None:
        """看门狗超时重挂回调（main 注入 manager.place_trigger）。"""
        self._rearm_fn = fn

    def _watchdog_tick(self, now: float) -> None:
        """I8: 停止超时未完成的算法并告警（SpreadTaker 追价不会自止）。

        2026-10-16 用户实证：追价 300s 超时杀算法后触发单已消费——挂单"自动消失"。
        零成交的超时改为原价重挂触发单（行情恢复自动再触发）；部分成交不重挂（人工核对）。"""
        import time as _time

        timeout = getattr(self._algo, "timeout", 300)
        for algoid, started in list(self._algo_started.items()):
            if now - started <= timeout:
                continue
            self._algo_started.pop(algoid, None)
            self._mark_stopping(algoid)
            if self._engine is not None:
                self._engine.stop_algo(algoid)
            rearm = self._algo_rearm.pop(algoid, None)
            traded = self._algo_traded.get(algoid, 0)
            if rearm is not None and traded == 0 and self._rearm_fn is not None:
                try:
                    sid, action, spread, lots, closing = rearm
                    self._rearm_fn(sid, action, spread, lots, is_close_order=closing)
                    self._push.notice(
                        "warn", f"价差算法 {algoid} 追价超时（{timeout} 秒未成交），已停止并"
                        + ("原价重挂平仓触发单" if closing else "原价重挂触发单")
                        + "——行情恢复后自动再触发（挂单未丢失）")
                    continue
                except Exception as e:
                    from faos_app.log import log

                    log(f"FAOS_WATCHDOG_REARM_FAIL {algoid} {e}")
            self._push.notice(
                "warn", f"价差算法 {algoid} 追价超时（{timeout} 秒未完成），已自动停止，请检查腿部持仓"
            )
        self._cleanup_orphan_algos()

    def _engine_algos(self):
        """引擎当前在跑的 algoid 集合（真实引擎 algo_engine.algos；Fake/缺失返回 None）。"""
        eng = self._engine
        if eng is None:
            return None
        ae = getattr(eng, "algo_engine", eng)
        algos = getattr(ae, "algos", None)
        return set(algos.keys()) if isinstance(algos, dict) else None

    def _cleanup_orphan_algos(self) -> None:
        """引擎已无该算法但策略仍记在 active_algos（完成事件丢失）→ 清理状态。

        2026-10-22：旧实现靠 update_order 提前移除 ALLTRADED 委托来防「卡执行中」，
        代价是对冲腿被重复下单；改为此处按引擎实际状态自愈，不动委托/对冲逻辑。"""
        alive = self._engine_algos()
        if alive is None or self._store is None:
            return
        for s in self._store.list():
            changed = False
            for algoid in list(s.active_algos):
                if algoid in alive or algoid in self._algo_started:
                    continue  # 仍在引擎或仍在计时（刚启动）
                s.active_algos.remove(algoid)
                changed = True
                if self._monitor is not None:
                    self._monitor.on_algo_finished(s.id, algoid)
                self._push.notice(
                    "warn", f"策略 #{s.id} {s.pair} 算法 {algoid} 已结束但未收到完成事件，"
                    "状态已清理——请核对腿部/策略持仓")
            if changed:
                if not s.active_algos:
                    s.executing_action = None
                    s.status = (StrategyStatus.armed if s.triggers
                                else StrategyStatus.active)
                self._store.update(s)
                self._push.strategy(s)

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

    def _process_algo_event(self, item) -> None:
        if hasattr(item, "status") and hasattr(item, "vt_orderid") and not hasattr(item, "algoid"):
            self._process_order_event(item)  # OrderData（EVENT_ORDER）
            return
        if hasattr(item, "offset") and hasattr(item, "vt_orderid"):
            self._process_trade_event(item)  # TradeData（EVENT_TRADE）
            return
        self._process_spread_algo(item)

    def _process_order_event(self, order) -> None:
        """算法委托回报：登记委托号→策略（供成交入账归属）；撤单回报 → 风控计数（同单去重）。"""
        from vnpy.trader.constant import Status as _Status

        ref = getattr(order, "reference", "") or ""
        prefix = f"{APP_NAME}_"
        if ref.startswith(prefix) and order.vt_orderid not in self._order_pair:
            self._order_pair[order.vt_orderid] = ref[len(prefix):]
        if order.status != _Status.CANCELLED:
            return
        if order.vt_orderid in self._stop_cancelled:
            self._stop_cancelled.discard(order.vt_orderid)
            return  # 停止/超时/急停/退出导致的主动撤单不计（2026-10-22 用户定稿）
        # 只统计「本策略正在跑的算法」的追价撤单：委托→算法（引擎 order_algo_map），
        # 算法已停止/已不在运行（含上次会话遗留单的就绪清理）一律不计
        algo = self._algo_of_order(order)
        if algo is not None:
            if getattr(algo, "stopped", False) or                     getattr(algo, "algoid", None) not in self._algo_started:
                return
        for s in self._store.list() if self._store else []:
            if vt_symbol_of(s.leg_a) != order.vt_symbol:
                continue
            # 只统计本策略算法委托的追价撤单：reference 归属（手动/无限易/他策略不计，
            # 2026-10-22 实证：没启动策略撤单数也一直涨——共享 A 腿符号被误计）
            if ref != f"{APP_NAME}_{s.pair}":
                continue
            if order.vt_orderid in s.cancelled_orderids:
                return  # 同单重复回报只计一次
            s.cancelled_orderids.append(order.vt_orderid)
            s.cancel_count_a += 1
            self._store.update(s)
            self._push.strategy(s)
            if s.cancel_count_a >= s.max_cancels_a:
                self._halt_on_cap(s)
            return

    def _algo_of_order(self, order):
        """委托号 → 算法对象（真实引擎 order_algo_map；Fake/缺失返回 None）。"""
        eng = self._engine
        if eng is None:
            return None
        ae = getattr(eng, "algo_engine", eng)
        m = getattr(ae, "order_algo_map", None)
        if not isinstance(m, dict):
            return None
        return m.get(order.vt_orderid)

    def _process_trade_event(self, trade) -> None:
        """本策略算法成交 → 策略级今昨账本入账（2026-10-19 定稿，修平仓归属错位）。

        只入账本策略算法的成交（reference 归属）；手动交易/其他终端不动账本，
        差异由 reconcile_holdings 今昨对账告警兜底。入账前先做跨日结转（夜盘
        21:00 边界，今日新仓从今仓起算）。"""
        pair = self._order_pair.get(trade.vt_orderid)
        if not pair or self._store is None:
            return
        if trade.vt_tradeid in self._booked_tradeids:
            return  # 同一成交重复事件只入一次
        s = self._store.get_by_pair(pair)
        if s is None:
            return
        from datetime import datetime as _dt

        from faos_app.leg_book import apply_trade_to_book, roll_book

        self._booked_tradeids.add(trade.vt_tradeid)
        roll_book(s, _dt.now())
        apply_trade_to_book(s, trade.vt_symbol.split(".")[0],
                            getattr(trade.direction, "value", trade.direction),
                            getattr(trade.offset, "value", trade.offset),
                            trade.volume)
        self._store.update(s)
        self._push.strategy(s)
        from faos_app.log import log

        log(f"FAOS_LEG_BOOK #{s.id} {pair} {trade.vt_symbol} "
            f"{getattr(trade.direction, 'value', trade.direction)}@"
            f"{getattr(trade.offset, 'value', trade.offset)} "
            f"{trade.volume} → {s.leg_book.get(trade.vt_symbol.split('.')[0])}")

    def _plan_close_split(self, strategy: Strategy, trigger) -> Optional[dict]:
        """平仓显式今昨拆分（2026-10-19 定稿）：按策略账本 ∩ 账户实际，先平昨
        （上期所平今费率高）后期；账本未建/不足 → None（回落账户级净仓转换，
        行为与修复前一致，不更差）。"""
        from datetime import datetime as _dt

        from faos_app.leg_book import plan_close_split, roll_book
        from faos_app.leg_ops import account_position_book

        roll_book(strategy, _dt.now())
        try:
            actual = account_position_book(self._ctp.get_positions())
        except Exception:
            return None
        legs = [(strategy.leg_a, vt_symbol_of(strategy.leg_a)),
                (strategy.leg_b, vt_symbol_of(strategy.leg_b))]
        key = "long" if trigger.action == TriggerAction.open_short else "short"
        return plan_close_split(strategy, actual, legs, key, trigger.lots)

    def _process_spread_algo(self, item: AlgoItem) -> None:
        # 盘口停滞提醒：算法跑 20 秒一笔未发（count=秒）→ 大概率某腿无双边报价（死腿/收盘）
        if (item.status not in _FINISHED_STATUS and item.count >= 20
                and getattr(item, "chase_a", 0) == 0 and getattr(item, "chase_b", 0) == 0
                and item.spread_name not in self._algo_wait_warned):
            self._algo_wait_warned.add(item.spread_name)
            self._push.notice(
                "warn",
                f"{item.spread_name} 算法已启动但价差盘口无效（某腿可能无双边报价/行情停滞），"
                "暂不下单；请检查两腿合约流动性，长期无价差将超时自动停止")
        self._algo_latest[item.spread_name] = item  # 未完成算法的最新快照
        if item.algoid in self._algo_started:
            self._algo_traded[item.algoid] = item.traded_volume
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
        _done_rearm = self._algo_rearm.get(item.algoid)
        self._algo_rearm.pop(item.algoid, None)
        self._algo_traded.pop(item.algoid, None)
        action = self._algo_action.pop(item.algoid, None)
        closing = self._algo_close_mark.pop(item.algoid, False)
        if item.traded_volume > 0:
            if closing and action == TriggerAction.open_long:
                tpl = "平空完成（买回 {v:.0f} 份）{slip}"
            elif closing and action == TriggerAction.open_short:
                tpl = "平多完成（卖出 {v:.0f} 份）{slip}"
            else:
                tpl = _ACTION_DONE_NOTICE.get(action, "价差算法完成（成交 {v:.0f} 份）") + "{slip}"
            # 滑点提醒（2026-10-19 定稿）：成交均价较触发价的差 + A 腿追价次数
            slip = ""
            rearm = _done_rearm
            chase_a = getattr(item, "chase_a", 0)
            parts = []
            if rearm is not None and getattr(item, "traded_price", 0):
                diff = round(item.traded_price - rearm[2], 6)
                if abs(diff) > 1e-9:
                    good = "有利" if ((action == TriggerAction.open_long and diff < 0)
                                      or (action == TriggerAction.open_short and diff > 0)) else "不利"
                    parts.append(f"滑点 {abs(diff):g}（{good}，成交均价 {item.traded_price:g} vs 触发 {rearm[2]:g}）")
            if chase_a > 1:
                parts.append(f"A 腿追价 {chase_a - 1} 次")
            if parts:
                slip = "；" + "，".join(parts)
            self._push.notice("success", tpl.format(v=item.traded_volume, slip=slip))
        from faos_app.log import log

        log(f"FAOS_ALGO_DONE {item.spread_name} {item.status.value} traded={item.traded_volume}")
        # 双向记账（2026-10-20 引入，10-21 修正方向）：多头份/空头份分别累计。
        # 平仓通道成交是「消耗」不是「新增」——平空单 direction=LONG 但应减空头份
        # （2026-10-21 实证：按 direction 累加导致平仓后净额反增 +1、虚拟份数滚大
        #  → 总份校验失效（柜台 6 手/FAOS 显示 4））
        traced = item.traded_volume
        if closing and action == TriggerAction.open_long:      # 平空：买回消耗空头份
            s.held_short_units = round(s.held_short_units - traced, 6)
        elif closing and action == TriggerAction.open_short:   # 平多：卖出消耗多头份
            s.held_long_units = round(s.held_long_units - traced, 6)
        elif item.direction == Direction.LONG:
            s.held_long_units = round(s.held_long_units + traced, 6)
        else:
            s.held_short_units = round(s.held_short_units + traced, 6)
        if s.held_long_units < 0 or s.held_short_units < 0:
            log(f"FAOS_BOOK_NEGATIVE {item.spread_name} 账本为负（平仓量超出策略归属）"
                f" long={s.held_long_units:g} short={s.held_short_units:g}，已归零")
            s.held_long_units = max(0.0, s.held_long_units)
            s.held_short_units = max(0.0, s.held_short_units)
        s.held_units = round(s.held_long_units - s.held_short_units, 6)
        self._store.update(s)
        self._push.strategy(s)
        # 停止/超时路径的部分成交：两腿可能未对冲完（单腿敞口）——点名提醒核对
        if item.status == Status.CANCELLED and item.traded_volume > 0:
            self._push.notice(
                "warn",
                f"{item.spread_name} 算法停止时已成交 {item.traded_volume:.0f} 份，"
                "两腿可能未全部对冲（单腿敞口），请在腿部卡片/无限易核对持仓")
        if self._monitor is not None:
            self._monitor.on_algo_finished(s.id, item.algoid)

    def pending_units(self, strategy_id: int) -> float:
        """该策略在途算法的净敞口份数（已启动未完成）——挂单净额校验计入
        （2026-10-20 实证：执行中 held 未更新，1 秒内双挂同向单 → 总份 1 开成 2 份）。"""
        total = 0.0
        for rearm in self._algo_rearm.values():
            sid, action, _, lots, _closing = rearm
            if sid != strategy_id:
                continue
            # 净额口径：盘口方向即净额变化方向（开多/平空均为 +，开空/平多均为 -）
            total += lots if _ACTION_DIRECTION[action] == Direction.LONG else -lots
        return round(total, 6)
