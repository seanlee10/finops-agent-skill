import json
from pathlib import Path

import pytest

from finops.config import ConfigError, load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_loads_acme_example():
    c = load_config(str(EXAMPLES / "acme.json"))
    org = c.dimensions[0]
    assert org.name == "org"
    assert [l.source for l in org.levels] == ["attribute:user.id", "map", "map"]
    assert [l.name for l in org.levels] == ["user", "team", "cost_center"]
    assert org.levels[1].mapping["p.nair"] == "Platform"
    assert org.levels[2].mapping["Platform"] == "CC-100"
    assert len([b for b in c.budgets if b.month == "2026-11"]) == 4
    assert {b.value: b.usd for b in c.budgets}["Platform"] == 906.0


def test_loads_assistant_example():
    c = load_config(str(EXAMPLES / "assistant-synthetic.json"))
    dims = {d.name: [l.name for l in d.levels] for d in c.dimensions}
    assert dims == {"by_space": ["space", "group", "cost_center"], "by_project": ["project", "team"]}
    assert c.segments == ["auth.status"]
    assert c.outcome.rule == "eval_label"
    assert c.outcome.completed_labels == ["completed"]
    assert c.outcome.labels == ["completed", "partial", "failed"]


def test_unknown_source_raises(tmp_path):
    cfg = json.loads((EXAMPLES / "acme.json").read_text())
    cfg["dimensions"][0]["levels"][0]["source"] = "foo"
    cfg["dimensions"][0]["levels"][1]["mapping"]["file"] = str(EXAMPLES / "acme/org_chart.csv")
    cfg["dimensions"][0]["levels"][2]["mapping"]["file"] = str(EXAMPLES / "acme/org_chart.csv")
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="foo"):
        load_config(str(p))


def test_map_source_on_first_level_raises(tmp_path):
    cfg = json.loads((EXAMPLES / "acme.json").read_text())
    cfg["dimensions"][0]["levels"] = cfg["dimensions"][0]["levels"][1:]
    for l in cfg["dimensions"][0]["levels"]:
        l["mapping"]["file"] = str(EXAMPLES / "acme/org_chart.csv")
    cfg["budgets"] = []
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="levels\\[0\\]"):
        load_config(str(p))
