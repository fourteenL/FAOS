"""策略领域模型。"""

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class TriggerAction(str, enum.Enum):
    open_short = "open_short"    # 空开：卖A买B，触发条件 空开>=X
    open_long = "open_long"      # 多开：买A卖B，触发条件 多开<=X
    close_short = "close_short"  # 空平：买回价差对冲空头，触发条件 空平<=X
    close_long = "close_long"    # 多平：卖出价差对冲多头，触发条件 空开>=X


class StrategyStatus(str, enum.Enum):
    unsubscribed = "unsubscribed"  # 未订阅
    pending = "pending"            # 等待行情
    active = "active"              # 空闲（已订阅）
    armed = "armed"                # 监控中（有未触发的开仓触发单）
    executing = "executing"        # 执行中（价差算法运行）
    stopped = "stopped"            # 已停止


class Trigger(BaseModel):
    trigger_id: str
    action: TriggerAction
    spread: float
    lots: float
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Strategy(BaseModel):
    id: int
    leg_a: str
    leg_b: str
    product: Optional[str] = None
    single_lots: float = 1
    total_lots: float = 1
    status: StrategyStatus = StrategyStatus.unsubscribed
    triggers: list[Trigger] = []
    held_units: float = 0  # 已开价差份数（净）
    active_algos: list[str] = []
    slip_a: int = 0  # A 腿（主动）滑点，跳（挂价排队默认不加滑点）
    slip_b: int = 100  # B 腿（被动）滑点，跳（对冲快速成交）
    order_type_a: str = "挂价"  # A 腿报单类型：挂价(排队)/对手价/最新价/市价
    order_type_b: str = "对手价"  # B 腿报单类型

    @model_validator(mode="before")
    @classmethod
    def _migrate_order_type(cls, data):
        """旧记录的统一 order_type 迁移为 A/B 两字段。"""
        if isinstance(data, dict) and "order_type" in data:
            old = data.pop("order_type")
            data.setdefault("order_type_a", old)
            data.setdefault("order_type_b", old)
        return data
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pair(self) -> str:
        return f"{self.leg_a}&{self.leg_b}"
