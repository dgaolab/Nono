import os
import sys
import pytest

from nono_librarian.lib import build


def test_tiers_have_expected_keys():
    for tier in ("narrow", "medium", "broad"):
        t = build.TIERS[tier]
        assert {"sub_queries", "max_results", "metadata", "full_text",
                "nodes_min", "nodes_max"} <= set(t)


def test_select_candidates_dedups_excludes_and_caps():
    out = build.select_candidates(
        [["1", "2", "3"], ["2", "4", "5"]], known_pmids={"3"}, cap=3)
    assert out == ["1", "2", "4"]


def test_slugify_is_snake_case_and_short():
    assert build.slugify("Melatonin & the SCN clock, revisited!") in (
        "melatonin_the_scn", "melatonin_scn_clock")
    assert " " not in build.slugify("A B C D E F")


def test_assign_ids_sequential_with_files():
    nodes = [{"title": "Sleep latency"}, {"title": "Clock genes"}]
    out = build.assign_ids(nodes, start=1)
    assert out[0]["id"] == "node_001"
    assert out[0]["file"].startswith("node_001_")
    assert out[1]["id"] == "node_002"


def test_render_node_markdown_frontmatter_and_body():
    node = {"id": "node_001", "title": "Sleep latency", "tags": ["sleep"],
            "summary": "Melatonin shortens sleep latency.", "detail": "Detail text.",
            "keywords": ["melatonin"], "entities": [{"name": "melatonin", "type": "drug"}],
            "supports": {"2": "Reports reduced latency."},
            "related_nodes": [], "relationships": {}}
    fm, body = build.render_node_markdown(node, today="2026-06-24")
    assert fm["id"] == "node_001"
    assert fm["evaluation_status"] == "pending"
    assert fm["pubmed_ids"][0] == {"pmid": "2", "supports": "Reports reduced latency.",
                                   "verified": False, "evidence_tier": "unclassified"}
    assert "## Summary" in body and "## Detail" in body and "### Literature" in body


_REL_NODES = [
    {"id": "node_001", "title": "A", "summary": "sa", "pmids": ["1", "2"]},
    {"id": "node_002", "title": "B", "summary": "sb", "pmids": ["2"]},
    {"id": "node_003", "title": "C", "summary": "sc", "pmids": ["9"]},
]


def test_shared_pmid_edges_links_overlapping_nodes():
    out = build._shared_pmid_edges(_REL_NODES)
    # node_001 & node_002 share PMID 2 → a related_to edge; node_003 shares none
    assert any({e["source"], e["target"]} == {"node_001", "node_002"} for e in out)
    assert all("node_003" not in (e["source"], e["target"]) for e in out)


def test_apply_relationships_populates_node_links():
    nodes = [{"id": "node_001", "related_nodes": [], "relationships": {}},
             {"id": "node_002", "related_nodes": [], "relationships": {}}]
    edges = [{"source": "node_001", "target": "node_002", "relationship": "supports"}]
    build.apply_relationships(nodes, edges)
    assert "node_002" in nodes[0]["related_nodes"]
    assert nodes[0]["relationships"]["node_002"] == "supports"


def test_assemble_manifest_minimal_shape():
    nodes = [{"id": "node_001", "title": "T", "file": "node_001_t.md", "tags": ["c"],
              "summary": "s", "keywords": ["k"], "supports": {"2": "x"},
              "entities": [], "evidence_tier": "unclassified"}]
    m = build.assemble_manifest("KG_X", "topic", "narrow", ["q1"], nodes, [], "2026-06-24")
    assert m["kg_name"] == "KG_X"
    assert m["data_sources"] == ["pubmed"]
    assert m["search_profile"]["sub_queries"] == ["q1"]
    n = m["nodes"][0]
    assert n["pubmed_ids"] == ["2"]            # manifest stores PMIDs as strings
    assert n["evaluation_status"] == "pending"


def test_weak_spots_finds_under_referenced_and_failed():
    nodes = [
        {"id": "node_001", "pubmed_ids": ["1"], "evaluation_status": "passed"},
        {"id": "node_002", "pubmed_ids": ["1", "2"], "evaluation_status": "passed"},
        {"id": "node_003", "pubmed_ids": ["3", "4"], "evaluation_status": "failed"},
    ]
    assert set(build.weak_spots(nodes)) == {"node_001", "node_003"}
