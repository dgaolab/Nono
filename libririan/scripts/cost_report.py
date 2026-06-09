#!/usr/bin/env python3
"""Token and cost accounting from Claude Code transcript JSONL files.

Modes:
    --hook                read a Stop-hook JSON payload from stdin
                          ({session_id, transcript_path}), compute totals,
                          append one JSON line to the cost log. NEVER fails
                          (exit 0 always) — a hook must not disrupt sessions.
    --summary [--last N]  print a table of the latest entry per session.
    <transcript> [--session-id S]
                          direct mode: print the computed entry JSON to stdout
                          without writing the log (tests / ad-hoc use).

Assistant transcript lines carry message.usage (input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens) and message.model.
Streamed partials repeat the same message.id with growing usage — we dedupe
by id, last occurrence wins. Sibling agent-*.jsonl files in the transcript
directory whose first line carries the same sessionId are summed in too
(subagent / evaluation-worker usage).
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

# USD per million tokens. KEEP CURRENT with platform.claude.com pricing.
# cache_write assumes the default 5-minute TTL (1.25x input);
# cache_read is 0.1x input. Override with --price-file.
PRICES = {
    "claude-fable-5":   {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 1.0},
    "claude-opus-4-8":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-opus-4-7":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-opus-4-6":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
    "claude-haiku-4-5": {"input": 1.0,  "output": 5.0,  "cache_write": 1.25, "cache_read": 0.1},
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG = os.path.join(REPO_ROOT, "_cost_log.jsonl")


def price_for(model: str, prices: dict) -> dict | None:
    """Longest-prefix match so dated/suffixed IDs (claude-haiku-4-5-20251001,
    claude-fable-5[1m]) resolve to their base price row."""
    best = None
    for key in prices:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return prices[best] if best else None


def parse_transcript(path: str) -> dict[str, dict]:
    """Sum usage per model for one transcript file."""
    by_msg: dict[str, tuple[str, dict]] = {}
    anon = 0
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError:
        return {}
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            msg_id = msg.get("id")
            if not msg_id:
                anon += 1
                msg_id = f"_anon_{anon}"
            by_msg[msg_id] = (msg.get("model", "unknown"), usage)

    totals: dict[str, dict] = {}
    for model, usage in by_msg.values():
        t = totals.setdefault(model, {"input": 0, "output": 0,
                                      "cache_read": 0, "cache_write": 0})
        t["input"] += usage.get("input_tokens") or 0
        t["output"] += usage.get("output_tokens") or 0
        t["cache_read"] += usage.get("cache_read_input_tokens") or 0
        t["cache_write"] += usage.get("cache_creation_input_tokens") or 0
    return totals


def merge_totals(into: dict, frm: dict) -> None:
    for model, t in frm.items():
        dest = into.setdefault(model, {"input": 0, "output": 0,
                                       "cache_read": 0, "cache_write": 0})
        for k in dest:
            dest[k] += t[k]


def find_agent_transcripts(transcript_path: str, session_id: str) -> list[str]:
    """Sibling agent-*.jsonl files whose first line carries this sessionId."""
    if not session_id:
        return []
    out = []
    pattern = os.path.join(os.path.dirname(os.path.abspath(transcript_path)),
                           "agent-*.jsonl")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                first = json.loads(fh.readline())
        except (OSError, json.JSONDecodeError):
            continue
        if first.get("sessionId") == session_id:
            out.append(path)
    return out


def build_entry(transcript: str, session_id: str, prices: dict) -> dict:
    totals = parse_transcript(transcript)
    for agent_path in find_agent_transcripts(transcript, session_id):
        merge_totals(totals, parse_transcript(agent_path))

    cost = 0.0
    unpriced = []
    for model, t in totals.items():
        p = price_for(model, prices)
        if p is None:
            unpriced.append(model)
            continue
        cost += (t["input"] * p["input"] + t["output"] * p["output"]
                 + t["cache_read"] * p["cache_read"]
                 + t["cache_write"] * p["cache_write"]) / 1_000_000

    entry = {
        "session_id": session_id,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transcript": transcript,
        "models": totals,
        "est_cost_usd": round(cost, 4),
    }
    if unpriced:
        entry["unpriced_models"] = sorted(unpriced)
    return entry


def run_hook(log_file: str, prices: dict) -> int:
    try:
        payload = json.load(sys.stdin)
        transcript = payload.get("transcript_path", "")
        session_id = payload.get("session_id", "")
        if not transcript or not os.path.exists(transcript):
            return 0
        entry = build_entry(transcript, session_id, prices)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # a hook must never disrupt the session
    return 0


def run_summary(log_file: str, last_n: int) -> int:
    if not os.path.exists(log_file):
        print("No cost log found.", file=sys.stderr)
        return 0
    latest: dict[str, dict] = {}
    with open(log_file, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = entry.get("session_id", "?")
            latest[sid] = entry  # totals are cumulative; last line is authoritative
    rows = sorted(latest.values(), key=lambda e: e.get("ts", ""))[-last_n:]
    print(f"{'timestamp':<22} {'session':<38} {'input':>10} {'output':>9} "
          f"{'cache_rd':>10} {'cache_wr':>9} {'est_usd':>8}")
    for e in rows:
        t = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for m in (e.get("models") or {}).values():
            for k in t:
                t[k] += m.get(k, 0)
        print(f"{e.get('ts', '?'):<22} {e.get('session_id', '?'):<38} "
              f"{t['input']:>10} {t['output']:>9} {t['cache_read']:>10} "
              f"{t['cache_write']:>9} {e.get('est_cost_usd', 0):>8.4f}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Token/cost accounting from Claude Code transcripts.")
    parser.add_argument("transcript", nargs="?", default=None,
                        help="Transcript JSONL path (direct mode)")
    parser.add_argument("--hook", action="store_true",
                        help="Stop-hook mode: read payload JSON from stdin, append to log")
    parser.add_argument("--summary", action="store_true",
                        help="Print the latest entry per session from the cost log")
    parser.add_argument("--last", type=int, default=20,
                        help="Rows to show in --summary (default 20)")
    parser.add_argument("--session-id", default="",
                        help="Session id for direct mode (enables agent-file discovery)")
    parser.add_argument("--log-file", default=DEFAULT_LOG,
                        help=f"Cost log path (default {DEFAULT_LOG})")
    parser.add_argument("--price-file", default=None,
                        help="JSON file overriding the built-in price table")
    args = parser.parse_args()

    prices = PRICES
    if args.price_file:
        with open(args.price_file, "r", encoding="utf-8") as fh:
            prices = json.load(fh)

    if args.hook:
        sys.exit(run_hook(args.log_file, prices))
    if args.summary:
        sys.exit(run_summary(args.log_file, args.last))
    if not args.transcript:
        parser.error("provide a transcript path, or use --hook / --summary")
    entry = build_entry(args.transcript, args.session_id, prices)
    json.dump(entry, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
