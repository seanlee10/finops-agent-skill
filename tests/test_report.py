import json
from datetime import datetime, timedelta, timezone

from finops.config import Budget, Config, Dimension, Level, Outcome, Project
from finops.report import SECTION_HEADINGS, build, render_md

KST = timezone(timedelta(hours=9))
START = datetime(2026, 11, 1, tzinfo=KST)
END = datetime(2026, 11, 9, 12, tzinfo=KST)
LONG = ("Hi, I am looking for a three bedroom house near a good primary school with a big backyard, "
        "ideally close to the train line, and my budget is around nine hundred thousand dollars in total ok")


def make_config(**over):
    projects = [Project("p1", "ra-prod", "s1", "group-a", {
        "attributes.user.id": "W_USER", "attributes.session.id": "W_SESS", "attributes.auth.status": "W_AUTH"}),
        Project("p2", "ra-search", "s1", "group-a", {"attributes.session.id": "W_SESS2"})]
    dims = [Dimension("by_project", [Level("project", "project"), Level("team", "map", {"ra-prod": "Assistant"})])]
    cfg = dict(name="t", tz_offset_hours=9, currency="USD", projects=projects, dimensions=dims,
               budgets=[Budget("by_project", "team", "Assistant", "2026-11", 100.0)], unit="session",
               outcome=Outcome("eval_label", "task_completion", ["completed"], True, ["completed", "failed"]),
               segments=["auth.status"], cost_agent=True)
    cfg.update(over)
    return Config(**cfg)


class FakeClient:
    def __init__(self, totals=None, pivots=None, issues=None, outcomes=None):
        self.totals = totals if totals is not None else {"p1": {"m": 6.0}, "p2": {"m": 1.5}}
        self.pivots = pivots if pivots is not None else {
            "W_SESS": {"a": 1.0, "b": 3.0, "c": 2.0},
            "W_SESS2": {"d": 1.5},
            "W_AUTH": {"authenticated": 4.0, "anonymous": 2.0},
        }
        self.issues = issues if issues is not None else {"p1": [{"title": "Tools inflate prompts", "severity": "high",
                                                                "status": "OPEN", "entity": "m",
                                                                "estimated_savings_usd": None}]}
        self.outcomes = outcomes if outcomes is not None else {"a": "completed", "b": "failed", "c": "completed", "d": "completed"}
        self.calls = []

    def project_cost_by_model(self, project_id, start, end):
        return dict(self.totals.get(project_id, {}))

    def pivot(self, widget_id, start, end, filters=None):
        self.calls.append((widget_id, filters))
        if filters:
            value = filters[0]["filterItems"][0]["categoricalValues"][0]
            seg = {"authenticated": {"a": 1.0, "b": 3.0}, "anonymous": {"c": 2.0}}
            return {k: {"cost": v, "tokens": 0, "sessions": 1} for k, v in seg.get(value, {}).items()}
        return {k: {"cost": v, "tokens": 0, "sessions": 1} for k, v in self.pivots.get(widget_id, {}).items()}

    def cost_agent_issues(self, space_id, project_id):
        return list(self.issues.get(project_id, []))

    def session_outcomes(self, widget_id, model_id, eval_name, labels, start, end, filter_type="annotation"):
        return dict(self.outcomes)

    def cost_agent_enabled(self, space_id, project_id):
        return True


def run(cfg=None, client=None):
    cfg = cfg or make_config()
    result = build(cfg, client or FakeClient(), START, END, END)
    return result, render_md(result, cfg)


def test_section_order():
    _, md = run()
    positions = [md.index(h) for h in SECTION_HEADINGS]
    assert positions == sorted(positions)


def test_totals_and_reconcile():
    result, md = run()
    assert result["total"] == 7.5
    assert result["reconciles"] is True
    assert result["trees"]["by_project"]["team"] == {"Assistant": 6.0, "Unmapped": 1.5}
    assert "$7.50" in md.splitlines()[0] or "$7.50" in md


