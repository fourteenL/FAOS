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
            s.executing_action = None  # 本侧算法停后无其它算法 → 方向交还 held_units 驱动
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
                      lots: Optional[float] = None, companion_spread: Optional[float] = None,
                      replace_trigger_id: Optional[str] = None,
                      is_close_order: bool = False) -> Trigger:
        """挂触发单；被拒（重复/超总份/无持仓等）时写 FAOS_TRIGGER_REJECT 日志再抛出。

        2026-10-13 实测教训：用户平仓被拒但 faos.log 无痕迹，问题无法定位——
        被拒的挂单必须留证据。replace_trigger_id=修改挂单价场景（净敞口按新旧差值计）。"""
        try:
            return self._place_trigger(strategy_id, action, spread, lots, companion_spread,
                                       replace_trigger_id, is_close_order)
        except ValueError as e:
            from faos_app.log import log

            log(f"FAOS_TRIGGER_REJECT #{strategy_id} {action.value}@{spread} "
                f"lots={lots if lots is not None else '默认'} {e}")
            raise

    def _place_trigger(self, strategy_id: int, action: TriggerAction, spread: float,
                       lots: Optional[float] = None, companion_spread: Optional[float] = None,
                       replace_trigger_id: Optional[str] = None,
                       is_close_order: bool = False) -> Trigger:
        s = self._require(strategy_id)
        if s.status == StrategyStatus.stopped:
            raise ValueError("策略已停止，无法挂触发单")
        lots = s.single_lots if lots is None else lots
        if lots <= 0:
            raise ValueError("手数必须为正数")
        # 2026-10-16 定稿修订：持仓位时反方向挂单=正常开仓单（锁仓开仓不净仓），
        # 平仓通道只认「平仓」按钮的 is_close_order 标记
        same = next((t for t in s.triggers if t.action == action), None)
        if same is not None and same.trigger_id != replace_trigger_id:
            if action in _OPEN_ACTIONS:
                raise ValueError("该方向开仓触发单已存在（做空/做多可并存，同方向仅一张），请用「修改」改价")
            raise ValueError("该方向平仓触发单已存在")
        if action == TriggerAction.close_short and s.held_units >= 0:
            raise ValueError("无空头价差持仓，无法挂空平触发单")
        if action == TriggerAction.close_long and s.held_units <= 0:
            raise ValueError("无多头价差持仓，无法挂多平触发单")
        if action in _OPEN_ACTIONS:
            # 总份按净额校验（与交易所双向净仓一致）：对向开仓（含反向平仓）不占新额度；
            # 修改挂单价（replace）时旧挂单将被撤掉——净敞口按新旧差值计
            # （2026-10-15 夜盘实证：持仓满份时改自己挂单的价被误拒，只能调总份绕过）
            effective = lots
            if replace_trigger_id is not None:
                old_t = next((t for t in s.triggers
                              if t.trigger_id == replace_trigger_id and t.action == action), None)
                if old_t is not None:
                    effective = lots - old_t.lots
            signed = effective if action == TriggerAction.open_long else -effective
            after = round(s.held_units + signed, 6)
            if abs(after) > s.total_lots:
                raise ValueError(
                    f"超出总份限制：成交后净持仓 {after:+g} 份 > 总份 {s.total_lots:g} 份")
        setattr(s, _MANUAL_FIELD[action], spread)  # 下单价保存进策略价差（行情退役后差价来源=人工）
        if companion_spread is not None:  # 开仓下单时对应平仓价一并保存（用户定稿）
            setattr(s, _CLOSE_COMPANION[action], companion_spread)
        from faos_app.log import log

        log(f"FAOS_TRIGGER_PLACED #{s.id} {s.pair} {action.value}@{spread} lots={lots}")
        t = Trigger(trigger_id=f"t{uuid4().hex[:8]}", action=action, spread=spread, lots=lots,
                    is_close=is_close_order)
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
        旧默认交易参数（5/5/对手价/对手价）归一为新默认（A挂价+0 / B对手价+100）；
        重订全部策略两腿行情（本地价差架构订阅是会话态，不重订则触发永不评估——
        2026-10-12 实证：重启后连主力月都『无报价』）。"""
        all_legs = sorted({f"{s.leg_a}.SHFE" for s in self._store.list()}
                          | {f"{s.leg_b}.SHFE" for s in self._store.list()})
        if all_legs:
            self._legs_subscriber(all_legs)  # 网关缓冲到登录后生效
        for s in self._store.list():
            changed = False
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

        expected: dict = {}  # (vt_symbol, long/short) -> 期望手数（策略自身成交推导）
        for s in self._store.list():
            vol = abs(s.held_units) * (s.single_lots or 1)
            if vol <= 0:
                continue
            a, b = vt_symbol_of(s.leg_a), vt_symbol_of(s.leg_b)
            a_side, b_side = ("short", "long") if s.held_units < 0 else ("long", "short")
            expected[(a, a_side)] = expected.get((a, a_side), 0) + vol
            expected[(b, b_side)] = expected.get((b, b_side), 0) + vol

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
