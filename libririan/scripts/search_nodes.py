#!/usr/bin/env python3
"""Rank KG manifest nodes by relevance to a free-text query.

Usage:
    python3 scripts/search_nodes.py "query text" <manifest1> [manifest2 ...] \
        [--top N] [--evidence-min TIER] [--tag-filter TAG]

Reads only manifest.json files (no node .md I/O), computes a weighted
relevance score per node, and outputs ranked results as JSON to stdout.

Scoring signals (weights sum to 1.0):
  keyword_score  0.35  Jaccard overlap of query tokens vs node keywords
  entity_score   0.30  Entity name/ID matching
  summary_score  0.20  TF-IDF cosine similarity
  tag_score      0.10  Tag substring matching
  + eval_bonus   0.05  If evaluation_status == "passed"
  + tier_bonus   0.05  Higher evidence tiers score more
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "do", "for", "from", "had", "has", "have", "he", "her", "his", "how",
    "if", "in", "into", "is", "it", "its", "may", "no", "not", "of", "on",
    "or", "our", "out", "own", "she", "so", "such", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "to", "too",
    "up", "upon", "was", "we", "were", "what", "when", "which", "while",
    "who", "will", "with", "would", "yet",
})

EVIDENCE_TIER_ORDER = {
    "meta_analysis": 7,
    "rct": 6,
    "cohort": 5,
    "case_series": 4,
    "case_report": 3,
    "review": 2,
    "opinion": 1,
    "unclassified": 0,
}

TIER_BONUS = {
    "meta_analysis": 0.05,
    "rct": 0.04,
    "cohort": 0.03,
    "case_series": 0.02,
    "case_report": 0.01,
    "review": 0.005,
    "opinion": 0.0,
    "unclassified": 0.0,
}

W_KEYWORD = 0.35
W_ENTITY = 0.30
W_SUMMARY = 0.20
W_TAG = 0.10
EVAL_BONUS = 0.05


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, remove stop words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in STOP_WORDS]


def extract_entities_from_query(query: str) -> list[str]:
    """Heuristic extraction of biomedical entity-like terms from a query.

    Rules:
      - All-caps tokens of 2-6 chars (gene symbols: SCN1A, BRCA1, TP53)
      - Title-case multi-word phrases (disease/drug names: Dravet syndrome)
      - Tokens matching common ID patterns (HGNC:*, OMIM:*, HP:*)
    """
    entities = []

    # Gene symbols: all-uppercase, 2-8 chars, may contain digits
    for match in re.finditer(r"\b([A-Z][A-Z0-9]{1,7})\b", query):
        candidate = match.group(1)
        # Exclude common English words that happen to be uppercase
        if candidate not in {"THE", "AND", "FOR", "NOT", "BUT", "ARE", "WAS",
                             "HAS", "HAD", "CAN", "HOW", "WHO", "ALL", "NEW",
                             "ONE", "TWO", "USE", "ITS"}:
            entities.append(candidate)

    # Title-case phrases: "Dravet syndrome", "mTOR signaling"
    # Must start with a capitalized word that is NOT a common English word
    common_title_words = {"what", "how", "when", "where", "why", "who", "which",
                          "does", "can", "are", "the", "this", "that", "there"}
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[a-z]+){1,3})\b", query):
        phrase = match.group(1)
        first_word = phrase.split()[0].lower()
        if first_word not in common_title_words and len(phrase) > 4:
            entities.append(phrase)

    # Formal IDs: HGNC:12345, OMIM:607208, HP:0001250
    for match in re.finditer(r"\b((?:HGNC|OMIM|HP|KEGG|CHEMBL|NCT)\S+)\b", query):
        entities.append(match.group(1))

    return entities


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def score_keywords(query_tokens: list[str], node_keywords: list[str]) -> tuple[float, list[str]]:
    """Jaccard-like overlap with substring bonus.

    Returns (score, list_of_matched_keywords).
    """
    if not query_tokens or not node_keywords:
        return 0.0, []

    kw_lower = [kw.lower() for kw in node_keywords]
    matched = []
    match_weight = 0.0
    matched_kw_indices: set[int] = set()

    for qt in query_tokens:
        for i, kw in enumerate(kw_lower):
            if i in matched_kw_indices:
                continue
            if qt == kw:
                match_weight += 1.0
                matched.append(node_keywords[i])
                matched_kw_indices.add(i)
                break
            elif qt in kw or kw in qt:
                match_weight += 0.5
                matched.append(node_keywords[i])
                matched_kw_indices.add(i)
                break

    union_size = len(set(query_tokens) | set(kw_lower))
    score = match_weight / max(1, union_size)
    return min(score, 1.0), list(dict.fromkeys(matched))


def score_entities(query_entities: list[str], node_entities: list[dict]) -> tuple[float, list[str]]:
    """Match extracted query entities against node entity names and IDs.

    Returns (score, list_of_matched_entity_identifiers).
    """
    if not query_entities or not node_entities:
        return 0.0, []

    matched = []
    total_weight = 0.0

    for qe in query_entities:
        qe_lower = qe.lower()
        best_match_weight = 0.0
        best_match_label = None

        for ent in node_entities:
            name = ent.get("name", "")
            norm_id = ent.get("normalized_id", "")

            # Exact normalized_id match (strongest)
            if norm_id and qe == norm_id:
                best_match_weight = 1.0
                best_match_label = norm_id
                break

            # Name exact match
            if name.lower() == qe_lower:
                if best_match_weight < 0.7:
                    best_match_weight = 0.7
                    best_match_label = norm_id or name

            # Substring match on name
            elif qe_lower in name.lower() or name.lower() in qe_lower:
                if best_match_weight < 0.4:
                    best_match_weight = 0.4
                    best_match_label = norm_id or name

        if best_match_weight > 0:
            total_weight += best_match_weight
            if best_match_label:
                matched.append(best_match_label)

    score = total_weight / max(1, len(query_entities))
    return min(score, 1.0), list(dict.fromkeys(matched))


def build_idf(documents: list[list[str]]) -> dict[str, float]:
    """Compute inverse document frequency across a list of tokenized documents."""
    n = len(documents)
    if n == 0:
        return {}
    df: Counter = Counter()
    for doc in documents:
        df.update(set(doc))
    return {term: math.log((n + 1) / (count + 1)) + 1 for term, count in df.items()}


def tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector as a dict."""
    tf = Counter(tokens)
    return {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()}


def cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    common_keys = set(v1) & set(v2)
    if not common_keys:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in common_keys)
    mag1 = math.sqrt(sum(x * x for x in v1.values()))
    mag2 = math.sqrt(sum(x * x for x in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def score_summary(query_tokens: list[str], summary_tokens: list[str],
                  idf: dict[str, float]) -> float:
    """TF-IDF cosine similarity between query and summary."""
    if not query_tokens or not summary_tokens:
        return 0.0
    v_query = tfidf_vector(query_tokens, idf)
    v_summary = tfidf_vector(summary_tokens, idf)
    return cosine_similarity(v_query, v_summary)


def score_tags(query_tokens: list[str], tags: list[str]) -> float:
    """1.0 if any query token is a substring of any tag, else 0.0."""
    if not query_tokens or not tags:
        return 0.0
    tags_lower = [t.lower() for t in tags]
    for qt in query_tokens:
        for tag in tags_lower:
            if qt in tag or tag in qt:
                return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Rank KG manifest nodes by relevance to a query.")
    parser.add_argument("query", help="Free-text question or search query")
    parser.add_argument("manifests", nargs="+",
                        help="Paths to one or more manifest.json files")
    parser.add_argument("--top", type=int, default=10,
                        help="Max number of results to return (default: 10)")
    parser.add_argument("--evidence-min", dest="evidence_min",
                        choices=list(EVIDENCE_TIER_ORDER.keys()),
                        help="Minimum evidence tier to include")
    parser.add_argument("--tag-filter", dest="tag_filter",
                        help="Only include nodes with this tag (case-insensitive)")
    parser.add_argument("--include-quarantined", dest="include_quarantined",
                        action="store_true",
                        help="Include quarantined nodes in results (excluded by default)")
    parser.add_argument("--compact", action="store_true",
                        help="Omit score_breakdown, match details, and query_analysis")
    args = parser.parse_args()

    min_tier_rank = EVIDENCE_TIER_ORDER.get(args.evidence_min, -1) if args.evidence_min else -1

    # Load all nodes from all manifests
    all_nodes = []  # list of (kg_name, node_entry)
    for manifest_path in args.manifests:
        if not os.path.exists(manifest_path):
            print(f"Error: file not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in {manifest_path}: {e}", file=sys.stderr)
            sys.exit(1)

        kg_name = manifest.get("kg_name", os.path.basename(os.path.dirname(manifest_path)))
        for node in manifest.get("nodes", []):
            all_nodes.append((kg_name, node))

    if not all_nodes:
        output = {"results": [], "summary": {"total_scanned": 0, "matched": 0}}
        if not args.compact:
            output["query_analysis"] = {}
        json.dump(output, sys.stdout, indent=2)
        print()
        sys.exit(0)

    total_before_filter = len(all_nodes)

    # Pre-filter by evidence tier
    if min_tier_rank > 0:
        all_nodes = [(kg, n) for kg, n in all_nodes
                     if EVIDENCE_TIER_ORDER.get(n.get("evidence_tier", "unclassified"), 0) >= min_tier_rank]

    # Pre-filter quarantined nodes (default: exclude)
    if not args.include_quarantined:
        all_nodes = [(kg, n) for kg, n in all_nodes
                     if not n.get("quarantined", False)]

    # Pre-filter by tag
    if args.tag_filter:
        tag_lower = args.tag_filter.lower()
        all_nodes = [(kg, n) for kg, n in all_nodes
                     if any(tag_lower == t.lower() for t in n.get("tags", []))]

    # Tokenize query
    query_tokens = tokenize(args.query)
    query_entities = extract_entities_from_query(args.query)

    # Build IDF from all summaries
    all_summary_tokens = [tokenize(n.get("summary", "")) for _, n in all_nodes]
    idf = build_idf(all_summary_tokens)

    # Score each node
    results = []
    entity_ids_matched = set()

    for idx, (kg_name, node) in enumerate(all_nodes):
        node_id = node.get("id", "")
        keywords = node.get("keywords", [])
        entities = node.get("entities", [])
        summary = node.get("summary", "")
        tags = node.get("tags", [])
        eval_status = node.get("evaluation_status", "pending")
        tier = node.get("evidence_tier", "unclassified")

        kw_score, matched_kw = score_keywords(query_tokens, keywords)
        ent_score, matched_ent = score_entities(query_entities, entities)
        sum_score = score_summary(query_tokens, all_summary_tokens[idx], idf)
        tag_s = score_tags(query_tokens, tags)

        eval_b = EVAL_BONUS if eval_status == "passed" else 0.0
        tier_b = TIER_BONUS.get(tier, 0.0)
        quarantine_penalty = -0.10 if node.get("quarantined", False) else 0.0

        final_score = (W_KEYWORD * kw_score +
                       W_ENTITY * ent_score +
                       W_SUMMARY * sum_score +
                       W_TAG * tag_s +
                       eval_b + tier_b + quarantine_penalty)

        if final_score > 0:
            entity_ids_matched.update(matched_ent)
            entry = {
                "kg": kg_name,
                "node_id": node_id,
                "title": node.get("title", ""),
                "file": node.get("file", ""),
                "score": round(final_score, 4),
                "evidence_tier": tier,
                "evaluation_status": eval_status,
                "quarantined": node.get("quarantined", False),
            }
            if not args.compact:
                entry["score_breakdown"] = {
                    "keyword_score": round(kw_score, 4),
                    "entity_score": round(ent_score, 4),
                    "summary_score": round(sum_score, 4),
                    "tag_score": round(tag_s, 4),
                    "eval_bonus": round(eval_b, 4),
                    "tier_bonus": round(tier_b, 4),
                }
                entry["matched_keywords"] = matched_kw
                entry["matched_entities"] = matched_ent
            results.append(entry)

    # Sort by score descending, take top N
    results.sort(key=lambda r: r["score"], reverse=True)
    total_matched = len(results)
    results = results[:args.top]

    output = {
        "results": results,
        "summary": {
            "total_scanned": total_before_filter,
            "matched": total_matched,
        },
    }
    if not args.compact:
        output["query_analysis"] = {
            "tokens": query_tokens,
            "extracted_entities": query_entities,
            "entity_ids_matched": sorted(entity_ids_matched),
        }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
