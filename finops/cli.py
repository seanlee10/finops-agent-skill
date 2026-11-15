import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from .budget import month_window
from .config import ConfigError, load_config
from .report import build, render_investigation, render_md
from .source import Client, SourceError


def _parse(s: str, tz: timezone) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def parse_window(start: Optional[str], end: Optional[str], tz_offset_hours: float,
                 now: Optional[datetime]) -> Tuple[datetime, datetime]:
    tz = timezone(timedelta(hours=tz_offset_hours))
    e = _parse(end, tz) if end else (now or datetime.now(timezone.utc))
    s = _parse(start, tz) if start else month_window(e, tz_offset_hours)[0]
    return s, e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FinOps report from Arize cost aggregates")
    ap.add_argument("--config", required=True)
    ap.add_argument("--start", help="ISO start; default is the start of the month in the config's timezone")
    ap.add_argument("--end", help="ISO end; default is now")
    ap.add_argument("--format", choices=["md", "json", "investigation"], default="md")
    args = ap.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    start, end = parse_window(args.start, args.end, config.tz_offset_hours, None)
    try:
        result = build(config, Client(), start, end, end)
    except SourceError as e:
        print(f"Arize query failed: {e}", file=sys.stderr)
        return 3
    if args.format == "json":
        print(json.dumps(result, indent=1))
    elif args.format == "investigation":
        print(render_investigation(result, config))
    else:
        print(render_md(result, config))
    return 0
