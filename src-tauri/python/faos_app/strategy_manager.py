"""策略管理器：创建/订阅/删除/停止/触发单挂管（行情订阅=本地 CTP 腿订阅，服务端行情链路已退役）。"""

import re
from datetime import datetime
from typing import Callable, Optional
from uuid import uuid4

from faos_app.models import SpreadQuote, Strategy, StrategyStatus, Trigger, TriggerAction
from faos_app.store import StrategyStore

_OPEN_ACTIONS = {TriggerAction.open_short, TriggerAction.open_long}
_LIQUID_TOP_N = 6  # 活跃月份截取数（持仓量前 N 参与组合；远月无报价=价差无法合成）
_SYMBOL_RE = re.compile(r"^[a-zA-Z]{1,3}\d{3,4}$")  # 合约号=品种字母+月号（ag2706；裸 2706 无行情）


def _valid_symbol(sym: str) -> bool:
    return bool(_SYMBOL_RE.match(sym))


def _filter_liquid_months_impl(product, matched, oi_hint):
    """有持仓量数据时只保留 OI 前 N 的月份（死腿远月不参与组合）。"""
    if not oi_hint:
        return matched
    ranked = sorted(((v, s) for s, v in ((sym, oi_hint.get(f"{sym}.SHFE", 0)) for sym in matched)
                     if v and v > 0), reverse=True)
    if len(ranked) <= _LIQUID_TOP_N:
        return [s for _, s in ranked] if ranked else matched
    return sorted(s for _, s in ranked[:_LIQUID_TOP_N])


def _is_delivery_month(symbol: str) -> bool:
    """合约交割月=当前月（如 10 月的 ag2610）→ 临近交割流动性枯竭，不参与组合。"""
    from datetime import date

    from faos_app.contract_seed import _contract_month

    ym = _contract_month(symbol)
    if not ym:
        return False
    today = date.today()
    return ym == (today.year, today.month)


