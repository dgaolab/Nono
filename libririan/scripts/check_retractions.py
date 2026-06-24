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
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
