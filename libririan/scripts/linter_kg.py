#!/usr/bin/env python3
"""Structural health checks for a knowledge graph.

Usage:
    python3 scripts/linter_kg.py <kg_folder> [--severity error|warning|info] \
        [--checks check1,check2,...] [--fix]

Runs 10 structural checks and pre-computes semantic check candidates
for the LLM phase. Outputs JSON to stdout, diagnostics to stderr.

Exit codes: 0 = clean/info only, 1 = warnings, 2 = errors.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


# ---------------------------------------------------------------------------
# KGLinter
# ---------------------------------------------------------------------------

class KGLinter:
    """Load KG data once and run structural health checks."""

    ALL_CHECKS = [
        "orphan_nodes",
        "under_referenced",
        "dangling_edges",
        "file_manifest_drift",
        "stats_drift",
        "ledger_drift",
        "evaluation_gaps",
        "quarantine_drift",
        "evidence_tier_imbalance",
        "tag_coverage_gaps",
        "duplicate_entities",
    ]

    def __init__(self, kg_folder: str):
        self.kg_folder = os.path.abspath(kg_folder)
        self.findings: list[dict] = []
        self.fixed_count = 0

        # Load manifest
        manifest_path = os.path.join(self.kg_folder, "manifest.json")
        if not os.path.exists(manifest_path):
            print(f"Error: manifest.json not found in {kg_folder}", file=sys.stderr)
            sys.exit(1)
        with open(manifest_path, "r", encoding="utf-8") as fh:
            self.manifest = json.load(fh)

        # Build node index from manifest
        self.manifest_nodes = {n["id"]: n for n in self.manifest.get("nodes", []) if "id" in n}
        self.node_ids = set(self.manifest_nodes.keys())
        self.edges = self.manifest.get("edges", [])

        # Load ledger (optional)
        ledger_path = os.path.join(self.kg_folder, "_pmid_ledger.json")
        self.ledger = None
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as fh:
                    self.ledger = json.load(fh)
            except Exception as e:
                print(f"Warning: could not load ledger: {e}", file=sys.stderr)

        # Load evaluation log (optional)
        eval_path = os.path.join(self.kg_folder, "_evaluation_log.json")
        self.eval_log = None
        if os.path.exists(eval_path):
            try:
                with open(eval_path, "r", encoding="utf-8") as fh:
                    self.eval_log = json.load(fh)
            except Exception:
                pass

        # Discover node files on disk
        self.disk_files = set(glob.glob(os.path.join(self.kg_folder, "nodes", "*.md")))

        # Parse node files (frontmatter only)
        self.node_fm: dict[str, dict] = {}  # node_id -> frontmatter
        for file_path in sorted(self.disk_files):
            try:
                fm, _ = parse(file_path)
                nid = fm.get("id")
                if nid:
                    self.node_fm[nid] = fm
            except Exception as e:
                print(f"Warning: could not parse {file_path}: {e}", file=sys.stderr)

    def _add(self, check_id: str, severity: str, message: str,
             category: str = "structural", node_id: str | None = None,
             details: dict | None = None, fixable: bool = False):
        self.findings.append({
            "check_id": check_id,
            "severity": severity,
            "category": category,
            "message": message,
            "node_id": node_id,
            "details": details or {},
            "fixable": fixable,
        })

    # ------------------------------------------------------------------
    # Check 1: Orphan nodes (no inbound edges)
    # ------------------------------------------------------------------
    def check_orphan_nodes(self):
        inbound: Counter = Counter()
        for edge in self.edges:
            target = edge.get("target")
            if target:
                inbound[target] += 1

        for nid in self.node_ids:
            if inbound[nid] == 0:
                outbound = sum(1 for e in self.edges if e.get("source") == nid)
                self._add("orphan_nodes", "warning",
                          f"Node {nid} has zero inbound edges",
                          node_id=nid,
                          details={"outbound_count": outbound,
                                   "title": self.manifest_nodes[nid].get("title", "")})

    # ------------------------------------------------------------------
    # Check 2: Under-referenced nodes
    # ------------------------------------------------------------------
    def check_under_referenced(self):
        for nid, node in self.manifest_nodes.items():
            pmid_count = len(node.get("pubmed_ids", []))
            ext_count = len(node.get("external_ids", []))
            total_refs = pmid_count + ext_count

            if total_refs == 0:
                self._add("under_referenced", "error",
                          f"Node {nid} has zero references (violates KG rules)",
                          node_id=nid,
                          details={"pmid_count": 0, "external_id_count": 0})
            elif pmid_count == 1 and ext_count == 0:
                self._add("under_referenced", "warning",
                          f"Node {nid} has only 1 PMID (fragile evidence)",
                          node_id=nid,
                          details={"pmid_count": 1, "external_id_count": 0})

    # ------------------------------------------------------------------
    # Check 3: Dangling edges
    # ------------------------------------------------------------------
    def check_dangling_edges(self):
        for i, edge in enumerate(self.edges):
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src not in self.node_ids:
                self._add("dangling_edges", "error",
                          f"Edge #{i} source '{src}' not in node list",
                          details={"edge_index": i, "source": src, "target": tgt,
                                   "missing": "source"})
            if tgt not in self.node_ids:
                self._add("dangling_edges", "error",
                          f"Edge #{i} target '{tgt}' not in node list",
                          details={"edge_index": i, "source": src, "target": tgt,
                                   "missing": "target"})

        # Cross-KG edges: only check local_node
        for edge in self.manifest.get("cross_kg_edges", []):
            local = edge.get("local_node", "")
            if local and local not in self.node_ids:
                self._add("dangling_edges", "error",
                          f"Cross-KG edge local_node '{local}' not in node list",
                          details={"local_node": local,
                                   "remote_kg": edge.get("remote_kg", ""),
                                   "remote_node": edge.get("remote_node", "")})

    # ------------------------------------------------------------------
    # Check 4: File-manifest drift
    # ------------------------------------------------------------------
    def check_file_manifest_drift(self):
        # Direction A: manifest entries with no file on disk
        for nid, node in self.manifest_nodes.items():
            file_rel = node.get("file", "")
            if file_rel:
                full = os.path.join(self.kg_folder, file_rel)
                if not os.path.exists(full):
                    self._add("file_manifest_drift", "error",
                              f"Manifest references {file_rel} but file is missing",
                              node_id=nid,
                              details={"direction": "manifest_missing_file",
                                       "file": file_rel})

        # Direction B: files on disk not in manifest
        manifest_files = set()
        for node in self.manifest_nodes.values():
            f = node.get("file", "")
            if f:
                manifest_files.add(os.path.abspath(os.path.join(self.kg_folder, f)))

        for disk_file in self.disk_files:
            abs_disk = os.path.abspath(disk_file)
            if abs_disk not in manifest_files:
                rel = os.path.relpath(disk_file, self.kg_folder)
                # Get the node ID from the orphan file's frontmatter
                fm_nid = None
                try:
                    orphan_fm, _ = parse(disk_file)
                    fm_nid = orphan_fm.get("id")
                except Exception:
                    pass
                self._add("file_manifest_drift", "error",
                          f"File {rel} exists on disk but is not in manifest",
                          node_id=fm_nid if isinstance(fm_nid, str) else None,
                          details={"direction": "file_missing_from_manifest",
                                   "file": rel},
                          fixable=True)

    # ------------------------------------------------------------------
    # Check 5: Stats drift
    # ------------------------------------------------------------------
    def check_stats_drift(self):
        stats = self.manifest.get("statistics", {})

        computed_nodes = len(self.node_fm)
        computed_edges = len(self.edges)

        # Unique PMIDs from node frontmatter
        all_pmids: set[str] = set()
        eval_passed = 0
        eval_failed = 0
        tier_dist: Counter = Counter()

        for fm in self.node_fm.values():
            for entry in fm.get("pubmed_ids", []):
                pmid = entry.get("pmid") if isinstance(entry, dict) else str(entry)
                if pmid:
                    all_pmids.add(str(pmid))
            es = fm.get("evaluation_status", "pending")
            if es == "passed":
                eval_passed += 1
            elif es == "failed":
                eval_failed += 1
            tier = fm.get("evidence_tier", "unclassified")
            tier_dist[tier] += 1

        drift_fields = []
        if stats.get("total_nodes") != computed_nodes:
            drift_fields.append(("total_nodes", stats.get("total_nodes"), computed_nodes))
        if stats.get("total_edges") != computed_edges:
            drift_fields.append(("total_edges", stats.get("total_edges"), computed_edges))
        if stats.get("total_unique_pmids") != len(all_pmids):
            drift_fields.append(("total_unique_pmids", stats.get("total_unique_pmids"), len(all_pmids)))
        if stats.get("evaluation_passed") != eval_passed:
            drift_fields.append(("evaluation_passed", stats.get("evaluation_passed"), eval_passed))
        if stats.get("evaluation_failed") != eval_failed:
            drift_fields.append(("evaluation_failed", stats.get("evaluation_failed"), eval_failed))

        # Evidence tier distribution
        manifest_tier_dist = stats.get("evidence_tier_distribution")
        if manifest_tier_dist is not None and dict(tier_dist) != manifest_tier_dist:
            drift_fields.append(("evidence_tier_distribution",
                                 manifest_tier_dist, dict(tier_dist)))

        # Quarantine stats
        computed_quarantined = sum(1 for fm in self.node_fm.values()
                                   if fm.get("quarantined", False))
        if (stats.get("quarantined_nodes") is not None
                and stats.get("quarantined_nodes") != computed_quarantined):
            drift_fields.append(("quarantined_nodes", stats.get("quarantined_nodes"), computed_quarantined))
        computed_active = computed_nodes - computed_quarantined
        if (stats.get("active_nodes") is not None
                and stats.get("active_nodes") != computed_active):
            drift_fields.append(("active_nodes", stats.get("active_nodes"), computed_active))

        for field, manifest_val, computed_val in drift_fields:
            self._add("stats_drift", "warning",
                      f"statistics.{field}: manifest={manifest_val}, actual={computed_val}",
                      details={"field": field, "manifest_value": manifest_val,
                               "computed_value": computed_val},
                      fixable=True)

    # ------------------------------------------------------------------
    # Check 6: Ledger drift
    # ------------------------------------------------------------------
    def check_ledger_drift(self):
        if self.ledger is None:
            return

        entries = self.ledger.get("entries", {})
        ledger_used = {pmid for pmid, e in entries.items()
                       if e.get("disposition") == "used"}

        # PMIDs in node files
        node_pmids: set[str] = set()
        for fm in self.node_fm.values():
            for entry in fm.get("pubmed_ids", []):
                pmid = entry.get("pmid") if isinstance(entry, dict) else str(entry)
                if pmid:
                    node_pmids.add(str(pmid))

        # Direction A: PMIDs in nodes but not in ledger at all
        all_ledger_pmids = set(entries.keys())
        missing_from_ledger = node_pmids - all_ledger_pmids
        for pmid in sorted(missing_from_ledger):
            self._add("ledger_drift", "warning",
                      f"PMID {pmid} in node files but not tracked in ledger",
                      details={"direction": "node_not_in_ledger", "pmid": pmid},
                      fixable=True)

        # Direction B: ledger "used" but not in any node
        orphaned_in_ledger = ledger_used - node_pmids
        for pmid in sorted(orphaned_in_ledger):
            self._add("ledger_drift", "warning",
                      f"PMID {pmid} marked 'used' in ledger but not in any node",
                      details={"direction": "ledger_used_not_in_node", "pmid": pmid},
                      fixable=True)

    # ------------------------------------------------------------------
    # Check 7: Evaluation gaps
    # ------------------------------------------------------------------
    def check_evaluation_gaps(self):
        pending = []
        for nid, node in self.manifest_nodes.items():
            if node.get("evaluation_status") == "pending":
                pending.append(nid)

        if not pending:
            return

        total = len(self.manifest_nodes)
        ratio = len(pending) / max(1, total)
        severity = "error" if ratio > 0.2 else "warning"

        self._add("evaluation_gaps", severity,
                  f"{len(pending)}/{total} nodes ({ratio:.0%}) still pending evaluation",
                  details={"pending_nodes": pending, "pending_count": len(pending),
                           "total_count": total, "ratio": round(ratio, 3)})

    # ------------------------------------------------------------------
    # Check 7b: Quarantine drift
    # ------------------------------------------------------------------
    def check_quarantine_drift(self):
        failed_not_quarantined = []
        quarantined_not_failed = []

        for nid, node in self.manifest_nodes.items():
            eval_status = node.get("evaluation_status", "pending")
            is_quarantined = node.get("quarantined", False)

            if eval_status == "failed" and not is_quarantined:
                failed_not_quarantined.append(nid)
            elif is_quarantined and eval_status != "failed":
                quarantined_not_failed.append(nid)

        if failed_not_quarantined:
            self._add("quarantine_drift", "warning",
                      f"{len(failed_not_quarantined)} node(s) failed evaluation but are not quarantined: "
                      f"{', '.join(sorted(failed_not_quarantined))}",
                      details={"failed_not_quarantined": sorted(failed_not_quarantined),
                               "count": len(failed_not_quarantined)},
                      fixable=True)

        if quarantined_not_failed:
            self._add("quarantine_drift", "info",
                      f"{len(quarantined_not_failed)} node(s) are quarantined but evaluation_status is not 'failed': "
                      f"{', '.join(sorted(quarantined_not_failed))}",
                      details={"quarantined_not_failed": sorted(quarantined_not_failed),
                               "count": len(quarantined_not_failed)})

    # ------------------------------------------------------------------
    # Check 8: Evidence tier imbalance
    # ------------------------------------------------------------------
    def check_evidence_tier_imbalance(self):
        tier_counts: Counter = Counter()
        for node in self.manifest_nodes.values():
            tier = node.get("evidence_tier", "unclassified")
            tier_counts[tier] += 1

        total = sum(tier_counts.values())
        if total == 0:
            return

        weak = tier_counts.get("opinion", 0) + tier_counts.get("review", 0)
        weak_pct = weak / total

        if weak_pct > 0.5:
            self._add("evidence_tier_imbalance", "info",
                      f"opinion+review nodes = {weak_pct:.0%} of total ({weak}/{total})",
                      details={"distribution": dict(tier_counts),
                               "opinion_review_pct": round(weak_pct, 3)})

        strong = tier_counts.get("meta_analysis", 0) + tier_counts.get("rct", 0)
        if strong == 0 and total > 10:
            self._add("evidence_tier_imbalance", "info",
                      f"No meta-analysis or RCT nodes in a KG with {total} nodes",
                      details={"distribution": dict(tier_counts)})

    # ------------------------------------------------------------------
    # Check 9: Tag coverage gaps
    # ------------------------------------------------------------------
    def check_tag_coverage_gaps(self):
        tag_counts: Counter = Counter()
        untagged = []

        for nid, node in self.manifest_nodes.items():
            tags = node.get("tags", [])
            if not tags:
                untagged.append(nid)
            for t in tags:
                tag_counts[t] += 1

        sparse = {t: c for t, c in tag_counts.items() if c == 1}
        if sparse:
            self._add("tag_coverage_gaps", "info",
                      f"{len(sparse)} tag(s) appear on only 1 node: {', '.join(sorted(sparse))}",
                      details={"sparse_tags": sparse})

        if untagged:
            self._add("tag_coverage_gaps", "info",
                      f"{len(untagged)} node(s) have no tags: {', '.join(untagged)}",
                      details={"untagged_nodes": untagged})

    # ------------------------------------------------------------------
    # Check 10: Duplicate entities
    # ------------------------------------------------------------------
    def check_duplicate_entities(self):
        entity_map: dict[str, list[dict]] = defaultdict(list)

        for nid, node in self.manifest_nodes.items():
            for ent in node.get("entities", []):
                norm_id = ent.get("normalized_id", "")
                if norm_id and not norm_id.startswith("?"):
                    entity_map[norm_id].append({
                        "node_id": nid,
                        "name": ent.get("name", ""),
                        "type": ent.get("type", ""),
                    })

        for norm_id, locations in entity_map.items():
            names = set(loc["name"] for loc in locations)
            if len(names) > 1:
                self._add("duplicate_entities", "info",
                          f"Entity {norm_id} has inconsistent names: {', '.join(sorted(names))}",
                          details={"normalized_id": norm_id, "variants": locations})

    # ------------------------------------------------------------------
    # Semantic check candidates (pre-computed for LLM phase)
    # ------------------------------------------------------------------
    def compute_semantic_candidates(self) -> dict:
        candidates: dict[str, list] = {
            "high_similarity_pairs": [],
            "frequent_entities_without_nodes": [],
            "old_pmid_nodes": [],
        }

        # Build edge set for quick lookup
        edge_set: set[tuple[str, str]] = set()
        for edge in self.edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            edge_set.add((src, tgt))
            edge_set.add((tgt, src))  # bidirectional check

        # High similarity pairs: share 2+ keywords or 2+ entities, no edge
        node_list = list(self.manifest_nodes.items())
        for i in range(len(node_list)):
            nid_a, node_a = node_list[i]
            kw_a = set(k.lower() for k in node_a.get("keywords", []))
            ent_a = set(e.get("normalized_id", "") for e in node_a.get("entities", [])
                        if e.get("normalized_id", "") and not e["normalized_id"].startswith("?"))
            for j in range(i + 1, len(node_list)):
                nid_b, node_b = node_list[j]
                if (nid_a, nid_b) in edge_set:
                    continue

                kw_b = set(k.lower() for k in node_b.get("keywords", []))
                ent_b = set(e.get("normalized_id", "") for e in node_b.get("entities", [])
                            if e.get("normalized_id", "") and not e["normalized_id"].startswith("?"))

                shared_kw = kw_a & kw_b
                shared_ent = ent_a & ent_b

                if len(shared_kw) >= 2 or len(shared_ent) >= 2:
                    candidates["high_similarity_pairs"].append({
                        "node_a": nid_a,
                        "node_b": nid_b,
                        "shared_keywords": sorted(shared_kw),
                        "shared_entities": sorted(shared_ent),
                    })

        # Frequent entities without dedicated nodes
        entity_usage: dict[str, list[str]] = defaultdict(list)  # norm_id -> [node_ids]
        for nid, node in self.manifest_nodes.items():
            for ent in node.get("entities", []):
                norm_id = ent.get("normalized_id", "")
                if norm_id and not norm_id.startswith("?"):
                    entity_usage[norm_id].append(nid)

        # Check if entity has a "dedicated" node (title contains entity name)
        node_titles_lower = {nid: node.get("title", "").lower()
                             for nid, node in self.manifest_nodes.items()}

        for norm_id, using_nodes in entity_usage.items():
            if len(using_nodes) < 3:
                continue
            # Get entity name from first occurrence
            ent_name = ""
            for nid, node in self.manifest_nodes.items():
                for ent in node.get("entities", []):
                    if ent.get("normalized_id") == norm_id:
                        ent_name = ent.get("name", "")
                        break
                if ent_name:
                    break

            # Check if any node title contains this entity name
            name_lower = ent_name.lower()
            has_dedicated = any(name_lower in title for title in node_titles_lower.values())

            if not has_dedicated:
                candidates["frequent_entities_without_nodes"].append({
                    "normalized_id": norm_id,
                    "name": ent_name,
                    "referenced_by": using_nodes,
                    "count": len(using_nodes),
                })

        # Old PMID nodes: newest PMID year < current_year - 3
        current_year = datetime.now().year
        cutoff_year = current_year - 3

        for nid, fm in self.node_fm.items():
            newest_year = 0
            for entry in fm.get("pubmed_ids", []):
                if isinstance(entry, dict):
                    # Try to get year from ledger
                    pmid = entry.get("pmid", "")
                    if self.ledger and pmid:
                        ledger_entry = self.ledger.get("entries", {}).get(str(pmid), {})
                        yr = ledger_entry.get("year")
                        if yr and isinstance(yr, int):
                            newest_year = max(newest_year, yr)

            if newest_year > 0 and newest_year < cutoff_year:
                candidates["old_pmid_nodes"].append({
                    "node_id": nid,
                    "title": self.manifest_nodes.get(nid, {}).get("title", ""),
                    "newest_pmid_year": newest_year,
                    "keywords": self.manifest_nodes.get(nid, {}).get("keywords", []),
                })

        return candidates

    # ------------------------------------------------------------------
    # Run checks
    # ------------------------------------------------------------------
    def run(self, checks: list[str] | None = None):
        to_run = checks if checks else self.ALL_CHECKS
        for check_id in to_run:
            method = getattr(self, f"check_{check_id}", None)
            if method:
                method()
            else:
                print(f"Warning: unknown check '{check_id}'", file=sys.stderr)

    # ------------------------------------------------------------------
    # Auto-fix
    # ------------------------------------------------------------------
    def fix(self):
        fixed = 0

        # Fix stats_drift: run update_manifest_stats.py
        stats_findings = [f for f in self.findings
                          if f["check_id"] == "stats_drift" and f["fixable"]]
        if stats_findings:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "update_manifest_stats.py")
            result = subprocess.run(
                ["python3", script, self.kg_folder],
                capture_output=True, text=True)
            if result.returncode == 0:
                fixed += len(stats_findings)
                print(f"Fixed: stats_drift ({len(stats_findings)} fields recomputed)",
                      file=sys.stderr)
            else:
                print(f"Warning: stats fix failed: {result.stderr}", file=sys.stderr)

        # Fix ledger_drift: run pmid_ledger.py sync
        ledger_findings = [f for f in self.findings
                           if f["check_id"] == "ledger_drift" and f["fixable"]]
        if ledger_findings:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "pmid_ledger.py")
            result = subprocess.run(
                ["python3", script, "sync", self.kg_folder],
                capture_output=True, text=True)
            if result.returncode == 0:
                fixed += len(ledger_findings)
                print(f"Fixed: ledger_drift ({len(ledger_findings)} issues synced)",
                      file=sys.stderr)
            else:
                print(f"Warning: ledger sync failed: {result.stderr}", file=sys.stderr)

        # Fix file_manifest_drift: add orphan node files to manifest
        drift_findings = [f for f in self.findings
                          if f["check_id"] == "file_manifest_drift" and f["fixable"]
                          and f.get("details", {}).get("direction") == "file_missing_from_manifest"]
        if drift_findings:
            manifest_path = os.path.join(self.kg_folder, "manifest.json")
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
                added = 0
                for finding in drift_findings:
                    rel_file = finding.get("details", {}).get("file", "")
                    abs_file = os.path.join(self.kg_folder, rel_file)
                    if not os.path.exists(abs_file):
                        continue
                    try:
                        fm, _ = parse(abs_file)
                    except Exception:
                        continue
                    node_id = fm.get("id")
                    if not node_id:
                        continue
                    # Skip if already in manifest (shouldn't happen, but be safe)
                    existing_ids = {n.get("id") for n in manifest.get("nodes", [])}
                    if node_id in existing_ids:
                        continue
                    pmids = []
                    for entry in fm.get("pubmed_ids", []):
                        pmid = entry.get("pmid") if isinstance(entry, dict) else str(entry)
                        if pmid:
                            pmids.append(str(pmid))
                    manifest.setdefault("nodes", []).append({
                        "id": node_id,
                        "title": fm.get("title", ""),
                        "file": rel_file,
                        "tags": fm.get("tags", []),
                        "summary": "",
                        "keywords": [],
                        "pubmed_ids": pmids,
                        "evaluation_status": fm.get("evaluation_status", "pending"),
                        "evidence_tier": fm.get("evidence_tier", "unclassified"),
                    })
                    added += 1
                if added > 0:
                    fd, tmp_path = tempfile.mkstemp(
                        dir=self.kg_folder, suffix=".json.tmp")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
                            json.dump(manifest, tmp_fh, indent=2, ensure_ascii=False)
                            tmp_fh.write("\n")
                        os.replace(tmp_path, manifest_path)
                    except Exception:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise
                    fixed += added
                    print(f"Fixed: file_manifest_drift ({added} orphan nodes added to manifest)",
                          file=sys.stderr)
            except Exception as e:
                print(f"Warning: file_manifest_drift fix failed: {e}", file=sys.stderr)

        # Fix quarantine_drift: quarantine failed-but-not-quarantined nodes
        qd_findings = [f for f in self.findings
                        if f["check_id"] == "quarantine_drift" and f["fixable"]]
        if qd_findings:
            failed_nodes = []
            for f in qd_findings:
                failed_nodes.extend(f.get("details", {}).get("failed_not_quarantined", []))

            if failed_nodes:
                fm_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "update_frontmatter.py")
                fix_count = 0
                for nid in failed_nodes:
                    node_entry = self.manifest_nodes.get(nid, {})
                    node_file = node_entry.get("file", "")
                    if not node_file:
                        continue
                    full_path = os.path.join(self.kg_folder, node_file)
                    if not os.path.exists(full_path):
                        continue
                    result = subprocess.run(
                        ["python3", fm_script, full_path, '{"quarantined": true}'],
                        capture_output=True, text=True)
                    if result.returncode == 0:
                        fix_count += 1

                # Update manifest nodes
                manifest_path = os.path.join(self.kg_folder, "manifest.json")
                try:
                    with open(manifest_path, "r", encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    failed_set = set(failed_nodes)
                    for node in manifest.get("nodes", []):
                        if node.get("id") in failed_set:
                            node["quarantined"] = True
                    fd, tmp_path = tempfile.mkstemp(
                        dir=self.kg_folder, suffix=".json.tmp")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
                            json.dump(manifest, tmp_fh, indent=2, ensure_ascii=False)
                            tmp_fh.write("\n")
                        os.replace(tmp_path, manifest_path)
                    except Exception:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise

                    # Recompute stats
                    stats_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "update_manifest_stats.py")
                    subprocess.run(["python3", stats_script, self.kg_folder],
                                   capture_output=True, text=True)

                    fixed += fix_count
                    print(f"Fixed: quarantine_drift ({fix_count} node(s) quarantined)",
                          file=sys.stderr)
                except Exception as e:
                    print(f"Warning: quarantine_drift fix failed: {e}", file=sys.stderr)

        # Final stats recomputation if any non-quarantine fixes touched the manifest
        if fixed > 0 and not qd_findings:
            stats_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "update_manifest_stats.py")
            subprocess.run(["python3", stats_script, self.kg_folder],
                           capture_output=True, text=True)

        self.fixed_count = fixed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Structural health checks for a KG.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--severity", choices=["error", "warning", "info"],
                        default="info",
                        help="Minimum severity to include (default: info)")
    parser.add_argument("--checks",
                        help="Comma-separated list of check IDs to run (default: all)")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-fix simple issues (stats, ledger drift)")
    parser.add_argument("--summary-only", action="store_true",
                        help="Omit individual findings, return only summary and semantic candidates")
    args = parser.parse_args()

    min_severity = SEVERITY_RANK[args.severity]
    checks = args.checks.split(",") if args.checks else None

    linter = KGLinter(args.kg_folder)
    linter.run(checks)

    if args.fix:
        linter.fix()

    # Compute semantic candidates
    semantic = linter.compute_semantic_candidates()

    # Filter by severity
    findings = [f for f in linter.findings
                if SEVERITY_RANK.get(f["severity"], 0) >= min_severity]

    # Build summary
    error_count = sum(1 for f in findings if f["severity"] == "error")
    warning_count = sum(1 for f in findings if f["severity"] == "warning")
    info_count = sum(1 for f in findings if f["severity"] == "info")

    output = {
        "kg_name": linter.manifest.get("kg_name", os.path.basename(args.kg_folder)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks_run": checks or KGLinter.ALL_CHECKS,
        "summary": {
            "total_findings": len(findings),
            "errors": error_count,
            "warnings": warning_count,
            "info": info_count,
            "fixable": sum(1 for f in findings if f["fixable"]),
            "fixed": linter.fixed_count,
        },
        "semantic_check_candidates": semantic,
    }
    if not args.summary_only:
        output["findings"] = findings

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()

    # Exit code
    if error_count > 0:
        sys.exit(2)
    elif warning_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
