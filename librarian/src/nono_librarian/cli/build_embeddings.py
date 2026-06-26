#!/usr/bin/env python3
"""Build/refresh a KG's node embedding index (_embeddings.json).

Hash-keyed incremental: re-embeds only nodes whose embedding-text changed,
drops removed nodes. Writes <kg_folder>/_embeddings.json atomically. The index
is a git-ignored derived cache. On model failure, exits non-zero and writes
nothing.

Usage:
    python3 scripts/build_embeddings.py <kg_folder> [--json]
"""

import argparse
import json
import os
import sys
import tempfile

from nono_librarian.lib import embeddings

INDEX_FILENAME = "_embeddings.json"


def compute_index(nodes, existing_index, embed_fn):
    """Return (index, stats). Reuses cached vectors for unchanged (by hash) nodes."""
    existing_nodes = (existing_index or {}).get("nodes", {})
    plan = []          # (node_id, hash, needs_embed: bool)
    to_embed_texts = []
    to_embed_ids = []
    reuse = {}         # node_id -> vector
    for node in nodes:
        nid = node.get("id", "")
        text = embeddings.node_embedding_text(node)
        h = embeddings.text_hash(text)
        prev = existing_nodes.get(nid)
        if prev and prev.get("hash") == h:
            reuse[nid] = prev["vector"]
            plan.append((nid, h, False))
        else:
            plan.append((nid, h, True))
            to_embed_texts.append(text)
            to_embed_ids.append(nid)
    vectors = embed_fn(to_embed_texts) if to_embed_texts else []
    embedded = dict(zip(to_embed_ids, vectors))
    index_nodes = {}
    for nid, h, needs in plan:
        index_nodes[nid] = {"hash": h, "vector": embedded[nid] if needs else reuse[nid]}
    manifest_ids = {n.get("id", "") for n in nodes}
    dropped = len(set(existing_nodes) - manifest_ids)
    stats = {"embedded": len(to_embed_ids), "reused": len(reuse),
             "dropped": dropped, "total": len(index_nodes)}
    return {"model": embeddings.MODEL_NAME, "dim": embeddings.DIM, "nodes": index_nodes}, stats


def _write_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build/refresh a KG's node embedding index.")
    parser.add_argument("kg_folder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = os.path.join(args.kg_folder, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {args.kg_folder}", file=sys.stderr)
        sys.exit(2)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        nodes = json.load(fh).get("nodes", [])

    index_path = os.path.join(args.kg_folder, INDEX_FILENAME)
    existing = None
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if existing.get("model") != embeddings.MODEL_NAME:
                existing = None   # model changed -> full rebuild
        except (json.JSONDecodeError, OSError):
            existing = None

    try:
        index, stats = compute_index(nodes, existing, embeddings.embed_texts)
    except embeddings.EmbeddingsUnavailable as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    _write_atomic(index_path, index)
    payload = {"kg": os.path.basename(os.path.abspath(args.kg_folder)), **stats}
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        print(f"Embeddings: {stats['embedded']} embedded, {stats['reused']} reused, "
              f"{stats['dropped']} dropped ({stats['total']} total).", file=sys.stderr)


if __name__ == "__main__":
    main()
