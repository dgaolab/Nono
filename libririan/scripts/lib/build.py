#!/usr/bin/env python3
"""Claude-free build reasoning — the model-driven steps of build-kg, made local.

Every function that needs the model takes an injected ``chat`` callable
(`scripts/lib/llm.py`); retrieval is done by the orchestrator via
`scripts/lib/pubmed.py`. The orchestrator owns IDs, dedup, PMID filtering, and
file writes — these functions only reason and return validated data.
"""

import re

from lib import llm

TIERS = {
    "narrow": {"sub_queries": 3, "max_results": 10, "metadata": 15,
               "full_text": 5, "nodes_min": 8, "nodes_max": 15},
    "medium": {"sub_queries": 4, "max_results": 20, "metadata": 25,
               "full_text": 8, "nodes_min": 15, "nodes_max": 30},
    "broad":  {"sub_queries": 6, "max_results": 30, "metadata": 40,
               "full_text": 12, "nodes_min": 25, "nodes_max": 45},
}


class BuildError(RuntimeError):
    """Raised when a model reply for a build step cannot be used."""


def _ask_json(chat, messages, *, temperature=0.2):
    """Call the model and parse a JSON object from the reply, or raise BuildError."""
    reply = chat(messages, temperature=temperature)
    try:
        return llm.extract_json_object(reply)
    except ValueError as e:
        raise BuildError(f"model reply was not valid JSON: {e}") from e


_PLAN_SYS = (
    "You plan a PubMed literature search for a biomedical topic. Classify the "
    "topic breadth and propose focused sub-queries. Reply with ONE JSON object "
    "and nothing else: "
    '{"breadth": "narrow|medium|broad", "sub_queries": ["...", "..."]}. '
    "narrow = single mechanism/intervention (3 sub-queries), medium = a topic "
    "with several facets (4), broad = multi-disciplinary survey (6). Each "
    "sub-query is a specific PubMed search string."
)


def plan_search(topic, *, chat, breadth_override=None):
    """Classify breadth and generate sub-queries in one model call."""
    user = f"TOPIC:\n{topic}"
    if breadth_override:
        n = TIERS[breadth_override]["sub_queries"]
        user += f"\n\nUse breadth='{breadth_override}' and produce exactly {n} sub-queries."
    obj = _ask_json(chat, [{"role": "system", "content": _PLAN_SYS},
                           {"role": "user", "content": user}])
    breadth = breadth_override or str(obj.get("breadth", "")).strip().lower()
    if breadth not in TIERS:
        breadth = "medium"
    subs = [str(s).strip() for s in (obj.get("sub_queries") or []) if str(s).strip()]
    if not subs:
        raise BuildError("no sub-queries produced")
    return {"breadth": breadth, "sub_queries": subs}


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


def _articles_blob(articles):
    return "\n\n".join(
        f"PMID {a['pmid']}: {a['title']}\n{a.get('abstract', '')}" for a in articles)


_SKELETON_SYS = (
    "You design a biomedical knowledge graph from article abstracts. Propose "
    "coherent knowledge nodes, each ONE citable claim/concept. Reply with ONE "
    "JSON object: {\"nodes\": [{\"title\": \"...\", \"summary\": \"one sentence\", "
    "\"pmids\": [\"<pmid>\", ...]}]}. Use ONLY PMIDs from the provided articles. "
    "Each node cites the PMIDs whose abstracts support it."
)


def propose_skeleton(topic, articles, *, chat, nodes_min, nodes_max):
    """Propose node skeletons, keeping only real PMIDs and non-empty nodes."""
    allowed = {a["pmid"] for a in articles}
    user = (f"TOPIC: {topic}\nPropose {nodes_min}-{nodes_max} nodes.\n\n"
            f"ARTICLES:\n{_articles_blob(articles)}")
    obj = _ask_json(chat, [{"role": "system", "content": _SKELETON_SYS},
                           {"role": "user", "content": user}])
    out = []
    for n in obj.get("nodes", []) or []:
        title = str(n.get("title", "")).strip()
        summary = str(n.get("summary", "")).strip()
        pmids = [p for p in (n.get("pmids") or []) if p in allowed]
        if title and summary and pmids:
            out.append({"title": title, "summary": summary, "pmids": pmids})
    if not out:
        raise BuildError("skeleton produced no usable nodes")
    return out


_NODE_SYS = (
    "You write one biomedical knowledge-graph node from its supporting articles. "
    "Reply with ONE JSON object: {\"title\": \"...\", \"summary\": \"one sentence\", "
    "\"detail\": \"a paragraph\", \"tags\": [\"category\", \"...\"], "
    "\"keywords\": [\"3-8 search terms\"], "
    "\"entities\": [{\"name\": \"...\", \"type\": \"gene|variant|phenotype|drug|pathway|protein|disease\"}], "
    "\"supports\": {\"<pmid>\": \"what this article contributes\"}}. "
    "tags[0] is a broad category. Use ONLY the provided PMIDs. Do NOT invent "
    "identifiers; entities carry name and type only."
)


def synthesize_node(skeleton_node, articles_by_pmid, *, chat):
    """Flesh out one node; filter supports to real PMIDs, strip entity IDs."""
    pmids = skeleton_node["pmids"]
    arts = [articles_by_pmid[p] for p in pmids if p in articles_by_pmid]
    user = (f"NODE TITLE: {skeleton_node['title']}\n"
            f"WORKING SUMMARY: {skeleton_node['summary']}\n"
            f"SUPPORTING PMIDS: {', '.join(pmids)}\n\n"
            f"ARTICLES:\n{_articles_blob(arts)}")
    obj = _ask_json(chat, [{"role": "system", "content": _NODE_SYS},
                           {"role": "user", "content": user}])
    allowed = set(pmids)
    supports = {k: str(v).strip() for k, v in (obj.get("supports") or {}).items()
                if k in allowed}
    if not supports:                       # never leave a node unreferenced
        supports = {p: skeleton_node["summary"] for p in pmids}
    entities = [{"name": str(e.get("name", "")).strip(), "type": str(e.get("type", "")).strip()}
                for e in (obj.get("entities") or []) if str(e.get("name", "")).strip()]
    tags = [str(t).strip() for t in (obj.get("tags") or []) if str(t).strip()] or ["general"]
    keywords = [str(k).strip() for k in (obj.get("keywords") or []) if str(k).strip()]
    return {
        "title": str(obj.get("title") or skeleton_node["title"]).strip(),
        "summary": str(obj.get("summary") or skeleton_node["summary"]).strip(),
        "detail": str(obj.get("detail", "")).strip(),
        "tags": tags,
        "category": tags[0],
        "keywords": keywords,
        "entities": entities,
        "supports": supports,
    }


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
