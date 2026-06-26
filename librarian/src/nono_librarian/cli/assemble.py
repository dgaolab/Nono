#!/usr/bin/env python3
"""`nono-librarian assemble` — turn agent _nodes.json into node files + manifest (no model)."""
import argparse
import datetime
import json
import os
import sys

import jsonschema

from nono_librarian.lib import build
from nono_librarian.lib.frontmatter import write as write_node
from nono_librarian.paths import data_file


def load_nodes_input(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    schema = json.loads(data_file("schemas", "nodes_input_schema.json").read_text())
    jsonschema.validate(raw, schema)
    return raw


def _node_seed(n):
    """Map one input node to the dict shape lib.build helpers expect."""
    supports = {pi["pmid"]: pi["supports"] for pi in n["pubmed_ids"]}
    return {
        "title": n["title"], "summary": n["summary"], "detail": n.get("detail", ""),
        "tags": n.get("tags") or ["general"], "category": (n.get("tags") or ["general"])[0],
        "keywords": n.get("keywords", []), "entities": n.get("entities", []),
        "pmids": n.get("pmids") or list(supports.keys()), "supports": supports,
        "related_nodes": [], "relationships": {},
    }


def build_nodes(raw_nodes, start_id):
    seeds = [_node_seed(n) for n in raw_nodes]
    nodes = build.assign_ids(seeds, start=start_id)
    title_to_id = {n["title"]: n["id"] for n in nodes}
    edges = []
    for raw, node in zip(raw_nodes, nodes):
        for rel in raw.get("related_to", []) or []:
            tgt = rel.get("target_id") or title_to_id.get(rel.get("target_title", ""))
            r = rel["relationship"]
            if tgt and tgt in title_to_id.values() and tgt != node["id"] \
                    and r in build.RELATIONSHIPS:
                edges.append({"source": node["id"], "target": tgt, "relationship": r})
    if not edges:
        edges = build._shared_pmid_edges(nodes)
    build.apply_relationships(nodes, edges)
    return nodes, edges


def judgments_from_input(raw_nodes, nodes):
    out = {}
    for raw, node in zip(raw_nodes, nodes):
        per = {}
        for pi in raw["pubmed_ids"]:
            if "verdict" in pi:
                per[pi["pmid"]] = {"verdict": pi["verdict"],
                                   "reasoning": pi.get("reasoning", ""),
                                   "quotes": pi.get("quotes", [])}
        if per:
            out[node["id"]] = per
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian assemble",
                                     description="Write node files + manifest from _nodes.json")
    parser.add_argument("kg_folder")
    parser.add_argument("--nodes", required=True, help="agent _nodes.json")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default="medium")
    parser.add_argument("--start-id", type=int, default=1)
    args = parser.parse_args(argv)

    raw = load_nodes_input(args.nodes)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    nodes, edges = build_nodes(raw["nodes"], args.start_id)

    nodes_dir = os.path.join(args.kg_folder, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)
    for n in nodes:
        fm, body = build.render_node_markdown(n, today)
        write_node(os.path.join(nodes_dir, n["file"]), fm, body)

    kg_name = os.path.basename(os.path.normpath(args.kg_folder))
    manifest = build.assemble_manifest(kg_name, args.topic, args.breadth,
                                       raw.get("sub_queries", []), nodes, edges, today)
    with open(os.path.join(args.kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(args.kg_folder, "_judgments.json"), "w", encoding="utf-8") as fh:
        json.dump(judgments_from_input(raw["nodes"], nodes), fh, indent=2)
    print(f"Assembled {len(nodes)} nodes → {args.kg_folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
