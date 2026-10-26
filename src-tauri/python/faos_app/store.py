"""策略 JSON 持久化存储（原子写 + 坏文件自愈）。

坏文件自愈（2026-10-26 用户实证：坏机器 strategies.json 空/非法 JSON → 启动即崩
→「点击没反应」）：加载时文件级坏（空/非法 JSON/顶层非数组）或条目级坏（单条
字段缺失/类型错）都不再抛异常——坏文件改名备份 `*.corrupt-时间戳`（保留证据），
合法条目尽量保留，空库/残库照常启动，`recovered_note` 供装配层弹提醒。
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from faos_app.models import Strategy


def _corrupt_backup(path: Path) -> Path:
    """坏文件改名备份（保留证据；重名时覆盖旧备份）。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    bak = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, bak)
    except OSError:
        pass  # 改名失败（占用等）→ 下次仍会重试自愈
    return bak


class StrategyStore:
    def __init__(self, path: Optional[Path] = None):
        self._path = path
        self._strategies: list[Strategy] = []
        self.recovered_note: Optional[str] = None  # 坏文件自愈说明（装配层弹提醒）
        if path is not None and path.exists():
            self._strategies, note = _load_resilient(path)
            self.recovered_note = note
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


def _load_resilient(path: Path) -> tuple[list[Strategy], Optional[str]]:
    """加载策略文件：任何损坏不抛异常。返回 (策略列表, 自愈说明|None)。"""
    from faos_app.log import log

    raw = path.read_text(encoding="utf-8")
    try:
        data = json_loads_or_none(raw)
        if not isinstance(data, list):
            raise ValueError(f"顶层应为数组，实际 {type(data).__name__}")
    except Exception as e:  # 文件级坏：空文件/非法 JSON/顶层非数组
        bak = _corrupt_backup(path)
        log(f"FAOS_STORE_RECOVERED 文件级损坏（{e}）→ 空库启动，原文件备份 {bak.name}")
        return [], (f"策略文件损坏（{e}），已备份为 {bak.name} 并以空库启动——"
                    "原策略需重新订阅；备份文件保留在 config 同目录供排查")

    good: list[Strategy] = []
    bad_idx: list[int] = []
    for i, x in enumerate(data):
        try:
            good.append(Strategy.model_validate(x))
        except Exception:
            bad_idx.append(i)
    if bad_idx:
        bak = _corrupt_backup(path)
        log(f"FAOS_STORE_RECOVERED 条目损坏（第 {bad_idx} 条）→ 保留 {len(good)} 条，"
            f"原文件备份 {bak.name}")
        return good, (f"策略文件第 {bad_idx} 条记录损坏已跳过（保留 {len(good)} 条），"
                      f"原文件备份 {bak.name}")
    return good, None


def json_loads_or_none(raw: str):
    """JSON 解析（空串/None 视为损坏抛 ValueError；纯 JSON 无 XML 外部实体问题）。"""
    import json

    if raw is None or not str(raw).strip():
        raise ValueError("文件为空")
    return json.loads(raw)


def json_list(path: Path) -> list:
    """兼容旧接口：直接解析（坏文件抛异常）。"""
    return json_loads_or_none(path.read_text(encoding="utf-8"))
