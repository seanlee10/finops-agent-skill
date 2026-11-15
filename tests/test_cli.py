import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from finops.cli import parse_window

ROOT = Path(__file__).resolve().parent.parent


def test_default_window_is_month_to_date_in_config_tz():
    now = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    start, end = parse_window(None, None, 9, now)
    assert start == datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=9)))
    assert end == now


def test_naive_iso_gets_config_tz():
    start, end = parse_window("2026-11-01T00:00:00", "2026-11-10T00:00:00", 9, None)
    assert start.utcoffset() == timedelta(hours=9) and end.day == 10


def test_bad_config_exits_nonzero(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{}")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "finops.py"), "--config", str(bad)],
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "missing key" in r.stderr


def test_investigation_format_is_accepted(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{}")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "finops.py"), "--config", str(bad),
                        "--format", "investigation"], capture_output=True, text=True)
    assert "invalid choice" not in r.stderr
    assert r.returncode == 2
