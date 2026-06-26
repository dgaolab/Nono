"""Deterministic KG node evaluator — applies agent-supplied verdicts with guardrail.

Given an agent verdict dict for each PMID in a node, this module fetches the
article text and applies the verbatim-quote guardrail (lib/evaluate.judge_pmid).
No model calls are made here.

Interfaces:
  judge_node(node_id, frontmatter, judgments_for_node, *, fetch_metadata, fetch_full_text)
  frontmatter_updates(entry)
  build_source_text(meta, full_text)
  _node_files(kg_folder, only_ids)
  _now()
"""

import datetime
import json
import os

from nono_librarian.lib import evaluate, pubmed
from nono_librarian.lib.frontmatter import parse as parse_node


def build_source_text(meta, full_text):
    """Concatenate the article text available for verification (abstract + body)."""
    parts = []
    if meta.get("abstract"):
        parts.append(meta["abstract"])
    if full_text:
        parts.append(full_text)
    return "\n\n".join(parts)


def judge_node(node_id, frontmatter, judgments_for_node, *,
               fetch_metadata, fetch_full_text):
    """Apply agent verdicts to one node's claims, guardrailed. No model call."""
    entries = frontmatter.get("pubmed_ids", []) or []
    pmids = [e["pmid"] for e in entries if e.get("pmid")]
    meta_map = fetch_metadata(pmids) if pmids else {}
    checks = []
    for e in entries:
        pmid = e.get("pmid")
        meta = meta_map.get(pmid)
        if not meta:
            checks.append({"pmid": pmid, "exists": False, "article_title": "",
                           "verdict": "unrelated",
                           "reasoning": "PMID not found in PubMed.", "quotes": []})
            continue
        full_text = ""
        if meta.get("pmcid"):
            try:
                full_text = fetch_full_text(meta["pmcid"])
            except pubmed.PubMedUnavailable:
                full_text = ""
        source_text = build_source_text(meta, full_text)
        judgment = judgments_for_node.get(pmid, {"verdict": "unrelated", "quotes": []})
        result = evaluate.judge_pmid(judgment, source_text=source_text)
        checks.append({"pmid": pmid, "exists": True, "article_title": meta["title"],
                       "verdict": result["verdict"],
                       "reasoning": result.get("reasoning", ""),
                       "quotes": result["quotes"]})
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
