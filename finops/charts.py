import json
from pathlib import Path
from typing import Dict, List

from .config import Config, load_config
from .source import Client
from .widgets import _find_dashboard, _resolve_model

COST = "attributes.llm.cost.total"
MODEL = "attributes.llm.model_name"
APP_URL = "https://app.arize.com"
ROWS, COLS = 8, 6

Q_TITLES = """query($id:ID!){node(id:$id){... on Dashboard{lineChartWidgets(first:200){edges{node{title}}}
barChartWidgets(first:200){edges{node{title}}}}}}"""
Q_ORG = """query($id:ID!){node(id:$id){... on Space{organization{id}}}}"""
M_DASHBOARD = """mutation($i:CreateDashboardMutationInput!){createDashboard(input:$i){dashboard{id}}}"""
M_LINE = """mutation($i:CreateLineChartWidgetMutationInput!){createLineChartWidget(input:$i){lineChartWidget{id}}}"""
M_BAR = """mutation($i:CreateBarChartWidgetMutationInput!){createBarChartWidget(input:$i){barChartWidget{id}}}"""


def _dim(name: str, data_type: str) -> dict:
    return {"id": f"dimension__{name}", "name": name, "dataType": data_type, "category": "spanProperty"}


def _plot(title: str, model_id: str, position: int) -> dict:
    return {"title": title, "position": position, "modelId": model_id, "modelVersionIds": [],
            "modelEnvironmentName": "tracing", "dimensionCategory": "spanProperty", "filters": []}


def _cost_plot(title: str, model_id: str, position: int = 0, split: str = "") -> dict:
    p = {**_plot(title, model_id, position), "metric": "sum", "dimension": _dim(COST, "DOUBLE")}
    if split:
        p.update(splitByEnabled=True, splitByDimension=split, splitByDimensionCategory="spanProperty",
                 splitByAllValuesLiveUpdateEnabled=True)
    return p


def _attributes(config: Config) -> List[str]:
    keys = [d.attribute for d in config.dimensions if d.attribute] + list(config.segments)
    return list(dict.fromkeys(keys))


def chart_plan(config: Config) -> Dict[str, List[dict]]:
    """space_id -> widget specs. Model ids may be blank until resolved."""
    plan: Dict[str, List[dict]] = {}
    for p in config.projects:
        plan.setdefault(p.space_id, [])
    for space_id, widgets in plan.items():
        projects = [p for p in config.projects if p.space_id == space_id]
        widgets.append({"kind": "line", "title": "Daily spend by project",
                        "plots": [_cost_plot(p.name, p.id, i) for i, p in enumerate(projects)]})
        for p in projects:
            widgets.append({"kind": "line", "title": f"{p.name} · daily spend by model",
                            "plots": [_cost_plot(p.name, p.id, 0, MODEL)]})
            for a in _attributes(config):
                widgets.append({"kind": "line", "title": f"{p.name} · daily spend by {a}",
                                "plots": [_cost_plot(p.name, p.id, 0, f"attributes.{a}")]})
            for a in _attributes(config):
                bar = {**_plot(p.name, p.id, 0), "aggregation": "count", "dimension": _dim(f"attributes.{a}", "STRING")}
                widgets.append({"kind": "bar", "title": f"{p.name} · LLM spans by {a}", "plots": [bar]})
    return plan


def _grid(i: int) -> List[int]:
    row = 1 + (i // 2) * ROWS
    col = 1 + (i % 2) * COLS
    return [row, col, row + ROWS, col + COLS]


def _dashboard_name(config: Config) -> str:
    return f"FinOps charts ({config.name})"


def setup_charts(config_path: str, client: Client, dry_run: bool = False) -> List[dict]:
    config = load_config(config_path)
    raw = json.loads(Path(config_path).read_text())
    for idx, p in enumerate(config.projects):
        if not p.id:
            p.id = _resolve_model(client, p.space_id, p.name)
            raw["projects"][idx]["id"] = p.id
    space_names = {p.space_id: p.space_name for p in config.projects}
    charts = dict(raw.get("charts") or {})
    out: List[dict] = []

    for space_id, widgets in chart_plan(config).items():
        name = _dashboard_name(config)
        did = _find_dashboard(client, space_id, name)
        titles = set()
        if did:
            node = client.query(Q_TITLES, {"id": did})
            titles = {e["node"]["title"] for k in ("lineChartWidgets", "barChartWidgets") for e in node[k]["edges"]}
        elif not dry_run:
            did = client.execute(M_DASHBOARD, {"i": {"name": name, "spaceId": space_id}})["createDashboard"]["dashboard"]["id"]
        used = len(titles)
        for w in widgets:
            if w["title"] in titles:
                out.append({"space": space_names[space_id], "title": w["title"], "action": "reuse"})
                continue
            out.append({"space": space_names[space_id], "title": w["title"], "action": "create"})
            if dry_run:
                continue
            i = {"title": w["title"], "dashboardId": did, "gridPosition": _grid(used), "plots": w["plots"],
                 "showLegend": True}
            if w["kind"] == "line":
                client.execute(M_LINE, {"i": {**i, "widgetType": "lineChartWidget",
                                              "timeSeriesMetricType": "modelDataMetric",
                                              "timeSeriesDataGranularity": "day"}})
            else:
                client.execute(M_BAR, {"i": {**i, "isStacked": False, "topN": 20, "sortOrder": "vol_desc"}})
            used += 1
        if did and not dry_run:
            org = client.query(Q_ORG, {"id": space_id})["organization"]["id"]
            charts[space_names[space_id]] = f"{APP_URL}/organizations/{org}/spaces/{space_id}/dashboards/{did}"

    if not dry_run:
        raw["charts"] = charts
        Path(config_path).write_text(json.dumps(raw, indent=2) + "\n")
    return out
