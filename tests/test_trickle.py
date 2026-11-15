import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "synthetic"))

from assistant_gen import TZ, due_annotations, due_sessions, plan_sessions  # noqa: E402

START = datetime(2026, 11, 11, tzinfo=TZ)
UNTIL = datetime(2026, 11, 18, tzinfo=TZ)


def test_plan_is_independent_of_when_it_runs():
    a = plan_sessions(START, UNTIL, 11)
    b = plan_sessions(START, UNTIL, 11)
    assert [s["session_id"] for s in a] == [s["session_id"] for s in b]


def test_due_sessions_sends_finished_unsent_sessions_after_cutoff():
    sessions = plan_sessions(START, UNTIL, 11)
    now = datetime(2026, 11, 12, 15, 0, tzinfo=TZ)
    cutoff = datetime(2026, 11, 11, 13, 0, tzinfo=TZ)
    due = due_sessions(sessions, set(), now, cutoff, lag=timedelta(minutes=10))
    assert due
    for s in due:
        assert datetime.fromisoformat(s["start"]) >= cutoff
        assert datetime.fromisoformat(s["turns"][-1]["t"]) <= now - timedelta(minutes=10)
    sent = {s["session_id"] for s in due[:5]}
    again = due_sessions(sessions, sent, now, cutoff, lag=timedelta(minutes=10))
    assert not sent & {s["session_id"] for s in again}
    assert len(again) == len(due) - 5


def test_due_annotations_wait_for_ingestion():
    now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    sessions = [{"session_id": "a", "annotate": True}, {"session_id": "b", "annotate": True},
                {"session_id": "c", "annotate": False}, {"session_id": "d", "annotate": True}]
    sent_at = {"a": (now - timedelta(hours=1)).isoformat(), "b": (now - timedelta(minutes=5)).isoformat(),
               "c": (now - timedelta(hours=1)).isoformat(), "d": (now - timedelta(hours=2)).isoformat()}
    out = due_annotations(sessions, sent_at, {"d"}, now, wait=timedelta(minutes=30))
    assert [s["session_id"] for s in out] == ["a"]
