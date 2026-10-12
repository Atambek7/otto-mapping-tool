from collections import defaultdict
from datetime import datetime, date, time, timedelta, timezone
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo


def calculate_start_date(daily_time: str = "08:00", tz_name: str = "Europe/Berlin") -> date:
    """
    Returns today if current local time in tz_name is before daily_time,
    otherwise returns tomorrow.
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    hour, minute = map(int, daily_time.split(":"))
    scheduled_today = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if now_local < scheduled_today:
        return now_local.date()
    else:
        return (now_local + timedelta(days=1)).date()


def distribute_products_round_robin(
    items: List[Dict[str, Any]],
    days: int = 7,
    daily_time: str = "08:00",
    tz_name: str = "Europe/Berlin",
    start_date: Optional[date] = None,
    min_dates: Optional[Dict[str, date]] = None,
) -> Dict[str, Any]:
    """
    Distributes products across `days` using an Interleaved Round-Robin category mixture.
    
    1. Groups items by category_id.
    2. Builds an interleaved sequence [c1_0, c2_0, ..., cK_0, c1_1, c2_1, ...]
    3. Partitions the sequence into days using round-robin while strictly adhering to `min_dates`
       (e.g. anti-collision delays from opposite account publication).
    4. Attaches precise scheduled_date in UTC calculated from daily_time in Europe/Berlin.
    """
    if not items:
        return {
            "total_products": 0,
            "days": days,
            "daily_time": daily_time,
            "timezone": tz_name,
            "schedule": {},
            "category_summary": {},
        }

    if days < 1:
        days = 1

    # 1. Group items by category_id
    cat_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        cat_id = str(item.get("category_id") or "uncategorized")
        cat_groups[cat_id].append(item)

    # Sort categories to ensure deterministic ordering
    sorted_categories = sorted(cat_groups.keys())

    # 2. Interleaved Round-Robin chaining
    interleaved: List[Dict[str, Any]] = []
    max_len = max(len(group) for group in cat_groups.values()) if cat_groups else 0

    for idx in range(max_len):
        for cat_id in sorted_categories:
            group = cat_groups[cat_id]
            if idx < len(group):
                interleaved.append(group[idx])

    # 3. Partition across days respecting min_dates
    if start_date is None:
        start_date = calculate_start_date(daily_time, tz_name)

    # Compute minimum day offset for each item
    min_day_map: Dict[str, int] = {}
    if min_dates:
        for it in interleaved:
            ean = it.get("product_ean")
            if ean and ean in min_dates:
                req_date = min_dates[ean]
                diff_days = (req_date - start_date).days
                min_day_map[ean] = max(1, diff_days + 1)

    max_req_day = max(min_day_map.values()) if min_day_map else 1
    effective_days = max(days, max_req_day)

    tz = ZoneInfo(tz_name)
    hour, minute = map(int, daily_time.split(":"))

    daily_schedule: Dict[int, List[Dict[str, Any]]] = {d: [] for d in range(1, effective_days + 1)}
    day_timestamps: Dict[int, datetime] = {}
    day_counts: Dict[int, int] = {d: 0 for d in range(1, effective_days + 1)}

    for d in range(1, effective_days + 1):
        target_day = start_date + timedelta(days=d - 1)
        local_dt = datetime.combine(target_day, time(hour, minute), tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        day_timestamps[d] = utc_dt

    for i, item in enumerate(interleaved):
        ean = item.get("product_ean")
        min_d = min_day_map.get(ean, 1)

        # Candidate days starting from min_d
        allowed_days = list(range(min_d, effective_days + 1))
        # Pick the least populated day among allowed days (stable chronological tie-breaking)
        best_day = min(allowed_days, key=lambda d: (day_counts[d], d - min_d))

        day_counts[best_day] += 1
        scheduled_item = dict(item)
        scheduled_item["scheduled_day"] = best_day
        scheduled_item["scheduled_date"] = day_timestamps[best_day]
        if min_d > 1:
            scheduled_item["stagger_delayed"] = True
            if ean and min_dates and ean in min_dates:
                scheduled_item["stagger_min_date"] = min_dates[ean].isoformat()

        daily_schedule[best_day].append(scheduled_item)

    # 4. Compile category breakdown per day for dashboard preview
    category_summary: Dict[str, Dict[str, int]] = {}
    for d in range(1, effective_days + 1):
        counts: Dict[str, int] = defaultdict(int)
        for itm in daily_schedule[d]:
            cat = str(itm.get("category_name") or itm.get("category_id") or "uncategorized")
            counts[cat] += 1
        category_summary[f"Day {d}"] = dict(counts)

    return {
        "total_products": len(items),
        "days": effective_days,
        "daily_time": daily_time,
        "timezone": tz_name,
        "start_date": start_date.isoformat(),
        "schedule": daily_schedule,
        "category_summary": category_summary,
    }
