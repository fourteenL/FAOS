"""策略 JSON 持久化存储（原子写）。"""

import os
import threading
from pathlib import Path
from typing import Optional

from faos_app.models import Strategy


class StrategyStore:
    def __init__(self, path: Optional[Path] = None):
        self._path = path
        if path is not None and path.exists():
            self._strategies = [
                Strategy.model_validate(x) for x in json_list(path)
            ]
        else:
            self._strategies = []
        # 会话内单调编号：删除不复用；跨重启可能复用已删最大号（编号仅展示用）
        self._max_ever = max((s.id for s in self._strategies), default=0)
        self._lock = threading.Lock()  # portal/vnpy 线程并发写保护

    # -- 查询 --------------------------------------------------------------

    def list(self) -> list[Strategy]:
        return list(self._strategies)

    def get(self, strategy_id: int) -> Optional[Strategy]:
        for s in self._strategies:
            if s.id == strategy_id:
                return s
        return None

    def get_by_pair(self, pair: str) -> Optional[Strategy]:
        for s in self._strategies:
            if s.pair == pair:
                return s
        return None

    def next_id(self) -> int:
        self._max_ever = max(self._max_ever, max((s.id for s in self._strategies), default=0))
        self._max_ever += 1
        return self._max_ever

    # -- 变更 --------------------------------------------------------------

    def add(self, strategy: Strategy) -> Strategy:
        self._strategies.append(strategy)
        self._flush()
        return strategy

    def update(self, strategy: Strategy) -> Strategy:
        for i, s in enumerate(self._strategies):
            if s.id == strategy.id:
                self._strategies[i] = strategy
                break
        else:
            raise ValueError(f"策略不存在: {strategy.id}")
        self._flush()
        return strategy

    def remove(self, strategy_id: int) -> bool:
        before = len(self._strategies)
        self._strategies = [s for s in self._strategies if s.id != strategy_id]
        removed = len(self._strategies) < before
        if removed:
            self._flush()
        return removed

    # -- 持久化 ------------------------------------------------------------

    def _flush(self) -> None:
        if self._path is None:
            return
        payload = "[\n" + ",\n".join(
            "  " + s.model_dump_json() for s in self._strategies
        ) + "\n]"
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


def json_list(path: Path) -> list:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
