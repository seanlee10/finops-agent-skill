import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from .budget import budget_rows, month_window
from .config import Config, Project
from .rollup import TOLERANCE, UNMAPPED, Leaf, reconciles, rollup, unmapped
from .source import SourceError, attr_filter
from .units import unit_economics

MAX_VALUE_LEN = 64
SESSION_KEY = "attributes.session.id"
MULTIPLE = "(multiple)"
NO_VALUE = "(no value)"
INVOICE_NOTE = "Costs are as recorded in Arize traces; provider or gateway invoices are not reconciled."
SECTION_HEADINGS = ["## Chargeback", "## Budgets", "## Unit economics", "## Cost Agent issues", "## Data quality"]


def _safe(value: str) -> bool:
    return len(value) <= MAX_VALUE_LEN


def _attr_leaves(p: Project, key: str, total: float, rows: Dict[str, dict], notes: List[str]) -> List[Leaf]:
    leaves, dropped = [], 0
    for value, v in rows.items():
        if _safe(value):
            leaves.append(Leaf(p.space_id, p.id, {key: value}, v["cost"], int(v["sessions"])))
        else:
            dropped += 1
            leaves.append(Leaf(p.space_id, p.id, {}, v["cost"], int(v["sessions"])))
    if dropped:
        notes.append(f"{p.name}: {dropped} value(s) of `{key}` longer than {MAX_VALUE_LEN} characters were "
                     f"counted as {UNMAPPED} and not shown.")
    residual = total - sum(l.cost for l in leaves)
    if residual > TOLERANCE:
        leaves.append(Leaf(p.space_id, p.id, {}, residual, 0))
        notes.append(f"{p.name}: ${residual:,.2f} of spend has no `{key}`; counted as {UNMAPPED}.")
    elif residual < -TOLERANCE:
        notes.append(f"{p.name}: `{key}` breakdown exceeds the project total by ${-residual:,.2f}.")
    return leaves


