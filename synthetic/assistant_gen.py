"""Synthetic traces shaped like a LangGraph property-search assistant, for exercising the FinOps skill.

Three projects across two spaces. Spans carry llm.cost.* (with cache reads), session.id,
metadata.thread_id, auth.status on every span, stream.cancelled on some final turns and ERROR
on some root turns. No message content is generated. Session outcomes ("task_completion":
completed / partial / failed) are written afterwards as session-level annotations.

  python3 synthetic/assistant_gen.py --dry-run
  python3 synthetic/assistant_gen.py --send --space-a <id> --space-b <id>   # ARIZE_API_KEY from env
  python3 synthetic/assistant_gen.py --annotate --space-a <id> --space-b <id>
"""

import argparse
import json
import os
import random
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "assistant-ledger.json"
TZ = timezone(timedelta(hours=10))
MODEL = "gpt-5.6-luna"
# Placeholder prices (USD per 1M tokens) for synthetic data only.
PRICE_IN, PRICE_CACHED, PRICE_OUT = 1.25, 0.125, 10.0
TOOLS = ["search_listings", "get_property_details", "find_nearby_amenities"]
ANNOTATION = "task_completion"

# project -> (space key, sessions per day, turns range, annotation coverage)
PROJECTS = {
    "synth-assistant": ("a", 60, (1, 6), 1.0),
    "synth-search": ("a", 20, (1, 4), 0.5),
    "synth-valuation": ("b", 8, (1, 3), 1.0),
}


def ns(dt):
    return int(dt.timestamp() * 1e9)


def plan_sessions(start, end, seed):
    rnd = random.Random(seed)
    sessions = []
    day = start
    while day < end:
        for project, (_, per_day, (tmin, tmax), coverage) in PROJECTS.items():
            for _ in range(rnd.randint(int(per_day * 0.7), int(per_day * 1.3))):
                t0 = day + timedelta(minutes=rnd.randint(6 * 60, 23 * 60))
                if t0 > end:
                    continue
                auth = "authenticated" if rnd.random() < 0.6 else "anonymous"
                turns = []
                n = rnd.randint(tmin, tmax if auth == "authenticated" else max(tmin, tmax - 2))
                t = t0
                for i in range(n):
                    llm_calls = rnd.randint(1, 3)
                    calls = []
                    for _ in range(llm_calls):
                        prompt = rnd.randint(3000, 12000)
                        cached = int(prompt * rnd.uniform(0.3, 0.5))
                        out = rnd.randint(200, 800)
                        cost_p = (prompt - cached) * PRICE_IN / 1e6 + cached * PRICE_CACHED / 1e6
                        cost_c = out * PRICE_OUT / 1e6
                        calls.append({"prompt": prompt, "cached": cached, "out": out,
                                      "cost_prompt": round(cost_p, 8), "cost_cached": round(cached * PRICE_CACHED / 1e6, 8),
                                      "cost_completion": round(cost_c, 8)})
                    turns.append({"t": t.isoformat(), "calls": calls,
                                  "tools": rnd.sample(TOOLS, rnd.randint(0, 3)),
                                  "error": rnd.random() < 0.03,
                                  "cancelled": i == n - 1 and rnd.random() < 0.05})
                    t += timedelta(seconds=rnd.randint(30, 240))
                bad = any(x["error"] or x["cancelled"] for x in turns)
                r = rnd.random()
                outcome = "failed" if bad else ("completed" if r < 0.74 else "partial" if r < 0.92 else "failed")
                cost = sum(c["cost_prompt"] + c["cost_completion"] for x in turns for c in x["calls"])
                sessions.append({"project": project, "session_id": f"sess-{uuid.UUID(int=rnd.getrandbits(128))}",
                                 "thread_id": f"thr-{uuid.UUID(int=rnd.getrandbits(128))}", "auth": auth,
                                 "start": t0.isoformat(), "turns": turns, "outcome": outcome,
                                 "annotate": rnd.random() < coverage, "cost": round(cost, 8)})
        day += timedelta(days=1)
    return sessions


