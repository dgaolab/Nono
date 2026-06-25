#!/usr/bin/env python3
"""Claude-free KG evaluator — the deterministic equivalent of evaluate-kg-worker.

Drives the local model (`lib/llm.py`) and NCBI E-utilities (`lib/pubmed.py`) to
re-verify a KG's node claims against primary sources, with no Claude and no MCP.
The orchestrator owns control flow and file writes; the model only judges one
claim against one article at a time, behind the verbatim-quote guardrail in
`lib/evaluate.py`.

Per node, for each cited PMID:
  1. fetch metadata (existence + abstract) via pubmed.fetch_metadata,
  2. fetch PMC full text when available (degrading to abstract-only if PMC is
     down — full text is an enhancement here, not a hard requirement),
  3. ask the model whether the article supports that PMID's `supports` claim,
  4. keep the verdict only if a verbatim quote backs it (the guardrail).
A node passes if any reference supports it (Step E3). Results are written to
`_evaluation_log.json` and merged into node frontmatter + manifest stats via the
existing deterministic scripts.

If the model endpoint is unavailable the run aborts WITHOUT mutating anything —
the same "never mutate on failure" contract as check_retractions.py.

Usage:
    python3 scripts/librarian_evaluate.py <kg_folder> [--nodes id1,id2]
                [--attempts N] [--json]
"""

import argparse
import datetime
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import evaluate, llm, pubmed
from lib.frontmatter import parse as parse_node


def build_source_text(meta, full_text):
    """Concatenate the article text available for verification (abstract + body)."""
    parts = []
    if meta.get("abstract"):
        parts.append(meta["abstract"])
    if full_text:
        parts.append(full_text)
    return "\n\n".join(parts)


def evaluate_node(node_id, frontmatter, *, fetch_metadata, fetch_full_text, chat,
                  attempts=2):
    """Evaluate one node's claims and return its _evaluation_log.json entry.

    Seams (`fetch_metadata`, `fetch_full_text`, `chat`) are injected so this is
    unit-testable with fakes. A model error from ``chat`` propagates — the caller
    aborts the whole run rather than writing a half-finished verdict.
    """
    entries = frontmatter.get("pubmed_ids", []) or []
    pmids = [e["pmid"] for e in entries if e.get("pmid")]
    meta_map = fetch_metadata(pmids) if pmids else {}

    checks = []
    for e in entries:
        pmid = e.get("pmid")
        meta = meta_map.get(pmid)
        if not meta:
            checks.append({
                "pmid": pmid, "exists": False, "article_title": "",
                "verdict": "unrelated", "reasoning": "PMID not found in PubMed.",
                "quotes": [],
            })
            continue
        claim = e.get("supports") or frontmatter.get("title", "")
        full_text = ""
        if meta.get("pmcid"):
            try:
                full_text = fetch_full_text(meta["pmcid"])
            except pubmed.PubMedUnavailable:
                full_text = ""  # degrade to abstract-only; PMC is optional here
        source_text = build_source_text(meta, full_text)
        result = evaluate.verify_pmid(
            claim, article_title=meta["title"], source_text=source_text,
            chat=chat, attempts=attempts)
        checks.append({
            "pmid": pmid, "exists": True, "article_title": meta["title"],
            "verdict": result["verdict"], "reasoning": result["reasoning"],
            "quotes": result["quotes"],
        })

    status, note = evaluate.node_verdict(checks)
    return {"node_id": node_id, "pmid_checks": checks,
            "overall_status": status, "notes": note}


def frontmatter_updates(entry):
    """Map an evaluation entry to the frontmatter patch for update_frontmatter.py.

    Sets each reference's ``verified`` flag (and ``quotes`` for verified ones),
    the node's ``evaluation_status``, and ``quarantined`` (a failed node is
    quarantined; a passing one is explicitly un-quarantined on re-eval).
    """
    passed = entry["overall_status"] == "passed"
    pubmed_ids = []
    for c in entry["pmid_checks"]:
        verified = c["verdict"] in evaluate.SUPPORTING
        ref = {"pmid": c["pmid"], "verified": verified}
        if verified:
            ref["quotes"] = c.get("quotes", [])
        pubmed_ids.append(ref)
    return {
        "evaluation_status": entry["overall_status"],
        "quarantined": not passed,
        "pubmed_ids": pubmed_ids,
    }


# ---------------------------------------------------------------------------
# Thin file-I/O glue around the tested core (reuses existing scripts).
# ---------------------------------------------------------------------------

def _node_files(kg_folder, only_ids):
    """Resolve node_id → file path from the manifest, optionally filtered."""
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    out = {}
    for n in manifest.get("nodes", []):
        if only_ids and n["id"] not in only_ids:
            continue
        out[n["id"]] = os.path.join(kg_folder, "nodes", os.path.basename(n["file"]))
    return out


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude-free KG evaluator")
    parser.add_argument("kg_folder")
    parser.add_argument("--nodes", default=None,
                        help="comma-separated node IDs (default: all nodes)")
    parser.add_argument("--attempts", type=int, default=2,
                        help="model parse retries per reference")
    parser.add_argument("--json", action="store_true",
                        help="print the evaluation entries as JSON to stdout")
    args = parser.parse_args(argv)

    only_ids = set(args.nodes.split(",")) if args.nodes else None
    node_files = _node_files(args.kg_folder, only_ids)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    entries = []
    try:
        for node_id, path in node_files.items():
            fm, _body = parse_node(path)
            entry = evaluate_node(node_id, fm, fetch_metadata=pubmed.fetch_metadata,
                                  fetch_full_text=pubmed.fetch_full_text, chat=llm.chat,
                                  attempts=args.attempts)
            entry["timestamp"] = _now()
            entries.append(entry)
    except llm.LLMUnavailable as e:
        print(f"Error: local model unavailable — no changes written: {e}",
              file=sys.stderr)
        return 2

    # Persist results and merge into node files + manifest stats.
    log_path = os.path.join(args.kg_folder, "_evaluation_log.json")
    with open(log_path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)

    for entry in entries:
        updates = frontmatter_updates(entry)
        upd_path = os.path.join(args.kg_folder, f"_eval_upd_{entry['node_id']}.json")
        with open(upd_path, "w", encoding="utf-8") as fh:
            json.dump(updates, fh)
        subprocess.run([sys.executable, os.path.join(scripts_dir, "update_frontmatter.py"),
                        node_files[entry["node_id"]], "--updates-file", upd_path], check=True)
        os.remove(upd_path)

    subprocess.run([sys.executable, os.path.join(scripts_dir, "update_manifest_stats.py"),
                    args.kg_folder], check=True)

    passed = sum(1 for e in entries if e["overall_status"] == "passed")
    print(f"Evaluated {len(entries)} nodes: {passed} passed, "
          f"{len(entries) - passed} failed.")
    if args.json:
        print(json.dumps(entries, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
