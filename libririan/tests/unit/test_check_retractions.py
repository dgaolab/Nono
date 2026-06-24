import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import check_retractions

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check_retractions.py"))


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
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--esearch-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["retracted_pmids"] == ["999"]
    assert out["checked_count"] == 2
