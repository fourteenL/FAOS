"""合约种子文件：本地离线测试用 + 品种主力合约（按品种分组，显式主力）。

文件结构（config/contract_seed.json，随仓库分发，可直接编辑）：
{
  "updated_at": "2026-10-09",
  "products": {
    "ag": {"contracts": ["ag2610.SHFE", ...], "dominant": "ag2611"},
    ...
  }
}

- CTP 就绪时以 vnpy 真实合约覆盖刷新（按品种分组 + 主力=最近未来月推断）
- 非开盘时段品种展开回退到种子文件/内置兜底 → 开发阶段也能订阅测试
- dominant 不在 contracts 内（手工笔误）→ 回退日期推断
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

# 开发兜底（种子文件缺失时用；真实合约以开盘刷新为准）
SEED_DEFAULT = {
    "ag": {"contracts": ["ag2610", "ag2612", "ag2702", "ag2704", "ag2706"], "dominant": None},
    "au": {"contracts": ["au2610", "au2612", "au2602", "au2604", "au2606"], "dominant": None},
}

SEED_NAME = "contract_seed.json"


def seed_path() -> Path:
    """种子文件定位：仓库/工作目录的 config/contract_seed.json（exe 同目录兼容）。"""
    candidates = []
    try:
        here = Path(__file__).resolve()
        for parent in here.parents[:6]:
            candidates.append(parent / "config" / SEED_NAME)
    except Exception:
        pass
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
    """读种子全文（含 products 分组）；无/坏文件返回空 dict。"""
    p = seed_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _grouped_symbols(data: dict, product: str) -> tuple[list, str]:
    """分组数据 → (裸合约列表, dominant 裸号)。dominant 无效时回退推断。"""
    entry = (data.get("products") or {}).get(product) or {}
    contracts = sorted({c.split(".")[0] for c in entry.get("contracts", [])
                        if isinstance(c, str) and c.endswith(".SHFE")
                        and "".join(ch for ch in c.split(".")[0] if ch.isalpha()) == product})
    dominant = str(entry.get("dominant") or "").split(".")[0]
    if dominant not in contracts:
        dominant = dominant_of(contracts, product)
    return contracts, dominant


def save_seed(symbols: list) -> None:
    """覆盖写种子：OMS 合约列表 → 按品种分组 + 主力推断（保留既有品种备注性数据）。"""
    groups: dict[str, list] = {}
    for vt in symbols:
        if not vt.endswith(".SHFE"):
            continue
        sym = vt.split(".")[0]
        product = "".join(ch for ch in sym if ch.isalpha())
        groups.setdefault(product, []).append(vt)
    old = load_seed()
    products = {}
    for product, vts in sorted(groups.items()):
        contracts = sorted(vts)
        products[product] = {
            "contracts": contracts,
            "dominant": dominant_of([c.split(".")[0] for c in contracts], product),
        }
    data = {"updated_at": date.today().isoformat(), "products": products}
    p = seed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def seed_age_days() -> float:
    data = load_seed()
    try:
        return (date.today() - date.fromisoformat(data["updated_at"])).days
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
    """品种主力合约推断：配置覆盖 > 最近未来交割月 > 最近过去月。"""
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
    """品种合约来源：优先 OMS 实时，其次种子文件（分组+显式主力），最后内置兜底。

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

    data = load_seed()
    contracts, dominant = _grouped_symbols(data, product)
    if contracts:
        if dominant_overrides and product in dominant_overrides:  # 配置覆盖优先于种子显式值
            dominant = dominant_of(contracts, product, dominant_overrides)
        return contracts, dominant, True

    builtin = sorted(SEED_DEFAULT.get(product, {}).get("contracts", []))
    return builtin, dominant_of(builtin, product, dominant_overrides), True