def test_footer_has_invoice_disclaimer_and_unmapped():
    _, md = run()
    footer = md[md.index("## Data quality"):]
    assert "Costs are as recorded in Arize traces; provider or gateway invoices are not reconciled." in footer
    assert "Unmapped" in footer and "$1.50" in footer


def test_empty_window_report():
    client = FakeClient(totals={}, pivots={}, issues={}, outcomes={})
    result, md = run(client=client)
    assert result["total"] == 0.0
    assert "$0.00" in md
    assert "n/a" in md


def test_no_message_content_in_output():
    client = FakeClient()
    client.pivots["W_AUTH"] = {"authenticated": 4.0, LONG: 2.0}
    result, md = run(client=client)
    blob = md + json.dumps(result)
    assert LONG not in blob
    assert LONG[:40] not in blob
    footer = md[md.index("## Data quality"):]
    assert "auth.status" in footer and "longer than 64" in footer


def test_not_reconciled_banner():
    client = FakeClient()
    client.pivots["W_SESS"] = {"a": 1.0}
    cfg = make_config(dimensions=[Dimension("org", [Level("user", "attribute:user.id")])])
    client.pivots["W_USER"] = {"u1": 9.0}
    _, md = run(cfg, client)
    assert md.startswith("**Totals do not reconcile**")


def test_cost_agent_issues_listed_per_project():
    _, md = run()
    section = md[md.index("## Cost Agent issues"):md.index("## Data quality")]
    assert "ra-prod" in section and "Tools inflate prompts" in section
    assert "ra-search" in section and "No open Cost Agent issues" in section


def test_sampled_outcomes_labelled_estimate():
    client = FakeClient(outcomes={"a": "completed"})
    _, md = run(client=client)
    assert "estimate (1 of 4 sessions evaluated)" in md


def test_budgets_skipped_outside_month_to_date():
    cfg = make_config()
    result = build(cfg, FakeClient(), datetime(2026, 11, 3, tzinfo=KST), END, END)
    assert result["budgets"] == []
    assert any("month-to-date" in n for n in result["data_quality"]["notes"])


def test_missing_widget_noted():
    cfg = make_config()
    cfg.projects[1].widgets = {}
    result, _ = run(cfg)
    assert any("ra-search" in n and "session" in n for n in result["data_quality"]["notes"])


def test_segment_on_root_spans_only_uses_session_costs():
    class RootOnly(FakeClient):
        def pivot(self, widget_id, start, end, filters=None):
            out = super().pivot(widget_id, start, end, filters)
            if filters or widget_id == "W_AUTH":
                return {k: {**v, "cost": 0.0} for k, v in out.items()}
            return out
    result, md = run(client=RootOnly())
    assert result["segments"]["auth.status"] == {"authenticated": 4.0, "anonymous": 2.0, "(no value)": 1.5}
    seg = result["units"]["by_segment"]["auth.status"]
    assert seg["authenticated"]["total_spend"] == 4.0
    assert seg["anonymous"]["cost_per_completed"] == 2.0


def test_unreadable_project_is_excluded_with_note():
    from finops.source import SourceError

    class Partial(FakeClient):
        def project_cost_by_model(self, project_id, start, end):
            if project_id == "p2":
                raise SourceError("Forbidden access: requestor is not allowed to perform this operation.")
            return super().project_cost_by_model(project_id, start, end)

        def pivot(self, widget_id, start, end, filters=None):
            if widget_id == "W_SESS2":
                raise SourceError("Forbidden access")
            return super().pivot(widget_id, start, end, filters)

        def cost_agent_issues(self, space_id, project_id):
            if project_id == "p2":
                raise SourceError("Forbidden access")
            return super().cost_agent_issues(space_id, project_id)

    result, md = run(client=Partial())
    assert result["total"] == 6.0
    assert result["excluded_projects"] == ["ra-search"]
    assert "ra-search" not in result["projects"]
    assert md.startswith("**Incomplete**")
    assert any("ra-search" in n and "Forbidden" in n for n in result["data_quality"]["notes"])