def due_sessions(sessions, sent, now, cutoff, lag=timedelta(minutes=10)):
    """Sessions that started at or after cutoff, whose last turn is at least lag old, not yet sent."""
    return [s for s in sessions
            if s["session_id"] not in sent
            and datetime.fromisoformat(s["start"]) >= cutoff
            and datetime.fromisoformat(s["turns"][-1]["t"]) <= now - lag]


def due_annotations(sessions, sent_at, annotated, now, wait=timedelta(minutes=30)):
    """Sent sessions flagged for annotation, sent at least wait ago, not yet annotated."""
    return [s for s in sessions
            if s["annotate"] and s["session_id"] in sent_at and s["session_id"] not in annotated
            and datetime.fromisoformat(sent_at[s["session_id"]]) <= now - wait]


def trickle(args, api_key):
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"sent_at": {}, "annotated": []}
    now = datetime.now(TZ)
    sessions = plan_sessions(datetime.fromisoformat(args.start), datetime.fromisoformat(args.until), args.seed)
    due = due_sessions(sessions, set(state["sent_at"]), now, datetime.fromisoformat(args.trickle_from))
    if due:
        emit(due, {"a": args.space_a, "b": args.space_b}, api_key)
        stamp = datetime.now(timezone.utc).isoformat()
        for s in due:
            state["sent_at"][s["session_id"]] = stamp
        state_path.write_text(json.dumps(state))
    print(f"{now.isoformat(timespec='seconds')} sent {len(due)} sessions (${sum(s['cost'] for s in due):,.4f})")
    pending = due_annotations(sessions, state["sent_at"], set(state["annotated"]), datetime.now(timezone.utc))
    if pending and args.project_ids:
        try:
            annotate(pending, json.loads(args.project_ids), api_key)
        except SystemExit as e:
            print(f"annotation deferred: {e}")
        else:
            state["annotated"] += [s["session_id"] for s in pending]
            state_path.write_text(json.dumps(state))


def summary(sessions):
    out = {}
    for s in sessions:
        p = out.setdefault(s["project"], {"sessions": 0, "cost": 0.0, "by_auth": {}, "annotated": 0})
        p["sessions"] += 1
        p["cost"] += s["cost"]
        p["by_auth"][s["auth"]] = p["by_auth"].get(s["auth"], 0.0) + s["cost"]
        p["annotated"] += s["annotate"]
    return out


FLUSH_EVERY = 25


def emit(sessions, spaces, api_key):
    from arize.otel import register
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    providers = {}
    for n, s in enumerate(sessions, 1):
        key = (PROJECTS[s["project"]][0], s["project"])
        if key not in providers:
            providers[key] = register(space_id=spaces[key[0]], api_key=api_key, project_name=s["project"],
                                      set_global_tracer_provider=False)
        tracer = providers[key].get_tracer("assistant-synthetic")
        common = {"session.id": s["session_id"], "metadata.thread_id": s["thread_id"], "auth.status": s["auth"]}
        for turn in s["turns"]:
            t = datetime.fromisoformat(turn["t"])
            root = tracer.start_span("conversation-turn", start_time=ns(t))
            root.set_attributes({**common, "openinference.span.kind": "CHAIN"})
            ctx = trace.set_span_in_context(root)
            agent = tracer.start_span("model", context=ctx, start_time=ns(t))
            agent.set_attributes({**common, "openinference.span.kind": "AGENT"})
            actx = trace.set_span_in_context(agent)
            tt = t
            for c in turn["calls"]:
                llm = tracer.start_span("ChatOpenAI", context=actx, start_time=ns(tt))
                llm.set_attributes({**common, "openinference.span.kind": "LLM", "llm.model_name": MODEL,
                                    "llm.provider": "openai",
                                    "llm.token_count.prompt": c["prompt"], "llm.token_count.completion": c["out"],
                                    "llm.token_count.total": c["prompt"] + c["out"],
                                    "llm.token_count.prompt_details.cache_read": c["cached"],
                                    "llm.cost.prompt": c["cost_prompt"], "llm.cost.completion": c["cost_completion"],
                                    "llm.cost.prompt_details.cache_read": c["cost_cached"],
                                    "llm.cost.total": round(c["cost_prompt"] + c["cost_completion"], 8)})
                tt += timedelta(seconds=random.uniform(1, 6))
                llm.end(end_time=ns(tt))
            for name in turn["tools"]:
                tool = tracer.start_span(name, context=actx, start_time=ns(tt))
                tool.set_attributes({**common, "openinference.span.kind": "TOOL", "tool.name": name})
                tt += timedelta(seconds=random.uniform(0.2, 2))
                tool.end(end_time=ns(tt))
            agent.end(end_time=ns(tt))
            if turn["cancelled"]:
                root.set_attribute("stream.cancelled", True)
            if turn["error"]:
                root.set_status(Status(StatusCode.ERROR, "upstream error"))
            root.end(end_time=ns(tt))
        if n % FLUSH_EVERY == 0:
            for p in providers.values():
                p.force_flush()
    for p in providers.values():
        p.force_flush()
        p.shutdown()