def build(config: Config, client, start: datetime, end: datetime, as_of: datetime) -> dict:
    notes: List[str] = []
    models: Dict[str, float] = {}
    project_totals: Dict[str, float] = {}
    excluded: List[str] = []
    readable: List[Project] = []
    for p in config.projects:
        try:
            by_model = client.project_cost_by_model(p.id, start, end)
        except SourceError as e:
            excluded.append(p.name)
            notes.append(f"{p.name}: excluded, its data could not be read ({e}).")
            continue
        readable.append(p)
        project_totals[p.id] = sum(by_model.values())
        for m, c in by_model.items():
            models[m] = models.get(m, 0.0) + c
    total = sum(project_totals.values())

    trees = {}
    for dim in config.dimensions:
        leaves: List[Leaf] = []
        key = dim.attribute
        for p in readable:
            t = project_totals[p.id]
            if key is None:
                leaves.append(Leaf(p.space_id, p.id, {}, t, 0))
                continue
            widget = p.widgets.get(f"attributes.{key}")
            if not widget:
                notes.append(f"{p.name}: no widget for `{key}`; ${t:,.2f} counted as {UNMAPPED}. "
                             f"Run scripts/setup_widgets.py.")
                leaves.append(Leaf(p.space_id, p.id, {}, t, 0))
                continue
            try:
                rows = client.pivot(widget, start, end)
            except SourceError as e:
                notes.append(f"{p.name}: `{key}` breakdown unavailable ({e}); ${t:,.2f} counted as {UNMAPPED}.")
                leaves.append(Leaf(p.space_id, p.id, {}, t, 0))
                continue
            if not rows and t > TOLERANCE:
                notes.append(f"{p.name}: `{key}` breakdown returned no rows although the project has "
                             f"${t:,.2f} of spend (the query may have failed); counted as {UNMAPPED}.")
                leaves.append(Leaf(p.space_id, p.id, {}, t, 0))
                continue
            leaves += _attr_leaves(p, key, t, rows, notes)
        trees[dim.name] = rollup(leaves, dim, config.projects)

    month_start, _ = month_window(as_of, config.tz_offset_hours)
    if start == month_start:
        budgets = budget_rows(trees, config.budgets, as_of, config.tz_offset_hours)
    else:
        budgets = []
        if config.budgets:
            notes.append("Budgets are shown only for month-to-date windows.")

    session_costs: Dict[Tuple[str, str], float] = {}
    outcomes: Dict[Tuple[str, str], str] = {}
    membership: Dict[str, Dict[Tuple[str, str], Set[str]]] = {s: {} for s in config.segments}
    o = config.outcome
    for p in readable:
        sw = p.widgets.get(SESSION_KEY)
        if not sw:
            notes.append(f"{p.name}: no session widget; excluded from unit economics.")
            continue
        try:
            sessions = client.pivot(sw, start, end)
        except SourceError as e:
            notes.append(f"{p.name}: excluded from unit economics ({e}).")
            continue
        if not sessions and project_totals[p.id] > TOLERANCE:
            notes.append(f"{p.name}: session breakdown returned no rows although the project has "
                         f"${project_totals[p.id]:,.2f} of spend (the query may have failed); "
                         f"excluded from unit economics.")
            continue
        mine = {(p.id, sid): v["cost"] for sid, v in sessions.items() if sid}
        session_costs.update(mine)
        if o.rule == "eval_label":
            try:
                got = client.session_outcomes(sw, p.id, o.eval_name, o.labels or o.completed_labels, start, end,
                                              filter_type=o.filter_type)
            except SourceError as e:
                notes.append(f"{p.name}: session outcomes unavailable ({e}).")
                got = {}
            outcomes.update({(p.id, sid): label for sid, label in got.items()})
        elif o.rule == "root_status":
            try:
                errs = client.pivot(sw, start, end, [attr_filter(p.id, "", "ERROR", dimension="status_code")])
            except SourceError as e:
                notes.append(f"{p.name}: error status unavailable ({e}); outcomes not computed.")
                errs = None
            if errs is not None:
                for k in mine:
                    outcomes[k] = "failed" if k[1] in errs else "completed"
        for seg in config.segments:
            ww = p.widgets.get(f"attributes.{seg}")
            if not ww:
                notes.append(f"{p.name}: no widget for segment `{seg}`.")
                continue
            try:
                values = client.pivot(ww, start, end)
                dropped = 0
                for value in values:
                    if not value:
                        continue
                    if not _safe(value):
                        dropped += 1
                        continue
                    for sid in client.pivot(sw, start, end, [attr_filter(p.id, seg, value)]):
                        if (p.id, sid) in mine:
                            membership[seg].setdefault((p.id, sid), set()).add(value)
            except SourceError as e:
                notes.append(f"{p.name}: segment `{seg}` unavailable ({e}).")
                continue
            if dropped:
                notes.append(f"{p.name}: {dropped} value(s) of segment `{seg}` longer than {MAX_VALUE_LEN} "
                             f"characters were dropped.")

    segment_costs: Dict[str, Dict[str, Dict[Tuple[str, str], float]]] = {}
    segment_spend: Dict[str, Dict[str, float]] = {}
    for seg in config.segments:
        by_value: Dict[str, Dict[Tuple[str, str], float]] = {}
        for k, values in membership[seg].items():
            value = next(iter(values)) if len(values) == 1 else MULTIPLE
            by_value.setdefault(value, {})[k] = session_costs[k]
        segment_costs[seg] = by_value
        spend = {v: sum(c.values()) for v, c in by_value.items()}
        rest = total - sum(spend.values())
        if rest > TOLERANCE:
            spend[NO_VALUE] = rest
        segment_spend[seg] = spend
        if MULTIPLE in by_value:
            notes.append(f"`{seg}`: {len(by_value[MULTIPLE])} session(s) carry more than one value and are "
                         f"shown as {MULTIPLE}.")

    completed = o.completed_labels if o.rule == "eval_label" else ["completed"]
    units = unit_economics(session_costs, outcomes, completed)
    units["by_segment"] = {seg: unit_economics_by(segment_costs[seg], outcomes, completed)
                           for seg in config.segments}
    units["rule"] = o.rule
    gap = total - units["total_spend"]
    if gap > TOLERANCE:
        notes.append(f"${gap:,.2f} of ${total:,.2f} spend is not attributable to a session and is not in "
                     f"unit economics.")

    cost_agent: Optional[Dict[str, List[dict]]] = None
    if config.cost_agent:
        cost_agent = {}
        for p in readable:
            try:
                issues = client.cost_agent_issues(p.space_id, p.id)
            except SourceError as e:
                notes.append(f"{p.name}: Cost Agent issues unavailable ({e}).")
                cost_agent[p.name] = {"state": "error", "issues": []}
                continue
            state = "issues" if issues else "none"
            if not issues:
                try:
                    if not client.cost_agent_enabled(p.space_id, p.id):
                        state = "not_set_up"
                except SourceError:
                    state = "unknown"
            cost_agent[p.name] = {"state": state, "issues": issues}

    ok = all(reconciles(t, total) for t in trees.values())
    return {
        "config": config.name,
        "window": {"start": _stamp(start, config), "end": _stamp(end, config), "as_of": _stamp(as_of, config)},
        "total": total,
        "projects": {p.name: project_totals[p.id] for p in readable},
        "excluded_projects": excluded,
        "models": models,
        "trees": trees,
        "budgets": budgets,
        "segments": segment_spend,
        "units": units,
        "cost_agent": cost_agent,
        "data_quality": {"notes": notes, "unmapped": {d: unmapped(t) for d, t in trees.items() if unmapped(t)}},
        "reconciles": ok,
    }


