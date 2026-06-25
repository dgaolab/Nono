#!/usr/bin/env python3
"""Deterministic evidence tier classification for KG nodes.

Scans PMID titles from the ledger for study-type keywords and assigns
per-PMID and node-level evidence_tier fields in node frontmatter.

Usage:
    python3 scripts/classify_evidence_tier.py <kg_folder> [--dry-run] [--update-ledger]
"""

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse, write


# ---------------------------------------------------------------------------
# Tier classification — two methods, structured PubMed types preferred.
# ---------------------------------------------------------------------------

# Method 1: PubMed publication_types → tier mapping.
# PubMed assigns structured type tags like "Randomized Controlled Trial",
# "Meta-Analysis", "Review", etc.  These are the gold standard.
# Map each known tag to (tier_label, score).  An article can have multiple
# tags; the highest-scoring one wins.
PUBTYPE_MAP: dict[str, tuple[str, int]] = {
    # meta_analysis (7)
    "meta-analysis":           ("meta_analysis", 7),
    "systematic review":       ("meta_analysis", 7),
    # rct (6)
    "randomized controlled trial": ("rct", 6),
    "clinical trial, phase iii":   ("rct", 6),
    "clinical trial, phase iv":    ("rct", 6),
    "pragmatic clinical trial":    ("rct", 6),
    # cohort (5)
    "observational study":     ("cohort", 5),
    "cohort study":            ("cohort", 5),   # rare but exists
    "clinical trial":          ("cohort", 5),
    "clinical trial, phase i": ("cohort", 5),
    "clinical trial, phase ii":("cohort", 5),
    "multicenter study":       ("cohort", 5),
    "comparative study":       ("cohort", 5),
    "twin study":              ("cohort", 5),
    "validation study":        ("cohort", 5),
    # case_series (4)
    "clinical study":          ("case_series", 4),
    "evaluation study":        ("case_series", 4),
    # case_report (3)
    "case reports":            ("case_report", 3),
    # review (2)
    "review":                  ("review", 2),
    "scientific integrity review": ("review", 2),
    # opinion (1)
    "editorial":               ("opinion", 1),
    "letter":                  ("opinion", 1),
    "comment":                 ("opinion", 1),
    "published erratum":       ("opinion", 1),
    "personal narrative":      ("opinion", 1),
}

# Tags to ignore — they describe format, not study design.
PUBTYPE_IGNORE = {
    "journal article", "english abstract", "research support, n.i.h., extramural",
    "research support, n.i.h., intramural", "research support, non-u.s. gov't",
    "research support, u.s. gov't, non-p.h.s.", "research support, u.s. gov't, p.h.s.",
    "in vitro", "retracted publication", "preprint",
}


def classify_publication_types(pub_types: list[str]) -> tuple[str, int]:
    """Classify from PubMed publication_types tags.

    Returns the highest-scoring (tier_label, score) among the tags.
    Falls back to ("unclassified", 0) if no informative tag matches.
    """
    if not pub_types:
        return ("unclassified", 0)

    best: tuple[str, int] = ("unclassified", 0)
    for tag in pub_types:
        normalized = tag.strip().lower()
        if normalized in PUBTYPE_IGNORE:
            continue
        tier = PUBTYPE_MAP.get(normalized)
        if tier and tier[1] > best[1]:
            best = tier
    return best


# Method 2: Title regex fallback — same patterns as before, used only when
# publication_types are unavailable (ledger bootstrapped without metadata).
TITLE_RULES: list[tuple[str, int, list[re.Pattern]]] = [
    ("meta_analysis", 7, [
        re.compile(r"meta.analysis", re.IGNORECASE),
        re.compile(r"systematic review", re.IGNORECASE),
    ]),
    ("rct", 6, [
        re.compile(r"randomized", re.IGNORECASE),
        re.compile(r"\brct\b", re.IGNORECASE),
        re.compile(r"randomized clinical trial", re.IGNORECASE),
        re.compile(r"controlled trial", re.IGNORECASE),
    ]),
    ("cohort", 5, [
        re.compile(r"cohort", re.IGNORECASE),
        re.compile(r"longitudinal", re.IGNORECASE),
        re.compile(r"prospective", re.IGNORECASE),
        re.compile(r"retrospective\s+(study|studies|analysis|cohort|review)", re.IGNORECASE),
    ]),
    ("case_series", 4, [
        re.compile(r"case series", re.IGNORECASE),
    ]),
    ("case_report", 3, [
        re.compile(r"case report", re.IGNORECASE),
    ]),
    ("review", 2, [
        re.compile(r"(?<!systematic )review", re.IGNORECASE),
    ]),
    ("opinion", 1, [
        re.compile(r"editorial", re.IGNORECASE),
        re.compile(r"\bletter\b", re.IGNORECASE),
        re.compile(r"\bcomment\b", re.IGNORECASE),
        re.compile(r"\bopinion\b", re.IGNORECASE),
        re.compile(r"perspective", re.IGNORECASE),
    ]),
]

# Publication-type tiers describe *what the article is* (a review, an editorial),
# while study-design tiers describe *what the article studies* (a cohort, an RCT).
# When both match (e.g., "a review of randomized trials"), the publication type wins.
_PUB_TYPE_TIERS = {"meta_analysis", "review", "opinion"}


