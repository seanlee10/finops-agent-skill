import json
from pathlib import Path

from finops.config import Dimension, Level, Project, load_config
from finops.rollup import UNMAPPED, Leaf, reconciles, rollup, unmapped

ROOT = Path(__file__).resolve().parent.parent


def test_acme_user_team_cc_rollup():
    c = load_config(str(ROOT / "examples" / "acme.json"))
    users = json.loads((ROOT / "tests" / "fixtures" / "v1_users.json").read_text())
    leaves = [Leaf("S", c.projects[0].id, {"user.id": u}, v["spend"], v["sessions"]) for u, v in users.items()]
    tree = rollup(leaves, c.dimensions[0], c.projects)
    assert round(tree["cost_center"]["CC-100"], 2) == 333.61
    assert round(tree["cost_center"]["CC-200"], 2) == 53.28
    assert round(tree["team"]["Platform"], 2) == 302.64
    assert reconciles(tree, sum(v["spend"] for v in users.values()))
    assert unmapped(tree) == {}


def _rea_shape():
    projects = [Project("p1", "ra-prod", "s1", "group-a"), Project("p2", "ra-search", "s1", "group-a"),
                Project("p3", "listings", "s2", "group-b")]
    dim = Dimension("by_space", [
        Level("space", "space"),
        Level("group", "map", {"group-a": "Consumer", "group-b": "Agents"}),
        Level("cost_center", "map", {"Consumer": "CC-300", "Agents": "CC-400"}),
    ])
    leaves = [Leaf("s1", "p1", {}, 10.0, 3), Leaf("s1", "p2", {}, 5.0, 2), Leaf("s2", "p3", {}, 2.5, 1)]
    return projects, dim, leaves


def test_space_group_cc_rollup():
    projects, dim, leaves = _rea_shape()
    tree = rollup(leaves, dim, projects)
    assert tree["space"] == {"group-a": 15.0, "group-b": 2.5}
    assert tree["group"] == {"Consumer": 15.0, "Agents": 2.5}
    assert tree["cost_center"] == {"CC-300": 15.0, "CC-400": 2.5}
    assert reconciles(tree, 17.5)


def test_unmapped_values_reconcile():
    projects, dim, leaves = _rea_shape()
    projects.append(Project("p4", "new-thing", "s3", "group-c"))
    leaves.append(Leaf("s3", "p4", {}, 1.25, 1))
    tree = rollup(leaves, dim, projects)
    assert tree["group"][UNMAPPED] == 1.25
    assert tree["cost_center"][UNMAPPED] == 1.25
    assert unmapped(tree) == {"group": 1.25, "cost_center": 1.25}
    assert reconciles(tree, 18.75)


def test_missing_attribute_is_unmapped():
    dim = Dimension("org", [Level("user", "attribute:user.id"), Level("team", "map", {"u1": "T"})])
    tree = rollup([Leaf("s", "p", {}, 2.0, 1), Leaf("s", "p", {"user.id": "u1"}, 3.0, 1)], dim, [])
    assert tree["user"] == {UNMAPPED: 2.0, "u1": 3.0}
    assert tree["team"] == {UNMAPPED: 2.0, "T": 3.0}


def test_reconcile_detects_mismatch():
    projects, dim, leaves = _rea_shape()
    tree = rollup(leaves, dim, projects)
    assert not reconciles(tree, 17.6)
    tree["group"]["Consumer"] -= 0.02
    assert not reconciles(tree, 17.5)
