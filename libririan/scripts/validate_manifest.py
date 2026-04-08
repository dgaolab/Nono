#!/usr/bin/env python3
"""Validate a KG manifest.json against the JSON schema.

Usage:
    python3 scripts/validate_manifest.py <manifest_path> [--schema <schema_path>]

Outputs {"valid": true} or {"valid": false, "errors": [...]}.
Also performs soft checks (printed to stderr) for node file existence,
edge reference validity, and statistics consistency.
"""

import argparse
import json
import os
import sys


def _find_project_root() -> str:
    """Walk up from this script to find the project root (contains schemas/)."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(d, "schemas")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(__file__))


def _soft_checks(manifest: dict, manifest_path: str):
    """Run non-fatal consistency checks, print warnings to stderr."""
    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
    warnings = []

    nodes = manifest.get("nodes", [])
    node_ids = {n["id"] for n in nodes if "id" in n}

    # Check node files exist on disk
    for node in nodes:
        file_path = node.get("file", "")
        if file_path:
            full_path = os.path.join(manifest_dir, file_path)
            if not os.path.exists(full_path):
                warnings.append(f"Node file missing: {file_path}")

    # Check edge references point to valid node IDs
    for edge in manifest.get("edges", []):
        if edge.get("source") not in node_ids:
            warnings.append(f"Edge source '{edge.get('source')}' not in node list")
        if edge.get("target") not in node_ids:
            warnings.append(f"Edge target '{edge.get('target')}' not in node list")

    # Check statistics consistency
    stats = manifest.get("statistics", {})
    if stats.get("total_nodes") != len(nodes):
        warnings.append(
            f"statistics.total_nodes ({stats.get('total_nodes')}) != actual node count ({len(nodes)})"
        )
    if stats.get("total_edges") != len(manifest.get("edges", [])):
        warnings.append(
            f"statistics.total_edges ({stats.get('total_edges')}) != actual edge count ({len(manifest.get('edges', []))})"
        )

    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)

    return warnings


def main():
    parser = argparse.ArgumentParser(description="Validate manifest.json against schema.")
    parser.add_argument("manifest_path", help="Path to manifest.json")
    parser.add_argument("--schema", help="Path to JSON Schema file (default: schemas/graph_schema.json)")
    args = parser.parse_args()

    # Try to import jsonschema
    try:
        import jsonschema
    except ImportError:
        print("Error: 'jsonschema' package is required. Install with: pip install jsonschema",
              file=sys.stderr)
        sys.exit(1)

    # Load manifest
    if not os.path.exists(args.manifest_path):
        print(f"Error: file not found: {args.manifest_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {args.manifest_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Load schema
    schema_path = args.schema
    if not schema_path:
        project_root = _find_project_root()
        schema_path = os.path.join(project_root, "schemas", "graph_schema.json")

    if not os.path.exists(schema_path):
        print(f"Error: schema not found: {schema_path}", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)

    # Validate
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.path)):
        path = "$.{}".format(".".join(str(p) for p in error.absolute_path)) if error.absolute_path else "$"
        errors.append({"path": path, "message": error.message})

    # Soft checks
    _soft_checks(manifest, args.manifest_path)

    if errors:
        result = {"valid": False, "errors": errors}
        json.dump(result, sys.stdout, indent=2)
        print()
        sys.exit(1)
    else:
        result = {"valid": True}
        json.dump(result, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
