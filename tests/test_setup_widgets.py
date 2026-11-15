import json
from pathlib import Path

from finops.source import Client
from finops.widgets import grid_position, plan_groupings, setup

ROOT = Path(__file__).resolve().parent.parent


class FakeArize:
    """Programmatic GraphQL stand-in: dashboards, pivot widgets and model lookup by name."""

    def __init__(self, dashboards=None, models=None):
        self.dashboards = dashboards or {}
        self.models = models or {}
        self.mutations = []
        self.next_id = 1

    def __call__(self, url, body, headers):
        q, v = body["query"], body["variables"]
        if q.lstrip().startswith("mutation"):
            self.mutations.append((q, v))
            i = v["i"]
            if "createDashboard" in q:
                did = f"D{self.next_id}"
                self.next_id += 1
                self.dashboards[did] = {"name": i["name"], "space": i["spaceId"], "widgets": []}
                return {"data": {"createDashboard": {"dashboard": {"id": did}}}}
            wid = f"W{self.next_id}"
            self.next_id += 1
            self.dashboards[i["dashboardId"]]["widgets"].append(
                {"id": wid, "modelId": i["modelId"], "groupedDimension": i["groupedDimension"],
                 "gridPosition": i["gridPosition"]})
            return {"data": {"createPivotTableWidget": {"pivotTableWidget": {"id": wid}}}}
        if "dashboards" in q:
            edges = [{"node": {"id": d, "name": x["name"]}} for d, x in self.dashboards.items()
                     if x["space"] == v["id"] and v["q"] in x["name"]]
            return {"data": {"node": {"dashboards": {"edges": edges}}}}
        if "pivotTableWidgets" in q:
            ws = self.dashboards[v["id"]]["widgets"]
            return {"data": {"node": {"pivotTableWidgets": {"edges": [{"node": w} for w in ws]}}}}
        if "models" in q:
            edges = [{"node": {"id": mid, "name": n}} for n, mid in self.models.items() if n == v["q"]]
            return {"data": {"node": {"models": {"edges": edges}}}}
        raise AssertionError(q)


def copy_config(tmp_path, name="assistant-synthetic"):
    src = ROOT / "examples"
    cfg = json.loads((src / f"{name}.json").read_text())
    for d in cfg["dimensions"]:
        for l in d["levels"]:
            if "mapping" in l:
                l["mapping"]["file"] = str(src / l["mapping"]["file"])
    for b in cfg["budgets"]:
        b["file"] = str(src / b["file"])
    for proj in cfg["projects"]:
        proj["id"], proj["widgets"] = "", {}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    return p


def test_plan_groupings_assistant(tmp_path):
    from finops.config import load_config
    c = load_config(str(copy_config(tmp_path)))
    assert plan_groupings(c) == ["attributes.session.id", "attributes.auth.status"]
    c2 = load_config(str(ROOT / "examples" / "acme.json"))
    assert plan_groupings(c2) == ["attributes.session.id", "attributes.user.id"]


def test_dry_run_lists_widgets_without_mutations(tmp_path):
    p = copy_config(tmp_path)
    before = p.read_text()
    fake = FakeArize(models={"synth-assistant": "M1", "synth-search": "M2", "synth-valuation": "M3"})
    plan = setup(str(p), Client(url="http://x", transport=fake), dry_run=True)
    assert fake.mutations == []
    assert p.read_text() == before
    assert len(plan) == 6
    assert {x["action"] for x in plan} == {"create"}


def test_creates_and_writes_ids(tmp_path):
    p = copy_config(tmp_path)
    fake = FakeArize(models={"synth-assistant": "M1", "synth-search": "M2", "synth-valuation": "M3"})
    setup(str(p), Client(url="http://x", transport=fake))
    cfg = json.loads(p.read_text())
    prod = cfg["projects"][0]
    assert prod["id"] == "M1"
    assert set(prod["widgets"]) == {"attributes.session.id", "attributes.auth.status"}
    spaces = sorted((d["space"], d["name"]) for d in fake.dashboards.values())
    assert spaces == [("U3BhY2U6MTAwMTpkZW1v", "FinOps engine — do not edit (assistant-synthetic)"),
                      ("U3BhY2U6MTAwMjpkZW1v", "FinOps engine — do not edit (assistant-synthetic)")]


def test_idempotent_reuse(tmp_path):
    p = copy_config(tmp_path)
    fake = FakeArize(models={"synth-assistant": "M1", "synth-search": "M2", "synth-valuation": "M3"})
    client = Client(url="http://x", transport=fake)
    setup(str(p), client)
    n = len(fake.mutations)
    plan = setup(str(p), client)
    assert len(fake.mutations) == n
    assert {x["action"] for x in plan} == {"reuse"}


def test_grid_positions_non_overlapping():
    cells = set()
    for i in range(9):
        r1, c1, r2, c2 = grid_position(i)
        assert 1 <= c1 < c2 <= 13
        for r in range(r1, r2):
            for c in range(c1, c2):
                assert (r, c) not in cells
                cells.add((r, c))