def test_root_status_with_slash_in_project_id():
    cfg = make_config(outcome=Outcome("root_status"), segments=[])
    cfg.projects[0].id = "TW9kZWw6/MTIz"

    class C(FakeClient):
        def project_cost_by_model(self, project_id, start, end):
            return {"m": 6.0} if project_id == "TW9kZWw6/MTIz" else {"m": 1.5}

        def pivot(self, widget_id, start, end, filters=None):
            if filters and filters[0]["filterItems"][0]["dimension"]["name"] == "status_code":
                return {"b": {"cost": 3.0, "tokens": 0, "sessions": 1}} if widget_id == "W_SESS" else {}
            return super().pivot(widget_id, start, end, None)
    result, _ = run(cfg, C())
    assert result["units"]["completed"] == 3
    assert result["units"]["failed_spend"] == 3.0


def test_filtered_pivot_failure_does_not_abort_report():
    from finops.source import SourceError

    class C(FakeClient):
        def pivot(self, widget_id, start, end, filters=None):
            if filters:
                raise SourceError("timeout")
            return super().pivot(widget_id, start, end, None)
    result, md = run(client=C())
    assert result["total"] == 7.5
    assert any("auth.status" in n and "timeout" in n for n in result["data_quality"]["notes"])


def test_empty_breakdown_with_spend_is_flagged():
    client = FakeClient()
    client.pivots["W_SESS"] = {}
    result, md = run(client=client)
    assert any("ra-prod" in n and "returned no rows" in n for n in result["data_quality"]["notes"])


def test_segment_table_sums_to_total_and_multi_value_sessions_counted_once():
    class C(FakeClient):
        def pivot(self, widget_id, start, end, filters=None):
            if filters:
                value = filters[0]["filterItems"][0]["categoricalValues"][0]
                seg = {"authenticated": ["a", "b"], "anonymous": ["b", "c"]}
                return {k: {"cost": 0, "tokens": 0, "sessions": 1} for k in seg.get(value, [])}
            return super().pivot(widget_id, start, end, None)
    result, md = run(client=C())
    seg = result["segments"]["auth.status"]
    assert seg["authenticated"] == 1.0 and seg["anonymous"] == 2.0 and seg["(multiple)"] == 3.0
    assert round(sum(seg.values()), 6) == 7.5
    assert "(not in a session)" in seg or "(no value)" in seg


def test_unit_spend_gap_noted():
    client = FakeClient()
    client.pivots["W_SESS"] = {"a": 1.0, "b": 3.0}
    result, _ = run(client=client)
    assert any("not attributable to a session" in n for n in result["data_quality"]["notes"])


def test_outcome_filter_type_passed_through():
    seen = {}

    class C(FakeClient):
        def session_outcomes(self, widget_id, model_id, eval_name, labels, start, end, filter_type="annotation"):
            seen["ft"] = filter_type
            return super().session_outcomes(widget_id, model_id, eval_name, labels, start, end)
    cfg = make_config(outcome=Outcome("eval_label", "task_completion", ["completed"], True,
                                      ["completed", "failed"], "llmEval"))
    run(cfg, C())
    assert seen["ft"] == "llmEval"


def test_window_shown_in_config_timezone_to_the_second():
    utc_end = datetime(2026, 11, 10, 8, 2, 9, 351027, tzinfo=timezone.utc)
    result = build(make_config(), FakeClient(), START, utc_end, utc_end)
    assert result["window"]["start"] == "2026-11-01T00:00:00+09:00"
    assert result["window"]["end"] == "2026-11-10T17:02:09+09:00"
    assert "2026-11-10T17:02:09+09:00" in render_md(result, make_config())


def _cost_section(md):
    return md[md.index("## Cost Agent issues"):md.index("## Data quality")]


def test_cost_agent_not_set_up_is_distinct_from_no_issues():
    class C(FakeClient):
        def cost_agent_enabled(self, space_id, project_id):
            return project_id == "p1"
    client = C(issues={})
    _, md = run(client=client)
    section = _cost_section(md)
    assert "**ra-prod**: No open Cost Agent issues." in section
    assert "**ra-search**: Cost Agent is not set up on this project." in section
    assert "or Cost Agent does not run" not in section


