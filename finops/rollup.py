from dataclasses import dataclass, field
from typing import Dict, List

from .config import Dimension, Project

UNMAPPED = "Unmapped"
TOLERANCE = 0.01

Tree = Dict[str, Dict[str, float]]


@dataclass
class Leaf:
    space_id: str
    project_id: str
    attr: Dict[str, str] = field(default_factory=dict)
    cost: float = 0.0
    sessions: int = 0


def _first_value(leaf: Leaf, source: str, projects: Dict[str, Project]) -> str:
    if source == "project":
        p = projects.get(leaf.project_id)
        return p.name if p else UNMAPPED
    if source == "space":
        p = projects.get(leaf.project_id)
        return p.space_name if p else UNMAPPED
    v = leaf.attr.get(source.split(":", 1)[1])
    return v if v not in (None, "") else UNMAPPED


def rollup(leaves: List[Leaf], dim: Dimension, projects: List[Project]) -> Tree:
    by_id = {p.id: p for p in projects}
    tree: Tree = {l.name: {} for l in dim.levels}
    for leaf in leaves:
        value = _first_value(leaf, dim.levels[0].source, by_id)
        for i, level in enumerate(dim.levels):
            if i > 0:
                value = UNMAPPED if value == UNMAPPED else (level.mapping or {}).get(value, UNMAPPED)
            bucket = tree[level.name]
            bucket[value] = bucket.get(value, 0.0) + leaf.cost
    return tree


def unmapped(tree: Tree) -> Dict[str, float]:
    return {lvl: vals[UNMAPPED] for lvl, vals in tree.items() if vals.get(UNMAPPED)}


def reconciles(tree: Tree, grand_total: float) -> bool:
    return all(abs(sum(vals.values()) - grand_total) <= TOLERANCE for vals in tree.values())
