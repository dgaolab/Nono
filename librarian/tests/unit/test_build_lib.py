import os
import sys
import pytest

from nono_librarian.lib import build


def _chat_returning(reply):
    def chat(messages, **kw):
        return reply
    return chat


def test_tiers_have_expected_keys():
    for tier in ("narrow", "medium", "broad"):
        t = build.TIERS[tier]
        assert {"sub_queries", "max_results", "metadata", "full_text",
                "nodes_min", "nodes_max"} <= set(t)


def test_plan_search_returns_breadth_and_subqueries():
    chat = _chat_returning(
        '{"breadth": "medium", "sub_queries": ["a immune response", "b delivery"]}')
    out = build.plan_search("mRNA vaccines", chat=chat)
    assert out["breadth"] == "medium"
    assert out["sub_queries"] == ["a immune response", "b delivery"]


def test_plan_search_honors_breadth_override():
    chat = _chat_returning('{"breadth": "broad", "sub_queries": ["x", "y", "z"]}')
    out = build.plan_search("t", chat=chat, breadth_override="narrow")
    assert out["breadth"] == "narrow"


def test_plan_search_raises_on_unparseable():
    chat = _chat_returning("I cannot help")
    with pytest.raises(build.BuildError):
        build.plan_search("t", chat=chat)


def test_select_candidates_dedups_excludes_and_caps():
    out = build.select_candidates(
        [["1", "2", "3"], ["2", "4", "5"]], known_pmids={"3"}, cap=3)
    assert out == ["1", "2", "4"]


_ARTS = [
    {"pmid": "1", "title": "Melatonin and clock genes", "abstract": "Melatonin entrains the SCN."},
    {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."},
]


def test_propose_skeleton_filters_hallucinated_pmids_and_empty_nodes():
    reply = (
        '{"nodes": ['
        '{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "pmids": ["1", "999"]},'
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]},'
        '{"title": "Ghost", "summary": "Nothing real.", "pmids": ["999"]}'
        ']}'
    )
    def chat(messages, **kw):
        return reply
    out = build.propose_skeleton("melatonin", _ARTS, chat=chat, nodes_min=1, nodes_max=10)
    assert len(out) == 2
    assert out[0]["pmids"] == ["1"]          # 999 dropped
    assert out[1]["pmids"] == ["2"]
    assert all(n["title"] and n["summary"] for n in out)


def test_synthesize_node_shapes_fields_and_filters_supports():
    skel = {"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]}
    arts = {"2": {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."}}
    reply = (
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.",'
        '"detail": "Across trials melatonin reduced latency.",'
        '"tags": ["sleep", "melatonin"], "keywords": ["melatonin", "sleep latency"],'
        '"entities": [{"name": "melatonin", "type": "drug", "normalized_id": "FAKE:1"}],'
        '"supports": {"2": "Reports reduced sleep latency.", "999": "should be dropped"}}'
    )
    def chat(messages, **kw):
        return reply
    out = build.synthesize_node(skel, arts, chat=chat)
    assert out["category"] == "sleep"
    assert set(out["supports"]) == {"2"}                 # 999 dropped
    assert out["entities"][0] == {"name": "melatonin", "type": "drug"}  # no id
    assert out["keywords"] and out["detail"]


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


def test_propose_relationships_validates_edges():
    reply = ('{"edges": [{"source": "node_001", "target": "node_002", "relationship": "supports"},'
             '{"source": "node_001", "target": "node_999", "relationship": "supports"},'
             '{"source": "node_002", "target": "node_001", "relationship": "bogus"}]}')
    def chat(messages, **kw):
        return reply
    out = build.propose_relationships(_REL_NODES, chat=chat)
    assert {"source": "node_001", "target": "node_002", "relationship": "supports"} in out
    assert all(e["target"] != "node_999" for e in out)
    assert all(e["relationship"] != "bogus" for e in out)


def test_propose_relationships_falls_back_to_shared_pmids():
    def chat(messages, **kw):
        return "garbage"
    out = build.propose_relationships(_REL_NODES, chat=chat)
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


def test_gap_fill_queries_returns_strings():
    def chat(messages, **kw):
        return '{"queries": ["alt term A", "MeSH B"]}'
    out = build.gap_fill_queries("topic", ["summary one"], chat=chat, count=2)
    assert out == ["alt term A", "MeSH B"]
