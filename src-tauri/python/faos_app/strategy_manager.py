"""策略管理器：创建/订阅/删除/停止/触发单挂管（行情订阅=本地 CTP 腿订阅，服务端行情链路已退役）。"""

from datetime import datetime
from typing import Callable, Optional
from uuid import uuid4

from faos_app.models import SpreadQuote, Strategy, StrategyStatus, Trigger, TriggerAction
from faos_app.store import StrategyStore

_OPEN_ACTIONS = {TriggerAction.open_short, TriggerAction.open_long}
_MANUAL_FIELD = {
    TriggerAction.open_short: "manual_open_short",
    TriggerAction.open_long: "manual_open_long",
    TriggerAction.close_short: "manual_close_short",
    TriggerAction.close_long: "manual_close_long",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StrategyManager:
    def __init__(self, store: StrategyStore, push,
                 legs_subscriber: Optional[Callable[[list], None]] = None,
                 contracts_provider: Optional[Callable[[], list]] = None,
                 contract_stale_notifier: Optional[Callable[[str], None]] = None,
                 settings=None):
        self._store = store
        self._push = push
        self._legs_subscriber = legs_subscriber or (lambda symbols: None)  # CTP 行情订阅两腿
        self._contracts_provider = contracts_provider or (lambda: [])      # vnpy 合约列表（品种展开用）
        self._contract_stale_notifier = contract_stale_notifier or (lambda product: None)
        self._settings = settings
        self._dominant_hints: dict[str, dict] = {}  # product -> {vt_symbol: 持仓量}（市场实测）
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
        s.status = StrategyStatus.active  # 本地创建即生效（无服务端回执）
        self._store.add(s)
        self._legs_subscriber([f"{leg_a}.SHFE", f"{leg_b}.SHFE"])  # CTP 订阅两腿行情
        self._push.strategy(s)
        return s

    def create_product(self, product: str, single_lots: float, total_lots: float) -> tuple[int, int]:
        """品种展开：B 腿固定为品种主力合约（配置覆盖>最近未来月），A 腿=其余合约依次配对。"""
        product = product.strip()
        if not product.isalpha():
            raise ValueError("品种代码应为纯字母，如 ag")
        from faos_app.contract_seed import symbols_for_product

        matched, seed_dominant, stale = symbols_for_product(
            product, self._contracts_provider())
        # 主力优先级：用户显式配置（月号或全名）> 市场持仓量实测 > 种子显式值 > 日期推断
        want = self._dominant_overrides().get(product.lower())
        configured = next((s for s in matched if s.endswith(str(want))), None) if want else None
        dominant = configured or self._dominant_hint_of(product, matched) or seed_dominant
        if not dominant:
            dominant = matched[0] if matched else ""
        if stale:  # 来源为种子/兜底（非开盘或 OMS 未就绪），提醒后仍可用（离线测试）
            self._contract_stale_notifier(product)
        if not matched:
            raise ValueError(f"品种 {product} 无可用合约（开盘连接 CTP 后自动刷新合约表；或检查品种代码）")
        others = [s for s in matched if s != dominant]
        created, legs = 0, []
        for sym in others:  # 组合：sym&主力（B 腿=主力，被动对冲腿）
            pair = f"{sym}&{dominant}"
            if self._find_by_pair(pair) is not None:
                continue
            s = Strategy(id=self._store.next_id(), leg_a=sym, leg_b=dominant,
                         product=product, single_lots=single_lots, total_lots=total_lots,
                         status=StrategyStatus.active)
            self._store.add(s)
            self._push.strategy(s)
            legs += [f"{sym}.SHFE", f"{dominant}.SHFE"]
            created += 1
        if legs:
            self._legs_subscriber(sorted(set(legs)))
        existing = sum(1 for s in self._store.list() if self._product_of(s.pair) == product)
        return created, existing

    def _dominant_overrides(self) -> dict:
        """主力配置覆盖（settings.product_dominant，可选）。"""
        return dict(getattr(self._settings, "product_dominant", None) or {})

    def set_dominant_hint(self, product: str, oi_map: dict) -> None:
        """市场持仓量提示（品种订阅前实测；create_product 时优先于日期推断）。"""
        product = product.strip().lower()
        self._dominant_hints[product] = dict(oi_map or {})
        try:  # 市场主力回写种子（离线兜底与下次刷新都用市场实测值）
            from faos_app.contract_seed import update_seed_dominant

            ranked = sorted(((v, c) for c, v in self._dominant_hints[product].items() if v > 0),
                            reverse=True)
            if ranked:
                update_seed_dominant(product, ranked[0][1].split(".")[0])
        except Exception:
            pass

    def _dominant_hint_of(self, product: str, matched: list) -> Optional[str]:
        hint = self._dominant_hints.get(product.strip().lower())
        if not hint:
            return None
        ranked = sorted(((oi, s) for s, oi in hint.items() if oi > 0), reverse=True)
        for _, vt in ranked:
            sym = vt.split(".")[0]
            if sym in matched:
                return sym
        return None

    def subscribe_product_contracts(self, product: str) -> list:
        """订阅该品种全部合约行情（为持仓量实测供数）；返回裸合约号列表。"""
        from faos_app.contract_seed import symbols_for_product

        matched, _, _ = symbols_for_product(product.strip().lower(), self._contracts_provider())
        if matched:
            self._legs_subscriber([f"{s}.SHFE" for s in matched])
        return matched

    def subscribe(self, strategy_id: int) -> Strategy:
        s = self._require(strategy_id)
        self._legs_subscriber([f"{s.leg_a}.SHFE", f"{s.leg_b}.SHFE"])
        if s.status in (StrategyStatus.unsubscribed, StrategyStatus.stopped):
            s.status = StrategyStatus.pending
            self._store.update(s)
            self._push.strategy(s)
        return s

    def delete(self, strategy_id: int) -> list[Strategy]:
        s = self._require(strategy_id)
        for hook in self.on_stop_hooks:  # 撤算法/触发单，避免孤儿腿
            hook(s)
        self._store.remove(strategy_id)
        self._push.strategy_deleted(strategy_id)
        return self._store.list()

    def delete_product(self, product: str) -> list[Strategy]:
        """品种退订：删除该品种全部策略并一次批量退订行情。"""
        product = product.strip().lower()
        if not product.isalpha():
            raise ValueError("品种代码应为纯字母，如 ag")
        matched = [s for s in self._store.list()
                   if self._product_of(s.pair).lower() == product]
        for s in matched:
            for hook in self.on_stop_hooks:
                hook(s)
            self._store.remove(s.id)
            self._push.strategy_deleted(s.id)
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
                   slip_b=None, order_type_a=None, order_type_b=None,
                   max_cancels_a=None) -> Strategy:
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
        if max_cancels_a is not None:
            if max_cancels_a < 1:
                raise ValueError("A 腿撤单上限必须为正数")
            s.max_cancels_a = int(max_cancels_a)
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
        setattr(s, _MANUAL_FIELD[action], spread)  # 下单价保存进策略价差（行情退役后差价来源=人工）
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

    # -- 行情驱动 -------------------------------------------------------------

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
