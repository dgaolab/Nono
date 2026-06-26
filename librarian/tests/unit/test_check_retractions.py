import json
import os
import subprocess
import sys

from nono_librarian.cli import check_retractions


def _write_ledger(kg, entries):
    (kg / "_pmid_ledger.json").write_text(json.dumps({
        "kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "entries": entries,
        "statistics": {"total": len(entries), "used": 0, "irrelevant": 0, "failed": 0, "superseded": 0},
    }), encoding="utf-8")


def test_collect_used_pmids_only_used(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "irrelevant", "first_seen": "x", "last_checked": "x", "assigned_nodes": []},
        "333": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    assert check_retractions.collect_used_pmids(str(kg)) == ["111", "333"]


def test_find_retracted_unions_chunks():
    # query_fn echoes the retracted subset; here pretend 222 and 555 are retracted
    retracted = {"222", "555"}
    def query_fn(chunk):
        return {p for p in chunk if p in retracted}
    got = check_retractions.find_retracted(["111", "222", "333", "444", "555"], query_fn)
    assert got == {"222", "555"}


def test_find_retracted_empty_when_none():
    assert check_retractions.find_retracted(["111", "222"], lambda chunk: set()) == set()


def test_cli_detection_with_fixture(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "999": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    fixture = tmp_path / "retracted.json"
    fixture.write_text(json.dumps({"retracted": ["999"]}), encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.check_retractions", str(kg), "--esearch-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["retracted_pmids"] == ["999"]
    assert out["checked_count"] == 2


from nono_librarian.lib.frontmatter import parse as parse_node


def _node(kg, nid, pmids, eval_status="passed", external=0):
    pubmed = "\n".join(
        f'  - pmid: "{p}"\n    supports: "s"\n    verified: true' for p in pmids)
    ext = ""
    if external:
        ext = "external_ids:\n" + "".join(
            f'  - source: "clinicaltrials"\n    id: "NCT{n:08d}"\n' for n in range(external))
    fm = (f'id: "{nid}"\n'
          f'pubmed_ids:\n{pubmed}\n'
          f'{ext}'
          f'evaluation_status: "{eval_status}"\nquarantined: false\n')
    (kg / "nodes" / f"{nid}.md").write_text(f"---\n{fm}---\n\nbody\n", encoding="utf-8")


def _full_kg(tmp_path):
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_002"]},
        "333": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_002"]},
    })
    (kg / "manifest.json").write_text(json.dumps({
        "kg_name": "KG_Test", "topic": "t", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "nodes": [
            {"id": "node_001", "title": "Solo", "file": "nodes/node_001.md", "tags": ["x"],
             "summary": "s", "keywords": ["k"], "pubmed_ids": ["111"], "evaluation_status": "passed"},
            {"id": "node_002", "title": "Multi", "file": "nodes/node_002.md", "tags": ["x"],
             "summary": "s", "keywords": ["k"], "pubmed_ids": ["222", "333"], "evaluation_status": "passed"}],
        "edges": [], "statistics": {"total_nodes": 2, "total_edges": 0, "total_unique_pmids": 3,
                                    "evaluation_passed": 2, "evaluation_failed": 0}}), encoding="utf-8")
    _node(kg, "node_001", ["111"])               # single ref -> will lose all support
    _node(kg, "node_002", ["222", "333"])         # two refs -> keeps support
    return kg


def _run_sweep(kg, retracted_pmids, tmp_path):
    fixture = tmp_path / "r.json"
    fixture.write_text(json.dumps({"retracted": retracted_pmids}), encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "nono_librarian.cli.check_retractions", str(kg), "--esearch-fixture", str(fixture), "--json"],
                          capture_output=True, text=True)


def test_single_ref_node_quarantined(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, ["111"], tmp_path)
    assert res.returncode == 0, res.stderr
    fm, _ = parse_node(str(kg / "nodes" / "node_001.md"))
    assert fm["evaluation_status"] == "failed"
    assert fm["quarantined"] is True
    ref = next(r for r in fm["pubmed_ids"] if r["pmid"] == "111")
    assert ref["retracted"] is True and ref["verified"] is False
    ledger = json.loads((kg / "_pmid_ledger.json").read_text())
    assert ledger["entries"]["111"]["disposition"] == "retracted"
    summary = json.loads(res.stdout)
    assert {"pmid": "111", "nodes": ["node_001"], "action": "quarantined"} in summary["retractions"]


def test_multi_ref_node_flagged_not_quarantined(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, ["222"], tmp_path)
    assert res.returncode == 0, res.stderr
    fm, _ = parse_node(str(kg / "nodes" / "node_002.md"))
    assert fm["evaluation_status"] == "passed"      # still has 333
    assert fm.get("quarantined", False) is False
    ref = next(r for r in fm["pubmed_ids"] if r["pmid"] == "222")
    assert ref["retracted"] is True and ref["verified"] is False
    summary = json.loads(res.stdout)
    assert {"pmid": "222", "nodes": ["node_002"], "action": "flagged"} in summary["retractions"]


def test_clean_sweep_no_changes_but_advances_last_checked(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, [], tmp_path)
    assert res.returncode == 0, res.stderr
    ledger = json.loads((kg / "_pmid_ledger.json").read_text())
    assert all(e["disposition"] == "used" for e in ledger["entries"].values())
    assert all(e["last_checked"] != "old" for e in ledger["entries"].values())
    summary = json.loads(res.stdout)
    assert summary["retractions"] == []


def test_malformed_fixture_exits_nonzero_no_mutation(tmp_path):
    kg = _full_kg(tmp_path)
    before = (kg / "_pmid_ledger.json").read_text()
    fixture = tmp_path / "bad.json"
    fixture.write_text("{not json", encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.check_retractions", str(kg), "--esearch-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode != 0
    assert (kg / "_pmid_ledger.json").read_text() == before   # untouched