_MANUAL_FIELD = {
    TriggerAction.open_short: "manual_open_short",
    TriggerAction.open_long: "manual_open_long",
    TriggerAction.close_short: "manual_close_short",
    TriggerAction.close_long: "manual_close_long",
}
_CLOSE_COMPANION = {
    TriggerAction.open_short: "manual_close_short",
    TriggerAction.open_long: "manual_close_long",
    TriggerAction.close_short: "manual_open_short",
    TriggerAction.close_long: "manual_open_long",
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
        self._pending_units_provider = None  # 在途算法净敞口查询（main 注入 spread_service.pending_units）
        self._close_risk_locked = False      # 收盘风控下单锁（窗口内拒新挂单）
        self._account_pool_provider = None   # 账户可平份数查询（main 注入 spread_service.account_close_pool）
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

    def set_close_risk_lock(self, on: bool) -> None:
        """收盘风控下单锁（True=窗口内拒新挂单；时点后自动解除）。"""
        self._close_risk_locked = bool(on)

    def set_pending_units_provider(self, fn) -> None:
        """在途算法净敞口查询注入（main 装配：spread_service.pending_units）。"""
        self._pending_units_provider = fn

    def set_account_pool_provider(self, fn) -> None:
        """账户实际可平份数查询注入（main 装配：spread_service.account_close_pool）。

        平仓池兜底（2026-10-21）：策略账本为 0 但柜台有仓（老策略/手动交易/账本
        漂移）时，按账户实际持仓放行平仓——避免「无限易明明有仓、FAOS 报无持仓」。"""
        self._account_pool_provider = fn

    def _account_pool(self, strategy: "Strategy", action) -> Optional[float]:
        """柜台实际可平份数；None=未知（未连接/查询失败）。"""
        if self._account_pool_provider is None:
            return None
        try:
            v = self._account_pool_provider(strategy, action)
            return None if v is None else float(v)
        except Exception:
            return None

    def close_pool(self, strategy: "Strategy", action) -> float:
        """可平份数（平仓按钮/点火校验统一口径）：策略账本 ∩ 柜台实际。

        - 柜台未知（未连接）→ 只信账本（离线不误挡）
        - 账本为 0 而柜台有仓（老策略/手动交易/账本漂移）→ 按柜台实际
        - 两者都有 → 取小（绝不超出柜台可平量，防溢出量走净仓转换反向开新仓）"""
        ledger = (strategy.held_short_units if action == TriggerAction.open_long
                  else strategy.held_long_units)
        acct = self._account_pool(strategy, action)
        if acct is None:
            return ledger
        if ledger < 1e-9:
            return acct
        return min(ledger, acct)

    def _pending_units(self, strategy_id: int) -> dict:
        """在途开仓方向份数 {"long","short"}；provider 缺席/异常时全 0。"""
        if self._pending_units_provider is None:
            return {"long": 0.0, "short": 0.0}
        try:
            v = self._pending_units_provider(strategy_id)
            return v if isinstance(v, dict) else {"long": 0.0, "short": 0.0}
        except Exception:
            return {"long": 0.0, "short": 0.0}

    # -- 创建 / 生命周期 ------------------------------------------------------

    def create_pair(self, leg_a: str, leg_b: str, single_lots: float, total_lots: float) -> Strategy:
        leg_a, leg_b = leg_a.strip(), leg_b.strip()
        if not leg_a or not leg_b or leg_a == leg_b:
            raise ValueError("合约对无效：两腿必须不同且非空")
        for leg in (leg_a, leg_b):
            if not _valid_symbol(leg):
                raise ValueError(
                    f"合约号 {leg} 无效：应为品种字母+月号，如 ag2706（漏品种前缀则永无行情，挂单永不触发）")
        if "&" in leg_a or "&" in leg_b:
            raise ValueError("合约号不能包含 &")
        if self._find_by_pair(f"{leg_a}&{leg_b}") is not None:
            raise ValueError(f"合约对 {leg_a}&{leg_b} 已存在（同一行情流不允许重复策略）")
        s = Strategy(
            id=self._store.next_id(), leg_a=leg_a, leg_b=leg_b,
            single_lots=single_lots, total_lots=total_lots,
            sort_key=self._next_sort_key(),
        )
        s.status = StrategyStatus.active  # 本地创建即生效（无服务端回执）
        self._store.add(s)
        self._legs_subscriber([f"{leg_a}.SHFE", f"{leg_b}.SHFE"])  # CTP 订阅两腿行情
        self._push.strategy(s)
        return s

    def _next_sort_key(self) -> float:
        return max((s.sort_key for s in self._store.list()), default=0) + 1

    def reorder_strategies(self, ordered_ids: list) -> list[Strategy]:
        """拖动排序（2026-10-14 定稿）：按传入顺序重排 sort_key（1..n）；
        未包含的策略按原序附后，未知 id 忽略，空单不动。"""
        items = sorted(self._store.list(), key=lambda x: (x.sort_key, x.id))
        by_id = {x.id: x for x in items}
        known = [i for i in ordered_ids if i in by_id]
        if not known:
            return items
        rest = [s.id for s in items if s.id not in set(known)]
        for idx, sid in enumerate(known + rest, start=1):
            st = by_id[sid]
            if st.sort_key != idx:
                st.sort_key = idx
                self._store.update(st)
                self._push.strategy(st)
        return sorted(self._store.list(), key=lambda x: (x.sort_key, x.id))

    def create_product(self, product: str, single_lots: float, total_lots: float) -> tuple[int, int]:
        """品种展开：B 腿固定为品种主力合约（配置覆盖>最近未来月），A 腿=其余合约依次配对。"""
        product = product.strip()
        if not product.isalpha():
            raise ValueError("品种代码应为纯字母，如 ag")
        from faos_app.contract_seed import symbols_for_product

        matched, seed_dominant, stale = symbols_for_product(
            product, self._contracts_provider())
        matched = [s for s in matched if not _is_delivery_month(s)]  # 当月交割=死腿（盘口枯竭，夜盘实测）
        matched = self._filter_liquid_months(product, matched)  # 远月死腿（OI 不足）剔除
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
                         sort_key=self._next_sort_key(),
                         status=StrategyStatus.active)
            self._store.add(s)
            self._push.strategy(s)
            legs += [f"{sym}.SHFE", f"{dominant}.SHFE"]
            created += 1
        if legs:
            self._legs_subscriber(sorted(set(legs)))
        existing = sum(1 for s in self._store.list() if self._product_of(s.pair) == product)
        return created, existing

    def _filter_liquid_months(self, product: str, matched: list) -> list:
        hint = self._dominant_hints.get(product.strip().lower())
        try:
            return _filter_liquid_months_impl(product, matched, hint)
        except Exception:
            return matched

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
        from faos_app.log import log

        log(f"FAOS_OP #{strategy_id} {s.pair} 停止策略（撤挂单 "
            f"{[(t.action.value, t.spread) for t in s.triggers] or '无'}+停算法 {s.active_algos or '无'}，持仓保留）")
        for hook in self.on_stop_hooks:
            hook(s)
        s.triggers = []
        s.active_algos = []
        s.executing_action = None  # DONE 事件可能晚到/永不到，方向不残留幻影
        s.status = StrategyStatus.active  # 一律保持在线（pending 期间停止同样转 active，回执由严格同步处理）
        self._store.update(s)
        self._push.strategy(s)
        return s

    def resubscribe_all(self) -> int:
        """重订全部策略两腿行情（登录成功/on_ready 兜底——会话态订阅防丢）。"""
        all_legs = sorted({f"{s.leg_a}.SHFE" for s in self._store.list()}
                          | {f"{s.leg_b}.SHFE" for s in self._store.list()})
        if all_legs:
            self._legs_subscriber(all_legs)
        return len(all_legs)

    def stop_all(self) -> list[Strategy]:
        """一键停止全部策略（撤触发单+停算法，持仓保留，策略保持在线）。"""
        from faos_app.log import log

        log(f"FAOS_OP 全部停止（{len(self._store.list())} 个策略，撤挂单+停算法，持仓保留）")
        for s in list(self._store.list()):
            self.stop(s.id)
        return self._store.list()

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
            # 2026-10-17 定稿：A 腿撤单上限上限=2000——不填默认 2000，超过按 2000 处理
            if max_cancels_a < 1:
                raise ValueError("A 腿撤单上限必须为正数")
            s.max_cancels_a = min(int(max_cancels_a), 2000)
        self._store.update(s)
        self._push.strategy(s)
        return s

    def stop_direction(self, strategy_id: int, side: str) -> Strategy:
        """方向级停止：撤销该侧触发单，停该侧算法，持仓保留。

        归属侧（2026-10-20 夜盘实证修订）：平仓通道单反向归属——平空仓挂的
        open_long(is_close) 属做空侧（用户在做空区点平仓/停止平仓），平多仓的
        open_short(is_close) 属做多侧；普通开仓挂单按 action 本侧。"""
        if side not in ("short", "long"):
            raise ValueError(f"未知方向: {side}")
        s = self._require(strategy_id)
        from faos_app.log import log

        log(f"FAOS_OP #{strategy_id} {s.pair} 停止{'做空' if side == 'short' else '做多'}侧"
            f"（撤挂单+停该侧算法，持仓保留）")

        def on_side(action_value: str, is_close: bool) -> bool:
            base = "short" if action_value in ("open_short", "close_short") else "long"
            if not is_close or action_value not in ("open_short", "open_long"):
                return base == side  # 普通挂单/原生平仓动作按本侧
            return base != side  # 平仓通道反向单：反转归属（平空=open_long→做空侧）

        s.triggers = [t for t in s.triggers if not on_side(t.action.value, bool(t.is_close))]
        for hook in self.on_direction_stop_hooks:
            hook(s, side)
        if not s.active_algos:
            s.executing_action = None  # 本侧算法停后无其它算法 → 方向交还 held_units 驱动
            s.status = StrategyStatus.armed if s.triggers else StrategyStatus.active
        self._store.update(s)
        self._push.strategy(s)
        return s

    def set_lots(self, strategy_id: int, single_lots: Optional[float] = None,
                 total_lots: Optional[float] = None) -> Strategy:
        s = self._require(strategy_id)
        old = (s.single_lots, s.total_lots)
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
        from faos_app.log import log

        log(f"FAOS_OP #{strategy_id} {s.pair} 设置份数 单份/总份 {old[0]:g}/{old[1]:g} → "
            f"{s.single_lots:g}/{s.total_lots:g}")
        self._store.update(s)
        self._push.strategy(s)
        return s

    # -- 触发单 ---------------------------------------------------------------

    def place_trigger(self, strategy_id: int, action: TriggerAction, spread: float,
                      lots: Optional[float] = None, companion_spread: Optional[float] = None,
                      replace_trigger_id: Optional[str] = None,
                      is_close_order: bool = False,
                      timeout_rearm: int = 0) -> Trigger:
        """挂触发单；被拒（重复/超总份/无持仓等）时写 FAOS_TRIGGER_REJECT 日志再抛出。

        2026-10-13 实测教训：用户平仓被拒但 faos.log 无痕迹，问题无法定位——
        被拒的挂单必须留证据（REJECT 带持仓/总份上下文）。replace_trigger_id=
        修改挂单价场景（净敞口按新旧差值计）。"""
        try:
            return self._place_trigger(strategy_id, action, spread, lots, companion_spread,
                                       replace_trigger_id, is_close_order, timeout_rearm)
        except ValueError as e:
            from faos_app.log import log

            log(f"FAOS_TRIGGER_REJECT #{strategy_id} {action.value}@{spread} "
                f"lots={lots if lots is not None else '默认'} {e} "
                f"[held={self._require(strategy_id).held_units:+g} 总份={self._require(strategy_id).total_lots:g} "
                f"挂单={[(t.action.value, t.spread) for t in self._require(strategy_id).triggers] or '无'}]")
            raise

    def _place_trigger(self, strategy_id: int, action: TriggerAction, spread: float,
                       lots: Optional[float] = None, companion_spread: Optional[float] = None,
                       replace_trigger_id: Optional[str] = None,
                       is_close_order: bool = False,
                       timeout_rearm: int = 0) -> Trigger:
        s = self._require(strategy_id)
        if self._close_risk_locked:
            raise ValueError("临近收盘/休市（风控锁），时点前暂不接受新挂单，时点后自动恢复")
        if s.status == StrategyStatus.stopped:
            raise ValueError("策略已停止，无法挂触发单")
        lots = s.single_lots if lots is None else lots
        if lots <= 0:
            raise ValueError("手数必须为正数")
        # 平仓份数封顶持仓（2026-10-20 实证「持 1 平 2 → 溢出量走净仓转换开新仓」：
        # 单腿+错向敞口）——平仓按钮通道一律 clamp 到该方向持仓份数，无仓拒单。
        # 2026-10-20 夜盘修订：按方向双向份（锁仓时净额=0 会误拒双向持仓的平仓）
        if is_close_order and action in _OPEN_ACTIONS:
            pool = self.close_pool(s, action)  # 账本 ∩ 柜台实际（见 close_pool 口径）
            if pool < 1e-9:
                raise ValueError("该方向无价差持仓可平（策略账本与柜台持仓均为 0）")
            lots = min(lots, pool)
        # 开平语义第五次演进（2026-10-19 夜盘实测后用户终稿）：反方向挂单恢复为
        # 正常开仓（上期所双向锁仓=设计效果，与同向加仓一致）；平仓唯一入口仍是
        # 「平仓」按钮的 is_close 通道。此前当日曾停用反方向（防程序猜平仓意图），
        # 实测用户持仓位时反向开单被一律拦下（faos.log 21:31 REJECT 实证）——
        # 误导源是 UI 按持仓方向猜「平仓」标注，改为按单据 is_close 真实通道标注。
        same = next((t for t in s.triggers if t.action == action), None)
        if same is not None and same.trigger_id != replace_trigger_id:
            if action in _OPEN_ACTIONS:
                raise ValueError("该方向开仓触发单已存在（做空/做多可并存，同方向仅一张），请用「修改」改价")
            raise ValueError("该方向平仓触发单已存在")
        if action == TriggerAction.close_short and s.held_units >= 0:
            raise ValueError("无空头价差持仓，无法挂空平触发单")
        if action == TriggerAction.close_long and s.held_units <= 0:
            raise ValueError("无多头价差持仓，无法挂多平触发单")
        if action in _OPEN_ACTIONS and not is_close_order:
            # 总份校验（方向独立口径）；平仓通道（is_close）不占额度——份数已 clamp 到持仓；
            # 修改挂单价（replace）时旧挂单将被撤掉——净敞口按新旧差值计
            # （2026-10-15 夜盘实证：持仓满份时改自己挂单的价被误拒，只能调总份绕过）；
            # 在途算法计入（2026-10-20 实证：执行中 held 未更新，1 秒双挂 → 总份 1 开成 2 份）
            effective = lots
            if replace_trigger_id is not None:
                old_t = next((t for t in s.triggers
                              if t.trigger_id == replace_trigger_id and t.action == action), None)
                if old_t is not None:
                    effective = lots - old_t.lots
            # 方向独立口径（2026-10-24 用户定稿）：做多/做空各自限总份、互不占用
            # （例：单份 2 总份 4 → 多仓可开满 4 份，同时空仓也可开满 4 份）；
            # 在途开仓计入（执行中 held 未更新，防 1 秒双挂超额）；平仓通道不占额度。
            key = "long" if action == TriggerAction.open_long else "short"
            pend = self._pending_units(s.id)
            held_dir = s.held_long_units if key == "long" else s.held_short_units
            after = round(held_dir + pend[key] + effective, 6)
            if after > s.total_lots:
                raise ValueError(
                    f"超出{'做多' if key == 'long' else '做空'}总份限制："
                    f"持仓 {held_dir:g} + 在途 {pend[key]:g} + 本单 {effective:g}"
                    f" = {after:g} 份 > 总份 {s.total_lots:g} 份（做多/做空各自限总份）")
        setattr(s, _MANUAL_FIELD[action], spread)  # 下单价保存进策略价差（行情退役后差价来源=人工）
        if companion_spread is not None:  # 开仓下单时对应平仓价一并保存（用户定稿）
            setattr(s, _CLOSE_COMPANION[action], companion_spread)
        from faos_app.log import log

        log(f"FAOS_TRIGGER_PLACED #{s.id} {s.pair} {action.value}@{spread} lots={lots}"
            f"{'（平仓通道）' if is_close_order else ''}"
            + (f"（超时重挂 第{timeout_rearm}次）" if timeout_rearm else ""))
        t = Trigger(trigger_id=f"t{uuid4().hex[:8]}", action=action, spread=spread, lots=lots,
                    is_close=is_close_order, rearm_count=timeout_rearm)
        s.triggers.append(t)
        if s.status in (StrategyStatus.active, StrategyStatus.unsubscribed, StrategyStatus.pending):
            s.status = StrategyStatus.armed
        self._store.update(s)
        self._push.strategy(s)
        return t

    def cancel_trigger(self, strategy_id: int, trigger_id: str) -> Strategy:
        s = self._require(strategy_id)
        t = next((x for x in s.triggers if x.trigger_id == trigger_id), None)
        if t is not None:
            from faos_app.log import log

            log(f"FAOS_OP #{strategy_id} {s.pair} 撤销挂单 {t.action.value}@{t.spread}"
                f"{'（平仓通道）' if t.is_close else ''}")
        s.triggers = [t2 for t2 in s.triggers if t2.trigger_id != trigger_id]
        if s.status == StrategyStatus.armed and not s.triggers:
            s.status = StrategyStatus.active
        self._store.update(s)
        self._push.strategy(s)
        return s

    # -- 重启恢复 ---------------------------------------------------------------

    def recover(self) -> None:
        """启动恢复：清除运行中算法状态；触发单保留并重挂（armed）；
        旧默认交易参数（5/5/对手价/对手价）归一为新默认（A挂价+0 / B对手价+100）；
        账本跨日结转（td→yd，2026-10-19：今昨账本驱动平仓拆分）；
        重订全部策略两腿行情（本地价差架构订阅是会话态，不重订则触发永不评估——
        2026-10-12 实证：重启后连主力月都『无报价』）。"""
        from datetime import datetime as _dt
        from faos_app.leg_book import roll_book

        all_legs = sorted({f"{s.leg_a}.SHFE" for s in self._store.list()}
                          | {f"{s.leg_b}.SHFE" for s in self._store.list()})
        if all_legs:
            self._legs_subscriber(all_legs)  # 网关缓冲到登录后生效
        for s in self._store.list():
            changed = False
            if roll_book(s, _dt.now()):
                changed = True
            if (s.slip_a, s.slip_b, s.order_type_a, s.order_type_b) == (5, 5, "对手价", "对手价"):
                s.slip_a, s.slip_b = 0, 100
                s.order_type_a, s.order_type_b = "挂价", "对手价"
                changed = True
            if s.active_algos:
                s.active_algos = []
                s.executing_action = None  # 重启后 DONE 永远不会来，方向不残留幻影
                self._push.notice(
                    "warn",
                    f"策略 #{s.id} 重启前存在未完成价差单，已清除算法状态，请人工核对持仓",
                )
                changed = True
            if s.status == StrategyStatus.executing:
                s.status = StrategyStatus.armed if s.triggers else StrategyStatus.active
                changed = True
            if _is_delivery_month(s.leg_a) or _is_delivery_month(s.leg_b):
                # 存量死腿组合（当月交割腿，盘口枯竭价差无法合成）——启动即清理
                for hook in self.on_stop_hooks:
                    hook(s)
                self._store.remove(s.id)
                self._push.strategy_deleted(s.id)
                self._push.notice(
                    "warn", f"策略 #{s.id} {s.pair} 含当月交割合约（盘口枯竭），已自动清理；"
                    "如需该品种请重新订阅（新版自动剔除死腿月份）")
                continue
            if not (_valid_symbol(s.leg_a) and _valid_symbol(s.leg_b)):
                # 存量非法合约号（裸月号无品种前缀，永无行情）——启动即清理（2026-10-13 实测踩坑）
                self._store.remove(s.id)
                self._push.strategy_deleted(s.id)
                self._push.notice(
                    "warn", f"策略 #{s.id} {s.pair} 合约号无效（缺品种字母前缀，永无行情），已自动清理")
                continue
            if changed:
                self._store.update(s)
                self._push.strategy(s)

    def clamp_ledger_to_account(self, ctp) -> int:
        """按柜台实际持仓裁剪不可能的账本（2026-10-21 数据修复）。

        记账方向 bug（平仓被记成反向新增）会在 strategies.json 留下虚拟份数：
        策略声明某腿某方向份数 > 柜台该合约该方向总持仓 = 物理上不可能。此处只
        向下裁剪这类不可能的声明（只减不增、绝不改正常持仓），柜台无持仓数据
        （未连接/未加载）时不动——避免把正常持仓裁成 0。返回修正条数。"""
        from faos_app.leg_ops import account_position_book
        from faos_app.spread_service import vt_symbol_of

        try:
            book = account_position_book(ctp.get_positions())
        except Exception:
            return 0
        if not book:
            return 0
        fixes = []
        for s in self._store.list():
            changed = False
            a = book.get(vt_symbol_of(s.leg_a)) or {}
            b = book.get(vt_symbol_of(s.leg_b)) or {}
            tot = lambda row, d: row.get(f"{d}_today", 0) + row.get(f"{d}_yd", 0)
            # 价差份的物理上限：多头价差需两腿都「多」、空头价差需两腿都「空」（A/B 取小）
            for field, cap, label in (
                ("held_long_units", min(tot(a, "long"), tot(b, "long")), "多"),
                ("held_short_units", min(tot(a, "short"), tot(b, "short")), "空"),
            ):
                have = getattr(s, field) or 0
                if have > cap + 1e-9:
                    setattr(s, field, float(cap))
                    fixes.append(f"#{s.id} {s.pair} {label}头价差 {have:g}→{cap:g} 份")
                    changed = True
            if changed:
                s.held_units = round((s.held_long_units or 0) - (s.held_short_units or 0), 6)
                self._store.update(s)
                self._push.strategy(s)
        if fixes:
            from faos_app.log import log

            log(f"FAOS_LEDGER_CLAMPED {len(fixes)} 条：" + "、".join(fixes))
            self._push.notice(
                "warn",
                "已按柜台实际持仓校正策略账本（裁剪超出柜台持仓的虚拟份数，"
                + "、".join(fixes)
                + "）——此前版本平仓记账方向错误遗留；如柜台有多出的持仓请手动平掉")
        return len(fixes)

    def reconcile_holdings(self, ctp) -> None:
        """CTP 就绪后核对账户实际持仓 vs 策略归属持仓（按各策略自身成交记录推导）。

        2026-10-13 用户定稿：告警化，不改写 held_units——全账户 min 配对会把
        手动交易/其他终端的仓位误归属给策略（共享主力腿）。策略持仓唯一事实源
        =自身算法成交累计；差异只弹告警点名腿与手数，人工核对。"""
        from faos_app.spread_service import vt_symbol_of

        def side(sym, want):
            total = 0
            for p in ctp.get_positions():
                v = getattr(p.direction, "value", str(p.direction))
                d = {"多": "long", "空": "short"}.get(v, str(v).lower())
                if p.vt_symbol == sym and d == want:
                    total += p.volume
            return total

        expected: dict = {}  # (vt_symbol, long/short) -> 期望手数（策略双向份推导，单位=手）
        for s in self._store.list():
            lv, sv = s.held_long_units, s.held_short_units
            if lv <= 0 and sv <= 0:
                continue
            a, b = vt_symbol_of(s.leg_a), vt_symbol_of(s.leg_b)
            expected[(a, "long")] = expected.get((a, "long"), 0) + lv   # 做多份：A 多
            expected[(a, "short")] = expected.get((a, "short"), 0) + sv  # 做空份：A 空
            expected[(b, "short")] = expected.get((b, "short"), 0) + lv  # B 空
            expected[(b, "long")] = expected.get((b, "long"), 0) + sv    # B 多

        symbols = {sym for sym, _ in expected}
        symbols |= {p.vt_symbol for p in ctp.get_positions() if p.volume}
        diffs = []
        for sym in sorted(symbols):
            for d, cn in (("long", "多"), ("short", "空")):
                delta = side(sym, d) - expected.get((sym, d), 0)
                if abs(delta) > 1e-6:
                    diffs.append(f"{sym} {cn}{delta:+.0f} 手")
        if diffs:
            self._push.notice(
                "warn",
                "账户持仓与策略记录不一致（可能为手动交易/其他终端/中断残留）："
                + "、".join(diffs)
                + "。策略持仓以自身成交为准，请人工核对。")
            return  # 总量已不一致：今昨比较无意义（口径混入手动交易）
        self._reconcile_today_yesterday(ctp)

    def _reconcile_today_yesterday(self, ctp) -> None:
        """今昨错位对账（2026-10-19 定稿，配合策略级今昨账本）：总量一致但今昨
        结构错位才告警——这是平仓归属错位的准确信号（老策略账本未建/手动平今
        也会触发，点名腿与今昨手数，人工核对）。"""
        from faos_app.leg_book import roll_book
        from faos_app.spread_service import vt_symbol_of
        from datetime import datetime as _dt

        expected: dict = {}  # (vt_symbol, long/short, today/yd) -> 期望手数（策略账本聚合）
        for s in self._store.list():
            roll_book(s, _dt.now())  # 对账口径先结转到当前交易日
            for leg, entry in (s.leg_book or {}).items():
                for d in ("long", "short"):
                    expected[(vt_symbol_of(leg), d, "today")] = \
                        expected.get((vt_symbol_of(leg), d, "today"), 0) + entry[f"{d}_td"]
                    expected[(vt_symbol_of(leg), d, "yd")] = \
                        expected.get((vt_symbol_of(leg), d, "yd"), 0) + entry[f"{d}_yd"]
        if not expected:
            return  # 无策略建账本（全部空账本）→ 今昨对账无从比起

        actual: dict = {}
        for p in ctp.get_positions():
            if not p.volume:
                continue
            v = getattr(p.direction, "value", str(p.direction))
            d = {"多": "long", "空": "short"}.get(v, str(v).lower())
            if d not in ("long", "short"):
                continue
            yd = min(getattr(p, "yd_volume", 0) or 0, p.volume)
            actual[(p.vt_symbol, d, "yd")] = actual.get((p.vt_symbol, d, "yd"), 0) + yd
            actual[(p.vt_symbol, d, "today")] = actual.get((p.vt_symbol, d, "today"), 0) + p.volume - yd

        diffs = []
        for key in sorted(set(expected) | set(actual)):
            delta = actual.get(key, 0) - expected.get(key, 0)
            if abs(delta) > 1e-6:
                sym, d, w = key
                cn = "多" if d == "long" else "空"
                wn = "今仓" if w == "today" else "昨仓"
                diffs.append(f"{sym} {cn}{wn}{delta:+.0f} 手")
        if diffs:
            self._push.notice(
                "warn",
                "持仓今昨结构与策略账本不一致（策略级今昨账本 2026-10-19 起；老策略或"
                "手动平今/平昨会产生此差异，不影响总量）："
                + "、".join(diffs)
                + "。平仓将按账户实际拆分（回落保护），如需精确归属可在「今昨仓管理」手工对齐。")

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

