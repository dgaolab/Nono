"""`nono-pi evidence-score <out>` — deterministic per-node evidence-strength scores.

A reproducible proxy for evidence robustness (NOT a statistical hypothesis test),
computed purely from fields the librarian populates in each KG's manifest. Fed to
the aims-loop critic as quantitative grounding.
"""
import argparse
import json
import os
import sys

# Base weight by highest evidence tier among a node's references.
TIER_WEIGHT = {
    "meta_analysis": 1.0, "rct": 0.9, "cohort": 0.7, "case_series": 0.5,
    "case_report": 0.4, "review": 0.5, "opinion": 0.3, "unclassified": 0.3,
}
EVAL_FACTOR = {"passed": 1.0, "pending": 0.7, "failed": 0.3}


def score_node(node):
    """Return (score in [0,1], factors dict) for one manifest node."""
    tier = node.get("evidence_tier", "unclassified")
    tier_w = TIER_WEIGHT.get(tier, 0.3)
    n_pmids = len(node.get("pubmed_ids", []) or [])
    sources_factor = min(n_pmids, 3) / 3
    eval_status = node.get("evaluation_status", "pending")
    eval_f = EVAL_FACTOR.get(eval_status, 0.7)
    quar = bool(node.get("quarantined", False))
    quar_f = 0.1 if quar else 1.0
    score = round(tier_w * eval_f * quar_f * (0.5 + 0.5 * sources_factor), 3)
    return score, {
        "evidence_tier": tier, "tier_weight": tier_w,
        "n_pmids": n_pmids, "sources_factor": round(sources_factor, 3),
        "evaluation_status": eval_status, "eval_factor": eval_f,
        "quarantined": quar,
    }


def score_kg(kg_dir):
    with open(os.path.join(kg_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    scores = {}
    for node in manifest.get("nodes", []):
        s, factors = score_node(node)
        scores[node["id"]] = {"score": s, "factors": factors}
    return scores


def write_scores(out_dir, slug):
    kg_dir = os.path.join(out_dir, "kgs", slug)
    scores = score_kg(kg_dir)
    with open(os.path.join(kg_dir, "_evidence_score.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
    mean = round(sum(v["score"] for v in scores.values()) / len(scores), 3) if scores else 0.0
    return scores, mean


def _kg_slugs(out_dir):
    kgs_dir = os.path.join(out_dir, "kgs")
    if not os.path.isdir(kgs_dir):
        return []
    return sorted(d for d in os.listdir(kgs_dir)
                  if os.path.exists(os.path.join(kgs_dir, d, "manifest.json")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi evidence-score")
    ap.add_argument("out_dir")
    ap.add_argument("--kg", default=None, help="one KG slug; default = all built KGs")
    args = ap.parse_args(argv)
    slugs = [args.kg] if args.kg else _kg_slugs(args.out_dir)
    if not slugs:
        print("nono-pi evidence-score: no built KGs found", file=sys.stderr)
        return 2
    for slug in slugs:
        scores, mean = write_scores(args.out_dir, slug)
        print(f"{slug}: {len(scores)} nodes, mean strength {mean} "
              f"→ kgs/{slug}/_evidence_score.json")
    return 0
