#!/usr/bin/env python3
"""Deterministic retraction sweep for a KG's cited PubMed corpus.

Detects which cited PMIDs (ledger disposition "used") have been retracted, by
intersecting them with PubMed's "Retracted Publication" publication type via
NCBI esearch. No MCP, no LLM. On any network/parse error the script exits
non-zero WITHOUT mutating the ledger or node files.

Usage:
    python3 scripts/check_retractions.py <kg_folder> [--esearch-fixture FILE] [--json]

The --esearch-fixture FILE is a JSON object {"retracted": ["pmid", ...]} that
replaces live E-utilities calls (used by tests); a cited PMID is treated as
retracted iff it appears in that list.

Set NCBI_API_KEY in the environment to lift the rate limit.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datetime
import subprocess

from lib.frontmatter import parse as parse_node, write as write_node
from append_log import append_entry

EUTILS_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CHUNK_SIZE = 200
RETRACTED_PT = '"Retracted Publication"[Publication Type]'


def collect_used_pmids(kg_folder: str) -> list[str]:
    """Sorted PMIDs whose ledger disposition is 'used'."""
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    return sorted(p for p, e in ledger.get("entries", {}).items()
                  if e.get("disposition") == "used")


def esearch_retracted(pmids: list[str], api_key: str | None) -> set[str]:
    """Return the subset of `pmids` PubMed reports as retracted (one query)."""
    if not pmids:
        return set()
    term = "(" + " OR ".join(f"{p}[uid]" for p in pmids) + ") AND " + RETRACTED_PT
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": str(len(pmids))}
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ESEARCH + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    idlist = data.get("esearchresult", {}).get("idlist", [])
    return {str(p) for p in idlist}


def find_retracted(used_pmids: list[str], query_fn) -> set[str]:
    """Chunk `used_pmids` and union query_fn(chunk) across chunks."""
    retracted: set[str] = set()
    for i in range(0, len(used_pmids), CHUNK_SIZE):
        chunk = used_pmids[i:i + CHUNK_SIZE]
        retracted |= query_fn(chunk)
    return retracted


def _build_query_fn(args, api_key):
    """Return query_fn(chunk)->set, live or fixture-backed."""
    if args.esearch_fixture:
        with open(args.esearch_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        retracted_set = {str(p) for p in fixture.get("retracted", [])}
        return lambda chunk: {p for p in chunk if p in retracted_set}

    sleep = 0.11 if api_key else 0.34
    state = {"first": True}

    def live(chunk):
        if not state["first"]:
            time.sleep(sleep)  # NCBI rate etiquette between chunks
        state["first"] = False
        return esearch_retracted(chunk, api_key)
    return live


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _valid_support(fm: dict) -> int:
    """Count references that still support the node: verified, non-retracted PMIDs + all external_ids."""
    pmid_support = sum(1 for r in fm.get("pubmed_ids", []) or []
                       if r.get("verified") is True and r.get("retracted") is not True)
    return pmid_support + len(fm.get("external_ids", []) or [])


def _flag_ledger(kg_folder: str, retracted: set[str], swept: list[str]) -> None:
    """Mark retracted entries and advance last_checked on all swept entries. Atomic write."""
    import tempfile
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    now = _now_iso()
    entries = ledger.get("entries", {})
    for pmid in swept:
        if pmid in entries:
            entries[pmid]["last_checked"] = now
    for pmid in retracted:
        if pmid in entries:
            entries[pmid]["disposition"] = "retracted"
            entries[pmid]["notes"] = f"Retraction detected by sweep on {now[:10]}."
    ledger["updated"] = datetime.date.today().isoformat()
    ledger["version"] = ledger.get("version", 0) + 1
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_retractions(kg_folder: str, retracted: set[str], swept: list[str]) -> list[dict]:
    """Flag ledger + nodes, conditionally quarantine, return the retractions summary."""
    # Reverse index PMID -> nodes, from the ledger.
    with open(os.path.join(kg_folder, "_pmid_ledger.json"), "r", encoding="utf-8") as fh:
        entries = json.load(fh).get("entries", {})

    _flag_ledger(kg_folder, retracted, swept)

    summary: list[dict] = []
    any_quarantine = False
    for pmid in sorted(retracted):
        nodes = entries.get(pmid, {}).get("assigned_nodes", [])
        action = "flagged"
        for nid in nodes:
            node_path = os.path.join(kg_folder, "nodes", f"{nid}.md")
            # node files may be named <id>_<slug>.md; resolve by glob if exact name missing
            if not os.path.exists(node_path):
                import glob as _glob
                matches = _glob.glob(os.path.join(kg_folder, "nodes", f"{nid}_*.md"))
                if not matches:
                    continue
                node_path = matches[0]
            fm, body = parse_node(node_path)
            for r in fm.get("pubmed_ids", []) or []:
                if r.get("pmid") == pmid:
                    r["retracted"] = True
                    r["verified"] = False
            if _valid_support(fm) == 0:
                fm["evaluation_status"] = "failed"
                body = body.rstrip() + (
                    f"\n\n> [!warning] Retraction\n> Reference PMID {pmid} was retracted; "
                    f"this node lost its last valid supporting reference and was quarantined "
                    f"pending re-evaluation.\n")
                action = "quarantined"
                any_quarantine = True
            write_node(node_path, fm, body)
        summary.append({"pmid": pmid, "nodes": list(nodes), "action": action})

    # Sync quarantined flags + manifest from evaluation_status (only if a node changed).
    if any_quarantine:
        enforce = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enforce_quarantine.py")
        subprocess.run([sys.executable, enforce, kg_folder], capture_output=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Deterministic retraction sweep for a KG.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--esearch-fixture", default=None,
                        help='JSON {"retracted": [pmid,...]} replacing live E-utilities (tests)')
    parser.add_argument("--json", action="store_true", help="Emit the structured summary as JSON")
    args = parser.parse_args()

    used = collect_used_pmids(args.kg_folder)
    api_key = os.environ.get("NCBI_API_KEY")
    query_fn = _build_query_fn(args, api_key)

    try:
        retracted = sorted(find_retracted(used, query_fn))
    except Exception as e:
        print(f"Error: retraction esearch failed: {e}", file=sys.stderr)
        sys.exit(1)

    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "checked_count": len(used), "retracted_pmids": retracted}

    retractions = apply_retractions(args.kg_folder, set(retracted), used)
    summary["retractions"] = retractions

    n_q = sum(1 for r in retractions if r["action"] == "quarantined")
    n_f = sum(1 for r in retractions if r["action"] == "flagged")
    try:
        append_entry(args.kg_folder, "retraction",
                     f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted; "
                     f"{n_q} node(s) quarantined, {n_f} flagged.")
    except Exception:
        pass  # never fail the sweep over logging

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted; "
              f"{n_q} quarantined, {n_f} flagged.", file=sys.stderr)


if __name__ == "__main__":
    main()