# ---- 收盘前风控（2026-10-19 定稿） ----------------------------------------------

DEFAULT_SESSIONS = [
    {"name": "日盘第一节", "start": "09:00", "end": "10:15"},
    {"name": "日盘第二节", "start": "10:30", "end": "11:30"},
    {"name": "日盘第三节", "start": "13:30", "end": "15:00"},
    {"name": "夜盘-有色", "start": "21:00", "end": "次日 1:00"},
    {"name": "夜盘-贵金属", "start": "21:00", "end": "次日 2:30"},
]


def _hm(sec_str: str) -> int:
    """"H:MM" / "次日 H:MM" → 当天秒数（次日=次日时钟的实际时刻）。"""
    t = str(sec_str).strip().replace("次日", "").strip()
    h, m = t.split(":")
    return int(h) * 3600 + int(m) * 60


def _cross_midnight(end_str: str) -> bool:
    return "次日" in str(end_str)


def load_market_sessions(extra_dirs=None) -> list:
    """时段表：config/market_sessions.json（可手工编辑）> 内置默认。"""
    import json as _json
    from pathlib import Path as _Path
    from faos_app.config import default_data_dir
    candidates = [default_data_dir() / "market_sessions.json"]
    if extra_dirs:
        candidates += [_Path(d) / "market_sessions.json" for d in extra_dirs]
    for f in candidates:
        try:
            if f.exists():
                data = _json.loads(f.read_text(encoding="utf-8"))
                sessions = data.get("sessions") if isinstance(data, dict) else data
                if isinstance(sessions, list) and sessions:
                    return sessions
        except Exception:
            continue
    return DEFAULT_SESSIONS


