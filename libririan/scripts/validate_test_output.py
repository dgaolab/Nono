#!/usr/bin/env python3
"""Validate test output from a --test mode KG build against expected fixtures.

Usage:
    python3 scripts/validate_test_output.py --kg tests/output/KG_Melatonin_Circadian
    python3 scripts/validate_test_output.py --kg tests/output/KG_Melatonin_Circadian --expected tests/fixtures/expected/expected_checks.json

Checks:
  1. Required files exist
  2. Manifest schema validation (delegates to validate_manifest.py)
  3. Ledger schema validation (delegates to pmid_ledger.py validate)
  4. Node count in expected range
  5. PMID assignment correctness
  6. Evidence tier correctness (deterministic from classify_evidence_tier.py)
  7. Evaluation completeness
  8. Quarantine correctness
  9. Edge validity
 10. Ledger integrity (all mock PMIDs tracked)

Exit codes: 0 = all pass, 1 = failures found.
"""

import argparse
import json
import os
import subprocess
import sys


def _find_project_root() -> str:
    """Walk up from this script to find the project root (contains schemas/)."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(d, "schemas")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(__file__))


PROJECT_ROOT = _find_project_root()


class CheckResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if self.detail:
            msg += f" — {self.detail}"
        return msg


def load_json(path: str) -> dict | list | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_required_files(kg_folder: str, expected: dict) -> list[CheckResult]:
    """Check that all required output files exist."""
    results = []
    required = expected.get("structural", {}).get("required_files", [])
    for fname in required:
        path = os.path.join(kg_folder, fname)
        exists = os.path.exists(path)
        results.append(CheckResult(
            f"file_exists:{fname}",
            exists,
            "" if exists else f"missing: {path}"
        ))
    # Also check nodes/ directory has files
    nodes_dir = os.path.join(kg_folder, "nodes")
    has_nodes = os.path.isdir(nodes_dir) and len(os.listdir(nodes_dir)) > 0
    results.append(CheckResult(
        "file_exists:nodes/",
        has_nodes,
        "" if has_nodes else "nodes/ directory empty or missing"
    ))
    return results


def check_manifest_schema(kg_folder: str) -> CheckResult:
    """Delegate to validate_manifest.py."""
    manifest_path = os.path.join(kg_folder, "manifest.json")
    script = os.path.join(PROJECT_ROOT, "scripts", "validate_manifest.py")
    try:
        result = subprocess.run(
            [sys.executable, script, manifest_path],
            capture_output=True, text=True, timeout=30
        )
        output = json.loads(result.stdout) if result.stdout.strip() else {}
        valid = output.get("valid", False)
        detail = "" if valid else str(output.get("errors", []))
        return CheckResult("manifest_schema", valid, detail)
    except Exception as e:
        return CheckResult("manifest_schema", False, str(e))


def check_ledger_schema(kg_folder: str) -> CheckResult:
    """Delegate to pmid_ledger.py validate."""
    script = os.path.join(PROJECT_ROOT, "scripts", "pmid_ledger.py")
    try:
        result = subprocess.run(
            [sys.executable, script, "validate", kg_folder],
            capture_output=True, text=True, timeout=30
        )
        passed = result.returncode == 0
        detail = "" if passed else result.stderr.strip() or result.stdout.strip()
        return CheckResult("ledger_schema", passed, detail)
    except Exception as e:
        return CheckResult("ledger_schema", False, str(e))


def check_node_count(manifest: dict, expected: dict) -> CheckResult:
    """Verify node count is in expected range."""
    nodes = manifest.get("nodes", [])
    count = len(nodes)
    nc = expected.get("node_count", {})
    lo, hi = nc.get("min", 0), nc.get("max", 999)
    passed = lo <= count <= hi
    return CheckResult(
        "node_count",
        passed,
        f"{count} nodes (expected {lo}-{hi})"
    )


def check_pmid_assignments(manifest: dict, ledger: dict, expected: dict) -> list[CheckResult]:
    """Verify PMID assignment correctness per expected_checks."""
    results = []
    pmid_exp = expected.get("pmid_expectations", {})

    # Build a map: pmid -> list of (node_id, quarantined)
    pmid_to_nodes: dict[str, list[tuple[str, bool]]] = {}
    for node in manifest.get("nodes", []):
        nid = node.get("id", "")
        q = node.get("quarantined", False)
        for pmid in node.get("pubmed_ids", []):
            pmid_to_nodes.setdefault(str(pmid), []).append((nid, q))

    for pmid, exp in pmid_exp.items():
        assigned_nodes = pmid_to_nodes.get(pmid, [])
        active_nodes = [(nid, q) for nid, q in assigned_nodes if not q]

        if exp.get("must_be_assigned"):
            passed = len(active_nodes) > 0
            results.append(CheckResult(
                f"pmid_assigned:{pmid}",
                passed,
                f"in {len(active_nodes)} active node(s)" if passed else "not assigned to any active node"
            ))

        if exp.get("must_be_assigned") is False:
            # Should NOT be in any active (non-quarantined) node
            q_check = exp.get("quarantine_if_assigned", False)
            if q_check:
                passed = len(active_nodes) == 0
                results.append(CheckResult(
                    f"pmid_not_active:{pmid}",
                    passed,
                    "correctly excluded/quarantined" if passed else f"found in {len(active_nodes)} active node(s)"
                ))
            else:
                passed = len(assigned_nodes) == 0
                results.append(CheckResult(
                    f"pmid_unassigned:{pmid}",
                    passed,
                    "correctly unassigned" if passed else f"found in {len(assigned_nodes)} node(s)"
                ))

    return results


def check_evidence_tiers(manifest: dict, ledger: dict, expected: dict) -> list[CheckResult]:
    """Verify evidence tiers are correct (deterministic from classify_evidence_tier.py)."""
    results = []
    pmid_exp = expected.get("pmid_expectations", {})
    ledger_entries = ledger.get("entries", {}) if ledger else {}

    # Build set of PMIDs that are actually assigned to nodes (classify script only processes these)
    manifest_pmids = set()
    for node in manifest.get("nodes", []):
        for p in node.get("pubmed_ids", []):
            manifest_pmids.add(str(p))

    for pmid, exp in pmid_exp.items():
        expected_tier = exp.get("expected_tier")
        if not expected_tier:
            continue
        entry = ledger_entries.get(pmid, {})
        actual_tier = entry.get("evidence_tier")
        if actual_tier is None:
            # Tier not set in ledger. Several valid reasons:
            disp = entry.get("disposition", "missing")
            # 1. PMID was irrelevant/failed — never classified
            if disp in ("irrelevant", "failed"):
                results.append(CheckResult(
                    f"evidence_tier:{pmid}",
                    True,
                    f"disposition={disp}, tier check skipped"
                ))
                continue
            # 2. PMID not assigned to any node — classify script only processes node PMIDs
            if pmid not in manifest_pmids:
                results.append(CheckResult(
                    f"evidence_tier:{pmid}",
                    True,
                    f"not assigned to any node, tier not computed by classify script"
                ))
                continue
            # 3. Expected tier is "unclassified" — classify script skips writing "unclassified" to ledger
            if expected_tier == "unclassified":
                results.append(CheckResult(
                    f"evidence_tier:{pmid}",
                    True,
                    f"None in ledger (classify script skips writing 'unclassified')"
                ))
                continue
        passed = actual_tier == expected_tier
        results.append(CheckResult(
            f"evidence_tier:{pmid}",
            passed,
            f"{actual_tier}" if passed else f"expected {expected_tier}, got {actual_tier}"
        ))

    return results


def check_evaluation_completeness(manifest: dict, expected: dict) -> list[CheckResult]:
    """Verify all nodes have been evaluated (no pending status)."""
    results = []
    eval_exp = expected.get("evaluation", {})

    if eval_exp.get("no_pending_evaluation_status"):
        pending = [n["id"] for n in manifest.get("nodes", [])
                   if n.get("evaluation_status") == "pending"]
        passed = len(pending) == 0
        results.append(CheckResult(
            "no_pending_evaluations",
            passed,
            "" if passed else f"pending: {pending}"
        ))

    if eval_exp.get("all_nodes_evaluated"):
        evaluated = [n for n in manifest.get("nodes", [])
                     if n.get("evaluation_status") in ("passed", "failed")]
        total = len(manifest.get("nodes", []))
        passed = len(evaluated) == total
        results.append(CheckResult(
            "all_nodes_evaluated",
            passed,
            f"{len(evaluated)}/{total} evaluated"
        ))

    return results


def check_edges(manifest: dict) -> list[CheckResult]:
    """Verify all edges reference existing node IDs."""
    results = []
    node_ids = {n["id"] for n in manifest.get("nodes", [])}
    bad_edges = []
    for edge in manifest.get("edges", []):
        src, tgt = edge.get("source"), edge.get("target")
        if src not in node_ids or tgt not in node_ids:
            bad_edges.append(f"{src}->{tgt}")
    passed = len(bad_edges) == 0
    results.append(CheckResult(
        "edge_validity",
        passed,
        "" if passed else f"invalid edges: {bad_edges}"
    ))
    return results


def check_all_nodes_have_references(manifest: dict) -> CheckResult:
    """Every node must have at least one PMID or external_id."""
    no_refs = []
    for node in manifest.get("nodes", []):
        pmids = node.get("pubmed_ids", [])
        ext = node.get("external_ids", [])
        if len(pmids) == 0 and len(ext) == 0:
            no_refs.append(node.get("id", "?"))
    passed = len(no_refs) == 0
    return CheckResult(
        "all_nodes_have_references",
        passed,
        "" if passed else f"nodes without refs: {no_refs}"
    )


def check_search_profile(manifest: dict) -> CheckResult:
    """BUILD must persist search_profile (consumed by preflight.py and UPDATE searches)."""
    profile = manifest.get("search_profile")
    if not isinstance(profile, dict):
        return CheckResult("search_profile", False, "search_profile missing from manifest")
    missing = [k for k in ("breadth", "sub_queries") if not profile.get(k)]
    if missing:
        return CheckResult("search_profile", False,
                           f"search_profile missing fields: {missing}")
    return CheckResult("search_profile", True)


def check_ledger_integrity(ledger: dict, expected: dict) -> list[CheckResult]:
    """Verify all mock PMIDs are tracked in the ledger."""
    results = []
    ledger_exp = expected.get("ledger", {})
    entries = ledger.get("entries", {}) if ledger else {}

    all_pmids = ledger_exp.get("all_mock_pmids_present", [])
    missing = [p for p in all_pmids if p not in entries]
    passed = len(missing) == 0
    results.append(CheckResult(
        "ledger_all_pmids_tracked",
        passed,
        f"{len(entries)} entries" if passed else f"missing from ledger: {missing}"
    ))

    expected_total = ledger_exp.get("total_pmids_tracked")
    if expected_total is not None:
        actual = len(entries)
        # Allow >= because remediation may add PMIDs
        passed = actual >= expected_total
        results.append(CheckResult(
            "ledger_total_count",
            passed,
            f"{actual} tracked (expected >= {expected_total})"
        ))

    return results


def main():
    parser = argparse.ArgumentParser(description="Validate --test mode KG output.")
    parser.add_argument("--kg", required=True, help="Path to the test KG output folder")
    parser.add_argument(
        "--expected",
        default=os.path.join(PROJECT_ROOT, "tests", "fixtures", "expected", "expected_checks.json"),
        help="Path to expected_checks.json"
    )
    args = parser.parse_args()

    kg_folder = args.kg
    if not os.path.isdir(kg_folder):
        print(f"Error: KG folder not found: {kg_folder}", file=sys.stderr)
        sys.exit(1)

    expected = load_json(args.expected)
    if expected is None:
        print(f"Error: expected checks not found: {args.expected}", file=sys.stderr)
        sys.exit(1)

    manifest = load_json(os.path.join(kg_folder, "manifest.json"))
    ledger = load_json(os.path.join(kg_folder, "_pmid_ledger.json"))

    all_results: list[CheckResult] = []

    # 1. Required files
    all_results.extend(check_required_files(kg_folder, expected))

    # 2-3. Schema validation (only if files exist)
    if manifest is not None:
        all_results.append(check_manifest_schema(kg_folder))
    else:
        all_results.append(CheckResult("manifest_schema", False, "manifest.json not found"))

    if ledger is not None:
        all_results.append(check_ledger_schema(kg_folder))
    else:
        all_results.append(CheckResult("ledger_schema", False, "_pmid_ledger.json not found"))

    # 4-10. Content checks (require manifest)
    if manifest is not None:
        all_results.append(check_node_count(manifest, expected))
        all_results.extend(check_pmid_assignments(manifest, ledger, expected))
        all_results.extend(check_evidence_tiers(manifest, ledger, expected))
        all_results.extend(check_evaluation_completeness(manifest, expected))
        all_results.extend(check_edges(manifest))
        all_results.append(check_all_nodes_have_references(manifest))
        all_results.append(check_search_profile(manifest))

    if ledger is not None:
        all_results.extend(check_ledger_integrity(ledger, expected))

    # Report
    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)

    print(f"\n=== Test Validation: {passed} passed, {failed} failed ===\n")
    for r in all_results:
        print(str(r))
    print()

    if failed > 0:
        print(f"RESULT: {failed} check(s) FAILED")
        sys.exit(1)
    else:
        print("RESULT: All checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