def rest(method, path, api_key, body=None):
    req = urllib.request.Request(f"https://api.arize.com{path}", json.dumps(body).encode() if body else None,
                                 {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read().decode()[:800]}")


def annotate(sessions, project_ids, api_key):
    by_project = {}
    for s in sessions:
        if s["annotate"]:
            by_project.setdefault(s["project"], []).append(s)
    for project, items in by_project.items():
        items.sort(key=lambda s: s["start"])
        while items:
            w0 = datetime.fromisoformat(items[0]["start"]) - timedelta(minutes=1)
            w1 = min(w0 + timedelta(days=6, hours=23), datetime.now(timezone.utc) - timedelta(minutes=1))
            batch = [s for s in items if datetime.fromisoformat(s["start"]) < w1][:100]
            items = items[len(batch):]
            rest("POST", "/v2/spans/annotate", api_key, {
                "project_id": project_ids[project], "granularity": "SESSION",
                "start_time": w0.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end_time": w1.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "annotations": [{"record_id": s["session_id"], "values": [{"name": ANNOTATION, "label": s["outcome"]}]}
                                for s in batch]})
            print(f"annotated {len(batch)} sessions in {project}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-11-01T00:00:00+10:00")
    ap.add_argument("--end", help="default: now minus 30 minutes")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--space-a")
    ap.add_argument("--space-b")
    ap.add_argument("--project-ids", help="JSON {project name: id}, required for --annotate")
    ap.add_argument("--only", action="append", help="send only these projects (repeatable)")
    ap.add_argument("--trickle", action="store_true",
                    help="send only sessions whose time has come; run on a schedule")
    ap.add_argument("--until", default="2026-11-18T00:00:00+10:00")
    ap.add_argument("--trickle-from", help="ignore planned sessions that start before this time")
    ap.add_argument("--state", default=str(HERE / "trickle-state.json"))
    args = ap.parse_args()

    if args.trickle:
        trickle(args, os.environ["ARIZE_API_KEY"])
        return
    if args.annotate:
        sessions = json.loads(LEDGER.read_text())
        annotate(sessions, json.loads(args.project_ids), os.environ["ARIZE_API_KEY"])
        return
    end = datetime.fromisoformat(args.end) if args.end else datetime.now(TZ) - timedelta(minutes=30)
    sessions = plan_sessions(datetime.fromisoformat(args.start), end, args.seed)
    for project, p in summary(sessions).items():
        auth = ", ".join(f"{k} ${v:,.2f}" for k, v in sorted(p["by_auth"].items()))
        print(f"{project:20} sessions={p['sessions']:4} annotated={p['annotated']:4} total=${p['cost']:,.4f} ({auth})")
    print(f"TOTAL sessions={len(sessions)} total=${sum(s['cost'] for s in sessions):,.4f}")
    if args.dry_run:
        return
    if args.send:
        LEDGER.write_text(json.dumps(sessions))
        chosen = [s for s in sessions if not args.only or s["project"] in args.only]
        emit(chosen, {"a": args.space_a, "b": args.space_b}, os.environ["ARIZE_API_KEY"])
        print(f"sent; ledger -> {LEDGER}")


if __name__ == "__main__":
    main()