def seconds_to_close(now, window: int = 60, sessions: Optional[list] = None) -> Optional[int]:
    """距最近时段收盘的秒数；各时段 end 前 window 秒内返回最小正数，否则 None。

    时段来自 market_sessions.json（含小节休息与夜盘跨午夜"次日 H:MM"）。"""
    sec_of_day = now.hour * 3600 + now.minute * 60 + now.second
    best = None
    for sess in (sessions if sessions is not None else DEFAULT_SESSIONS):
        try:
            start_sec = _hm(sess["start"])
            end_sec = _hm(sess["end"])
        except Exception:
            continue  # 无效时段配置跳过
        cross = _cross_midnight(sess["end"])
        if cross:
            in_session = sec_of_day >= start_sec or sec_of_day < end_sec
            remain = (end_sec - sec_of_day) % (24 * 3600)
        else:
            in_session = start_sec <= sec_of_day < end_sec
            remain = end_sec - sec_of_day
        if not in_session:
            continue
        if 0 < remain <= window:
            best = remain if best is None else min(best, remain)
    return best


class CloseRiskGuard:
    """收盘/休市风控（2026-10-23 定稿，最小实现——不碰交易发送链路）：

    - 窗口内（end 前 N 秒，默认 60）：stop_all + 开启下单锁（place_trigger 拒绝
      新挂单，含平仓通道——时点后自动解锁）；窗口内持续巡检，发现新活动再撤。
    - 时点边沿（窗口结束的第一 tick）：兜底再 stop_all + 撤销柜台全部剩余挂单
      （FAOS_CLOSE_SWEEP，防无限易残留孤儿单）。

    settings 读取：algo.close_risk_seconds（提前量，0=关闭）。"""

    def __init__(self, manager, settings=None, cancel_all=None):
        self._manager = manager
        self._settings = settings
        self._cancel_all = cancel_all  # 柜台总撤回调（main 注入，可缺省）
        self._in_window = False
        self._notified = False

    def _cfg(self):
        algo = getattr(self._settings, "algo", None)
        window = getattr(algo, "close_risk_seconds", 60) if algo else 60
        sessions = getattr(algo, "market_sessions", None) if algo else None
        if not sessions:  # 设置未覆盖 → 仓库/APPDATA 内置时段表文件
            sessions = load_market_sessions()
        return window, sessions

    def _has_activity(self) -> bool:
        return any(s.triggers or s.active_algos for s in self._manager.store.list())

    def tick(self, now) -> None:
        from faos_app.log import log

        window, sessions = self._cfg()
        if window <= 0:
            if self._in_window:
                self._in_window = False
                self._manager.set_close_risk_lock(False)
            return  # 风控关闭
        remain = seconds_to_close(now, window=window, sessions=sessions)
        if remain is not None:  # 窗口内：锁新单 + 持续撤活动
            first = not self._in_window
            self._in_window = True
            self._manager.set_close_risk_lock(True)
            if self._has_activity():
                log(f"FAOS_CLOSE_RISK 时点前 {remain}s 风控触发：停止全部下单并撤所有挂单")
                self._manager.stop_all()
            if first and not self._notified:
                self._notified = True
                self._manager.push.notice(
                    "warn",
                    f"收盘/休市风险控制：距时点 {remain} 秒，已停止全部下单并撤销挂单"
                    "（持仓保留），时点前暂不接受新挂单——临近收盘/休市流动性枯竭")
        elif self._in_window:  # 时点边沿：兜底总撤一次 + 解锁
            self._in_window = False
            self._manager.set_close_risk_lock(False)
            self._notified = False
            self._manager.stop_all()
            n = 0
            if self._cancel_all is not None:
                try:
                    n = int(self._cancel_all() or 0)
                except Exception as e:
                    log(f"FAOS_CLOSE_SWEEP_FAIL {e}")
            log(f"FAOS_CLOSE_SWEEP 时点后兜底：撤销柜台剩余挂单 {n} 笔")
            self._manager.push.notice(
                "warn", f"已到收盘/休市时点，兜底撤销柜台全部剩余挂单 {n} 笔（持仓保留）")
