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


class TriggerMonitor:
    def __init__(self, manager, executor: Optional[SpreadExecutor] = None):
        self._manager = manager
        self._executor = executor
        self._exec_fail_warned: set = set()  # (strategy_id, action) 告警一次防每帧刷屏

    def set_executor(self, executor: SpreadExecutor) -> None:
        self._executor = executor

    def on_spread(self, pair: str, q: SpreadQuote) -> None:
        for s in self._manager.store.list():
            if s.pair != pair or not s.triggers:
                continue
            if s.status not in (StrategyStatus.active, StrategyStatus.armed):
                continue  # I2: executing/其它状态不触发（开仓算法未完成时不对零持仓平仓）
            for t in list(s.triggers):
                if t.action == TriggerAction.close_short and s.held_units >= 0:
                    continue  # 状态漂移兜底：无空头持仓不执行空平
                if t.action == TriggerAction.close_long and s.held_units <= 0:
                    continue
                if not should_trigger(t.action, q, t.spread):
                    continue
                if self._executor is None:
                    continue  # 执行器未接入（CTP 未启动）：触发单保留
                from faos_app.log import log

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
                    log(f"FAOS_TRIGGER_DEFERRED #{s.id} 执行器返回 None（CTP 未就绪），触发单保留")
                    continue  # CTP 未就绪：触发单保留，状态不变
                log(f"FAOS_ALGO_STARTED #{s.id} algoid={algoid}")
                s.triggers.remove(t)
                s.active_algos.append(algoid)
                s.executing_action = t.action.value  # 执行中方向可见（触发单已消费、held 未更新）
                s.status = StrategyStatus.executing
                self._manager.store.update(s)
                self._manager.push.strategy(s)

    def on_algo_finished(self, strategy_id: int, algoid: str) -> None:
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
