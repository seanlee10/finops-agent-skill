import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from finops.source import Client, SourceError, attr_filter, default_url

FIX = Path(__file__).resolve().parent / "fixtures" / "graphql"
KST = timezone(timedelta(hours=9))
START = datetime(2026, 11, 1, tzinfo=KST)
END = datetime(2026, 11, 10, tzinfo=KST)


def load(name):
    return json.loads((FIX / name).read_text())


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return self.responses.pop(0)


def test_cost_by_model_sums():
    t = Recorder(load("cost_by_model.json"))
    out = Client(url="http://x/graphql", transport=t).project_cost_by_model("P", START, END)
    assert set(out) == {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
    assert round(sum(out.values()), 6) == 386.884705


def test_pivot_reads_single_response_fully():
    t = Recorder(load("pivot_by_user.json"))
    out = Client(url="http://x/graphql", transport=t).pivot("W", START, END)
    assert len(out) == 12
    assert round(sum(v["cost"] for v in out.values()), 6) == 386.884705
    assert out["a.kowalski"]["sessions"] == 11
    assert "first" not in t.calls[0][1]["variables"]


def test_pivot_null_cells_are_zero():
    resp = {"data": {"node": {"pivotData": {"columns": [
        {"id": "c", "dimension": "attributes.llm.cost.total", "aggregation": "sum"}],
        "rows": [{"groupValue": "x", "cells": [{"columnId": "c", "value": None}]}]}}}}
    out = Client(url="http://x/graphql", transport=Recorder(resp)).pivot("W", START, END)
    assert out == {"x": {"cost": 0.0, "tokens": 0.0, "sessions": 0.0}}


def test_dates_sent_with_offset():
    t = Recorder(load("cost_by_model.json"))
    Client(url="http://x/graphql", transport=t).project_cost_by_model("P", START, END)
    v = t.calls[0][1]["variables"]
    assert v["s"].endswith("+09:00") and v["e"].endswith("+09:00")


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        Client(url="http://x/graphql", transport=Recorder()).project_cost_by_model("P", datetime(2026, 11, 1), END)


def test_graphql_error_raises():
    t = Recorder({"errors": [{"message": "Unauthorized access"}], "data": {"node": None}})
    with pytest.raises(SourceError, match="Unauthorized"):
        Client(url="http://x/graphql", transport=t).pivot("W", START, END)


def test_cost_agent_issues_filters_open():
    t = Recorder(load("agent_issues.json"))
    out = Client(url="http://x/graphql", transport=t).cost_agent_issues("S", "TW9kZWw6MTAwNTpkZW1v")
    assert [i["title"] for i in out] == ["Opus turns carry very large prompt contexts"]
    assert set(out[0]) == {"title", "severity", "status", "entity", "estimated_savings_usd"}


def test_attr_filter_shape():
    f = attr_filter("M", "auth.status", "anonymous")
    item = f["filterItems"][0]
    assert f["modelId"] == "M"
    assert item["dimension"]["name"] == "attributes.auth.status"
    assert item["categoricalValues"] == ["anonymous"]


def test_session_outcomes_one_query_per_label():
    def rows(*ids):
        return {"data": {"node": {"pivotData": {"columns": [
            {"id": "c", "dimension": "attributes.llm.cost.total", "aggregation": "sum"}],
            "rows": [{"groupValue": i, "cells": [{"columnId": "c", "value": 0.1}]} for i in ids]}}}}
    t = Recorder(rows("s1", "s2"), rows("s3"))
    out = Client(url="http://x/graphql", transport=t).session_outcomes(
        "W", "M", "task_completion", ["completed", "failed"], START, END)
    assert out == {"s1": "completed", "s2": "completed", "s3": "failed"}
    assert len(t.calls) == 2


def test_default_url_in_sandbox(monkeypatch):
    monkeypatch.delenv("FINOPS_GRAPHQL_URL", raising=False)
    monkeypatch.setenv("SANDBOXCONTROLLER_URL", "http://sandboxcontroller")
    assert default_url() == "https://graphql.sandboxcontroller/graphql"
    monkeypatch.delenv("SANDBOXCONTROLLER_URL")
    assert default_url() == "https://app.arize.com/graphql"
    monkeypatch.setenv("FINOPS_GRAPHQL_URL", "https://app.eu-west-1a.arize.com/graphql")
    assert default_url() == "https://app.eu-west-1a.arize.com/graphql"


def _rows_resp(keys, cost=1.0):
    return {"data": {"node": {"pivotData": {"columns": [
        {"id": "c", "dimension": "attributes.llm.cost.total", "aggregation": "sum"}],
        "rows": [{"groupValue": k, "cells": [{"columnId": "c", "value": cost}]} for k in keys]}}}}


def test_pivot_splits_window_when_server_row_limit_hit():
    # 1,600 sessions, one every 7 minutes; the fake server truncates at 1,000 rows like Arize does.
    base = START
    sessions = {f"s{i}": base + timedelta(minutes=7 * i) for i in range(1600)}
    def transport(url, body, headers):
        v = body["variables"]
        s, e = datetime.fromisoformat(v["s"]), datetime.fromisoformat(v["e"])
        keys = [k for k, t in sessions.items() if s <= t < e]
        return _rows_resp(keys[:1000] if len(keys) > 1000 else keys)

    out = Client(url="http://x/graphql", transport=transport).pivot("W", START, END)
    assert len(out) == 1600
    assert sum(v["cost"] for v in out.values()) == 1600.0


def test_pivot_raises_when_slice_cannot_get_under_limit():
    t = lambda url, body, headers: _rows_resp([f"k{i}" for i in range(1000)])
    with pytest.raises(SourceError, match="1,000-row limit"):
        Client(url="http://x/graphql", transport=t).pivot("W", START, END)


def test_cost_agent_enabled_needs_active_cost_automation_on_project():
    autos = {"data": {"node": {"automations": [
        {"producer": "cost", "modelId": "M1", "disabledAtUnix": 0},
        {"producer": "cost", "modelId": "M2", "disabledAtUnix": 1794265432},
        {"producer": "signal", "modelId": "M3", "disabledAtUnix": 0},
        {"producer": None, "modelId": "M4", "disabledAtUnix": 0}]}}}
    c = Client(url="http://x/graphql", transport=lambda u, b, h: autos)
    assert c.cost_agent_enabled("S", "M1") is True
    assert c.cost_agent_enabled("S", "M2") is False
    assert c.cost_agent_enabled("S", "M3") is False
    assert c.cost_agent_enabled("S", "M9") is False
