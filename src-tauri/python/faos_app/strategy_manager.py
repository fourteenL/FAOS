"""策略管理器：创建/订阅/删除/停止/触发单挂管 + MQTT 回执与行情驱动的状态迁移。"""

from datetime import datetime
from typing import Callable, Optional
from uuid import uuid4

from faos_app.models import Strategy, StrategyStatus, Trigger, TriggerAction
from faos_app.mqtt_feed import MqStatus, SpreadQuote
from faos_app.store import StrategyStore

_OPEN_ACTIONS = {TriggerAction.open_short, TriggerAction.open_long}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StrategyManager:
    def __init__(self, store: StrategyStore, feed, push):
        self._store = store
        self._feed = feed
        self._push = push
        self._product_requests: dict[str, tuple[float, float]] = {}  # product -> (single, total)
        # 停止策略时的扩展点（T10 的 spread_service 注册撤算法钩子）
        self.on_stop_hooks: list[Callable[[Strategy], None]] = []
        # 方向级停止扩展点：hook(strategy, side)，side ∈ {"short","long"}
        self.on_direction_stop_hooks: list[Callable[[Strategy, str], None]] = []
        self.ORDER_TYPES = ("对手价", "挂价", "最新价", "市价")

    @property
    def store(self) -> StrategyStore:
        return self._store

    @property
    def push(self):
        return self._push

    @property
    def feed(self):
        return self._feed

    # -- 创建 / 生命周期 ------------------------------------------------------

    def create_pair(self, leg_a: str, leg_b: str, single_lots: float, total_lots: float) -> Strategy:
        leg_a, leg_b = leg_a.strip(), leg_b.strip()
        if not leg_a or not leg_b or leg_a == leg_b:
            raise ValueError("合约对无效：两腿必须不同且非空")
        if "&" in leg_a or "&" in leg_b:
            raise ValueError("合约号不能包含 &")
        if self._find_by_pair(f"{leg_a}&{leg_b}") is not None:
            raise ValueError(f"合约对 {leg_a}&{leg_b} 已存在（同一行情流不允许重复策略）")
        s = Strategy(
            id=self._store.next_id(), leg_a=leg_a, leg_b=leg_b,
            single_lots=single_lots, total_lots=total_lots,
        )
        self._store.add(s)
        # 创建即订阅（用户显式动作）；订阅状态见策略状态机
        self._feed.subscribe_pairs([s.pair])
        s.status = StrategyStatus.pending
        self._store.update(s)
        self._push.strategy(s)
        return s

    def create_product(self, product: str, single_lots: float, total_lots: float) -> tuple[int, int]:
        product = product.strip()
        if not product.isalpha():
            raise ValueError("品种代码应为纯字母，如 ag")
        # 品种条目直接发 tq/sub（服务端展开并推送该品种全部组合）；
        # 已存在的行在下一笔回执时更新单份/总份（重复订阅不再"无反应"）
        self._product_requests[product] = (single_lots, total_lots)
        self._feed.subscribe_pairs([product])
        existing = sum(1 for s in self._store.list() if self._product_of(s.pair) == product)
        return 0, existing

    def subscribe(self, strategy_id: int) -> Strategy:
        s = self._require(strategy_id)
        self._feed.subscribe_pairs([s.pair])
        if s.status in (StrategyStatus.unsubscribed, StrategyStatus.stopped):
            s.status = StrategyStatus.pending
            self._store.update(s)
            self._push.strategy(s)
        return s

    def delete(self, strategy_id: int) -> list[Strategy]:
        s = self._require(strategy_id)
        for hook in self.on_stop_hooks:  # 撤算法/触发单，避免孤儿腿
            hook(s)
        self._feed.unsubscribe_pairs([s.pair])
        self._store.remove(strategy_id)
        return self._store.list()

    def stop(self, strategy_id: int) -> Strategy:
        """停止：撤触发单+停算法，策略保持在线（active）——重启式下单需可再挂单。"""
        s = self._require(strategy_id)
        for hook in self.on_stop_hooks:
            hook(s)
        s.triggers = []
        s.active_algos = []
        s.status = StrategyStatus.active  # 一律保持在线（pending 期间停止同样转 active，回执由严格同步处理）
        self._store.update(s)
        self._push.strategy(s)
        return s

    def set_config(self, strategy_id: int, slip_a=None,
                   slip_b=None, order_type_a=None, order_type_b=None) -> Strategy:
        """策略交易参数：AB 腿各自的滑点与报单类型。"""
        s = self._require(strategy_id)
        if slip_a is not None:
            if slip_a < 0:
                raise ValueError("A 腿滑点不能为负数")
            s.slip_a = slip_a
        if slip_b is not None:
            if slip_b < 0:
                raise ValueError("B 腿滑点不能为负数")
            s.slip_b = slip_b
        for field, val in (("order_type_a", order_type_a), ("order_type_b", order_type_b)):
            if val is not None:
                if val not in self.ORDER_TYPES:
                    raise ValueError(f"报单类型无效，应为 {'/'.join(self.ORDER_TYPES)}")
                setattr(s, field, val)
        self._store.update(s)
        self._push.strategy(s)
        return s

    def stop_direction(self, strategy_id: int, side: str) -> Strategy:
        """方向级停止：撤销该方向触发单（做空侧=空开+空平），停该方向算法，持仓保留。"""
        if side not in ("short", "long"):
            raise ValueError(f"未知方向: {side}")
        s = self._require(strategy_id)
        side_actions = {
            "short": {TriggerAction.open_short, TriggerAction.close_short},
            "long": {TriggerAction.open_long, TriggerAction.close_long},
        }[side]
        s.triggers = [t for t in s.triggers if t.action not in side_actions]
        for hook in self.on_direction_stop_hooks:
            hook(s, side)
        if not s.active_algos:
            s.status = StrategyStatus.armed if s.triggers else StrategyStatus.active
        self._store.update(s)
        self._push.strategy(s)
        return s

    def set_lots(self, strategy_id: int, single_lots: Optional[float] = None,
                 total_lots: Optional[float] = None) -> Strategy:
        s = self._require(strategy_id)
        if single_lots is not None:
            if single_lots <= 0:
                raise ValueError("单份必须为正数")
            s.single_lots = single_lots
        if total_lots is not None:
            if total_lots < 0:
                raise ValueError("总份不能为负数")
            s.total_lots = total_lots
        if s.single_lots > s.total_lots:
            raise ValueError("单份不能大于总份")
        self._store.update(s)
        self._push.strategy(s)
        return s

    # -- 触发单 ---------------------------------------------------------------

    def place_trigger(self, strategy_id: int, action: TriggerAction, spread: float,
                      lots: Optional[float] = None) -> Trigger:
        s = self._require(strategy_id)
        if s.status == StrategyStatus.stopped:
            raise ValueError("策略已停止，无法挂触发单")
        lots = s.single_lots if lots is None else lots
        if lots <= 0:
            raise ValueError("手数必须为正数")
        has_open = any(t.action in _OPEN_ACTIONS for t in s.triggers)
        if action in _OPEN_ACTIONS and has_open:
            raise ValueError("已存在未触发的开仓触发单（每策略同时仅一张）")
        same_close = any(t.action == action for t in s.triggers)
        if same_close:
            raise ValueError("该方向平仓触发单已存在")
        if action == TriggerAction.close_short and s.held_units >= 0:
            raise ValueError("无空头价差持仓，无法挂空平触发单")
        if action == TriggerAction.close_long and s.held_units <= 0:
            raise ValueError("无多头价差持仓，无法挂多平触发单")
        if action in _OPEN_ACTIONS and abs(s.held_units) + lots > s.total_lots:
            raise ValueError(
                f"超出总份限制：已持仓 {abs(s.held_units)} 份 + 本次 {lots} 份 > 总份 {s.total_lots}"
            )
        t = Trigger(trigger_id=f"t{uuid4().hex[:8]}", action=action, spread=spread, lots=lots)
        s.triggers.append(t)
        if s.status in (StrategyStatus.active, StrategyStatus.unsubscribed, StrategyStatus.pending):
            s.status = StrategyStatus.armed
        self._store.update(s)
        self._push.strategy(s)
        return t

    def cancel_trigger(self, strategy_id: int, trigger_id: str) -> Strategy:
        s = self._require(strategy_id)
        s.triggers = [t for t in s.triggers if t.trigger_id != trigger_id]
        if s.status == StrategyStatus.armed and not s.triggers:
            s.status = StrategyStatus.active
        self._store.update(s)
        self._push.strategy(s)
        return s

    # -- 重启恢复 ---------------------------------------------------------------

    def recover(self) -> None:
        """启动恢复：清除运行中算法状态；触发单保留并重挂（armed）；
        旧默认交易参数（5/5/对手价/对手价）归一为新默认（A挂价+0 / B对手价+100）。"""
        for s in self._store.list():
            changed = False
            if (s.slip_a, s.slip_b, s.order_type_a, s.order_type_b) == (5, 5, "对手价", "对手价"):
                s.slip_a, s.slip_b = 0, 100
                s.order_type_a, s.order_type_b = "挂价", "对手价"
                changed = True
            if s.active_algos:
                s.active_algos = []
                self._push.notice(
                    "warn",
                    f"策略 #{s.id} 重启前存在未完成价差单，已清除算法状态，请人工核对持仓",
                )
                changed = True
            if s.status == StrategyStatus.executing:
                s.status = StrategyStatus.armed if s.triggers else StrategyStatus.active
                changed = True
            if changed:
                self._store.update(s)
                self._push.strategy(s)

    def reconcile_holdings(self, ctp) -> None:
        """CTP 就绪后以实际持仓校正 held_units（未配对敞口不计入份数）。"""
        from faos_app.spread_service import vt_symbol_of

        def side(sym, want):
            total = 0
            for p in ctp.get_positions():
                v = getattr(p.direction, "value", str(p.direction))
                d = {"多": "long", "空": "short"}.get(v, str(v).lower())
                if p.vt_symbol == sym and d == want:
                    total += p.volume
            return total

        for s in self._store.list():
            a, b = vt_symbol_of(s.leg_a), vt_symbol_of(s.leg_b)
            long_units = min(side(a, "long"), side(b, "short"))
            short_units = min(side(a, "short"), side(b, "long"))
            lots = s.single_lots or 1
            held = round(float(long_units - short_units) / lots, 6)
            if held != s.held_units:
                s.held_units = held
                self._store.update(s)
                self._push.strategy(s)
                self._push.notice(
                    "info",
                    f"策略 #{s.id} 持仓校正为 {abs(held)} 份（{'空头价差' if held < 0 else '多头价差' if held > 0 else '无持仓'}）",
                )

    # -- MQTT 回执 / 行情驱动 ---------------------------------------------------

    def on_mq_status(self, status: MqStatus) -> None:
        for bad in status.invalid:
            self._push.notice("warn", f"订阅条目无效: {bad}")

        subscribed = set(status.subscribed)
        pending = set(status.pending)

        # 品种展开：subscribed 中出现尚无策略的 pair → 建策略；
        # 已存在且其品种在本会话被重新订阅过 → 更新单份/总份（参数生效）
        for pair in sorted(subscribed | pending):
            if "&" not in pair:
                continue
            product = self._product_of(pair)
            existing = self._find_by_pair(pair)
            lots = self._product_requests.get(product)
            if existing is not None:
                if lots and (existing.single_lots, existing.total_lots) != lots:
                    existing.single_lots, existing.total_lots = lots
                    self._store.update(existing)
                    self._push.strategy(existing)
                continue
            use_lots = lots or (1, 1)
            leg_a, _, leg_b = pair.partition("&")
            s = Strategy(id=self._store.next_id(), leg_a=leg_a, leg_b=leg_b,
                         product=product or None, single_lots=use_lots[0], total_lots=use_lots[1],
                         status=StrategyStatus.pending)
            self._store.add(s)
            self._push.strategy(s)

        # 订阅列表与 MQTT 严格同步：服务端没有的一律删除（含 stopped/unsubscribed）
        from datetime import datetime as _dt

        now = _dt.now()
        for s in list(self._store.list()):
            if s.pair in subscribed or s.pair in pending:
                continue
            if s.status == StrategyStatus.pending:
                # 刚创建的 pending 等待回执（防旧回执竞态误删）；超 15 秒无回执 = 订阅未生效
                try:
                    age = (now - _dt.fromisoformat(s.created_at)).total_seconds()
                except Exception:
                    age = 999
                if age > 15:
                    self._store.remove(s.id)
                    self._push.strategy_deleted(s.id)
                    self._push.notice("warn", f"策略 #{s.id} {s.pair} 订阅未生效，已移除")
                continue
            self._store.remove(s.id)
            self._push.strategy_deleted(s.id)
            self._push.notice("warn", f"策略 #{s.id} {s.pair} 订阅已失效，已移除")

    def on_spread(self, pair: str, quote: SpreadQuote) -> None:
        s = self._find_by_pair(pair)
        if s is None:
            return
        if s.status == StrategyStatus.pending:
            s.status = StrategyStatus.active
            self._store.update(s)
            self._push.strategy(s)

    # -- 内部 -----------------------------------------------------------------

    def _require(self, strategy_id: int) -> Strategy:
        s = self._store.get(strategy_id)
        if s is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        return s

    def _find_by_pair(self, pair: str) -> Optional[Strategy]:
        for s in self._store.list():
            if s.pair == pair:
                return s
        return None

    @staticmethod
    def _product_of(pair: str) -> str:
        leg = pair.partition("&")[0]
        return "".join(ch for ch in leg if ch.isalpha())
