#!/usr/bin/env python3
"""Deterministic build helpers — the structural steps of build-kg.

All functions here are purely deterministic: they operate on data already
retrieved by the orchestrator and perform no model calls. The model-driven
steps (plan_search, propose_skeleton, synthesize_node, propose_relationships,
gap_fill_queries) have been removed; the agent orchestrator handles those
directly via its own reasoning loop.
"""

import re

TIERS = {
    "narrow": {"sub_queries": 3, "max_results": 10, "metadata": 15,
               "full_text": 5, "nodes_min": 8, "nodes_max": 15},
    "medium": {"sub_queries": 4, "max_results": 20, "metadata": 25,
               "full_text": 8, "nodes_min": 15, "nodes_max": 30},
    "broad":  {"sub_queries": 6, "max_results": 30, "metadata": 40,
               "full_text": 12, "nodes_min": 25, "nodes_max": 45},
}


class BuildError(RuntimeError):
    """Raised when a build step receives invalid or unusable input."""


def select_candidates(per_query_pmids, known_pmids, cap):
    """Flatten per-query PMID lists → deduped, ledger-excluded, capped order."""
    seen = set()
    out = []
    for pmids in per_query_pmids:
        for p in pmids:
            if p in seen or p in known_pmids:
                continue
            seen.add(p)
            out.append(p)
            if len(out) >= cap:
                return out
    return out


def slugify(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "_".join(words[:3]) if words else "node"


def assign_ids(nodes, start=1):
    out = []
    for i, n in enumerate(nodes):
        node = dict(n)
        num = start + i
        node["id"] = f"node_{num:03d}"
        node["file"] = f"{node['id']}_{slugify(node.get('title', 'node'))}.md"
        out.append(node)
    return out


def render_node_markdown(node, today):
    fm = {
        "id": node["id"],
        "title": node["title"],
        "tags": node.get("tags") or ["general"],
        "evidence_tier": "unclassified",
        "pubmed_ids": [
            {"pmid": p, "supports": claim, "verified": False, "evidence_tier": "unclassified"}
            for p, claim in node.get("supports", {}).items()
        ],
        "entities": node.get("entities", []),
        "related_nodes": node.get("related_nodes", []),
        "relationships": node.get("relationships", {}),
        "created": today,
        "updated": today,
        "evaluation_status": "pending",
    }
    related = "\n".join(
        f"- [[{rid}]] ({node['relationships'].get(rid, 'related_to')})"
        for rid in node.get("related_nodes", [])) or "- (none yet)"
    body = (
        f"# {node['title']}\n\n"
        f"## Summary\n{node['summary']}\n\n"
        f"## Detail\n{node.get('detail', '')}\n\n"
        f"## Evidence\n\n### Literature\n"
        f"- (stamped by stamp_literature.py)\n\n"
        f"## Related Concepts\n{related}\n"
    )
    return fm, body


RELATIONSHIPS = {"is_part_of", "depends_on", "supports", "contradicts",
                 "related_to", "derived_from", "mechanism_of"}


def _shared_pmid_edges(nodes):
    edges = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if set(a.get("pmids", [])) & set(b.get("pmids", [])):
                edges.append({"source": a["id"], "target": b["id"],
                              "relationship": "related_to"})
    return edges


def apply_relationships(nodes, edges):
    by_id = {n["id"]: n for n in nodes}
    for e in edges:
        s, t, r = e["source"], e["target"], e["relationship"]
        for a, b in ((s, t), (t, s)):
            node = by_id.get(a)
            if node is None:
                continue
            if b not in node["related_nodes"]:
                node["related_nodes"].append(b)
        by_id[s]["relationships"][t] = r
    for n in nodes:
        n["related_nodes"] = sorted(set(n["related_nodes"]))


def assemble_manifest(kg_name, topic, breadth, sub_queries, nodes, edges, today):
    return {
        "kg_name": kg_name,
        "topic": topic,
        "version": 1,
        "created": today,
        "updated": today,
        "data_sources": ["pubmed"],
        "search_profile": {"breadth": breadth, "sub_queries": sub_queries, "updated": today},
        "nodes": [
            {
                "id": n["id"], "title": n["title"], "file": f"nodes/{n['file']}",
                "tags": n.get("tags") or ["general"], "summary": n["summary"],
                "keywords": n.get("keywords", []),
                "pubmed_ids": list(n.get("supports", {}).keys()),
                "evaluation_status": "pending",
                "evidence_tier": n.get("evidence_tier", "unclassified"),
                "entities": n.get("entities", []),
            }
            for n in nodes
        ],
        "edges": edges,
        "statistics": {},
    }


def weak_spots(manifest_nodes):
    out = []
    for n in manifest_nodes:
        if len(n.get("pubmed_ids", [])) <= 1 or \
           n.get("evaluation_status") == "failed" or n.get("quarantined"):
            out.append(n["id"])
    return out
