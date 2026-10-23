"""差价触发监控：行情帧 vs 挂起触发单 → 经 SpreadExecutor 执行。

触发语义（spec §6.2）：
- open_short  空开: short_open  >= X（卖出价差，卖A买B）
- open_long   多开: long_open   <= X（买入价差，买A卖B）
- close_short 空平: short_cover <= X（买回价差）
- close_long  多平: short_open  >= X（卖出价差）

执行器返回 None 表示 CTP 未就绪：触发单保留（armed），待后续行情再试。
"""

from typing import Optional, Protocol

from faos_app.models import Strategy, StrategyStatus, Trigger, TriggerAction
from faos_app.models import SpreadQuote


def should_trigger(action: str, q: SpreadQuote, spread: float) -> bool:
    if action in (TriggerAction.open_short, TriggerAction.close_long):
        return q.short_open >= spread
    if action == TriggerAction.open_long:
        return q.long_open <= spread
    if action == TriggerAction.close_short:
        return q.short_cover <= spread  # 语义字段（api.md: short_cover ≡ long_open）
    raise ValueError(f"未知触发动作: {action}")


class SpreadExecutor(Protocol):
    def execute(self, strategy: Strategy, trigger: Trigger) -> Optional[str]:
        """返回 algoid；返回 None 表示 CTP 未就绪（触发单保留）。"""


_SIDE_OF = {
    TriggerAction.open_short: "short",
    TriggerAction.close_long: "short",
    TriggerAction.open_long: "long",
    TriggerAction.close_short: "long",
}


class TriggerMonitor:
    def __init__(self, manager, executor: Optional[SpreadExecutor] = None):
        self._manager = manager
        self._executor = executor
        self._exec_fail_warned: set = set()  # (strategy_id, action) 告警一次防每帧刷屏
        self._deferred_logged: set = set()  # (sid, action, spread) 未就绪重试只记一次日志
        self._running_sides: dict[int, set] = {}  # strategy_id -> 运行中算法方向集
        self._algo_side: dict[str, str] = {}      # algoid -> 方向（完成时回退）

    def set_executor(self, executor: SpreadExecutor) -> None:
        self._executor = executor

    def on_spread(self, pair: str, q: SpreadQuote) -> None:
        for s in self._manager.store.list():
            if s.pair != pair or not s.triggers:
                continue
            running = self._running_sides.get(s.id, set())
            for t in list(s.triggers):
                if s.status not in (StrategyStatus.active, StrategyStatus.armed):
                    if s.status != StrategyStatus.executing:
                        continue
                    # executing：仅对向开仓放行（2026-10-13 用户定稿）；
                    # 同向与平仓仍等算法完成（I2：开仓未完成不对零/漂移持仓平仓）
                    if t.action not in (TriggerAction.open_short, TriggerAction.open_long) \
                            or _SIDE_OF[t.action] in running:
                        continue
                if t.action == TriggerAction.close_short and s.held_short_units < 1e-9:
                    continue  # 状态漂移兜底：无空头持仓不执行空平（双向份口径）
                if t.action == TriggerAction.close_long and s.held_long_units < 1e-9:
                    continue
                # 失效平仓单（2026-10-21 实证：持仓已平/账本为 0 时 is_close 单仍会
                # 点火，净仓转换在柜台开出新仓）——点火前校验对应方向份数，缺失即撤销
                if getattr(t, "is_close", False) and t.action in (TriggerAction.open_long,
                                                                TriggerAction.open_short):
                    pool_fn = getattr(self._manager, "close_pool", None)
                    if pool_fn is not None:  # 账本 ∩ 柜台实际（未连接时只信账本）
                        pool = pool_fn(s, t.action)
                    else:
                        pool = (s.held_short_units if t.action == TriggerAction.open_long
                                else s.held_long_units)
                    if pool < 1e-9:
                        s.triggers.remove(t)
                        self._manager.store.update(s)
                        self._manager.push.strategy(s)
                        self._manager.push.notice(
                            "warn", f"策略 #{s.id} {s.pair} 平仓触发单已失效"
                            "（对应方向持仓已不存在），已自动撤销——避免反向开出新仓")
                        continue
                if not should_trigger(t.action, q, t.spread):
                    continue
                if self._executor is None:
                    continue  # 执行器未接入（CTP 未启动）：触发单保留
                from faos_app.log import log

                retry_key = (s.id, t.action.value, t.spread)
                if retry_key not in self._deferred_logged:  # 未就绪重试期间不每帧刷日志
                    log(f"FAOS_TRIGGER_FIRED #{s.id} {s.pair} {t.action.value}@{t.spread}")
                try:
                    algoid = self._executor.execute(s, t)
                except Exception as e:  # 风控拒单等业务异常：只影响本策略，不中断当帧
                    log(f"FAOS_TRIGGER_EXEC_FAIL #{s.id} {t.action.value} {e}")
                    key = (s.id, t.action.value)
                    if key not in self._exec_fail_warned:
                        self._exec_fail_warned.add(key)
                        self._manager.push.notice("error", f"策略 #{s.id} 触发执行失败：{e}")
                    continue  # 触发单保留；参数调整/风控解除后下一帧自动重试
                self._exec_fail_warned.discard((s.id, t.action.value))  # 成功后重置告警（可再次提醒）
                if algoid is None:
                    if retry_key not in self._deferred_logged:
                        log(f"FAOS_TRIGGER_DEFERRED #{s.id} 执行器返回 None（CTP 未就绪），触发单保留")
                        self._deferred_logged.add(retry_key)
                    continue  # CTP 未就绪：触发单保留，状态不变
                self._deferred_logged.discard(retry_key)
                log(f"FAOS_ALGO_STARTED #{s.id} algoid={algoid}")
                s.triggers.remove(t)
                s.active_algos.append(algoid)
                s.executing_action = t.action.value  # 执行中方向可见（触发单已消费、held 未更新）
                s.status = StrategyStatus.executing
                self._running_sides.setdefault(s.id, set()).add(_SIDE_OF[t.action])
                self._algo_side[algoid] = _SIDE_OF[t.action]
                self._manager.store.update(s)
                self._manager.push.strategy(s)

    def on_algo_finished(self, strategy_id: int, algoid: str) -> None:
        side = self._algo_side.pop(algoid, None)
        if side:
            sides = self._running_sides.get(strategy_id)
            if sides:
                sides.discard(side)
                if not sides:
                    del self._running_sides[strategy_id]
        s = self._manager.store.get(strategy_id)
        if s is None:
            return
        if algoid in s.active_algos:
            s.active_algos.remove(algoid)
        if not s.active_algos:
            s.executing_action = None  # 方向交还 held_units 驱动
            s.status = StrategyStatus.armed if s.triggers else StrategyStatus.active
        self._manager.store.update(s)
        self._manager.push.strategy(s)
