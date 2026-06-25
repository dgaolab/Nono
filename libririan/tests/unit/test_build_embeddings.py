import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import build_embeddings
from lib import embeddings


def _fake_embedder(record):
    """Return an embed_fn that maps each text to a deterministic vector and records calls."""
    def embed_fn(texts):
        record.append(list(texts))
        return [[float(len(t)), 1.0, 0.0] for t in texts]
    return embed_fn


def _node(nid, title, summary, keywords=()):
    return {"id": nid, "title": title, "summary": summary, "keywords": list(keywords)}


def test_compute_index_embeds_all_when_empty():
    calls = []
    nodes = [_node("n1", "A", "alpha"), _node("n2", "B", "beta")]
    index, stats = build_embeddings.compute_index(nodes, None, _fake_embedder(calls))
    assert index["model"] == embeddings.MODEL_NAME and index["dim"] == embeddings.DIM
    assert set(index["nodes"]) == {"n1", "n2"}
    assert stats == {"embedded": 2, "reused": 0, "dropped": 0, "total": 2}
    assert len(calls) == 1 and len(calls[0]) == 2          # one batched call, both texts


def test_compute_index_reuses_unchanged():
    calls = []
    nodes = [_node("n1", "A", "alpha"), _node("n2", "B", "beta")]
    index1, _ = build_embeddings.compute_index(nodes, None, _fake_embedder(calls))
    calls.clear()
    # second run with identical nodes -> nothing re-embedded
    index2, stats = build_embeddings.compute_index(nodes, index1, _fake_embedder(calls))
    assert stats["reused"] == 2 and stats["embedded"] == 0
    assert calls == [[]] or calls == []                    # embed_fn called with no texts (or skipped)
    assert index2["nodes"]["n1"]["vector"] == index1["nodes"]["n1"]["vector"]


def test_compute_index_reembeds_changed_and_drops_removed():
    calls = []
    nodes = [_node("n1", "A", "alpha"), _node("n2", "B", "beta")]
    index1, _ = build_embeddings.compute_index(nodes, None, _fake_embedder(calls))
    calls.clear()
    # n1 summary changed; n2 removed; n3 added
    new_nodes = [_node("n1", "A", "ALPHA-CHANGED"), _node("n3", "C", "gamma")]
    index2, stats = build_embeddings.compute_index(new_nodes, index1, _fake_embedder(calls))
    assert set(index2["nodes"]) == {"n1", "n3"}
    assert stats == {"embedded": 2, "reused": 0, "dropped": 1, "total": 2}


def test_main_exits_nonzero_and_writes_nothing_on_unavailable(tmp_path, monkeypatch):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    (kg / "manifest.json").write_text(json.dumps(
        {"kg_name": "KG_Test", "nodes": [_node("n1", "A", "alpha")]}), encoding="utf-8")

    def boom(texts):
        raise embeddings.EmbeddingsUnavailable("no model")
    monkeypatch.setattr(embeddings, "embed_texts", boom)
    monkeypatch.setattr(sys, "argv", ["build_embeddings.py", str(kg)])
    with pytest.raises(SystemExit) as exc:
        build_embeddings.main()
    assert exc.value.code == 1
    assert not (kg / "_embeddings.json").exists()
