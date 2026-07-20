#!/usr/bin/env python
"""metrics_report.py -- quick usage + feedback summary for MacroAgent.

Reads metrics.jsonl (written by log_event() in macroagent_app.py) and prints:
  - event counts, unique sessions, and the time span covered
  - AI QUALITY: the 👍/👎 helpful-rate on the strategy memo and chat replies
  - RELIABILITY: how often Gemini fell back to offline/mock output (degraded)
  - FUNNEL: session_start -> profile_saved -> strategy_generated -> paper_trade

Stdlib only, so it runs with any Python (no venv needed):
    python metrics_report.py                # reads ./metrics.jsonl
    python metrics_report.py other.jsonl    # reads a specific file
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# metrics.jsonl / this console may hold emoji -> force UTF-8 so Windows won't choke
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                       # noqa: BLE001 -- stream may not support it
    pass


def load_events(path: Path) -> list[dict]:
    """Parse the JSONL file, skipping any blank or half-written lines."""
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue                # tolerate a torn last line from a live write
    return events


def pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "metrics.jsonl")
    if not path.exists():
        print(f"No metrics file at {path.resolve()} — use the app first, then re-run.")
        return
    events = load_events(path)
    if not events:
        print(f"{path} is empty — no events logged yet.")
        return

    counts = Counter(e.get("event") for e in events)
    sessions = {e.get("session") for e in events if e.get("session")}
    stamps = sorted(e["ts"] for e in events if e.get("ts"))

    print("=" * 58)
    print(" MACROAGENT — METRICS REPORT")
    print("=" * 58)
    print(f" events: {len(events)}    sessions: {len(sessions)}")
    if stamps:
        print(f" span:   {stamps[0]}  ->  {stamps[-1]}")
    print()

    print(" EVENT COUNTS")
    for name, n in counts.most_common():
        print(f"   {name:<24} {n}")
    print()

    # --- AI QUALITY: the reason we log at all -------------------------------
    print(" AI QUALITY  (thumbs up / down)")
    for evt, label in [("memo_feedback", "Strategy memo"),
                       ("chat_feedback", "Chat replies")]:
        ratings = Counter(e.get("props", {}).get("rating")
                          for e in events if e.get("event") == evt)
        up, down = ratings.get("up", 0), ratings.get("down", 0)
        total = up + down
        tail = "" if total else "   (no ratings yet)"
        print(f"   {label:<14} +{up} / -{down}    helpful: {pct(up, total)}{tail}")
    print()

    # --- RELIABILITY: Gemini reachability ----------------------------------
    gen = [e for e in events if e.get("event") == "strategy_generated"]
    degraded = sum(1 for e in gen if e.get("props", {}).get("degraded"))
    print(" RELIABILITY")
    print(f"   memos generated:    {len(gen)}")
    print(f"   offline / degraded: {degraded}  ({pct(degraded, len(gen))} of memos)")
    print(f"   generation errors:  {counts.get('strategy_error', 0)}")
    print()

    # --- FUNNEL: unique sessions reaching each stage -----------------------
    reached: dict[str, set] = defaultdict(set)
    for e in events:
        s = e.get("session")
        if s:
            reached[e.get("event")].add(s)
    base = len(reached.get("session_start", set())) or len(sessions)
    print(" FUNNEL  (unique sessions)")
    for evt, label in [("session_start", "Visited"),
                       ("profile_saved", "Saved profile"),
                       ("strategy_generated", "Generated memo"),
                       ("paper_trade", "Placed paper trade")]:
        n = len(reached.get(evt, set()))
        print(f"   {label:<20} {n:>4}   {pct(n, base)}")
    print()

    # --- PAPER TRADES ------------------------------------------------------
    trades = [e for e in events if e.get("event") == "paper_trade"]
    if trades:
        by_side = Counter(e.get("props", {}).get("side") for e in trades)
        rejected = counts.get("paper_trade_rejected", 0)
        sides = "  ".join(f"{k}: {v}" for k, v in by_side.items())
        print(" PAPER TRADES")
        print(f"   executed: {len(trades)}   {sides}   rejected: {rejected}")
        print()

    print("=" * 58)


if __name__ == "__main__":
    main()
