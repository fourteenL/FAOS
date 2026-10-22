"""策略级今昨账本（2026-10-19 定稿）：修复平仓归属错位。

根因：平仓 lock=False 净仓转换时 vnpy 按账户聚合今昨仓拆平今/平昨单
（convert_order_request_net 先平今），共享主力腿的两策略各自平仓时会
错吃对方的今昨仓——策略自己的昨仓残留、别人的今仓被平（且上期所平今
费率更高）。方案：本策略自身成交累计今昨账本（单位=手），平仓按
「账本 ∩ 账户实际」显式拆平今/平昨委托（先平昨省费），账本不足的量
回落账户级净仓转换（现状兜底，不比修复前差）。

账本唯一事实源=本策略算法成交（与 held_units 同源口径）；跨交易日
自动今→昨结转（上期所交易日：夜盘 21:00 后归次日）。
"""

_EPS = 1e-6


def trading_day_str(now) -> str:
    """上期所交易日（"YYYY-MM-DD"）：夜盘 21:00 后归次日；凌晨夜盘延续归当天。"""
    from datetime import timedelta

    d = now.date()
    if now.hour >= 21:
        d += timedelta(days=1)
    return d.isoformat()


def roll_book(s, now) -> bool:
    """跨交易日结转：账本归属日 ≠ 当前交易日 → 各腿今仓转昨仓（td→yd）。

    幂等；返回是否有变更（book_day 锚定首日也算变更，账本为空时无实际影响）。"""
    day = trading_day_str(now)
    if s.book_day == day:
        return False
    s.book_day = day
    for entry in (s.leg_book or {}).values():
        entry["long_yd"] = round(entry.get("long_yd", 0) + entry.get("long_td", 0), 6)
        entry["long_td"] = 0
        entry["short_yd"] = round(entry.get("short_yd", 0) + entry.get("short_td", 0), 6)
        entry["short_td"] = 0
    return True


def _leg_entry(s, leg: str) -> dict:
    if s.leg_book is None:
        s.leg_book = {}
    return s.leg_book.setdefault(
        leg, {"long_td": 0, "long_yd": 0, "short_td": 0, "short_yd": 0})


def _clamp_entry(s, leg: str) -> None:
    """账本四项不为负（2026-10-22 实证：lock 转换平掉别策略昨仓时本策略账本转负，
    后续平仓拆分计划失真）——归零并留 FAOS_BOOK_NEGATIVE。"""
    entry = (s.leg_book or {}).get(leg)
    if not entry:
        return
    for k in ("long_td", "long_yd", "short_td", "short_yd"):
        if entry.get(k, 0) < 0:
            from faos_app.log import log

            log(f"FAOS_BOOK_NEGATIVE {getattr(s, 'pair', '')} {leg} {k}={entry[k]:g} 归零")
            entry[k] = 0


def apply_trade_to_book(s, leg: str, direction, offset, volume: float) -> None:
    """成交入账。direction/offset 接受 vnpy 枚举或值字符串（开/平/平今/平昨）。

    开仓 → 当日今仓；平仓 → 减对侧方向（卖平多、买平空），平今/平昨各归其位；
    笼统「平」对齐账户级净仓转换先平今后期。"""
    d = str(getattr(direction, "value", direction))
    d = {"多": "long", "空": "short", "long": "long", "short": "short"}.get(d, d)
    off = str(getattr(offset, "value", offset))
    entry = _leg_entry(s, leg)
    if off in ("开", "open", ""):
        entry[f"{d}_td"] = round(entry[f"{d}_td"] + volume, 6)
        _clamp_entry(s, leg)
        return
    opp = "short" if d == "long" else "long"
    if "昨" in off or "yesterday" in off.lower():
        entry[f"{opp}_yd"] = round(entry[f"{opp}_yd"] - volume, 6)
    elif "今" in off or "today" in off.lower():
        entry[f"{opp}_td"] = round(entry[f"{opp}_td"] - volume, 6)
    else:  # 「平」：先平今后期（与账户级净仓转换顺序一致）
        take_td = min(max(entry[f"{opp}_td"], 0), volume)
        entry[f"{opp}_td"] = round(entry[f"{opp}_td"] - take_td, 6)
        entry[f"{opp}_yd"] = round(entry[f"{opp}_yd"] - (volume - take_td), 6)
    _clamp_entry(s, leg)


def plan_close_split(s, actual_book: dict, leg_pairs: list, key: str, need: float):
    """平仓显式今昨拆分计划：按策略账本 ∩ 账户实际，先平昨（上期所平今费率高）后期。

    s.leg_book 键=腿裸符号；actual_book 键=vt_symbol（account_position_book 产出）。
    leg_pairs: [(裸符号, vt_symbol), ...]；key: 消耗的账本方向（平多=open_short→
    "long"，平空=open_long→"short"）；need: 每腿需平手数。
    返回 {vt_symbol: {"yd": n, "td": m}}；任一腿「账本∩实际」不足 need → None
    （整体回落账户级净仓转换，告警由对账兜底）。"""
    out = {}
    for leg, vt in leg_pairs:
        entry = (s.leg_book or {}).get(leg) or {}
        act = actual_book.get(vt) or {}
        yd = min(entry.get(f"{key}_yd", 0), act.get(f"{key}_yd", 0))
        td = min(entry.get(f"{key}_td", 0), act.get(f"{key}_today", 0))
        if yd + td < need - _EPS:
            return None
        yd_use = min(yd, need)
        out[vt] = {"yd": round(yd_use, 6), "td": round(need - yd_use, 6)}
    return out
