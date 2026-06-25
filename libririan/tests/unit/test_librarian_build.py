import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import librarian_build as lb

_ARTS = [
    {"pmid": "1", "title": "Melatonin and clock genes", "abstract": "Melatonin entrains the SCN."},
    {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."},
]


def _scripted_chat():
    """Return a chat that answers skeleton, then node, then relationships in order."""
    replies = iter([
        # skeleton
        '{"nodes": [{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "pmids": ["1"]},'
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]}]}',
        # node 1 synthesis
        '{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "detail": "d1",'
        '"tags": ["circadian"], "keywords": ["scn"], "entities": [], "supports": {"1": "entrains"}}',
        # node 2 synthesis
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "detail": "d2",'
        '"tags": ["sleep"], "keywords": ["latency"], "entities": [], "supports": {"2": "reduces"}}',
        # relationships
        '{"edges": [{"source": "node_001", "target": "node_002", "relationship": "related_to"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    return chat


def test_construct_graph_produces_nodes_and_manifest():
    nodes, manifest = lb.construct_graph(
        "melatonin", "KG_Mel", _ARTS, chat=_scripted_chat(),
        breadth="narrow", sub_queries=["q1"], today="2026-06-24")
    assert len(nodes) == 2
    assert manifest["nodes"][0]["id"] == "node_001"
    assert manifest["edges"][0]["relationship"] == "related_to"
    assert nodes[0]["related_nodes"] == ["node_002"]
