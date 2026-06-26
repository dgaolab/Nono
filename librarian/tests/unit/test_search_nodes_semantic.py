import json
import os
import subprocess
import sys

from nono_librarian.cli import search_nodes


def _write_kg(tmp_path, nodes, index=None):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    (kg / "manifest.json").write_text(json.dumps({"kg_name": "KG_Test", "nodes": nodes}), encoding="utf-8")
    if index is not None:
        (kg / "_embeddings.json").write_text(json.dumps(index), encoding="utf-8")
    return kg


def _node(nid, title, summary, keywords=()):
    return {"id": nid, "title": title, "file": f"nodes/{nid}.md", "summary": summary,
            "keywords": list(keywords), "tags": ["t"], "evaluation_status": "passed",
            "evidence_tier": "rct", "entities": []}


def test_score_semantic_clamps_and_handles_missing():
    assert search_nodes.score_semantic([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert search_nodes.score_semantic([1.0, 0.0], [-1.0, 0.0]) == 0.0   # negative cosine clamped
    assert search_nodes.score_semantic(None, [1.0]) == 0.0
    assert search_nodes.score_semantic([1.0], None) == 0.0


def test_load_embedding_index_absent_stale_and_good(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    assert search_nodes.load_embedding_index(str(kg)) is None        # absent
    (kg / "_embeddings.json").write_text(json.dumps(
        {"model": "OTHER", "dim": 3, "nodes": {}}), encoding="utf-8")
    assert search_nodes.load_embedding_index(str(kg)) is None        # stale model
    (kg / "_embeddings.json").write_text(json.dumps(
        {"model": search_nodes.embeddings.MODEL_NAME, "dim": 3, "nodes": {"n1": {"hash": "h", "vector": [1.0]}}}),
        encoding="utf-8")
    idx = search_nodes.load_embedding_index(str(kg))
    assert idx and idx["nodes"]["n1"]["vector"] == [1.0]


def test_baseline_lexical_ranking_without_index(tmp_path):
    # No _embeddings.json -> pure lexical; the query word matches n_match's keywords.
    kg = _write_kg(tmp_path, [
        _node("n_match", "Epilepsy", "seizure disorder", keywords=["epilepsy", "seizure"]),
        _node("n_other", "Cardiology", "heart rhythm", keywords=["cardiac", "arrhythmia"]),
    ])
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.search_nodes", "epilepsy seizure", str(kg / "manifest.json")],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["results"][0]["node_id"] == "n_match"
    # semantic_score present and zero when no index
    assert out["results"][0]["score_breakdown"]["semantic_score"] == 0.0


def test_semantic_boosts_node_close_to_query(tmp_path):
    # Two lexically-irrelevant nodes; the index makes n_close's vector match the query vector.
    index = {"model": search_nodes.embeddings.MODEL_NAME, "dim": 3, "nodes": {
        "n_close": {"hash": "h1", "vector": [1.0, 0.0, 0.0]},
        "n_far": {"hash": "h2", "vector": [0.0, 1.0, 0.0]},
    }}
    kg = _write_kg(tmp_path, [
        _node("n_close", "Alpha", "zzz", keywords=["zzz"]),
        _node("n_far", "Beta", "zzz", keywords=["zzz"]),
    ], index=index)
    qfix = tmp_path / "q.json"
    qfix.write_text(json.dumps([1.0, 0.0, 0.0]), encoding="utf-8")   # query vector == n_close's
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.search_nodes", "unrelated query", str(kg / "manifest.json"),
                          "--query-embedding-fixture", str(qfix)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    by_id = {r["node_id"]: r for r in out["results"]}
    assert by_id["n_close"]["score"] > by_id["n_far"]["score"]
    assert by_id["n_close"]["score_breakdown"]["semantic_score"] == 1.0
    assert by_id["n_far"]["score_breakdown"]["semantic_score"] == 0.0


def test_no_semantic_flag_ignores_index(tmp_path):
    index = {"model": search_nodes.embeddings.MODEL_NAME, "dim": 3, "nodes": {
        "n_close": {"hash": "h1", "vector": [1.0, 0.0, 0.0]}}}
    kg = _write_kg(tmp_path, [_node("n_close", "Alpha", "zzz", keywords=["zzz"])], index=index)
    qfix = tmp_path / "q.json"
    qfix.write_text(json.dumps([1.0, 0.0, 0.0]), encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "nono_librarian.cli.search_nodes", "unrelated", str(kg / "manifest.json"),
                          "--no-semantic", "--query-embedding-fixture", str(qfix)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    # node is lexically irrelevant -> with semantic off, it should not match at all
    assert out["results"] == [] or out["results"][0]["score_breakdown"]["semantic_score"] == 0.0
