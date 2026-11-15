from finops.units import unit_economics


def test_full_coverage_exact():
    costs = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
    outcomes = {"a": "completed", "b": "completed", "c": "failed", "d": "partial"}
    u = unit_economics(costs, outcomes, ["completed"])
    assert u["sessions"] == 4 and u["total_spend"] == 10.0
    assert u["cost_per_session"] == 2.5
    assert u["evaluated"] == 4 and u["coverage_pct"] == 100.0 and u["is_estimate"] is False
    assert u["completed"] == 2
    assert u["cost_per_completed"] == 5.0
    assert u["failed_spend"] == 7.0
    assert u["failed_spend_pct"] == 70.0


def test_sampled_marks_estimate():
    costs = {f"s{i}": 1.0 for i in range(100)}
    outcomes = {f"s{i}": ("completed" if i % 2 else "failed") for i in range(40)}
    u = unit_economics(costs, outcomes, ["completed"])
    assert u["is_estimate"] is True
    assert u["coverage_pct"] == 40.0
    assert u["total_spend"] == 100.0
    assert u["cost_per_completed"] == 2.0


def test_zero_completed_sessions():
    u = unit_economics({"a": 1.0}, {"a": "failed"}, ["completed"])
    assert u["cost_per_completed"] is None
    e = unit_economics({}, {}, ["completed"])
    assert e["cost_per_session"] is None and e["coverage_pct"] is None and e["cost_per_completed"] is None


def test_outcomes_for_unknown_sessions_ignored():
    u = unit_economics({"a": 1.0}, {"a": "completed", "zzz": "completed"}, ["completed"])
    assert u["evaluated"] == 1 and u["completed"] == 1


def test_segment_split():
    costs = {"a": 1.0, "b": 3.0, "c": 2.0}
    outcomes = {"a": "completed", "b": "failed", "c": "completed"}
    segs = {"authenticated": {"a": 1.0, "b": 3.0}, "anonymous": {"c": 2.0}}
    u = unit_economics(costs, outcomes, ["completed"], segs)
    assert u["by_segment"]["authenticated"]["cost_per_completed"] == 4.0
    assert u["by_segment"]["anonymous"]["cost_per_completed"] == 2.0
    assert u["by_segment"]["authenticated"]["sessions"] == 2
