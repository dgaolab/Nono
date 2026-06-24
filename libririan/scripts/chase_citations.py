#!/usr/bin/env python3
"""Deterministic citation-chasing discovery feed for a KG.

Follows backward references (pubmed_pubmed_refs) from the cited corpus
(ledger disposition "used"), dedups candidates against the ledger, ranks them
by co-citation frequency (iCite RCR tiebreak, best-effort), bounds the list,
and emits a JSON candidate feed. No MCP, no LLM, and READ-ONLY on the KG: it
writes nothing to the ledger or node files. On an elink network/parse error it
exits non-zero and emits no feed and no log entry.

Usage:
    python3 scripts/chase_citations.py <kg_folder> [--min-cocitation N] [--top-n N]
            [--json] [--elink-fixture FILE] [--icite-fixture FILE]

--elink-fixture FILE: JSON {"<seed_pmid>": ["<ref_pmid>", ...], ...} replacing
  live elink (tests).
--icite-fixture FILE: JSON {"<pmid>": <rcr_float>, ...} replacing live iCite
  (tests); a missing key yields rcr null for that PMID.

Set NCBI_API_KEY in the environment to lift the E-utilities rate limit.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_retractions import collect_used_pmids
from preflight import load_known_pmids

EUTILS_ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
REFS_LINKNAME = "pubmed_pubmed_refs"


def elink_references(pmid: str, api_key: str | None) -> list[str]:
    """Return the pubmed_pubmed_refs PMIDs for one seed (one elink call)."""
    params = {"dbfrom": "pubmed", "db": "pubmed", "linkname": REFS_LINKNAME,
              "id": pmid, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ELINK + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    refs: list[str] = []
    for linkset in data.get("linksets", []):
        for db in linkset.get("linksetdbs", []):
            if db.get("linkname") == REFS_LINKNAME:
                refs.extend(str(p) for p in db.get("links", []))
    return refs


def fetch_references(seeds: list[str], link_fn) -> dict[str, list[str]]:
    """Map each seed to its referenced PMIDs via link_fn(seed)->list."""
    return {seed: list(link_fn(seed)) for seed in seeds}


def build_candidates(refs_by_seed: dict[str, list[str]], known: set[str]) -> dict[str, dict]:
    """Candidate PMID -> {cocitation_count, referenced_by}, excluding known/seed PMIDs."""
    seeds = set(refs_by_seed)
    candidates: dict[str, dict] = {}
    for seed, refs in refs_by_seed.items():
        for ref in dict.fromkeys(refs):          # dedup within a single seed's list
            if ref in known or ref in seeds:
                continue
            entry = candidates.setdefault(ref, {"cocitation_count": 0, "referenced_by": []})
            entry["cocitation_count"] += 1
            entry["referenced_by"].append(seed)
    return candidates


def _build_link_fn(args, api_key):
    """Return link_fn(seed)->list, live or fixture-backed."""
    if args.elink_fixture:
        with open(args.elink_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        return lambda seed: [str(p) for p in fixture.get(seed, [])]

    sleep = 0.11 if api_key else 0.34
    state = {"first": True}

    def live(seed):
        if not state["first"]:
            time.sleep(sleep)  # NCBI rate etiquette between elink calls
        state["first"] = False
        return elink_references(seed, api_key)
    return live


def main():
    parser = argparse.ArgumentParser(description="Deterministic citation-chasing discovery feed.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--min-cocitation", type=int, default=2,
                        help="Drop candidates referenced by fewer than N seeds (default 2)")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Keep at most N candidates after ranking (default 20)")
    parser.add_argument("--elink-fixture", default=None,
                        help='JSON {"seed": [ref,...]} replacing live elink (tests)')
    parser.add_argument("--icite-fixture", default=None,
                        help='JSON {"pmid": rcr} replacing live iCite (tests)')
    parser.add_argument("--json", action="store_true", help="Emit the structured feed as JSON")
    args = parser.parse_args()

    seeds = collect_used_pmids(args.kg_folder)
    api_key = os.environ.get("NCBI_API_KEY")
    link_fn = _build_link_fn(args, api_key)

    try:
        refs_by_seed = fetch_references(seeds, link_fn)
    except Exception as e:
        print(f"Error: citation elink failed: {e}", file=sys.stderr)
        sys.exit(1)

    known = load_known_pmids(args.kg_folder)
    candidates = build_candidates(refs_by_seed, known)

    feed = sorted(
        ({"pmid": p, "cocitation_count": c["cocitation_count"],
          "referenced_by": sorted(c["referenced_by"])}
         for p, c in candidates.items()),
        key=lambda c: (-c["cocitation_count"], c["pmid"]))

    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "seed_count": len(seeds), "candidate_count": len(feed),
               "candidates": feed}
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Citation chase: {len(feed)} candidates from {len(seeds)} cited PMIDs.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