def test_cost_agent_query_failure_is_not_reported_as_no_issues():
    from finops.source import SourceError

    class C(FakeClient):
        def cost_agent_issues(self, space_id, project_id):
            if project_id == "p2":
                raise SourceError("timeout")
            return super().cost_agent_issues(space_id, project_id)
    _, md = run(client=C())
    section = _cost_section(md)
    assert "**ra-search**: Could not read Cost Agent issues" in section
    assert "ra-search**: No open" not in section


def test_cost_agent_setup_check_failure_says_unknown():
    from finops.source import SourceError

    class C(FakeClient):
        def cost_agent_enabled(self, space_id, project_id):
            raise SourceError("Forbidden")
    _, md = run(client=C(issues={}))
    section = _cost_section(md)
    assert "**ra-search**: No open Cost Agent issues found; could not check whether Cost Agent runs on this project." in section


def test_chart_links_under_title():
    cfg = make_config(charts={"workshop": "https://app.arize.com/organizations/O/spaces/S/dashboards/D"})
    _, md = run(cfg)
    lines = md.splitlines()
    i = lines.index("# FinOps report: t")
    assert "Charts: [workshop](https://app.arize.com/organizations/O/spaces/S/dashboards/D)" in lines[i:i + 6]


def test_no_chart_line_without_charts():
    _, md = run()
    assert "Charts:" not in md


def _over_budget_client():
    client = FakeClient()
    client.totals = {"p1": {"m": 96.0}, "p2": {"m": 1.5}}
    client.pivots["W_SESS"] = {"a": 30.0, "b": 40.0, "c": 26.0}
    return client


def test_investigation_sections_in_addendum_order():
    from finops.report import render_investigation
    cfg = make_config(charts={"workshop": "https://app.arize.com/x/dashboards/D"})
    result = build(cfg, _over_budget_client(), START, END, END)
    md = render_investigation(result, cfg)
    heads = ["### Summary", "### What was investigated", "### Diagnosis", "### Trace links", "### Next steps",
             "### Report"]
    pos = [md.index(h) for h in heads]
    assert pos == sorted(pos)
    trace = md[md.index("### Trace links"):md.index("### Next steps")]
    assert "[FinOps charts (workshop)](https://app.arize.com/x/dashboards/D)" in trace
    assert "## Data quality" in md[md.index("### Report"):] or "#### Data quality" in md


def test_investigation_flags_over_budget_as_signal_issue_card():
    from finops.report import render_investigation
    cfg = make_config()
    result = build(cfg, _over_budget_client(), START, END, END)
    md = render_investigation(result, cfg)
    blocks = md.split("```signal_issue\n")[1:]
    assert len(blocks) == 1
    card = json.loads(blocks[0].split("\n```")[0])
    assert card["issue_key"] == "budget-by-project-team-assistant"
    assert card["title"] == "Assistant projected over budget"
    assert card["severity"] == "high" and card["entity"] == "Assistant"
    assert "$100.00" in card["body_md"] and "```" not in card["body_md"]
    assert "type" not in card


def test_investigation_without_flags_says_no_issue():
    from finops.report import render_investigation
    cfg = make_config()
    result = build(cfg, FakeClient(), START, END, END)
    md = render_investigation(result, cfg)
    diag = md[md.index("### Diagnosis"):md.index("### Trace links")]
    assert "No issue found" in diag and "signal_issue" not in md
    assert "None" in md[md.index("### Trace links"):md.index("### Next steps")]


def test_investigation_report_headings_nest_under_report():
    from finops.report import render_investigation
    md = render_investigation(build(make_config(), FakeClient(), START, END, END), make_config())
    lines = md.splitlines()
    assert not [l for l in lines if l.startswith("# ") or l.startswith("## ")]
    assert "### FinOps report: t" in lines and "#### Data quality" in lines
