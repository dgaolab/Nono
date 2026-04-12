#!/usr/bin/env python3
"""Deterministic _index.md generator for a knowledge graph.

Usage:
    python3 scripts/generate_index.py <kg_folder> [--dry-run] [--overview-text <text>]

Reads manifest.json and generates the full _index.md with category groupings,
mermaid diagrams, statistics, and quarantine sections.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse, write, serialize


def _sanitize_mermaid_label(title, max_len=40):
    """Sanitize and truncate a title for use as a mermaid node label."""
    label = title.replace('"', '').replace('|', '/').replace('[', '').replace(']', '')
    if len(label) > max_len:
        label = label[:max_len - 3] + "..."
    return label


def _file_stem(file_field):
    """Extract stem from a manifest node file field like 'nodes/foo.md'."""
    name = file_field
    if name.startswith("nodes/"):
        name = name[len("nodes/"):]
    if name.endswith(".md"):
        name = name[:-3]
    return name


def _category_title(tag):
    """Convert a tag like 'some-category' to 'Some Category'."""
    return tag.replace("-", " ").title()


def _extract_overview(kg_folder):
    """Try to extract existing overview text from _index.md."""
    index_path = os.path.join(kg_folder, "_index.md")
    if not os.path.exists(index_path):
        return None
    try:
        _, body = parse(index_path)
    except (ValueError, FileNotFoundError):
        return None
    # Find text between ## Overview and the next ## heading
    match = re.search(r'## Overview\s*\n(.*?)(?=\n## |\Z)', body, re.DOTALL)
    if match:
        text = match.group(1).strip()
        if text:
            return text
    return None


def _build_mermaid_flat(active_nodes, active_edges, node_titles):
    """Build a flat mermaid graph (< 30 nodes)."""
    lines = ["```mermaid", "graph TD"]
    for edge in active_edges:
        src, tgt = edge["source"], edge["target"]
        rel = edge["relationship"]
        src_label = _sanitize_mermaid_label(node_titles.get(src, src))
        tgt_label = _sanitize_mermaid_label(node_titles.get(tgt, tgt))
        lines.append(f'    {src}["{src_label}"] -->|{rel}| {tgt}["{tgt_label}"]')
    if not active_edges:
        for node in active_nodes:
            label = _sanitize_mermaid_label(node_titles.get(node["id"], node["id"]))
            lines.append(f'    {node["id"]}["{label}"]')
    lines.append("```")
    return "\n".join(lines)


def _build_mermaid_subgraph(active_nodes, active_edges, node_titles, categories):
    """Build a subgraph-per-category mermaid graph (30-50 nodes)."""
    lines = ["```mermaid", "graph TD"]
    # Build category -> node list mapping
    cat_nodes = {}
    for node in active_nodes:
        cat = node["tags"][0] if node.get("tags") else "uncategorized"
        cat_nodes.setdefault(cat, []).append(node)
    for cat in sorted(cat_nodes.keys()):
        safe_cat = cat.replace("-", "_")
        lines.append(f'    subgraph {safe_cat}["{_category_title(cat)}"]')
        for node in sorted(cat_nodes[cat], key=lambda n: n["id"]):
            label = _sanitize_mermaid_label(node_titles[node["id"]])
            lines.append(f'        {node["id"]}["{label}"]')
        lines.append("    end")
    for edge in active_edges:
        lines.append(f'    {edge["source"]} -->|{edge["relationship"]}| {edge["target"]}')
    lines.append("```")
    return "\n".join(lines)


def _build_mermaid_category(active_nodes, active_edges, node_titles, categories):
    """Build category-level overview + per-category details (50+ nodes)."""
    cat_nodes = {}
    for node in active_nodes:
        cat = node["tags"][0] if node.get("tags") else "uncategorized"
        cat_nodes.setdefault(cat, []).append(node)

    # Category-level overview diagram
    lines = ["```mermaid", "graph TD"]
    cat_ids = {}
    for cat in sorted(cat_nodes.keys()):
        safe_id = "cat_" + cat.replace("-", "_")
        cat_ids[cat] = safe_id
        count = len(cat_nodes[cat])
        lines.append(f'    {safe_id}["{_category_title(cat)} ({count})"]')
    # Add inter-category edges
    node_to_cat = {}
    for cat, nodes in cat_nodes.items():
        for n in nodes:
            node_to_cat[n["id"]] = cat
    cat_edges_seen = set()
    for edge in active_edges:
        src_cat = node_to_cat.get(edge["source"])
        tgt_cat = node_to_cat.get(edge["target"])
        if src_cat and tgt_cat and src_cat != tgt_cat:
            pair = (src_cat, tgt_cat)
            if pair not in cat_edges_seen:
                cat_edges_seen.add(pair)
                lines.append(f'    {cat_ids[src_cat]} --> {cat_ids[tgt_cat]}')
    lines.append("```")
    parts = ["\n".join(lines)]

    # Per-category detail diagrams in collapsible sections
    for cat in sorted(cat_nodes.keys()):
        nodes_in_cat = sorted(cat_nodes[cat], key=lambda n: n["id"])
        node_ids_in_cat = {n["id"] for n in nodes_in_cat}
        cat_edges = [e for e in active_edges
                     if e["source"] in node_ids_in_cat or e["target"] in node_ids_in_cat]
        detail = [f'<details><summary>{_category_title(cat)} detail</summary>', "",
                  "```mermaid", "graph TD"]
        for node in nodes_in_cat:
            label = _sanitize_mermaid_label(node_titles[node["id"]])
            detail.append(f'    {node["id"]}["{label}"]')
        for edge in cat_edges:
            detail.append(f'    {edge["source"]} -->|{edge["relationship"]}| {edge["target"]}')
        detail.extend(["```", "", "</details>"])
        parts.append("\n".join(detail))

    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Generate _index.md from manifest.json")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print generated content to stdout instead of writing")
    parser.add_argument("--overview-text", default=None,
                        help="Overview text to use (overrides preserved text)")
    args = parser.parse_args()

    kg_folder = args.kg_folder
    manifest_path = os.path.join(kg_folder, "manifest.json")

    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {kg_folder}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    # Determine overview text
    overview_source = "placeholder"
    if args.overview_text:
        overview_text = args.overview_text
        overview_source = "provided"
    else:
        preserved = _extract_overview(kg_folder)
        if preserved:
            overview_text = preserved
            overview_source = "preserved"
        else:
            overview_text = "<!-- Write overview here -->"

    # Separate active vs quarantined nodes
    all_nodes = manifest.get("nodes", [])
    active_nodes = [n for n in all_nodes if not n.get("quarantined", False)]
    quarantined_nodes = [n for n in all_nodes if n.get("quarantined", False)]

    # Group active nodes by primary category (tags[0])
    categories = {}
    for node in active_nodes:
        cat = node["tags"][0] if node.get("tags") else "uncategorized"
        categories.setdefault(cat, []).append(node)
    for cat in categories:
        categories[cat].sort(key=lambda n: n["id"])

    # Build node ID sets for edge filtering
    active_ids = {n["id"] for n in active_nodes}
    node_titles = {n["id"]: n["title"] for n in active_nodes}

    # Filter edges to active-only
    all_edges = manifest.get("edges", [])
    active_edges = [e for e in all_edges
                    if e["source"] in active_ids and e["target"] in active_ids]

    stats = manifest.get("statistics", {})
    today = datetime.date.today().isoformat()

    # --- Build frontmatter ---
    frontmatter = {
        "kg_name": manifest.get("kg_name", ""),
        "topic": manifest.get("topic", ""),
        "total_nodes": len(active_nodes),
        "last_updated": today,
    }

    # --- Build body sections ---
    body_parts = []

    # a. Title
    body_parts.append(f'# Knowledge Graph: {manifest.get("topic", "")}')

    # b. Overview
    body_parts.append(f"## Overview\n\n{overview_text}")

    # c. Nodes by category
    nodes_lines = ["## Nodes"]
    for cat in sorted(categories.keys()):
        nodes_lines.append(f"\n### {_category_title(cat)}")
        for node in categories[cat]:
            stem = _file_stem(node.get("file", ""))
            tier = node.get("evidence_tier", "unclassified")
            nodes_lines.append(f"- [[{stem}]] - {node['title']} [{tier}]")
    body_parts.append("\n".join(nodes_lines))

    # d. Statistics
    stat_lines = ["## Statistics", ""]
    stat_lines.append(f"- Total nodes: {len(active_nodes)}")
    stat_lines.append(f"- Total unique references: {stats.get('total_unique_pmids', 0)}")
    eval_p = stats.get("evaluation_passed", 0)
    eval_f = stats.get("evaluation_failed", 0)
    stat_lines.append(f"- Evaluation: {eval_p} passed, {eval_f} failed")
    nct = stats.get("total_nct_ids", 0)
    if nct > 0:
        stat_lines.append(f"- Clinical trial IDs: {nct}")
    chembl = stats.get("total_chembl_ids", 0)
    if chembl > 0:
        stat_lines.append(f"- ChEMBL IDs: {chembl}")
    body_parts.append("\n".join(stat_lines))

    # e. Graph Structure (mermaid)
    n_active = len(active_nodes)
    if n_active < 30:
        mermaid_tier = "flat"
        mermaid_content = _build_mermaid_flat(active_nodes, active_edges, node_titles)
    elif n_active <= 50:
        mermaid_tier = "subgraph"
        mermaid_content = _build_mermaid_subgraph(
            active_nodes, active_edges, node_titles, categories)
    else:
        mermaid_tier = "category"
        mermaid_content = _build_mermaid_category(
            active_nodes, active_edges, node_titles, categories)
    body_parts.append(f"## Graph Structure\n\n{mermaid_content}")

    # f. Quarantine section
    if quarantined_nodes:
        q_lines = [
            f"<details><summary>Quarantined Nodes ({len(quarantined_nodes)})</summary>",
            "",
            "These nodes failed independent verification and are excluded from search and linking.",
            "They may be reinstated after UPDATE mode finds better references.",
            "",
        ]
        for node in sorted(quarantined_nodes, key=lambda n: n["id"]):
            stem = _file_stem(node.get("file", ""))
            updated = node.get("updated", "unknown")
            q_lines.append(f"- ~~[[{stem}]]~~ — evaluation failed ({updated})")
        q_lines.append("</details>")
        body_parts.append("\n".join(q_lines))

    body = "\n\n".join(body_parts) + "\n"

    # --- Output ---
    if args.dry_run:
        print(serialize(frontmatter, body))
    else:
        index_path = os.path.join(kg_folder, "_index.md")
        write(index_path, frontmatter, body)
        summary = {
            "total_nodes": len(all_nodes),
            "active_nodes": len(active_nodes),
            "quarantined_nodes": len(quarantined_nodes),
            "categories": len(categories),
            "mermaid_tier": mermaid_tier,
            "overview_source": overview_source,
        }
        json.dump(summary, sys.stdout)
        print()


if __name__ == "__main__":
    main()
