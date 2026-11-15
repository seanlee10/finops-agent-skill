"""Create the pivot widgets a FinOps config reads, and write their ids into the config.

Run once per deployment by a person with dashboard create access:
  ARIZE_API_KEY=... python3 scripts/setup_widgets.py --config examples/acme.json [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finops.source import Client, SourceError  # noqa: E402
from finops.widgets import setup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        plan = setup(args.config, Client(), dry_run=args.dry_run)
    except SourceError as e:
        print(f"Arize query failed: {e}", file=sys.stderr)
        return 3
    for x in plan:
        print(f"{x['action']:6} {x['project']:30} {x['grouping']:30} {x.get('widget', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
