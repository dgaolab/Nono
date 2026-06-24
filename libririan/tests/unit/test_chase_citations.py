import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import chase_citations

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "chase_citations.py"))


def _write_ledger(kg, entries):
    (kg / "_pmid_ledger.json").write_text(json.dumps({
        "kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "entries": entries,
        "statistics": {"total": len(entries), "used": 0, "irrelevant": 0, "failed": 0, "superseded": 0},
    }), encoding="utf-8")


def test_fetch_references_maps_each_seed():
    calls = {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"]}
    got = chase_citations.fetch_references(["111", "222"], lambda s: calls[s])
    assert got == {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"]}


def test_build_candidates_counts_cocitation_and_dedups():
    refs_by_seed = {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"], "333": ["bbb"]}
    # 'aaa' already in ledger (known) -> excluded; '333' is a seed referenced by none here
    known = {"111", "222", "333", "aaa"}
    cand = chase_citations.build_candidates(refs_by_seed, known)
    assert "aaa" not in cand                       # known -> excluded
    assert cand["bbb"]["cocitation_count"] == 3
    assert sorted(cand["bbb"]["referenced_by"]) == ["111", "222", "333"]
    assert cand["ccc"]["cocitation_count"] == 1


def test_build_candidates_excludes_seeds_themselves():
    refs_by_seed = {"111": ["222", "zzz"]}   # 111 references another seed 222
    known = {"111", "222"}
    cand = chase_citations.build_candidates(refs_by_seed, known)
    assert "222" not in cand                       # a seed is never its own candidate
    assert "zzz" in cand


def test_cli_discovery_with_fixture(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
        "old": {"disposition": "irrelevant", "first_seen": "x", "last_checked": "x", "assigned_nodes": []},
    })
    fixture = tmp_path / "elink.json"
    fixture.write_text(json.dumps({"111": ["aaa", "old"], "222": ["aaa", "bbb"]}), encoding="utf-8")
    # --min-cocitation 1 keeps the single-reference candidate 'bbb' once Task 3 adds default-2 bounding
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--min-cocitation", "1",
                          "--elink-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["seed_count"] == 2
    pmids = {c["pmid"]: c for c in out["candidates"]}
    assert "old" not in pmids                       # already in ledger
    assert pmids["aaa"]["cocitation_count"] == 2
    assert pmids["bbb"]["cocitation_count"] == 1
