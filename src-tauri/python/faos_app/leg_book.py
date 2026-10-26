"""策略级今昨账本（2026-10-19 定稿）：修复平仓归属错位。

本策略自身成交累计今昨账本（单位=手），账本唯一事实源=本策略算法成交；
跨交易日自动今→昨结转（上期所交易日：夜盘 21:00 后归次日）。
平仓今昨拆分按柜台实际持仓执行（2026-10-26 起，见 spread_service.
_plan_close_split）——账本经 close_pool 口径限定可平总量，防止吃他策略仓。
"""

from typing import Optional


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


def apply_trade_to_book(s, leg: str, direction, offset, volume: float) -> Optional[str]:
    """成交入账。direction/offset 接受 vnpy 枚举或值字符串（开/平/平今/平昨）。

    开仓 → 当日今仓；平仓 → 减对侧方向（卖平多、买平空），平今/平昨各归其位；
    笼统「平」对齐账户级净仓转换先平今后期。
    负值归零（平仓量超出策略归属——共享腿他策略仓/事故残留），返回溢出描述
    （None=无异常），由调用方记 FAOS_BOOK_NEGATIVE。"""
    d = str(getattr(direction, "value", direction))
    d = {"多": "long", "空": "short", "long": "long", "short": "short"}.get(d, d)
    off = str(getattr(offset, "value", offset))
    entry = _leg_entry(s, leg)
    if off in ("开", "open", ""):
        entry[f"{d}_td"] = round(entry[f"{d}_td"] + volume, 6)
    else:
        opp = "short" if d == "long" else "long"
        if "昨" in off or "yesterday" in off.lower():
            entry[f"{opp}_yd"] = round(entry[f"{opp}_yd"] - volume, 6)
        elif "今" in off or "today" in off.lower():
            entry[f"{opp}_td"] = round(entry[f"{opp}_td"] - volume, 6)
        else:  # 「平」：先平今后期（与账户级净仓转换顺序一致）
            take_td = min(max(entry[f"{opp}_td"], 0), volume)
            entry[f"{opp}_td"] = round(entry[f"{opp}_td"] - take_td, 6)
            entry[f"{opp}_yd"] = round(entry[f"{opp}_yd"] - (volume - take_td), 6)
    return _clamp_entry(entry)


def _clamp_entry(entry: dict) -> Optional[str]:
    """账本四项归零不为负（FAOS_BOOK_NEGATIVE），避免拆分计划/对账失真。"""
    bad = [f"{k} {entry[k]:g}→0" for k in ("long_td", "long_yd", "short_td", "short_yd")
           if entry.get(k, 0) < 0]
    for k in ("long_td", "long_yd", "short_td", "short_yd"):
        if entry.get(k, 0) < 0:
            entry[k] = 0.0
    return "、".join(bad) if bad else None