def unit_economics_by(costs_by_value: Dict[str, Dict[str, float]], outcomes, completed) -> Dict[str, dict]:
    out = {}
    for value, costs in costs_by_value.items():
        u = unit_economics(costs, outcomes, completed)
        u.pop("by_segment")
        out[value] = u
    return out


def _usd(v: Optional[float]) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.0f}%"


def _ratio_label(u: dict) -> str:
    if u["evaluated"] == 0:
        return " (no sessions evaluated)"
    if u["is_estimate"]:
        return f" — estimate ({u['evaluated']} of {u['sessions']} sessions evaluated)"
    return ""


def _stamp(dt: datetime, config: Config) -> str:
    tz = timezone(timedelta(hours=config.tz_offset_hours))
    return dt.astimezone(tz).replace(microsecond=0).isoformat()


COST_AGENT_EMPTY = {
    "none": "No open Cost Agent issues.",
    "not_set_up": "Cost Agent is not set up on this project.",
    "unknown": "No open Cost Agent issues found; could not check whether Cost Agent runs on this project.",
    "error": "Could not read Cost Agent issues (see Data quality).",
}


def render_md(result: dict, config: Config) -> str:
    L: List[str] = []
    if result.get("excluded_projects"):
        L += [f"**Incomplete**: {', '.join(result['excluded_projects'])} could not be read and "
              f"{'is' if len(result['excluded_projects']) == 1 else 'are'} excluded from every figure below.", ""]
    if not result["reconciles"]:
        L += ["**Totals do not reconcile**: at least one hierarchy level does not sum to the grand total. "
              "Do not use these figures for chargeback until the data-quality notes are resolved.", ""]
    w = result["window"]
    L += [f"# FinOps report: {config.name}", "",
          f"Window {w['start']} → {w['end']}. **Total spend {_usd(result['total'])}**.", ""]
    if config.charts:
        L += ["Charts: " + " · ".join(f"[{name}]({url})" for name, url in config.charts.items()), ""]
    flagged = [b["value"] for b in result["budgets"] if b["status"] != "OK"]
    if flagged:
        L += [f"Needs attention: {', '.join(flagged)}.", ""]

    L += ["## Chargeback", ""]
    for dim in config.dimensions:
        tree = result["trees"][dim.name]
        for level in reversed(dim.levels):
            vals = tree[level.name]
            L += [f"**{dim.name} · {level.name}**", "", "| Value | Spend | Share |", "|---|---:|---:|"]
            for value, c in sorted(vals.items(), key=lambda kv: -kv[1]):
                share = c / result["total"] * 100 if result["total"] else None
                L.append(f"| {value} | {_usd(c)} | {_pct(share)} |")
            L.append("")
    if result["models"]:
        L += ["**By model**", "", "| Model | Spend |", "|---|---:|"]
        L += [f"| {m} | {_usd(c)} |" for m, c in sorted(result["models"].items(), key=lambda kv: -kv[1])]
        L.append("")
    for seg, vals in result["segments"].items():
        if vals:
            L += [f"**By {seg}**", "", "| Value | Spend |", "|---|---:|"]
            L += [f"| {v} | {_usd(c)} |" for v, c in sorted(vals.items(), key=lambda kv: -kv[1])]
            L.append("")

    L += ["## Budgets", ""]
    if result["budgets"]:
        L += ["| Level | Value | Budget | Spent | Burn | Projected month-end | Status |",
              "|---|---|---:|---:|---:|---:|---|"]
        for b in result["budgets"]:
            L.append(f"| {b['dimension']} · {b['level']} | {b['value']} | {_usd(b['budget'])} | {_usd(b['spent'])} | "
                     f"{_pct(b['burn_pct'])} | {_usd(b['projected'])} ({_pct(b['projected_pct'])}) | {b['status']} |")
    else:
        L.append("No budgets apply to this window.")
    L.append("")

    u = result["units"]
    L += ["## Unit economics", "",
          f"Outcome rule: `{u['rule']}`{_ratio_label(u) if u['rule'] != 'none' else ''}.", "",
          "| | Sessions | Spend | Cost per session | Completed | Cost per completed session | Spend on non-completed |",
          "|---|---:|---:|---:|---:|---:|---:|"]

    def row(name, x):
        failed = f"{_usd(x['failed_spend'])} ({_pct(x['failed_spend_pct'])})" if x["evaluated"] else "n/a"
        return (f"| {name} | {x['sessions']} | {_usd(x['total_spend'])} | {_usd(x['cost_per_session'])} | "
                f"{x['completed'] if x['evaluated'] else 'n/a'} | {_usd(x['cost_per_completed'])} | {failed} |")
    L.append(row("All", u))
    for seg, by in u["by_segment"].items():
        for value, x in sorted(by.items()):
            L.append(row(f"{seg} = {value}", x))
    L.append("")

    L += ["## Cost Agent issues", ""]
    if result["cost_agent"] is None:
        L.append("Cost Agent cross-reference is turned off in this configuration.")
    else:
        for name, entry in result["cost_agent"].items():
            issues = entry["issues"]
            if not issues:
                L.append(f"- **{name}**: {COST_AGENT_EMPTY[entry['state']]}")
                continue
            L.append(f"- **{name}**:")
            for i in issues:
                sav = f", est. savings {_usd(i['estimated_savings_usd'])}" if i["estimated_savings_usd"] else ""
                L.append(f"  - [{i['severity'] or 'n/a'}] {i['title']}{sav}")
    L.append("")

    dq = result["data_quality"]
    L += ["## Data quality", ""]
    for d, levels in dq["unmapped"].items():
        for level, c in levels.items():
            L.append(f"- {d} · {level}: {_usd(c)} {UNMAPPED} (values missing from the mapping).")
    L += [f"- {n}" for n in dq["notes"]]
    L.append(f"- Totals reconcile across every level: {'yes' if result['reconciles'] else 'NO'}.")
    L.append(f"- {INVOICE_NOTE}")
    return "\n".join(L) + "\n"


