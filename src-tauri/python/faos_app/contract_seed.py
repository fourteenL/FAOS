"""合约种子文件：本地离线测试用。

- CTP 就绪时以 vnpy 真实合约列表覆盖刷新（开盘时段自动保鲜）
- 非开盘时段（vnpy 无合约）品种展开回退到种子文件 → 开发阶段也能订阅测试
- 种子过期（>7 天未刷新）时品种展开给出告警提示，但仍可用

文件：%APPDATA%/faos/contract_seed.json：{"updated_at": "...", "symbols": ["ag2610.SHFE", ...]}
"""

import json
import os
from datetime import datetime
from pathlib import Path

# 开发兜底种子：仅 ag/au 常见跨期（真实合约以开盘刷新为准）
SEED_DEFAULT = {
    "ag": ["ag2610", "ag2612", "ag2702", "ag2704", "ag2706"],
    "au": ["au2610", "au2612", "au2602", "au2604", "au2606"],
}

SEED_NAME = "contract_seed.json"


def seed_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "faos" / SEED_NAME


def load_seed() -> dict:
    """读种子：{"updated_at": iso, "symbols": [vt_symbol]}；无/坏文件返回空。"""
    p = seed_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        symbols = data.get("symbols")
        if isinstance(symbols, list) and all(isinstance(s, str) for s in symbols):
            return data
    except Exception:
        pass
    return {}


def save_seed(symbols: list) -> None:
    """覆盖写种子（CTP 就绪时调用）。"""
    p = seed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"updated_at": datetime.now().isoformat(timespec="seconds"), "symbols": sorted(symbols)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def seed_age_days() -> float:
    data = load_seed()
    try:
        then = datetime.fromisoformat(data["updated_at"])
        return (datetime.now() - then).total_seconds() / 86400
    except Exception:
        return 999.0


def symbols_for_product(product: str, oms_symbols: list) -> tuple[list, bool]:
    """品种合约来源：优先 OMS 实时，其次种子文件，最后内置兜底。

    返回 (裸合约号列表, stale)；stale=True 表示来源非实时（提醒开盘刷新）。
    """
    product = product.strip().lower()
    live = sorted({s.split(".")[0] for s in oms_symbols
                   if s.endswith(".SHFE")
                   and "".join(ch for ch in s.split(".")[0] if ch.isalpha()) == product})
    if live:
        return live, False

    seed = [s.split(".")[0] for s in load_seed().get("symbols", [])
            if s.endswith(".SHFE")
            and "".join(ch for ch in s.split(".")[0] if ch.isalpha()) == product]
    if seed:
        return sorted(set(seed)), True

    return sorted(SEED_DEFAULT.get(product, [])), True
