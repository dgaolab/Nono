#!/usr/bin/env python3
"""Append an operation entry to a KG's _log.md file.

Usage:
    python3 scripts/append_log.py <kg_folder> --op <operation> --summary "text"

Operations: build, update, evaluate, link, query, lint, schedule, preflight, digest, retraction, citation

Entries are prepended (reverse chronological, newest on top) with a
grep-parseable prefix:
    ## [2026-04-08T15:30:00Z] query | What is the role of SCN1A in Dravet?
    Nodes consulted: 5. Answer filed as node_025. Augmented: no.
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone


VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight", "digest", "retraction", "citation"}


def append_entry(kg_folder: str, op: str, summary: str, details: str = "") -> str:
    """Prepend an operation entry to <kg_folder>/_log.md. Returns the timestamp used."""
    kg_folder = os.path.abspath(kg_folder)
    log_path = os.path.join(kg_folder, "_log.md")

    if not os.path.isdir(kg_folder):
        raise FileNotFoundError(f"directory not found: {kg_folder}")
    if op not in VALID_OPS:
        raise ValueError(f"invalid op: {op!r} (valid: {sorted(VALID_OPS)})")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_entry = f"## [{timestamp}] {op} | {summary}\n"
    if details:
        new_entry += details.rstrip("\n") + "\n"
    new_entry += "\n"

    existing = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as fh:
            existing = fh.read()

    content = new_entry + existing

    fd, tmp_path = tempfile.mkstemp(dir=kg_folder, suffix=".md.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, log_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return timestamp


def main():
    parser = argparse.ArgumentParser(description="Append an operation entry to _log.md.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--op", required=True, choices=sorted(VALID_OPS),
                        help="Operation type")
    parser.add_argument("--summary", required=True,
                        help="One-line summary of what was done")
    parser.add_argument("--details", default="",
                        help="Optional multi-line details (newlines preserved)")
    args = parser.parse_args()

    try:
        timestamp = append_entry(args.kg_folder, args.op, args.summary, args.details)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Logged: [{timestamp}] {args.op}", file=sys.stderr)


if __name__ == "__main__":
    main()
