#!/usr/bin/env python3
"""`nono-librarian finalize` — deterministic finish pipeline for an assembled KG (no model)."""
import argparse
import datetime
import json
import os
import subprocess
import sys

from nono_librarian.cli import verify
from nono_librarian.lib import pubmed


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(*args):
    subprocess.run([sys.executable, "-m", *args], check=True)


def build_run_record(*, kg_name, mode, version, timestamp, nodes_created,
                     refs_added, passed, failed, since_date=None):
    run_id = timestamp.replace(":", "").replace("-", "") + f"-v{version}"
    return {"run_id": run_id, "kg_name": kg_name, "mode": mode, "timestamp": timestamp,
            "version": version, "since_date": since_date,
            "nodes_created": nodes_created, "nodes_revised": [],
            "refs_added": refs_added, "refs_failed": [],
            "eval_summary": {"evaluated": len(nodes_created), "passed": passed, "failed": failed}}


def _ledger_used_batch(candidates_path):
    if not candidates_path or not os.path.exists(candidates_path):
        return []
    with open(candidates_path, encoding="utf-8") as fh:
        cands = json.load(fh)
    batch = []
    for a in cands.get("articles", []):
        m = a.get("metadata", {})
        batch.append({"pmid": a["pmid"], "disposition": "used", "title": m.get("title"),
                      "authors": m.get("authors", []), "journal": m.get("journal"),
                      "year": m.get("year"),
                      "publication_types": m.get("publication_types", [])})
    return batch


def _refs_added(kg_folder, node_ids):
    """Group node IDs by the PMIDs they cite, from the manifest."""
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    refs = {}
    for n in manifest["nodes"]:
        if node_ids and n["id"] not in node_ids:
            continue
        for pmid in n.get("pubmed_ids", []):
            refs.setdefault(pmid, set()).add(n["id"])
    return [{"pmid": p, "nodes": sorted(ns)}
            for p, ns in sorted(refs.items(), key=lambda kv: int(kv[0]))]


def finalize_kg(kg_folder, *, mode, version, candidates_path=None,
                since_date=None, overview_text=None, run_subprocess=True):
    candidates_path = candidates_path or os.path.join(kg_folder, "_candidates.json")
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    kg_name = manifest["kg_name"]
    node_ids = [n["id"] for n in manifest["nodes"]]

    # 1. ledger: record used PMIDs
    if not os.path.exists(os.path.join(kg_folder, "_pmid_ledger.json")):
        _run("nono_librarian.cli.pmid_ledger", "init", kg_folder, "--kg-name", kg_name)
    batch = _ledger_used_batch(candidates_path)
    if batch:
        bpath = os.path.join(kg_folder, "_build_ledger_batch.json")
        with open(bpath, "w", encoding="utf-8") as fh:
            json.dump(batch, fh)
        _run("nono_librarian.cli.pmid_ledger", "batch-add", kg_folder, "--input", bpath)
        os.remove(bpath)

    # 2. evidence tiers + literature stamping
    _run("nono_librarian.cli.classify_evidence_tier", kg_folder, "--update-ledger")
    _run("nono_librarian.cli.stamp_literature", kg_folder)

    # 3. guardrailed evaluation writeback from agent verdicts
    judgments_path = os.path.join(kg_folder, "_judgments.json")
    judgments = verify.load_judgments(kg_folder, None) if os.path.exists(
        judgments_path) else {}
    entries = verify.verify_kg(kg_folder, judgments,
                               fetch_metadata=pubmed.fetch_metadata,
                               fetch_full_text=pubmed.fetch_full_text)
    passed = sum(1 for e in entries if e["overall_status"] == "passed")
    failed = len(entries) - passed

    # 4. quarantine, index, stats, validate, embeddings (non-fatal)
    _run("nono_librarian.cli.enforce_quarantine", kg_folder)
    if overview_text is not None:
        _run("nono_librarian.cli.generate_index", kg_folder, "--overview-text", overview_text)
    else:
        _run("nono_librarian.cli.generate_index", kg_folder)
    _run("nono_librarian.cli.update_manifest_stats", kg_folder)
    _run("nono_librarian.cli.validate_manifest", os.path.join(kg_folder, "manifest.json"))
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.build_embeddings", kg_folder],
                   check=False)

    # 5. run-record + digest (digest non-fatal), then log
    run_record = build_run_record(
        kg_name=kg_name, mode=mode, version=version, timestamp=_now_iso(),
        nodes_created=node_ids, refs_added=_refs_added(kg_folder, node_ids),
        passed=passed, failed=failed, since_date=since_date)
    runs_dir = os.path.join(kg_folder, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    rr_path = os.path.join(runs_dir, run_record["run_id"] + ".json")
    with open(rr_path, "w", encoding="utf-8") as fh:
        json.dump(run_record, fh, indent=2)
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.render_digest",
                    kg_folder, "--run-record", rr_path], check=False)
    _run("nono_librarian.cli.append_log", kg_folder, "--op", mode,
         "--summary", f"Local {mode.upper()}: {len(node_ids)} nodes, {passed} passed, {failed} failed.")
    return {"nodes": len(node_ids), "passed": passed, "failed": failed,
            "kg_folder": kg_folder, "run_id": run_record["run_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian finalize",
                                     description="Run the deterministic KG finish pipeline")
    parser.add_argument("kg_folder")
    parser.add_argument("--mode", choices=["build", "update"], default="build")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--since", default=None)
    parser.add_argument("--overview-text", default=None)
    args = parser.parse_args(argv)
    summary = finalize_kg(args.kg_folder, mode=args.mode, version=args.version,
                          since_date=args.since, overview_text=args.overview_text)
    print(f"{args.mode.upper()} finalized: {summary['nodes']} nodes "
          f"({summary['passed']} passed, {summary['failed']} failed) → {args.kg_folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
