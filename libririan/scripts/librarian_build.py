#!/usr/bin/env python3
"""Claude-free KG build orchestrator — deterministic equivalent of build-kg.md.

State machine over the build pipeline. Calls lib/llm.py for narrow reasoning
steps and lib/pubmed.py for retrieval; reuses every existing deterministic
script for ledger, evidence tiers, literature stamping, index, validation,
embeddings, digest, and the Phase-2 evaluator. Aborts without writing on model
unavailability (never-mutate-on-failure).

Usage:
    python3 scripts/librarian_build.py "<topic>" [--output NAME] [--since YYYY-MM-DD]
            [--breadth narrow|medium|broad] [--interactive]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import build, evaluate, llm, pubmed
from lib.frontmatter import write as write_node


def construct_graph(topic, kg_name, articles, *, chat, breadth, sub_queries,
                    today, start_id=1):
    """Skeleton → per-node synthesis → ids → relationships → manifest."""
    tier = build.TIERS[breadth]
    skeleton = build.propose_skeleton(
        topic, articles, chat=chat,
        nodes_min=tier["nodes_min"], nodes_max=tier["nodes_max"])
    by_pmid = {a["pmid"]: a for a in articles}
    synthesized = [build.synthesize_node(s, by_pmid, chat=chat) for s in skeleton]
    # carry each skeleton's pmids onto the synthesized node for relationship fallback
    for syn, skel in zip(synthesized, skeleton):
        syn.setdefault("pmids", skel["pmids"])
        syn.setdefault("related_nodes", [])
        syn.setdefault("relationships", {})
    nodes = build.assign_ids(synthesized, start=start_id)
    edges = build.propose_relationships(nodes, chat=chat)
    build.apply_relationships(nodes, edges)
    manifest = build.assemble_manifest(
        kg_name, topic, breadth, sub_queries, nodes, edges, today)
    return nodes, manifest
