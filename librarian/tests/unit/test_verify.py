import json
from nono_librarian.cli import verify
from nono_librarian.lib.frontmatter import write as write_node, parse as parse_node


def _meta(pmids):
    return {p: {"title": f"T{p}",
                "abstract": "BRCA1 loss impairs homologous recombination.",
                "pmcid": None} for p in pmids}


def _ft(_pmcid):
    return ""


def _seed_kg(tmp_path):
    kg = tmp_path / "KG"
    (kg / "nodes").mkdir(parents=True)
    fm = {"id": "node_001", "title": "BRCA1",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR",
                          "verified": False}],
          "evaluation_status": "pending"}
    write_node(str(kg / "nodes" / "node_001_brca1.md"), fm, "# BRCA1\n")
    (kg / "manifest.json").write_text(json.dumps(
        {"kg_name": "KG", "nodes": [{"id": "node_001", "title": "BRCA1",
         "file": "nodes/node_001_brca1.md", "pubmed_ids": ["111"],
         "evaluation_status": "pending"}], "edges": [], "statistics": {}}))
    return kg


def test_verify_marks_verified_with_quote(tmp_path):
    kg = _seed_kg(tmp_path)
    judg = {"node_001": {"111": {"verdict": "supported",
            "quotes": [{"text": "impairs homologous recombination", "source": "abstract"}]}}}
    entries = verify.verify_kg(str(kg), judg, fetch_metadata=_meta, fetch_full_text=_ft)
    assert entries[0]["overall_status"] == "passed"
    fm, _ = parse_node(str(kg / "nodes" / "node_001_brca1.md"))
    assert fm["pubmed_ids"][0]["verified"] is True
    assert fm["evaluation_status"] == "passed"


def test_verify_forces_fail_without_quote(tmp_path):
    kg = _seed_kg(tmp_path)
    judg = {"node_001": {"111": {"verdict": "supported",
            "quotes": [{"text": "not in source", "source": "abstract"}]}}}
    entries = verify.verify_kg(str(kg), judg, fetch_metadata=_meta, fetch_full_text=_ft)
    assert entries[0]["overall_status"] == "failed"
    fm, _ = parse_node(str(kg / "nodes" / "node_001_brca1.md"))
    assert fm["quarantined"] is True
