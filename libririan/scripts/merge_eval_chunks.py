#!/usr/bin/env python3
"""Merge evaluation chunk files into a single _evaluation_log.json.

Usage:
    python3 scripts/merge_eval_chunks.py <kg_folder> [--cleanup]

Reads all _eval_chunk_*.json files in the KG folder, merges them with any
existing _evaluation_log.json (dedup by node_id, later chunk wins), and
writes the merged result.

With --cleanup, deletes chunk files after successful merge.
"""

import argparse
import glob
import json
import os
import re
import sys
import tempfile


def _chunk_sort_key(path: str) -> int:
    """Extract the numeric chunk ID from a filename like _eval_chunk_3.json."""
    match = re.search(r"_eval_chunk_(\d+)\.json$", path)
    return int(match.group(1)) if match else 0


def main():
    parser = argparse.ArgumentParser(description="Merge evaluation chunk files.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete chunk files after successful merge")
    args = parser.parse_args()

    kg_folder = args.kg_folder
    if not os.path.isdir(kg_folder):
        print(f"Error: not a directory: {kg_folder}", file=sys.stderr)
        sys.exit(1)

    # Find chunk files
    pattern = os.path.join(kg_folder, "_eval_chunk_*.json")
    chunk_files = sorted(glob.glob(pattern), key=_chunk_sort_key)

    if not chunk_files:
        result = {"merged": 0, "from_chunks": 0, "total_entries": 0}
        json.dump(result, sys.stdout, indent=2)
        print()
        return

    # Load existing evaluation log if present
    log_path = os.path.join(kg_folder, "_evaluation_log.json")
    entries: dict[str, dict] = {}

    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, list):
                for entry in existing:
                    node_id = entry.get("node_id")
                    if node_id:
                        entries[node_id] = entry
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading existing {log_path}: {e}", file=sys.stderr)
            sys.exit(1)

    # Merge chunks in order (later chunks overwrite earlier for same node_id)
    merged_count = 0
    for chunk_path in chunk_files:
        try:
            with open(chunk_path, "r", encoding="utf-8") as fh:
                chunk_data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading {chunk_path}: {e}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(chunk_data, list):
            print(f"Error: {chunk_path} is not a JSON array", file=sys.stderr)
            sys.exit(1)

        for entry in chunk_data:
            node_id = entry.get("node_id")
            if not node_id:
                print(f"Error: entry without node_id in {chunk_path}", file=sys.stderr)
                sys.exit(1)
            entries[node_id] = entry
            merged_count += 1

    # Sort by node_id for deterministic output
    sorted_entries = sorted(entries.values(), key=lambda e: e.get("node_id", ""))

    # Write merged log (atomic)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(log_path), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            json.dump(sorted_entries, tmp_fh, ensure_ascii=False, indent=2)
            tmp_fh.write("\n")
        os.replace(tmp_path, log_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Cleanup chunk files
    if args.cleanup:
        for chunk_path in chunk_files:
            try:
                os.remove(chunk_path)
            except OSError as e:
                print(f"Warning: could not delete {chunk_path}: {e}", file=sys.stderr)

    result = {
        "merged": merged_count,
        "from_chunks": len(chunk_files),
        "total_entries": len(sorted_entries),
    }
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
