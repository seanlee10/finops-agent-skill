from typing import Dict, List, Optional


def _div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def unit_economics(session_costs: Dict[str, float], outcomes: Dict[str, str], completed_labels: List[str],
                   segments: Optional[Dict[str, Dict[str, float]]] = None) -> dict:
    """Spend and session counts are exact. Ratios use evaluated sessions only."""
    total = sum(session_costs.values())
    n = len(session_costs)
    evaluated = {s: outcomes[s] for s in session_costs if s in outcomes}
    done = [s for s, label in evaluated.items() if label in completed_labels]
    eval_spend = sum(session_costs[s] for s in evaluated)
    failed_spend = sum(session_costs[s] for s, label in evaluated.items() if label not in completed_labels)
    coverage = _div(len(evaluated) * 100.0, n)
    out = {
        "sessions": n,
        "total_spend": total,
        "cost_per_session": _div(total, n),
        "evaluated": len(evaluated),
        "coverage_pct": coverage,
        "completed": len(done),
        "cost_per_completed": _div(eval_spend, len(done)),
        "failed_spend": failed_spend,
        "failed_spend_pct": _div(failed_spend * 100.0, eval_spend),
        "is_estimate": coverage is not None and coverage < 100.0,
        "by_segment": {},
    }
    for value, costs in (segments or {}).items():
        out["by_segment"][value] = unit_economics(costs, outcomes, completed_labels)
        del out["by_segment"][value]["by_segment"]
    return out
