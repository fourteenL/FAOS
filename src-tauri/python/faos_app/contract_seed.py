"""合约种子文件：本地离线测试用 + 品种主力合约判定。

- 种子文件放仓库 config/ 目录（与 settings.local.json 同级，随仓库分发）
- CTP 就绪时以 vnpy 真实合约列表覆盖刷新（开盘时段自动保鲜）
- 非开盘时段品种展开回退到种子文件/内置兜底 → 开发阶段也能订阅测试

主力合约判定（B 腿固定为主力）：
1. settings.product_dominant 显式配置（如 {"ag": "2612"}）
2. 自动推断：该品种合约中「离今天最近的未来交割月」
   （上期所多数品种主力跟随近月轮换；贵金属等可用配置覆盖）

文件：config/contract_seed.json：{"updated_at": "...", "symbols": ["ag2610.SHFE", ...]}
"""

import json
import re
from datetime import date
from pathlib import Path

# 开发兜底种子：仅 ag/au 常见跨期（真实合约以开盘刷新为准；主力=最近未来月自动推断）
SEED_DEFAULT = {
    "ag": ["ag2610", "ag2612", "ag2702", "ag2704", "ag2706"],
    "au": ["au2610", "au2612", "au2602", "au2604", "au2606"],
}

SEED_NAME = "contract_seed.json"


def seed_path() -> Path:
    """种子文件定位：仓库/工作目录的 config/contract_seed.json。

    候选：① 从本模块位置向上逐级查找（开发态=仓库根）；
          ② exe 同目录 config/（打包态）。
    """
    candidates = []
    try:
        here = Path(__file__).resolve()
        for parent in here.parents[:6]:
            candidates.append(parent / "config" / SEED_NAME)
    except Exception:
        pass
    import sys

    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "config" / SEED_NAME)
    except Exception:
        pass
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0] if candidates else Path("config") / SEED_NAME


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
        {"updated_at": date.today().isoformat(), "symbols": sorted(symbols)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def seed_age_days() -> float:
    data = load_seed()
    try:
        then = date.fromisoformat(data["updated_at"])
        return (date.today() - then).days
    except Exception:
        return 999.0


_MONTH_RE = re.compile(r"(\d{3,4})$")


def _contract_month(symbol: str):
    """合约号尾部月份 → (year, month)；无法解析返回 None。"""
    m = _MONTH_RE.search(symbol)
    if not m:
        return None
    digits = m.group(1)
    if len(digits) == 3:  # 如 612 → 2026-12
        year, month = 2000 + int(digits[0]), int(digits[1:])
    else:  # 如 2612
        year, month = 2000 + int(digits[:2]), int(digits[2:])
    if not 1 <= month <= 12:
        return None
    return year, month


def dominant_of(symbols: list, product: str, overrides: dict = None) -> str:
    """品种主力合约：配置覆盖 > 最近未来交割月 > 最近交割月（含过去）。"""
    product = product.strip().lower()
    overrides = overrides or {}
    if product in overrides:
        want = str(overrides[product])
        for s in symbols:
            if s.endswith(want):
                return s
    dated = []
    for s in symbols:
        if "".join(ch for ch in s if ch.isalpha()) != product:
            continue
        ym = _contract_month(s)
        if ym:
            dated.append((ym, s))
    if not dated:
        return symbols[0] if symbols else ""
    today = date.today()
    this_month = today.replace(day=1)
    future = sorted((ym, s) for ym, s in dated if date(ym[0], ym[1], 1) > this_month)
    return (future or sorted(dated)[-1:])[0][1]  # 未来取最近月；全过期取最近的过去月


def symbols_for_product(product: str, oms_symbols: list,
                        dominant_overrides: dict = None) -> tuple[list, str, bool]:
    """品种合约来源：优先 OMS 实时，其次种子文件，最后内置兜底。

    返回 (裸合约号列表, 主力合约, stale)；stale=True 表示来源非实时（提醒开盘刷新）。
    """
    product = product.strip().lower()

    def pick(src):
        return sorted({s.split(".")[0] for s in src
                       if s.endswith(".SHFE")
                       and "".join(ch for ch in s.split(".")[0] if ch.isalpha()) == product})

    live = pick(oms_symbols)
    if live:
        return live, dominant_of(live, product, dominant_overrides), False

    seed = pick(load_seed().get("symbols", []))
    if seed:
        return seed, dominant_of(seed, product, dominant_overrides), True

    builtin = sorted(SEED_DEFAULT.get(product, []))
    return builtin, dominant_of(builtin, product, dominant_overrides), True
