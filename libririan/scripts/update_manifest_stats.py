#!/usr/bin/env python3
"""Recalculate and update statistics in a KG's manifest.json.

Usage:
    python3 scripts/update_manifest_stats.py <kg_folder> [--dry-run]

Reads all node .md files via frontmatter parsing, computes statistics
(total_nodes, total_edges, total_unique_pmids, evaluation_passed/failed,
evidence_tier_distribution, total_nct_ids, total_chembl_ids), and updates
the statistics section in manifest.json.
"""

import argparse
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse


def main():
    parser = argparse.ArgumentParser(description="Update manifest.json statistics from node files.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print computed statistics without writing")
    args = parser.parse_args()

    kg_folder = args.kg_folder
    manifest_path = os.path.join(kg_folder, "manifest.json")

    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {kg_folder}", file=sys.stderr)
        sys.exit(1)

    # Load manifest
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    # Find all node .md files
    node_files = sorted(glob.glob(os.path.join(kg_folder, "nodes", "*.md")))

    # Collect statistics from node files (single pass)
    total_nodes = 0
    all_pmids: set[str] = set()
    all_nct_ids: set[str] = set()
    all_chembl_ids: set[str] = set()
    eval_passed = 0
    eval_failed = 0
    quarantined_count = 0
    tier_distribution: dict[str, int] = {}
    node_fm_by_id: dict[str, dict] = {}

    for node_file in node_files:
        try:
            fm, _ = parse(node_file)
        except Exception as e:
            print(f"Warning: skipping {node_file}: {e}", file=sys.stderr)
            continue

        total_nodes += 1

        # Index by node ID for manifest sync
        node_id = fm.get("id")
        if node_id:
            node_fm_by_id[node_id] = fm

        # PMIDs
        for entry in fm.get("pubmed_ids", []):
            pmid = entry.get("pmid") if isinstance(entry, dict) else str(entry)
            if pmid:
                all_pmids.add(str(pmid))

        # External IDs
        for ext in fm.get("external_ids", []):
            source = ext.get("source", "")
            ext_id = ext.get("id", "")
            if source == "clinicaltrials" and ext_id:
                all_nct_ids.add(ext_id)
            elif source == "chembl" and ext_id:
                all_chembl_ids.add(ext_id)

        # Evaluation status
        eval_status = fm.get("evaluation_status", "pending")
        if eval_status == "passed":
            eval_passed += 1
        elif eval_status == "failed":
            eval_failed += 1

        # Quarantine status
        if fm.get("quarantined", False):
            quarantined_count += 1

        # Evidence tier
        tier = fm.get("evidence_tier", "unclassified")
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

    # Sync per-node evaluation_status and evidence_tier from node files into manifest

    for manifest_node in manifest.get("nodes", []):
        node_id = manifest_node.get("id")
        if node_id in node_fm_by_id:
            fm = node_fm_by_id[node_id]
            if "evaluation_status" in fm:
                manifest_node["evaluation_status"] = fm["evaluation_status"]
            if "evidence_tier" in fm:
                manifest_node["evidence_tier"] = fm["evidence_tier"]
            if "quarantined" in fm:
                manifest_node["quarantined"] = fm["quarantined"]

    # Edge count from manifest (edges are authoritative in manifest, not in node files)
    total_edges = len(manifest.get("edges", []))

    stats = {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_unique_pmids": len(all_pmids),
        "evaluation_passed": eval_passed,
        "evaluation_failed": eval_failed,
        "evidence_tier_distribution": tier_distribution,
        "total_nct_ids": len(all_nct_ids),
        "total_chembl_ids": len(all_chembl_ids),
        "quarantined_nodes": quarantined_count,
        "active_nodes": total_nodes - quarantined_count,
    }

    # Include PMID ledger stats if ledger exists
    ledger_path = os.path.join(kg_folder, "_pmid_ledger.json")
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as lfh:
                ledger = json.load(lfh)
            lstats = ledger.get("statistics", {})
            stats["ledger_total"] = lstats.get("total", 0)
            stats["ledger_irrelevant"] = lstats.get("irrelevant", 0)
        except Exception:
            pass

    if args.dry_run:
        json.dump(stats, sys.stdout, indent=2)
        print()
    else:
        manifest["statistics"] = stats
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(manifest_path), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
                json.dump(manifest, tmp_fh, ensure_ascii=False, indent=2)
                tmp_fh.write("\n")
            os.replace(tmp_path, manifest_path)
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
        json.dump(stats, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
