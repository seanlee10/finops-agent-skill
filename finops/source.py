"""Exact cost aggregates from Arize programmatic GraphQL.

Inside a managed-agent sandbox the endpoint is the `graphql.sandboxcontroller`
virtual host and the proxy injects `x-api-key`. Reading pivot widgets needs a
credential with dashboard read; the per-run Managed Agent key does not have it,
so the agent config links an `arize` integration with a read-only key.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

SANDBOX_URL = "https://graphql.sandboxcontroller/graphql"
# The pivot resolver caps its groupBy at 1,000 rows (sorted by the first column) and says nothing.
PIVOT_ROW_LIMIT = 1000
MIN_SLICE = timedelta(minutes=1)
PUBLIC_URL = "https://app.arize.com/graphql"

COLUMN_KEYS = {
    ("attributes.llm.cost.total", "sum"): "cost",
    ("attributes.llm.token_count.total", "sum"): "tokens",
    ("attributes.session.id", "cardinality"): "sessions",
}

Q_COST_BY_MODEL = """query($id:ID!,$s:DateTime!,$e:DateTime!){node(id:$id){... on Model{
costByModel(startDate:$s,endDate:$e){llmModelName cost}}}}"""

Q_PIVOT = """query($id:ID!,$s:DateTime!,$e:DateTime!,$f:[DashboardWidgetFilterItem!]!){node(id:$id){
... on PivotTableWidget{pivotData(startDate:$s,endDate:$e,filters:$f){
columns{id dimension aggregation} rows{groupValue cells{columnId value}}}}}}"""

Q_ISSUES = """query($id:ID!){node(id:$id){... on Space{agentIssues(producer:"cost",limit:200){
title severity status entity estimatedSavingsUsd projectId isCleared}}}}"""


Q_AUTOMATIONS = """query($id:ID!){node(id:$id){... on Space{automations{producer modelId disabledAtUnix}}}}"""


class SourceError(Exception):
    pass


def default_url() -> str:
    if os.environ.get("FINOPS_GRAPHQL_URL"):
        return os.environ["FINOPS_GRAPHQL_URL"]
    if os.environ.get("SANDBOXCONTROLLER_URL"):
        return SANDBOX_URL
    return PUBLIC_URL


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"datetime must be timezone-aware: {dt!r}")
    return dt.isoformat()


def _http(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise SourceError(f"cannot reach {url}: {e.reason}") from e


def attr_filter(model_id: str, key: str, value: str, filter_type: str = "spanProperty",
                dimension: Optional[str] = None) -> dict:
    name = dimension or f"attributes.{key}"
    return {"modelId": model_id, "filterItems": [{
        "filterType": filter_type,
        "operator": "equals",
        "dimension": {"id": name, "name": name, "dataType": "STRING", "category": filter_type},
        "categoricalValues": [value],
    }]}


class Client:
    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None,
                 transport: Optional[Callable[[str, dict, dict], dict]] = None):
        self.url = url or default_url()
        self.api_key = api_key if api_key is not None else os.environ.get("ARIZE_API_KEY")
        self.transport = transport or _http

    def execute(self, q: str, variables: dict) -> dict:
        headers = {"content-type": "application/json"}
        if self.api_key and self.url != SANDBOX_URL:
            headers["x-api-key"] = self.api_key
        resp = self.transport(self.url, {"query": q, "variables": variables}, headers)
        if resp.get("errors"):
            raise SourceError("; ".join(e.get("message", "?") for e in resp["errors"]))
        return resp.get("data") or {}

    def query(self, q: str, variables: dict) -> dict:
        node = self.execute(q, variables).get("node")
        if node is None:
            raise SourceError(f"node {variables.get('id')} not found or not readable")
        return node

    def project_cost_by_model(self, project_id: str, start: datetime, end: datetime) -> Dict[str, float]:
        node = self.query(Q_COST_BY_MODEL, {"id": project_id, "s": _iso(start), "e": _iso(end)})
        out: Dict[str, float] = {}
        for r in node.get("costByModel") or []:
            name = r.get("llmModelName") or "(unknown)"
            out[name] = out.get(name, 0.0) + float(r.get("cost") or 0)
        return out

    def pivot(self, widget_id: str, start: datetime, end: datetime,
              filters: Optional[List[dict]] = None) -> Dict[str, Dict[str, float]]:
        """All groups of a pivot widget. A response at the server's row limit is treated as truncated
        and the window is split in halves until every slice is under it; costs and tokens add across
        slices, while the per-group session cardinality becomes an upper bound."""
        rows = self._pivot_once(widget_id, start, end, filters)
        if len(rows) < PIVOT_ROW_LIMIT:
            return rows
        if end - start < MIN_SLICE * 2:
            raise SourceError(f"widget {widget_id}: more groups than the {PIVOT_ROW_LIMIT:,}-row limit "
                              f"in a {end - start} slice; figures would be incomplete")
        mid = start + (end - start) / 2
        out = self.pivot(widget_id, start, mid, filters)
        for key, vals in self.pivot(widget_id, mid, end, filters).items():
            acc = out.setdefault(key, {"cost": 0.0, "tokens": 0.0, "sessions": 0.0})
            for k, v in vals.items():
                acc[k] += v
        return out

    def _pivot_once(self, widget_id: str, start: datetime, end: datetime,
                    filters: Optional[List[dict]]) -> Dict[str, Dict[str, float]]:
        node = self.query(Q_PIVOT, {"id": widget_id, "s": _iso(start), "e": _iso(end), "f": filters or []})
        data = node["pivotData"]
        cols = {c["id"]: COLUMN_KEYS.get((c["dimension"], c["aggregation"])) for c in data["columns"]}
        out: Dict[str, Dict[str, float]] = {}
        for row in data["rows"]:
            vals = {"cost": 0.0, "tokens": 0.0, "sessions": 0.0}
            for cell in row["cells"]:
                key = cols.get(cell["columnId"])
                if key:
                    vals[key] = float(cell.get("value") or 0)
            g = row.get("groupValue")
            out["" if g is None else str(g)] = vals
        return out

    def cost_agent_issues(self, space_id: str, project_id: str) -> List[dict]:
        node = self.query(Q_ISSUES, {"id": space_id})
        return [
            {"title": i["title"], "severity": i.get("severity"), "status": i["status"],
             "entity": i.get("entity"), "estimated_savings_usd": i.get("estimatedSavingsUsd")}
            for i in node.get("agentIssues") or []
            if i.get("projectId") == project_id and i["status"] == "OPEN" and not i.get("isCleared")
        ]

    def cost_agent_enabled(self, space_id: str, project_id: str) -> bool:
        node = self.query(Q_AUTOMATIONS, {"id": space_id})
        return any(a.get("producer") == "cost" and a.get("modelId") == project_id and not a.get("disabledAtUnix")
                   for a in node.get("automations") or [])

    def session_outcomes(self, session_widget_id: str, model_id: str, eval_name: str, labels: List[str],
                         start: datetime, end: datetime, filter_type: str = "annotation") -> Dict[str, str]:
        """session.id -> label, one pivot query per label filtered on the session-level label."""
        dim = f"{filter_type}.{eval_name}.label" if filter_type != "spanProperty" else None
        out: Dict[str, str] = {}
        for label in labels:
            f = attr_filter(model_id, f"{eval_name}.label", label, filter_type=filter_type, dimension=dim)
            for sid in self.pivot(session_widget_id, start, end, [f]):
                if sid:
                    out.setdefault(sid, label)
        return out
