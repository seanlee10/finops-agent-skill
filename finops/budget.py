import calendar
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from .config import Budget
from .rollup import Tree

AT_RISK_PCT = 80.0


def month_window(as_of: datetime, tz_offset_hours: float) -> Tuple[datetime, datetime]:
    local = as_of.astimezone(timezone(timedelta(hours=tz_offset_hours)))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0), as_of


def budget_rows(trees: Dict[str, Tree], budgets: List[Budget], as_of: datetime,
                tz_offset_hours: float) -> List[dict]:
    start, _ = month_window(as_of, tz_offset_hours)
    month = start.strftime("%Y-%m")
    days_in_month = calendar.monthrange(start.year, start.month)[1]
    elapsed = (as_of - start).total_seconds() / 86400
    rows = []
    for b in budgets:
        if b.month != month:
            continue
        spent = trees.get(b.dimension, {}).get(b.level, {}).get(b.value, 0.0)
        projected = spent / elapsed * days_in_month if elapsed > 0 else spent
        burn_pct = spent / b.usd * 100 if b.usd else None
        projected_pct = projected / b.usd * 100 if b.usd else None
        if projected_pct is None:
            status = "NO BUDGET"
        elif projected > b.usd:
            status = "OVER (projected)"
        elif projected_pct >= AT_RISK_PCT:
            status = "AT RISK"
        else:
            status = "OK"
        rows.append({"dimension": b.dimension, "level": b.level, "value": b.value, "budget": b.usd,
                     "spent": spent, "burn_pct": burn_pct, "projected": projected,
                     "projected_pct": projected_pct, "status": status})
    return rows
