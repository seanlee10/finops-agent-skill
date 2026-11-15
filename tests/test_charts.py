import json
from pathlib import Path

from finops.charts import chart_plan, setup_charts
from finops.config import load_config
from finops.source import Client

ROOT = Path(__file__).resolve().parent.parent


def copy_config(tmp_path, name):
    src = ROOT / "examples"
    cfg = json.loads((src / f"{name}.json").read_text())
    for d in cfg["dimensions"]:
        for l in d["levels"]:
            if "mapping" in l:
                l["mapping"]["file"] = str(src / l["mapping"]["file"])
    for b in cfg["budgets"]:
        b["file"] = str(src / b["file"])
    cfg.pop("charts", None)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    return p


class FakeArize:
    def __init__(self):
        self.dashboards = {}
        self.mutations = []
        self.n = 0

    def __call__(self, url, body, headers):
        q, v = body["query"], body["variables"]
        if q.lstrip().startswith("mutation"):
            self.mutations.append((q, v["i"]))
            self.n += 1
            if "createDashboard" in q:
                did = f"D{self.n}"
                self.dashboards[did] = {"name": v["i"]["name"], "space": v["i"]["spaceId"], "titles": []}
                return {"data": {"createDashboard": {"dashboard": {"id": did}}}}
            self.dashboards[v["i"]["dashboardId"]]["titles"].append(v["i"]["title"])
            key = "createLineChartWidget" if "createLineChartWidget" in q else "createBarChartWidget"
            obj = "lineChartWidget" if key == "createLineChartWidget" else "barChartWidget"
            return {"data": {key: {obj: {"id": f"W{self.n}"}}}}
        if "organization" in q:
            return {"data": {"node": {"organization": {"id": "ORG"}}}}
        if "dashboards" in q:
            edges = [{"node": {"id": d, "name": x["name"]}} for d, x in self.dashboards.items()
                     if x["space"] == v["id"] and v["q"] in x["name"]]
            return {"data": {"node": {"dashboards": {"edges": edges}}}}
        if "lineChartWidgets" in q:
            t = self.dashboards[v["id"]]["titles"]
            return {"data": {"node": {"lineChartWidgets": {"edges": [{"node": {"title": x}} for x in t]},
                                      "barChartWidgets": {"edges": []}}}}
        raise AssertionError(q)


def test_plan_for_acme():
    c = load_config(str(ROOT / "examples" / "acme.json"))
    plan = chart_plan(c)
    assert list(plan) == ["U3BhY2U6MTAwMTpkZW1v"]
    titles = [w["title"] for w in plan["U3BhY2U6MTAwMTpkZW1v"]]
    assert titles == ["Daily spend by project",
                      "claude-code-finops-demo-live · daily spend by model",
                      "claude-code-finops-demo-live · daily spend by user.id",
                      "claude-code-finops-demo-live · LLM spans by user.id"]


def test_plan_groups_projects_per_space(tmp_path):
    c = load_config(str(copy_config(tmp_path, "assistant-synthetic")))
    plan = chart_plan(c)
    assert len(plan) == 2
    first = next(iter(plan.values()))[0]
    assert first["title"] == "Daily spend by project"
    assert [pl["title"] for pl in first["plots"]] == ["synth-assistant", "synth-search"]
    titles = [w["title"] for ws in plan.values() for w in ws]
    assert "synth-assistant · daily spend by auth.status" in titles


def test_setup_creates_line_and_bar_widgets_and_records_url(tmp_path):
    p = copy_config(tmp_path, "acme")
    fake = FakeArize()
    setup_charts(str(p), Client(url="http://x", transport=fake))
    widgets = [i for q, i in fake.mutations if "Widget" in q]
    assert len(widgets) == 4
    line = next(i for i in widgets if i["title"].endswith("by user.id") and "spend" in i["title"])
    assert line["widgetType"] == "lineChartWidget"
    assert line["timeSeriesDataGranularity"] == "day"
    plot = line["plots"][0]
    assert plot["metric"] == "sum" and plot["dimension"]["name"] == "attributes.llm.cost.total"
    assert plot["splitByEnabled"] and plot["splitByDimension"] == "attributes.user.id"
    bar = next(i for i in widgets if "LLM spans" in i["title"])
    assert bar["plots"][0]["dimension"]["name"] == "attributes.user.id"
    assert bar["plots"][0]["aggregation"] == "count"
    assert bar["sortOrder"] == "vol_desc"
    cfg = json.loads(p.read_text())
    assert cfg["charts"] == {"workshop": "https://app.arize.com/organizations/ORG/spaces/U3BhY2U6MTAwMTpkZW1v/dashboards/D1"}


def test_setup_is_idempotent(tmp_path):
    p = copy_config(tmp_path, "acme")
    fake = FakeArize()
    client = Client(url="http://x", transport=fake)
    setup_charts(str(p), client)
    n = len(fake.mutations)
    plan = setup_charts(str(p), client)
    assert len(fake.mutations) == n
    assert {x["action"] for x in plan} == {"reuse"}


def test_dry_run_changes_nothing(tmp_path):
    p = copy_config(tmp_path, "acme")
    before = p.read_text()
    fake = FakeArize()
    plan = setup_charts(str(p), Client(url="http://x", transport=fake), dry_run=True)
    assert fake.mutations == [] and p.read_text() == before
    assert len(plan) == 4 and {x["action"] for x in plan} == {"create"}
