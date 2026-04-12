#!/usr/bin/env python3
"""Enforce quarantine flags on KG node files based on evaluation_status.

Usage:
    python3 scripts/enforce_quarantine.py <kg_folder> [--dry-run]

Rules:
    evaluation_status == "failed"  -> quarantined = True
    evaluation_status == "passed"  -> quarantined = False
    Otherwise (pending / missing)  -> no change
"""

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse, write


def main():
    parser = argparse.ArgumentParser(
        description="Enforce quarantine flags based on evaluation_status.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without writing to disk")
    args = parser.parse_args()

    nodes_dir = os.path.join(args.kg_folder, "nodes")
    if not os.path.isdir(nodes_dir):
        print(f"Error: nodes directory not found: {nodes_dir}", file=sys.stderr)
        sys.exit(1)

    node_files = sorted(glob.glob(os.path.join(nodes_dir, "*.md")))
    quarantined = 0
    unquarantined = 0
    unchanged = 0

    for node_file in node_files:
        try:
            fm, body = parse(node_file)
        except Exception as e:
            print(f"Warning: could not parse {node_file}: {e}", file=sys.stderr)
            unchanged += 1
            continue

        eval_status = fm.get("evaluation_status", None)
        current_q = fm.get("quarantined", False)
        changed = False

        if eval_status == "failed" and current_q is not True:
            fm["quarantined"] = True
            changed = True
            quarantined += 1
        elif eval_status == "passed" and current_q is not False:
            fm["quarantined"] = False
            changed = True
            unquarantined += 1
        else:
            unchanged += 1

        if changed and not args.dry_run:
            write(node_file, fm, body)

    # Update manifest stats if any changes were made (unless dry-run)
    changed_count = quarantined + unquarantined
    if not args.dry_run and changed_count > 0:
        stats_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "update_manifest_stats.py")
        if os.path.exists(stats_script):
            subprocess.run([sys.executable, stats_script, args.kg_folder],
                           capture_output=True)

    summary = {
        "nodes_processed": len(node_files),
        "nodes_quarantined": quarantined,
        "nodes_unquarantined": unquarantined,
        "nodes_unchanged": unchanged,
    }
    json.dump(summary, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
