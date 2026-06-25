#!/usr/bin/env python3
"""Update YAML frontmatter fields in a KG node .md file.

Usage:
    python3 scripts/update_frontmatter.py <node_path> <json_updates> [--dry-run]
    python3 scripts/update_frontmatter.py <node_path> --updates-file <path> [--dry-run]

Examples:
    # Set evaluation_status and mark a PMID as verified
    python3 scripts/update_frontmatter.py KG_X/nodes/node_001_foo.md \
        '{"evaluation_status": "passed", "pubmed_ids": [{"pmid": "35486828", "verified": true}]}'

    # Add cross-KG links from a file
    python3 scripts/update_frontmatter.py KG_X/nodes/node_003_bar.md \
        --updates-file /tmp/updates.json

    # Preview changes without writing
    python3 scripts/update_frontmatter.py KG_X/nodes/node_001_foo.md \
        '{"evaluation_status": "failed"}' --dry-run
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse, serialize, write, deep_merge


def main():
    parser = argparse.ArgumentParser(description="Update YAML frontmatter in a node .md file.")
    parser.add_argument("node_path", help="Path to the node .md file")
    parser.add_argument("json_updates", nargs="?", default=None,
                        help="JSON string of fields to update")
    parser.add_argument("--updates-file", help="Path to a JSON file with updates (alternative to inline JSON)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the result without writing to disk")
    args = parser.parse_args()

    # Load updates from inline arg or file
    if args.updates_file:
        try:
            with open(args.updates_file, "r", encoding="utf-8") as fh:
                updates = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error reading updates file: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.json_updates:
        try:
            updates = json.loads(args.json_updates)
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in updates argument: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: provide either <json_updates> or --updates-file", file=sys.stderr)
        sys.exit(1)

    if not isinstance(updates, dict):
        print("Error: updates must be a JSON object", file=sys.stderr)
        sys.exit(1)

    # Parse existing file
    try:
        frontmatter, body = parse(args.node_path)
    except FileNotFoundError:
        print(f"Error: file not found: {args.node_path}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Merge
    merged = deep_merge(frontmatter, updates)

    if args.dry_run:
        # Print the full file content to stdout
        print(serialize(merged, body), end="")
    else:
        write(args.node_path, merged, body)
        # Output updated frontmatter as JSON for verification
        json.dump(merged, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()
