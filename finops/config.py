import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

UNITS = ("session", "trace")
OUTCOME_RULES = ("eval_label", "root_status", "none")
FIRST_LEVEL_SOURCES = ("space", "project")


class ConfigError(ValueError):
    pass


@dataclass
class Project:
    id: str
    name: str
    space_id: str
    space_name: str
    widgets: Dict[str, str] = field(default_factory=dict)


@dataclass
class Level:
    name: str
    source: str
    mapping: Optional[Dict[str, str]] = None


@dataclass
class Dimension:
    name: str
    levels: List[Level]

    @property
    def attribute(self) -> Optional[str]:
        src = self.levels[0].source
        return src.split(":", 1)[1] if src.startswith("attribute:") else None


@dataclass
class Budget:
    dimension: str
    level: str
    value: str
    month: str
    usd: float


@dataclass
class Outcome:
    rule: str = "none"
    eval_name: Optional[str] = None
    completed_labels: List[str] = field(default_factory=list)
    sample_ok: bool = True
    labels: List[str] = field(default_factory=list)
    filter_type: str = "annotation"


@dataclass
class Config:
    name: str
    tz_offset_hours: float
    currency: str
    projects: List[Project]
    dimensions: List[Dimension]
    budgets: List[Budget]
    unit: str
    outcome: Outcome
    segments: List[str]
    cost_agent: bool
    path: Optional[str] = None
    charts: Dict[str, str] = field(default_factory=dict)


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"{where}: missing key '{key}'")
    return d[key]


def _read_csv(base: Path, file: str, where: str) -> List[dict]:
    p = Path(file) if Path(file).is_absolute() else base / file
    if not p.exists():
        raise ConfigError(f"{where}: file not found: {p}")
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def _load_mapping(base: Path, spec: dict, where: str) -> Dict[str, str]:
    rows = _read_csv(base, _req(spec, "file", where), where)
    src, dst = _req(spec, "from", where), _req(spec, "to", where)
    out: Dict[str, str] = {}
    for r in rows:
        if src not in r or dst not in r:
            raise ConfigError(f"{where}: columns '{src}'/'{dst}' not in {spec['file']}")
        k, v = r[src].strip(), r[dst].strip()
        if k in out and out[k] != v:
            raise ConfigError(f"{where}: '{k}' maps to both '{out[k]}' and '{v}'")
        out[k] = v
    return out


def _level(base: Path, d: dict, idx: int, where: str) -> Level:
    w = f"{where}.levels[{idx}]"
    name, source = _req(d, "name", w), _req(d, "source", w)
    if idx == 0:
        if source not in FIRST_LEVEL_SOURCES and not source.startswith("attribute:"):
            raise ConfigError(f"{w}: source '{source}' must be space, project or attribute:<key>")
        if source.startswith("attribute:") and not source.split(":", 1)[1]:
            raise ConfigError(f"{w}: source '{source}' has an empty attribute key")
        return Level(name, source, None)
    if source != "map":
        raise ConfigError(f"{w}: source '{source}' must be 'map' above the first level")
    return Level(name, source, _load_mapping(base, _req(d, "mapping", w), w))


def load_config(path: str) -> Config:
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    base = p.parent

    projects = [
        Project(_req(d, "id", "projects"), _req(d, "name", "projects"), _req(d, "space_id", "projects"),
                _req(d, "space_name", "projects"), dict(d.get("widgets") or {}))
        for d in _req(raw, "projects", "config")
    ]
    dims = []
    for i, d in enumerate(_req(raw, "dimensions", "config")):
        where = f"dimensions[{i}]"
        levels = [_level(base, l, j, where) for j, l in enumerate(_req(d, "levels", where))]
        if not levels:
            raise ConfigError(f"{where}: no levels")
        dims.append(Dimension(_req(d, "name", where), levels))
    dim_levels = {d.name: {l.name for l in d.levels} for d in dims}

    budgets = []
    for i, b in enumerate(raw.get("budgets") or []):
        where = f"budgets[{i}]"
        dim, lvl = _req(b, "dimension", where), _req(b, "level", where)
        if lvl not in dim_levels.get(dim, set()):
            raise ConfigError(f"{where}: unknown dimension/level '{dim}.{lvl}'")
        col = b.get("value_column", lvl)
        for r in _read_csv(base, _req(b, "file", where), where):
            try:
                usd = float(r["budget_usd"])
            except (KeyError, ValueError) as e:
                raise ConfigError(f"{where}: bad budget_usd in {b['file']}") from e
            budgets.append(Budget(dim, lvl, r[col].strip(), r["month"].strip(), usd))

    unit = raw.get("unit", "session")
    if unit not in UNITS:
        raise ConfigError(f"unit: '{unit}' must be one of {UNITS}")
    o = raw.get("outcome") or {}
    completed = list(o.get("completed_labels") or [])
    outcome = Outcome(o.get("rule", "none"), o.get("eval_name"), completed, bool(o.get("sample_ok", True)),
                      list(dict.fromkeys(completed + list(o.get("labels") or []))),
                      o.get("filter_type", "annotation"))
    if outcome.rule not in OUTCOME_RULES:
        raise ConfigError(f"outcome.rule: '{outcome.rule}' must be one of {OUTCOME_RULES}")
    if outcome.rule == "eval_label" and not (outcome.eval_name and outcome.completed_labels):
        raise ConfigError("outcome: eval_label needs eval_name and completed_labels")

    return Config(
        name=_req(raw, "name", "config"),
        tz_offset_hours=float(raw.get("tz_offset_hours", 0)),
        currency=raw.get("currency", "USD"),
        projects=projects,
        dimensions=dims,
        budgets=budgets,
        unit=unit,
        outcome=outcome,
        segments=list(raw.get("segments") or []),
        cost_agent=bool(raw.get("cost_agent", True)),
        path=str(p),
        charts=dict(raw.get("charts") or {}),
    )
