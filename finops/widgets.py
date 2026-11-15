import json
from pathlib import Path
from typing import Dict, List, Tuple

from .config import Config, load_config
from .report import SESSION_KEY
from .source import Client, SourceError

WIDGET_ROWS, WIDGET_COLS = 8, 6

COLUMNS = [
    {"dimension": "attributes.llm.cost.total", "aggregation": "sum", "position": 0, "title": "Spend (USD)",
     "dimensionCategory": "spanProperty"},
    {"dimension": "attributes.llm.token_count.total", "aggregation": "sum", "position": 1, "title": "Tokens",
     "dimensionCategory": "spanProperty"},
    {"dimension": "attributes.session.id", "aggregation": "cardinality", "position": 2, "title": "Sessions",
     "dimensionCategory": "spanProperty"},
]

Q_FIND_DASHBOARD = """query($id:ID!,$q:String!){node(id:$id){... on Space{dashboards(first:50,search:$q){
edges{node{id name}}}}}}"""
Q_WIDGETS = """query($id:ID!){node(id:$id){... on Dashboard{pivotTableWidgets(first:200){
edges{node{id modelId groupedDimension gridPosition}}}}}}"""
Q_MODEL = """query($id:ID!,$q:String!){node(id:$id){... on Space{models(first:50,search:$q,useExactSearchMatch:true){
edges{node{id name}}}}}}"""
M_DASHBOARD = """mutation($i:CreateDashboardMutationInput!){createDashboard(input:$i){dashboard{id}}}"""
M_PIVOT = """mutation($i:CreatePivotTableWidgetMutationInput!){createPivotTableWidget(input:$i){
pivotTableWidget{id}}}"""


def plan_groupings(config: Config) -> List[str]:
    keys = [SESSION_KEY]
    for d in config.dimensions:
        if d.attribute:
            keys.append(f"attributes.{d.attribute}")
    keys += [f"attributes.{s}" for s in config.segments]
    return list(dict.fromkeys(keys))


def grid_position(i: int) -> List[int]:
    row = 1 + (i // 2) * WIDGET_ROWS
    col = 1 + (i % 2) * WIDGET_COLS
    return [row, col, row + WIDGET_ROWS, col + WIDGET_COLS]


def _dashboard_name(config: Config) -> str:
    return f"FinOps engine — do not edit ({config.name})"


def _find_dashboard(client: Client, space_id: str, name: str):
    edges = client.query(Q_FIND_DASHBOARD, {"id": space_id, "q": name})["dashboards"]["edges"]
    return next((e["node"]["id"] for e in edges if e["node"]["name"] == name), None)


def _resolve_model(client: Client, space_id: str, name: str) -> str:
    edges = client.query(Q_MODEL, {"id": space_id, "q": name})["models"]["edges"]
    ids = [e["node"]["id"] for e in edges if e["node"]["name"] == name]
    if len(ids) != 1:
        raise SourceError(f"project '{name}' resolves to {len(ids)} projects in space {space_id}")
    return ids[0]


def setup(config_path: str, client: Client, dry_run: bool = False) -> List[dict]:
    config = load_config(config_path)
    raw = json.loads(Path(config_path).read_text())
    groupings = plan_groupings(config)
    plan: List[dict] = []
    dashboards: Dict[str, str] = {}
    existing: Dict[str, Dict[Tuple[str, str], str]] = {}
    used: Dict[str, int] = {}

    for idx, p in enumerate(config.projects):
        model_id = p.id or _resolve_model(client, p.space_id, p.name)
        raw["projects"][idx]["id"] = model_id
        if p.space_id not in dashboards:
            did = _find_dashboard(client, p.space_id, _dashboard_name(config))
            if did is None and not dry_run:
                did = client.execute(M_DASHBOARD, {"i": {"name": _dashboard_name(config), "spaceId": p.space_id}}
                                     )["createDashboard"]["dashboard"]["id"]
            dashboards[p.space_id] = did or ""
            ws = client.query(Q_WIDGETS, {"id": did})["pivotTableWidgets"]["edges"] if did else []
            existing[p.space_id] = {(w["node"]["modelId"], w["node"]["groupedDimension"]): w["node"]["id"] for w in ws}
            used[p.space_id] = len(ws)
        did = dashboards[p.space_id]
        widgets = raw["projects"][idx].setdefault("widgets", {})
        for g in groupings:
            wid = existing[p.space_id].get((model_id, g))
            if wid:
                plan.append({"project": p.name, "grouping": g, "action": "reuse", "widget": wid})
                widgets[g] = wid
                continue
            plan.append({"project": p.name, "grouping": g, "action": "create"})
            if dry_run:
                continue
            i = {"dashboardId": did, "title": f"{p.name} by {g.replace('attributes.', '')}",
                 "gridPosition": grid_position(used[p.space_id]), "creationStatus": "published",
                 "modelId": model_id, "modelEnvironmentName": "tracing", "modelVersionEnvironmentMetadataIds": [],
                 "modelVersionIds": [], "groupedDimension": g, "groupedDimensionTitle": g.replace("attributes.", ""),
                 "groupedDimensionCategory": "spanProperty", "columns": COLUMNS, "filters": []}
            wid = client.execute(M_PIVOT, {"i": i})["createPivotTableWidget"]["pivotTableWidget"]["id"]
            used[p.space_id] += 1
            existing[p.space_id][(model_id, g)] = wid
            widgets[g] = wid
            plan[-1]["widget"] = wid

    if not dry_run:
        Path(config_path).write_text(json.dumps(raw, indent=2) + "\n")
    return plan
