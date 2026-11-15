from datetime import datetime, timedelta, timezone

from finops.budget import budget_rows, month_window
from finops.config import Budget

KST = timezone(timedelta(hours=9))


def test_v1_statuses():
    trees = {"org": {"team": {"Platform": 86.64, "Firmware": 2.762, "Data Science": 1.87}}}
    budgets = [Budget("org", "team", "Platform", "2026-11", 264.0), Budget("org", "team", "Firmware", "2026-11", 11.0),
               Budget("org", "team", "Data Science", "2026-11", 16.0)]
    rows = {r["value"]: r for r in budget_rows(trees, budgets, datetime(2026, 11, 9, 13, 29, tzinfo=KST), 9)}
    assert rows["Platform"]["status"] == "OVER (projected)"
    assert round(rows["Platform"]["projected_pct"]) == 115
    assert round(rows["Platform"]["projected"], 2) == 303.58
    assert rows["Firmware"]["status"] == "AT RISK"
    assert round(rows["Firmware"]["projected_pct"]) == 88
    assert rows["Data Science"]["status"] == "OK"


def test_month_start_in_config_timezone():
    as_of = datetime(2026, 11, 1, 3, 0, tzinfo=KST)
    start, end = month_window(as_of, 9)
    assert start == datetime(2026, 11, 1, tzinfo=KST)
    assert end == as_of
    utc_as_of = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)
    assert month_window(utc_as_of, 9)[0] == datetime(2026, 11, 1, tzinfo=KST)
    rows = budget_rows({"d": {"t": {"x": 1.0}}}, [Budget("d", "t", "x", "2026-11", 100.0)], as_of, 9)
    assert rows[0]["projected"] == 1.0 / 0.125 * 30


def test_budget_without_spend_is_ok():
    rows = budget_rows({"d": {"t": {}}}, [Budget("d", "t", "x", "2026-11", 50.0)], datetime(2026, 11, 9, tzinfo=KST), 9)
    assert rows[0]["spent"] == 0.0 and rows[0]["status"] == "OK"


def test_other_month_budgets_ignored():
    rows = budget_rows({"d": {"t": {"x": 5.0}}}, [Budget("d", "t", "x", "2026-10", 1.0)], datetime(2026, 11, 9, tzinfo=KST), 9)
    assert rows == []
