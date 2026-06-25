import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from linter_kg import KGLinter


def _write_kg(tmp_path, node_frontmatter_blocks):
    """Create a minimal KG: manifest.json + one node file per frontmatter block."""
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    nodes_index = []
    for i, fm_yaml in enumerate(node_frontmatter_blocks, start=1):
        nid = f"node_{i:03d}"
        (kg / "nodes" / f"{nid}.md").write_text(f"---\n{fm_yaml}---\n\nbody\n", encoding="utf-8")
        nodes_index.append({"id": nid, "title": "t", "file": f"nodes/{nid}.md",
                            "tags": ["x"], "summary": "s", "keywords": ["k"],
                            "pubmed_ids": ["111"], "evaluation_status": "passed"})
    manifest = {"kg_name": "KG_Test", "topic": "t", "created": "2026-01-01",
                "updated": "2026-01-01", "version": 1, "nodes": nodes_index, "edges": [],
                "statistics": {"total_nodes": len(nodes_index), "total_edges": 0,
                               "total_unique_pmids": 1, "evaluation_passed": len(nodes_index),
                               "evaluation_failed": 0}}
    (kg / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(kg)


def _findings(kg, check="quote_health"):
    linter = KGLinter(kg)
    linter.check_quote_health()
    return [f for f in linter.findings if f["check_id"] == check]


def test_verified_ref_without_quotes_is_info(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"


def test_verified_ref_with_quotes_is_clean(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: "a real sentence"\n        source: "abstract"\n')
    kg = _write_kg(tmp_path, [fm])
    assert _findings(kg) == []


def test_unverified_ref_without_quotes_is_clean(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: false\n')
    kg = _write_kg(tmp_path, [fm])
    assert _findings(kg) == []


def test_too_many_quotes_is_warning(tmp_path):
    items = "".join(f'      - text: "q{n}"\n        source: "abstract"\n' for n in range(4))
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          f'    quotes:\n{items}')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)


def test_bad_source_is_warning(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: "x"\n        source: "wikipedia"\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)


def test_empty_text_is_warning(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: ""\n        source: "abstract"\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)
