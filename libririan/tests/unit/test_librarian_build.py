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


def test_gather_articles_dedups_and_attaches_full_text():
    def esearch(q, retmax=10, **kw):
        return {"melatonin clock": ["1", "2"], "melatonin sleep": ["2", "3"]}[q]
    def fetch_metadata(pmids):
        return {p: {"title": f"T{p}", "abstract": f"abs{p}", "pmcid": ("PMC9" if p == "1" else None),
                    "authors": [], "journal": "J", "year": "2021", "publication_types": []}
                for p in pmids}
    def fetch_full_text(pmcid):
        return "FULLTEXT BODY"
    tier = lb.build.TIERS["narrow"]
    arts = lb.gather_articles(["melatonin clock", "melatonin sleep"],
                              esearch=esearch, fetch_metadata=fetch_metadata,
                              fetch_full_text=fetch_full_text, known_pmids=set(), tier=tier)
    pmids = {a["pmid"] for a in arts}
    assert pmids == {"1", "2", "3"}
    a1 = next(a for a in arts if a["pmid"] == "1")
    assert "FULLTEXT BODY" in a1["abstract"]      # full text appended for PMC article


def test_ledger_batch_for_used_shape():
    arts = [{"pmid": "1", "metadata": {"title": "T1", "authors": [], "journal": "J",
                                        "year": "2021", "publication_types": ["Journal Article"]}}]
    batch = lb.ledger_batch_for_used(arts)
    assert batch[0]["disposition"] == "used"
    assert batch[0]["pmid"] == "1"
    assert batch[0]["publication_types"] == ["Journal Article"]
