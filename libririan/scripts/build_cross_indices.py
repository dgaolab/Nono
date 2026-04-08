#!/usr/bin/env python3
"""Build cross-reference indices across multiple KG folders for /link-kg.

Usage:
    python3 scripts/build_cross_indices.py <kg_folder_1> <kg_folder_2> [...]

Reads manifests and node frontmatter from all KGs, builds three indices:
  - shared_references: PMIDs/NCT IDs/ChEMBL IDs cited by nodes in 2+ KGs
  - shared_entities: normalized entity IDs (excluding ?-prefixed) in 2+ KGs
  - keyword_overlap: node pairs across KGs sharing >=2 keywords

Outputs JSON to stdout.
"""

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse


def main():
    parser = argparse.ArgumentParser(description="Build cross-reference indices for KG linking.")
    parser.add_argument("kg_folders", nargs="+", help="Paths to KG folders (2 or more)")
    args = parser.parse_args()

    if len(args.kg_folders) < 2:
        print("Error: need at least 2 KG folders", file=sys.stderr)
        sys.exit(1)

    # Per-reference: ref_id -> list of (kg_name, node_id)
    ref_index: dict[str, list[dict]] = defaultdict(list)
    # Per-entity: normalized_id -> list of (kg_name, node_id, name, type)
    entity_index: dict[str, list[dict]] = defaultdict(list)
    # Per-node: (kg_name, node_id) -> set of keywords
    node_keywords: dict[tuple[str, str], set[str]] = {}

    for kg_folder in args.kg_folders:
        manifest_path = os.path.join(kg_folder, "manifest.json")
        if not os.path.exists(manifest_path):
            print(f"Error: manifest.json not found in {kg_folder}", file=sys.stderr)
            sys.exit(1)

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        kg_name = manifest.get("kg_name", os.path.basename(kg_folder))

        for node_entry in manifest.get("nodes", []):
            node_id = node_entry.get("id", "")
            node_file = node_entry.get("file", "")
            full_path = os.path.join(kg_folder, node_file) if node_file else ""

            # Collect references from manifest (flat PMID list)
            for pmid in node_entry.get("pubmed_ids", []):
                ref_index[str(pmid)].append({"kg": kg_name, "node_id": node_id})

            # Collect external IDs from manifest
            for ext in node_entry.get("external_ids", []):
                ext_id = ext.get("id", "")
                if ext_id:
                    ref_index[ext_id].append({"kg": kg_name, "node_id": node_id})

            # Keywords from manifest
            keywords = set(node_entry.get("keywords", []))
            if keywords:
                node_keywords[(kg_name, node_id)] = keywords

            # Entities from manifest (if present) or from node file
            entities = node_entry.get("entities", [])
            if not entities and full_path and os.path.exists(full_path):
                try:
                    fm, _ = parse(full_path)
                    entities = fm.get("entities", [])
                except Exception as e:
                    print(f"Warning: could not parse {full_path}: {e}", file=sys.stderr)

            for ent in entities:
                norm_id = ent.get("normalized_id", "")
                if norm_id and not norm_id.startswith("?"):
                    entity_index[norm_id].append({
                        "kg": kg_name,
                        "node_id": node_id,
                        "entity_name": ent.get("name", ""),
                        "entity_type": ent.get("type", ""),
                    })

    # Filter to cross-KG only (entries from 2+ different KGs)
    shared_refs = {}
    for ref_id, locations in ref_index.items():
        kgs = {loc["kg"] for loc in locations}
        if len(kgs) >= 2:
            shared_refs[ref_id] = locations

    shared_entities = {}
    for norm_id, locations in entity_index.items():
        kgs = {loc["kg"] for loc in locations}
        if len(kgs) >= 2:
            shared_entities[norm_id] = locations

    # Keyword overlap: compare nodes across different KGs
    keyword_overlaps = []
    nodes_list = list(node_keywords.items())
    for i in range(len(nodes_list)):
        kg_a, node_a = nodes_list[i][0]
        kw_a = nodes_list[i][1]
        for j in range(i + 1, len(nodes_list)):
            kg_b, node_b = nodes_list[j][0]
            kw_b = nodes_list[j][1]
            # Only cross-KG pairs
            if kg_a == kg_b:
                continue
            shared = kw_a & kw_b
            if len(shared) >= 2:
                keyword_overlaps.append({
                    "kg_a": kg_a,
                    "kg_b": kg_b,
                    "node_a": node_a,
                    "node_b": node_b,
                    "shared_keywords": sorted(shared),
                })

    output = {
        "shared_references": shared_refs,
        "shared_entities": shared_entities,
        "keyword_overlap": keyword_overlaps,
        "summary": {
            "total_shared_refs": len(shared_refs),
            "total_shared_entities": len(shared_entities),
            "total_keyword_overlaps": len(keyword_overlaps),
        },
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
