import json
import os
import subprocess
import sys

from nono_librarian.cli import chase_citations


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
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.chase_citations", str(kg), "--min-cocitation", "1",
                          "--elink-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["seed_count"] == 2
    pmids = {c["pmid"]: c for c in out["candidates"]}
    assert "old" not in pmids                       # already in ledger
    assert pmids["aaa"]["cocitation_count"] == 2
    assert pmids["bbb"]["cocitation_count"] == 1


def test_rank_candidates_orders_by_cocitation_then_rcr():
    candidates = {
        "aaa": {"cocitation_count": 3, "referenced_by": ["111", "222", "333"]},
        "bbb": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
        "ccc": {"cocitation_count": 2, "referenced_by": ["111", "333"]},
        "ddd": {"cocitation_count": 1, "referenced_by": ["111"]},
    }
    rcr = {"aaa": 1.0, "bbb": 0.5, "ccc": 9.9, "ddd": 50.0}
    ranked = chase_citations.rank_candidates(candidates, rcr, min_cocitation=2, top_n=10)
    # ddd dropped (below min); aaa first (count 3); ccc before bbb (count tie, higher RCR)
    assert [c["pmid"] for c in ranked] == ["aaa", "ccc", "bbb"]
    assert ranked[0]["rcr"] == 1.0


def test_rank_candidates_caps_top_n():
    candidates = {p: {"cocitation_count": 2, "referenced_by": ["111", "222"]}
                  for p in ["a", "b", "c", "d"]}
    ranked = chase_citations.rank_candidates(candidates, {}, min_cocitation=2, top_n=2)
    assert len(ranked) == 2


def test_rank_candidates_null_rcr_sorts_last_and_stable_by_pmid():
    candidates = {
        "200": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
        "100": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
    }
    ranked = chase_citations.rank_candidates(candidates, {"200": None, "100": None},
                                             min_cocitation=2, top_n=10)
    # equal count, both rcr None -> tiebreak by pmid ascending
    assert [c["pmid"] for c in ranked] == ["100", "200"]
    assert ranked[0]["rcr"] is None


def test_rcr_lookup_fixture(tmp_path):
    fixture = tmp_path / "icite.json"
    fixture.write_text(json.dumps({"111": 4.2}), encoding="utf-8")
    rcr_map, status = chase_citations.rcr_lookup(["111", "222"], str(fixture))
    assert status == "ok"
    assert rcr_map["111"] == 4.2 and rcr_map["222"] is None


def test_cli_feed_has_rcr_and_icite_status(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    (kg / "_log.md").write_text("", encoding="utf-8")
    elink = tmp_path / "elink.json"
    elink.write_text(json.dumps({"111": ["aaa", "bbb"], "222": ["aaa"]}), encoding="utf-8")
    icite = tmp_path / "icite.json"
    icite.write_text(json.dumps({"aaa": 7.0}), encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.chase_citations", str(kg), "--min-cocitation", "1", "--top-n", "5",
                          "--elink-fixture", str(elink), "--icite-fixture", str(icite), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["icite_status"] == "ok"
    top = out["candidates"][0]
    assert top["pmid"] == "aaa" and top["cocitation_count"] == 2 and top["rcr"] == 7.0
    # log entry written
    assert "citation |" in (kg / "_log.md").read_text()
