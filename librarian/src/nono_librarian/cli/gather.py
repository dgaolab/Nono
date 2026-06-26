#!/usr/bin/env python3
"""`nono-librarian gather` — deterministic PubMed retrieval for agent-driven build."""
import argparse
import json
import sys

from nono_librarian.lib import build, pubmed


def gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text,
                    known_pmids, tier, mindate=None):
    per_query = [
        esearch(q, retmax=tier["max_results"], **({"mindate": mindate} if mindate else {}))
        for q in sub_queries
    ]
    pmids = build.select_candidates(per_query, known_pmids, cap=tier["metadata"])
    meta_map = fetch_metadata(pmids) if pmids else {}
    articles = []
    for p in pmids:
        meta = meta_map.get(p)
        if not meta:
            continue
        articles.append({"pmid": p, "title": meta.get("title", ""),
                         "abstract": meta.get("abstract", ""), "metadata": meta})
    for a in articles[:tier["full_text"]]:
        pmcid = a["metadata"].get("pmcid")
        if not pmcid:
            continue
        try:
            ft = fetch_full_text(pmcid)
        except pubmed.PubMedUnavailable:
            ft = ""
        if ft:
            a["abstract"] = (a["abstract"] + "\n\n" + ft).strip()
    return articles


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian gather",
                                     description="Retrieve PubMed candidate articles")
    parser.add_argument("topic")
    parser.add_argument("--query", action="append", dest="queries", required=True,
                        help="a planned sub-query (repeatable)")
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default="medium")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD or YYYY/MM/DD lower bound")
    parser.add_argument("--out", default="_candidates.json")
    args = parser.parse_args(argv)

    tier = build.TIERS[args.breadth]
    mindate = args.since.replace("-", "/") if args.since else None
    articles = gather_articles(
        args.queries, esearch=pubmed.esearch, fetch_metadata=pubmed.fetch_metadata,
        fetch_full_text=pubmed.fetch_full_text, known_pmids=set(), tier=tier,
        mindate=mindate)
    payload = {"topic": args.topic, "breadth": args.breadth,
               "sub_queries": args.queries, "articles": articles}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Gathered {len(articles)} articles → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
