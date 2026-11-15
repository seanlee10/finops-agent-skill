"""Create the FinOps charts dashboard (daily spend lines, span-count bars) for a config, one per space,
and write the dashboard links into the config so reports link to them.

Run once per deployment by a person with dashboard create access:
  ARIZE_API_KEY=... python3 scripts/setup_charts.py --config examples/acme.json [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finops.charts import setup_charts  # noqa: E402
from finops.source import Client, SourceError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        plan = setup_charts(args.config, Client(), dry_run=args.dry_run)
    except SourceError as e:
        print(f"Arize query failed: {e}", file=sys.stderr)
        return 3
    for x in plan:
        print(f"{x['action']:6} {x['space']:12} {x['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
