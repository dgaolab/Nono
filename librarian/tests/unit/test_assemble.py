import json
from nono_librarian.cli import assemble
from nono_librarian.lib.frontmatter import parse as parse_node

RAW = {
    "sub_queries": ["brca1 repair"],
    "nodes": [
        {"title": "BRCA1 in HR repair", "summary": "BRCA1 enables HR.",
         "detail": "para", "tags": ["mechanism"], "keywords": ["brca1", "hr"],
         "entities": [{"name": "BRCA1", "type": "gene"}],
         "pmids": ["111"],
         "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 enables HR",
                         "verdict": "supported",
                         "quotes": [{"text": "BRCA1 enables HR", "source": "abstract"}]}]},
        {"title": "PARP synthetic lethality", "summary": "PARPi kills BRCA1-null.",
         "tags": ["therapy"], "pmids": ["111", "222"],
         "pubmed_ids": [{"pmid": "222", "supports": "PARPi is lethal in BRCA1-null"}]},
    ],
}


def test_assemble_writes_nodes_manifest_and_judgments(tmp_path):
    kg = tmp_path / "KG_Test"
    npath = tmp_path / "_nodes.json"
    npath.write_text(json.dumps(RAW))
    rc = assemble.main([str(kg), "--nodes", str(npath), "--topic", "BRCA1",
                        "--breadth", "narrow"])
    assert rc == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert [n["id"] for n in manifest["nodes"]] == ["node_001", "node_002"]
    # shared PMID 111 → fallback related_to edge between the two nodes
    assert any(e["source"] == "node_001" and e["target"] == "node_002"
               for e in manifest["edges"])
    fm, _ = parse_node(str(kg / "nodes" / manifest["nodes"][0]["file"].split("/")[-1]))
    assert fm["pubmed_ids"][0]["pmid"] == "111"
    judg = json.loads((kg / "_judgments.json").read_text())
    assert judg["node_001"]["111"]["verdict"] == "supported"


def test_assemble_rejects_invalid_input(tmp_path):
    import pytest, jsonschema
    bad = tmp_path / "_nodes.json"
    bad.write_text(json.dumps({"nodes": [{"title": "x"}]}))  # missing summary/pubmed_ids
    with pytest.raises(jsonschema.ValidationError):
        assemble.load_nodes_input(str(bad))


def test_assemble_start_id_appends(tmp_path):
    kg = tmp_path / "KG_Test"
    npath = tmp_path / "_nodes.json"
    npath.write_text(json.dumps(RAW))
    rc = assemble.main([str(kg), "--nodes", str(npath), "--topic", "BRCA1",
                        "--breadth", "narrow", "--start-id", "7"])
    assert rc == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["nodes"][0]["id"] == "node_007"


RAW2 = {
    "sub_queries": ["tp53 mutation"],
    "nodes": [
        {"title": "TP53 mutation in cancer", "summary": "TP53 is mutated in many cancers.",
         "detail": "para", "tags": ["mechanism"], "keywords": ["tp53"],
         "entities": [{"name": "TP53", "type": "gene"}],
         "pmids": ["999"],
         "pubmed_ids": [{"pmid": "999", "supports": "TP53 is mutated",
                         "verdict": "supported",
                         "quotes": [{"text": "TP53 is mutated", "source": "abstract"}]}]},
    ],
}


def test_assemble_merges_into_existing_manifest(tmp_path):
    """UPDATE mode: second assemble must merge into existing manifest, not overwrite."""
    kg = tmp_path / "KG_Test"

    # First pass: build from RAW (2 nodes → node_001, node_002)
    npath1 = tmp_path / "_nodes1.json"
    npath1.write_text(json.dumps(RAW))
    assemble.main([str(kg), "--nodes", str(npath1), "--topic", "BRCA1", "--breadth", "narrow"])

    manifest_v1 = json.loads((kg / "manifest.json").read_text())
    assert [n["id"] for n in manifest_v1["nodes"]] == ["node_001", "node_002"]
    assert manifest_v1["version"] == 1

    # Second pass: update with RAW2 (1 new node → node_003, using --start-id 3)
    npath2 = tmp_path / "_nodes2.json"
    npath2.write_text(json.dumps(RAW2))
    rc = assemble.main([str(kg), "--nodes", str(npath2), "--topic", "BRCA1",
                        "--breadth", "narrow", "--start-id", "3"])
    assert rc == 0

    manifest_v2 = json.loads((kg / "manifest.json").read_text())
    ids = [n["id"] for n in manifest_v2["nodes"]]
    # Both original nodes AND the new node must be present
    assert "node_001" in ids
    assert "node_002" in ids
    assert "node_003" in ids
    # Version must be bumped
    assert manifest_v2["version"] == 2