def _slug(*parts: str) -> str:
    return "-".join("".join(c if c.isalnum() else "-" for c in p.lower()).strip("-") for p in parts)


def _budget_card(b: dict) -> str:
    over = b["status"].startswith("OVER")
    body = (f"## Overview\n{b['value']} ({b['dimension']} · {b['level']}) has spent {_usd(b['spent'])} of its "
            f"{_usd(b['budget'])} budget ({_pct(b['burn_pct'])}).\n\n"
            f"## Evidence\n- Projected month-end: {_usd(b['projected'])} ({_pct(b['projected_pct'])} of budget)\n"
            f"- Status: {b['status']}\n\n"
            f"## Proposed fix\nReview {b['value']}'s spend drivers in the chargeback tables with its owner "
            f"before month end.")
    card = {"issue_key": f"budget-{_slug(b['dimension'], b['level'], b['value'])}",
            "title": f"{b['value']} {'projected over budget' if over else 'at risk of exceeding budget'}",
            "body_md": body.replace("```", "'''"), "severity": "high" if over else "medium",
            "entity": b["value"], "tags": ["budget", b["level"]]}
    return "```signal_issue\n" + json.dumps(card, indent=2) + "\n```"


def render_investigation(result: dict, config: Config) -> str:
    """The run's investigation file: the automation addendum's sections, then the full report."""
    w, u = result["window"], result["units"]
    flagged = [b for b in result["budgets"] if b["status"] not in ("OK", "NO BUDGET")]
    attention = ", ".join(f"{b['value']} ({b['status']}: {_pct(b['projected_pct'])} of budget at month end)"
                          for b in flagged)
    L = ["### Summary", "",
         f"- **Spend:** {_usd(result['total'])} from {w['start']} to {w['end']}.",
         f"- **Needs attention:** {attention or 'none'}.",
         f"- **Cost per completed session:** {_usd(u['cost_per_completed'])}{_ratio_label(u)}; "
         f"cost per session {_usd(u['cost_per_session'])} across {u['sessions']} sessions.", "",
         "### What was investigated", "",
         f"- Config `{config.name}`: {len(result['projects'])} project(s) "
         f"({', '.join(result['projects']) or 'none'}).",
         "- Cost by model per project and pivot aggregates by session and attribute, from Arize traces.",
         "- Budgets, unit economics and open Cost Agent issues for the same window.", "",
         "### Diagnosis", ""]
    if flagged:
        for b in flagged:
            L += [_budget_card(b), ""]
    else:
        L += ["No issue found: no budget is over or at risk.", ""]
    if result.get("excluded_projects") or not result["reconciles"]:
        L += ["Figures are incomplete or do not reconcile; see the report's Data quality section.", ""]
    L += ["### Trace links", ""]
    L += [f"[FinOps charts ({name})]({url})" for name, url in config.charts.items()] or ["None"]
    L += ["", "### Next steps", ""]
    L += [f"- Review {b['value']}'s spend with its owner before month end." for b in flagged] or ["None"]
    report = "\n".join("##" + l if l.startswith("#") else l for l in render_md(result, config).splitlines())
    L += ["", "### Report", "", report]
    return "\n".join(L)