def classify_title(title: str) -> tuple[str, int]:
    """Classify a title string into an evidence tier via regex.

    Used as a fallback when publication_types are not available.
    Returns (tier_label, score). Falls back to ("unclassified", 0).
    """
    if not title:
        return ("unclassified", 0)

    matches: list[tuple[str, int]] = []
    for label, score, patterns in TITLE_RULES:
        for pat in patterns:
            if pat.search(title):
                matches.append((label, score))
                break  # one match per tier is enough

    if not matches:
        return ("unclassified", 0)

    if len(matches) == 1:
        return matches[0]

    # Multiple tiers matched — check if both categories are present.
    pub_type_matches = [(l, s) for l, s in matches if l in _PUB_TYPE_TIERS]
    study_design_matches = [(l, s) for l, s in matches if l not in _PUB_TYPE_TIERS]

    if pub_type_matches and study_design_matches:
        # Publication-type overrides study-design.
        matches = pub_type_matches

    return max(matches, key=lambda t: t[1])


def classify_pmid(pub_types: list[str] | None, title: str | None) -> tuple[str, int]:
    """Classify a PMID using publication_types first, title regex as fallback."""
    if pub_types:
        result = classify_publication_types(pub_types)
        if result[0] != "unclassified":
            return result
    # Fallback to title regex
    return classify_title(title or "")


def best_tier(pmid_tiers: list[tuple[str, int]]) -> str:
    """Return the tier label with the highest score among a node's PMIDs."""
    if not pmid_tiers:
        return "unclassified"
    return max(pmid_tiers, key=lambda t: t[1])[0]


def main():
    parser = argparse.ArgumentParser(
        description="Classify evidence tiers for KG nodes from PMID title metadata.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing files")
    parser.add_argument("--update-ledger", action="store_true",
                        help="Also update evidence_tier in _pmid_ledger.json")
    args = parser.parse_args()

    kg_folder = args.kg_folder

    # Load PMID ledger
    ledger_path = os.path.join(kg_folder, "_pmid_ledger.json")
    ledger = {}
    ledger_data = None
    if os.path.exists(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as fh:
            ledger_data = json.load(fh)
        ledger = ledger_data.get("entries", {})
    else:
        print("Warning: _pmid_ledger.json not found, all PMIDs will remain unclassified",
              file=sys.stderr)

    # Discover node files
    node_files = sorted(glob.glob(os.path.join(kg_folder, "nodes", "*.md")))
    if not node_files:
        print("Warning: no node files found", file=sys.stderr)

    # Track changes for summary
    nodes_processed = 0
    nodes_reclassified = 0
    pmids_reclassified = 0
    pmids_no_title = 0
    tier_distribution: dict[str, int] = {}
    ledger_updates: dict[str, str] = {}  # pmid -> new tier

    for node_file in node_files:
        try:
            fm, body = parse(node_file)
        except Exception as e:
            print(f"Warning: skipping {node_file}: {e}", file=sys.stderr)
            continue

        nodes_processed += 1
        pubmed_ids = fm.get("pubmed_ids", [])
        if not pubmed_ids:
            continue

        node_changed = False
        node_pmid_tiers: list[tuple[str, int]] = []

        for entry in pubmed_ids:
            if not isinstance(entry, dict):
                continue

            pmid = entry.get("pmid")
            if not pmid:
                continue

            pmid_str = str(pmid)
            ledger_entry = ledger.get(pmid_str, {})
            title = ledger_entry.get("title") if isinstance(ledger_entry, dict) else None
            pub_types = ledger_entry.get("publication_types") if isinstance(ledger_entry, dict) else None

            if not title and not pub_types:
                pmids_no_title += 1
                node_pmid_tiers.append(("unclassified", 0))
                continue

            tier_label, tier_score = classify_pmid(pub_types, title)
            node_pmid_tiers.append((tier_label, tier_score))

            old_tier = entry.get("evidence_tier", "unclassified")
            if old_tier != tier_label:
                entry["evidence_tier"] = tier_label
                node_changed = True
                pmids_reclassified += 1

            if args.update_ledger and tier_label != "unclassified":
                ledger_updates[pmid_str] = tier_label

        # Set node-level evidence tier
        new_node_tier = best_tier(node_pmid_tiers)
        old_node_tier = fm.get("evidence_tier", "unclassified")
        if old_node_tier != new_node_tier:
            fm["evidence_tier"] = new_node_tier
            node_changed = True

        # Count for distribution
        tier_distribution[new_node_tier] = tier_distribution.get(new_node_tier, 0) + 1

        if node_changed:
            nodes_reclassified += 1
            if not args.dry_run:
                write(node_file, fm, body)

    # Update ledger if requested
    if args.update_ledger and ledger_updates and ledger_data and not args.dry_run:
        entries = ledger_data.get("entries", {})
        for pmid_str, tier_label in ledger_updates.items():
            if pmid_str in entries:
                entries[pmid_str]["evidence_tier"] = tier_label
        ledger_data["version"] = ledger_data.get("version", 0) + 1
        ledger_data["updated"] = datetime.date.today().isoformat()
        # Recompute statistics to keep them in sync with updated entries
        counts: dict[str, int] = {"used": 0, "irrelevant": 0, "failed": 0, "superseded": 0}
        for entry in ledger_data.get("entries", {}).values():
            disp = entry.get("disposition", "")
            if disp in counts:
                counts[disp] += 1
        counts["total"] = sum(counts.values())
        ledger_data["statistics"] = counts
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(ledger_path), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(ledger_data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_path, ledger_path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # Update manifest stats (unless dry-run)
    if not args.dry_run and nodes_reclassified > 0:
        stats_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "update_manifest_stats.py")
        if os.path.exists(stats_script):
            subprocess.run([sys.executable, stats_script, kg_folder],
                           capture_output=True)

    # Print summary
    summary = {
        "nodes_processed": nodes_processed,
        "nodes_reclassified": nodes_reclassified,
        "nodes_unchanged": nodes_processed - nodes_reclassified,
        "pmids_reclassified": pmids_reclassified,
        "pmids_no_title": pmids_no_title,
        "tier_distribution": tier_distribution,
    }
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
