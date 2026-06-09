#!/usr/bin/env python3
"""Deterministic pre-check for scheduled KG updates.

Runs the sub-queries persisted in manifest.json's search_profile against
NCBI E-utilities (esearch), dedups the returned PMIDs against the PMID
ledger, and reports whether enough novel literature exists to justify a
full /build-kg UPDATE run. No MCP, no LLM, no ledger writes.

Usage:
    python3 scripts/preflight.py <kg_folder> [--threshold N] [--since YYYY-MM-DD]
                                 [--log] [--esearch-fixture FILE]

Exit codes:
    0  ran successfully (read "proceed" from the JSON on stdout)
    1  network / parse error talking to E-utilities
    2  unusable manifest (missing manifest.json or search_profile)

Set NCBI_API_KEY in the environment to lift the rate limit from 3 to 10 req/s.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from append_log import append_entry

EUTILS_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
# Mirrors the per-tier max_results table in build-kg.md Phase 1b Step 0.
RETMAX_BY_BREADTH = {"narrow": 10, "medium": 20, "broad": 30}
DEFAULT_THRESHOLD = 3


def load_known_pmids(kg_folder: str) -> set[str]:
    """All PMIDs in the ledger, any disposition. Empty set if no ledger."""
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    return set(ledger.get("entries", {}).keys())


def resolve_since(manifest: dict, override: str | None) -> str:
    """Resolve the search window start as YYYY/MM/DD (E-utilities format)."""
    if override:
        return override.replace("-", "/")
    last_run = (manifest.get("schedule") or {}).get("last_run")
    if last_run:
        return last_run[:10].replace("-", "/")
    return manifest["updated"].replace("-", "/")


def esearch(query: str, since: str, retmax: int, api_key: str | None) -> dict:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "json",
        "datetype": "edat",
        "mindate": since,
        "maxdate": date.today().strftime("%Y/%m/%d"),
    }
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ESEARCH + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    result = data.get("esearchresult", {})
    return {"count": int(result.get("count", 0)),
            "idlist": [str(p) for p in result.get("idlist", [])]}


def main():
    parser = argparse.ArgumentParser(
        description="Deterministic preflight check for scheduled KG updates.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--threshold", type=int, default=None,
                        help="Minimum novel PMIDs to recommend a full run "
                             "(default: schedule.threshold in manifest, else 3)")
    parser.add_argument("--since", default=None,
                        help="Override the window start (YYYY-MM-DD); default derives "
                             "from schedule.last_run, then manifest.updated")
    parser.add_argument("--log", action="store_true",
                        help="Append a preflight entry to the KG's _log.md")
    parser.add_argument("--esearch-fixture", default=None,
                        help="JSON file mapping query -> {count, idlist}; replaces "
                             "live E-utilities calls (used by tests)")
    args = parser.parse_args()

    manifest_path = os.path.join(args.kg_folder, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {args.kg_folder}", file=sys.stderr)
        sys.exit(2)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    profile = manifest.get("search_profile")
    if not isinstance(profile, dict) or not profile.get("sub_queries"):
        print("Error: manifest has no search_profile — run /build-kg once to "
              "backfill it; preflight will work from the next run on.", file=sys.stderr)
        sys.exit(2)

    threshold = args.threshold
    if threshold is None:
        threshold = (manifest.get("schedule") or {}).get("threshold", DEFAULT_THRESHOLD)

    since = resolve_since(manifest, args.since)
    retmax = RETMAX_BY_BREADTH.get(profile.get("breadth", "medium"), 20)
    api_key = os.environ.get("NCBI_API_KEY")

    fixture = None
    if args.esearch_fixture:
        with open(args.esearch_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)

    known_pmids = load_known_pmids(args.kg_folder)

    per_query = []
    all_pmids: set[str] = set()
    for i, query in enumerate(profile["sub_queries"]):
        if fixture is not None:
            result = fixture.get(query, {"count": 0, "idlist": []})
        else:
            if i > 0:
                time.sleep(0.11 if api_key else 0.34)  # NCBI rate etiquette
            try:
                result = esearch(query, since, retmax, api_key)
            except Exception as e:
                print(f"Error: esearch failed for {query!r}: {e}", file=sys.stderr)
                sys.exit(1)
        ids = {str(p) for p in result.get("idlist", [])}
        all_pmids.update(ids)
        per_query.append({"query": query,
                          "total_hits": result.get("count", 0),
                          "novel": len(ids - known_pmids)})

    novel = sorted(all_pmids - known_pmids)

    report = {
        "kg": os.path.basename(os.path.abspath(args.kg_folder)),
        "since_date": since,
        "threshold": threshold,
        "per_query": per_query,
        "novel_count": len(novel),
        "novel_pmids": novel,
        "proceed": len(novel) >= threshold,
    }
    json.dump(report, sys.stdout, indent=2)
    print()

    if args.log:
        decision = ("proceeding with update" if report["proceed"]
                    else "below threshold, skipping update")
        append_entry(args.kg_folder, "preflight",
                     f"{len(novel)} novel PMIDs since {since} "
                     f"(threshold {threshold}) — {decision}.")


if __name__ == "__main__":
    main()
