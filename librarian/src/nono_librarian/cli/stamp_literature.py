#!/usr/bin/env python3
"""Deterministic post-processing: rewrite ### Literature sections in node
markdown files using PMID ledger metadata.

Usage:
    python3 scripts/stamp_literature.py <kg_folder> [--dry-run]
"""

import argparse
import glob
import json
import os
import re
import sys

from nono_librarian.lib.frontmatter import parse, write

SECTION_RE = re.compile(r"^### Literature\s*$", re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^#{1,3} ", re.MULTILINE)


def format_authors(authors):
    """Format ledger authors list into a short citation string."""
    if not authors:
        return "Unknown"
    names = [a.get("last_name", "") for a in authors if a.get("last_name")]
    if len(names) == 0:
        return "Unknown"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


def format_bullet(pmid, ledger_entry, evidence_tier):
    """Build a single literature bullet line from ledger metadata."""
    title = ledger_entry.get("title") or None
    journal = ledger_entry.get("journal") or None
    year = ledger_entry.get("year") or None
    authors = ledger_entry.get("authors") or None

    missing = title is None
    author_str = format_authors(authors)
    title_str = title if title else "(title unavailable)"
    journal_str = journal if journal else "(journal unavailable)"
    year_str = str(year) if year else "(year unavailable)"

    return (
        f"- **PMID {pmid}** ({author_str}, {year_str}, *{journal_str}*) "
        f"[{evidence_tier}]: {title_str}",
        missing,
    )


def replace_literature_section(body, new_content):
    """Replace the content under ### Literature with *new_content*.

    Returns the updated body, or None if no ### Literature heading found.
    """
    m = SECTION_RE.search(body)
    if m is None:
        return None

    section_start = m.end()
    # Find next heading at level 1-3, or end of body
    rest = body[section_start:]
    m2 = NEXT_HEADING_RE.search(rest)
    if m2:
        section_end = section_start + m2.start()
    else:
        section_end = len(body)

    new_body = body[: m.end()] + "\n\n" + new_content + "\n\n" + body[section_end:].lstrip("\n")
    # If section was at end of file, trim trailing extra newlines
    if m2 is None:
        new_body = new_body.rstrip("\n") + "\n"
    return new_body


def main():
    parser = argparse.ArgumentParser(
        description="Stamp ### Literature sections in KG node files from PMID ledger.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing files")
    args = parser.parse_args()

    kg_folder = args.kg_folder

    # 1. Load ledger
    ledger_path = os.path.join(kg_folder, "_pmid_ledger.json")
    if not os.path.exists(ledger_path):
        print(f"Error: {ledger_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(ledger_path, "r", encoding="utf-8") as fh:
        ledger_data = json.load(fh)
    ledger = ledger_data.get("entries", {})

    # 2. Discover node files
    node_files = sorted(glob.glob(os.path.join(kg_folder, "nodes", "*.md")))

    nodes_processed = 0
    nodes_stamped = 0
    nodes_skipped = 0
    pmids_missing_metadata = 0

    for node_file in node_files:
        try:
            fm, body = parse(node_file)
        except Exception as e:
            print(f"Warning: skipping {node_file}: {e}", file=sys.stderr)
            nodes_skipped += 1
            continue

        nodes_processed += 1
        pubmed_ids = fm.get("pubmed_ids", [])
        if not pubmed_ids:
            nodes_skipped += 1
            continue

        bullets = []
        for entry in pubmed_ids:
            if not isinstance(entry, dict):
                continue
            pmid = entry.get("pmid")
            if not pmid:
                continue
            pmid_str = str(pmid)
            evidence_tier = entry.get("evidence_tier", "unclassified")

            if pmid_str not in ledger:
                print(f"Warning: PMID {pmid_str} not in ledger, skipping", file=sys.stderr)
                pmids_missing_metadata += 1
                continue

            bullet, missing = format_bullet(pmid_str, ledger[pmid_str], evidence_tier)
            if missing:
                pmids_missing_metadata += 1
            bullets.append(bullet)

        if not bullets:
            nodes_skipped += 1
            continue

        new_body = replace_literature_section(body, "\n".join(bullets))
        if new_body is None:
            nodes_skipped += 1
            continue

        nodes_stamped += 1
        if not args.dry_run:
            write(node_file, fm, new_body)

    summary = {
        "nodes_processed": nodes_processed,
        "nodes_stamped": nodes_stamped,
        "nodes_skipped": nodes_skipped,
        "pmids_missing_metadata": pmids_missing_metadata,
    }
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
