#!/usr/bin/env python3
"""Parse a KG node .md file and output its frontmatter + body as JSON.

Usage:
    python3 scripts/parse_node.py <node_path> [--field <dotted.path>]

Examples:
    python3 scripts/parse_node.py KG_X/nodes/node_001_foo.md
    python3 scripts/parse_node.py KG_X/nodes/node_001_foo.md --field evaluation_status
    python3 scripts/parse_node.py KG_X/nodes/node_001_foo.md --field pubmed_ids.0.pmid
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse


def _resolve_field(obj, dotted_path: str):
    """Walk a dotted path like 'pubmed_ids.0.pmid' into a nested structure."""
    parts = dotted_path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        else:
            return None
    return current


def main():
    parser = argparse.ArgumentParser(description="Parse a KG node .md file to JSON.")
    parser.add_argument("node_path", help="Path to the node .md file")
    parser.add_argument("--field", help="Extract a single frontmatter field (dotted path)")
    args = parser.parse_args()

    try:
        frontmatter, body = parse(args.node_path)
    except FileNotFoundError:
        print(f"Error: file not found: {args.node_path}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading {args.node_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.field:
        value = _resolve_field(frontmatter, args.field)
        json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        result = {"frontmatter": frontmatter, "body": body}
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()
