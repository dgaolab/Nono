# Semantic Node Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-embedding `semantic_score` signal to node ranking, blended into the existing lexical scorer, with a git-ignored per-KG embedding cache and graceful lexical fallback.

**Architecture:** A new `scripts/lib/embeddings.py` is the single seam that touches a local fastembed ONNX model (lazy-loaded) plus pure helpers (`node_embedding_text`, `text_hash`, `cosine`). `scripts/build_embeddings.py` builds/refreshes a hash-keyed `<kg>/_embeddings.json` cache. `scripts/search_nodes.py` blends cosine similarity as a new weighted signal, falling back to pure lexical whenever embeddings are unavailable.

**Tech Stack:** Python 3.13, `pytest`; `fastembed` (ONNX, `BAAI/bge-small-en-v1.5`, 384-dim) imported defensively.

## Global Constraints

- **Embedding source:** local `fastembed` model `BAAI/bge-small-en-v1.5`, `DIM = 384`. No API, no per-query network cost.
- **`fastembed` is imported only inside `embed_texts`** — importing `lib.embeddings` must NOT import fastembed, so the test suite never loads the model.
- **Graceful degradation is the core invariant:** `search_nodes.py` must produce a lexical ranking in every embedding-failure mode (fastembed missing, index absent/stale/malformed, node missing from index, `--no-semantic`).
- **Re-balanced weights (main signals sum to 1.0):** `W_KEYWORD = 0.30`, `W_ENTITY = 0.25`, `W_SEMANTIC = 0.25`, `W_SUMMARY = 0.10`, `W_TAG = 0.10`; existing `EVAL_BONUS = 0.05`, tier bonus (≤0.05), quarantine penalty (−0.10) unchanged.
- **Embedded text per node:** `title + ". " + summary + " " + keywords` (joined), via `node_embedding_text`.
- **`_embeddings.json` is a git-ignored derived cache;** never source of truth; atomic writes only.
- No test runs the real model: `embed_texts` is faked/injected; the CLI semantic path uses a prebuilt fixture index + `--query-embedding-fixture`.
- Run all tests from `/home/dadi/nono/libririan` with `python3 -m pytest tests/unit/ -v`.

---

## File Structure

- `scripts/lib/embeddings.py` — **create**; embedding seam + pure helpers (Task 1).
- `tests/unit/test_embeddings_lib.py` — **create**; pure-helper tests (Task 1).
- `scripts/build_embeddings.py` — **create**; incremental index builder (Task 2).
- `tests/unit/test_build_embeddings.py` — **create**; `compute_index` + main-failure tests (Task 2).
- `scripts/search_nodes.py` — **modify**; hybrid semantic signal + fallback (Task 3).
- `tests/unit/test_search_nodes_semantic.py` — **create**; baseline lexical + semantic tests (Task 3).
- `requirements.txt`, `.gitignore`, `.claude/commands/build-kg.md` — **modify**; wiring (Task 4).

---

## Task 1: Embedding seam (`scripts/lib/embeddings.py`)

The single module that touches the model, plus pure helpers. No file I/O.

**Files:**
- Create: `scripts/lib/embeddings.py`
- Test: `tests/unit/test_embeddings_lib.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MODEL_NAME: str = "BAAI/bge-small-en-v1.5"`, `DIM: int = 384`.
  - `class EmbeddingsUnavailable(RuntimeError)`.
  - `embed_texts(texts: list[str]) -> list[list[float]]` — one DIM-length vector per text; raises `EmbeddingsUnavailable` if the model can't load/run; returns `[]` for `[]`.
  - `node_embedding_text(node: dict) -> str` — `title + ". " + summary + " " + keywords`.
  - `text_hash(text: str) -> str` — sha256 hex.
  - `cosine(a: list[float], b: list[float]) -> float` — 0.0 if either is empty, length-mismatched, or zero-magnitude.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_embeddings_lib.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib import embeddings


def test_constants():
    assert embeddings.MODEL_NAME == "BAAI/bge-small-en-v1.5"
    assert embeddings.DIM == 384


def test_cosine_identical_is_one():
    assert abs(embeddings.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    assert embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_empty_and_mismatch_and_zero():
    assert embeddings.cosine([], [1.0]) == 0.0
    assert embeddings.cosine([1.0, 2.0], [1.0]) == 0.0
    assert embeddings.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_node_embedding_text_composition():
    node = {"title": "Epilepsy", "summary": "A seizure disorder.", "keywords": ["scn1a", "dravet"]}
    assert embeddings.node_embedding_text(node) == "Epilepsy. A seizure disorder. scn1a dravet"


def test_node_embedding_text_tolerates_missing_fields():
    assert embeddings.node_embedding_text({}) == "."


def test_text_hash_stable_and_sensitive():
    assert embeddings.text_hash("abc") == embeddings.text_hash("abc")
    assert embeddings.text_hash("abc") != embeddings.text_hash("abd")


def test_embeddings_unavailable_is_runtimeerror():
    assert issubclass(embeddings.EmbeddingsUnavailable, RuntimeError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_embeddings_lib.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.embeddings'` (or attribute errors).

- [ ] **Step 3: Write the module**

Create `scripts/lib/embeddings.py`:

```python
#!/usr/bin/env python3
"""Embedding seam for semantic node search — the single place that touches the model.

`embed_texts` lazily loads a local fastembed ONNX model; every other helper is
pure. fastembed is imported ONLY inside the model loader, so importing this
module never pulls in fastembed (callers degrade to lexical when unavailable).
"""

import hashlib
import math

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384


class EmbeddingsUnavailable(RuntimeError):
    """Raised when the embedding model cannot be loaded or run."""


_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        try:
            from fastembed import TextEmbedding
        except Exception as e:  # ImportError or any transitive load failure
            raise EmbeddingsUnavailable(f"fastembed unavailable: {e}") from e
        try:
            _MODEL = TextEmbedding(model_name=MODEL_NAME)
        except Exception as e:
            raise EmbeddingsUnavailable(f"could not load model {MODEL_NAME}: {e}") from e
    return _MODEL


def embed_texts(texts):
    """Return one DIM-length vector (list[float]) per input text."""
    if not texts:
        return []
    model = _get_model()
    try:
        return [list(map(float, v)) for v in model.embed(list(texts))]
    except Exception as e:
        raise EmbeddingsUnavailable(f"embedding failed: {e}") from e


def node_embedding_text(node):
    """The text embedded for a node: title + summary + keywords."""
    title = (node.get("title") or "").strip()
    summary = (node.get("summary") or "").strip()
    keywords = " ".join(node.get("keywords") or [])
    return f"{title}. {summary} {keywords}".strip()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(y * y for y in b))
    if ma == 0 or mb == 0:
        return 0.0
    return dot / (ma * mb)
```

Note: `node_embedding_text({})` yields `". " .strip()` → `"."` (matches the test).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_embeddings_lib.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -q`
Expected: PASS (all).

```bash
git add scripts/lib/embeddings.py tests/unit/test_embeddings_lib.py
git commit -m "feat: embeddings seam (fastembed) + pure cosine/hash/text helpers"
```

---

## Task 2: Index builder (`scripts/build_embeddings.py`)

Hash-keyed incremental builder for `<kg>/_embeddings.json`. The core (`compute_index`) is pure and testable with an injected embedder; `main` wires the real one and handles model failure.

**Files:**
- Create: `scripts/build_embeddings.py`
- Test: `tests/unit/test_build_embeddings.py`

**Interfaces:**
- Consumes: `lib.embeddings` (`embed_texts`, `node_embedding_text`, `text_hash`, `MODEL_NAME`, `DIM`, `EmbeddingsUnavailable`).
- Produces:
  - `compute_index(nodes: list[dict], existing_index: dict | None, embed_fn) -> tuple[dict, dict]` — returns `(index, stats)`. `index` = `{"model", "dim", "nodes": {node_id: {"hash", "vector"}}}`. `stats` = `{"embedded", "reused", "dropped", "total"}`. `embed_fn(list[str]) -> list[vector]` is called once for the texts needing (re)embedding; unchanged nodes reuse cached vectors (embed_fn NOT called for them).
  - CLI: `python3 scripts/build_embeddings.py <kg_folder> [--json]` — writes `<kg_folder>/_embeddings.json` atomically; exit 2 if no manifest, exit 1 on `EmbeddingsUnavailable` (no file written).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_build_embeddings.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_build_embeddings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_embeddings'`.

- [ ] **Step 3: Write the builder**

Create `scripts/build_embeddings.py`:

```python
#!/usr/bin/env python3
"""Build/refresh a KG's node embedding index (_embeddings.json).

Hash-keyed incremental: re-embeds only nodes whose embedding-text changed,
drops removed nodes. Writes <kg_folder>/_embeddings.json atomically. The index
is a git-ignored derived cache. On model failure, exits non-zero and writes
nothing.

Usage:
    python3 scripts/build_embeddings.py <kg_folder> [--json]
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import embeddings

INDEX_FILENAME = "_embeddings.json"


def compute_index(nodes, existing_index, embed_fn):
    """Return (index, stats). Reuses cached vectors for unchanged (by hash) nodes."""
    existing_nodes = (existing_index or {}).get("nodes", {})
    plan = []          # (node_id, hash, needs_embed: bool)
    to_embed_texts = []
    to_embed_ids = []
    reuse = {}         # node_id -> vector
    for node in nodes:
        nid = node.get("id", "")
        text = embeddings.node_embedding_text(node)
        h = embeddings.text_hash(text)
        prev = existing_nodes.get(nid)
        if prev and prev.get("hash") == h:
            reuse[nid] = prev["vector"]
            plan.append((nid, h, False))
        else:
            plan.append((nid, h, True))
            to_embed_texts.append(text)
            to_embed_ids.append(nid)
    vectors = embed_fn(to_embed_texts) if to_embed_texts else []
    embedded = dict(zip(to_embed_ids, vectors))
    index_nodes = {}
    for nid, h, needs in plan:
        index_nodes[nid] = {"hash": h, "vector": embedded[nid] if needs else reuse[nid]}
    manifest_ids = {n.get("id", "") for n in nodes}
    dropped = len(set(existing_nodes) - manifest_ids)
    stats = {"embedded": len(to_embed_ids), "reused": len(reuse),
             "dropped": dropped, "total": len(index_nodes)}
    return {"model": embeddings.MODEL_NAME, "dim": embeddings.DIM, "nodes": index_nodes}, stats


def _write_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description="Build/refresh a KG's node embedding index.")
    parser.add_argument("kg_folder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = os.path.join(args.kg_folder, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {args.kg_folder}", file=sys.stderr)
        sys.exit(2)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        nodes = json.load(fh).get("nodes", [])

    index_path = os.path.join(args.kg_folder, INDEX_FILENAME)
    existing = None
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if existing.get("model") != embeddings.MODEL_NAME:
                existing = None   # model changed -> full rebuild
        except (json.JSONDecodeError, OSError):
            existing = None

    try:
        index, stats = compute_index(nodes, existing, embeddings.embed_texts)
    except embeddings.EmbeddingsUnavailable as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    _write_atomic(index_path, index)
    payload = {"kg": os.path.basename(os.path.abspath(args.kg_folder)), **stats}
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        print(f"Embeddings: {stats['embedded']} embedded, {stats['reused']} reused, "
              f"{stats['dropped']} dropped ({stats['total']} total).", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_build_embeddings.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -q`
Expected: PASS (all).

```bash
git add scripts/build_embeddings.py tests/unit/test_build_embeddings.py
git commit -m "feat: incremental node embedding index builder"
```

---

## Task 3: Hybrid scoring in `search_nodes.py`

Blend a `semantic_score` signal into the existing scorer, loading each KG's `_embeddings.json` and embedding the query once, with lexical fallback everywhere. Add a lexical baseline test first (there are currently no `search_nodes` tests) to lock the fallback behavior.

**Files:**
- Modify: `scripts/search_nodes.py` (imports, weights, two new functions, argparse, node-loading loop, query embedding, scoring loop, score_breakdown)
- Test: `tests/unit/test_search_nodes_semantic.py` (create)

**Interfaces:**
- Consumes: `lib.embeddings` (`embed_texts`, `cosine`, `MODEL_NAME`, `EmbeddingsUnavailable`); `_embeddings.json` from Task 2.
- Produces: `score_semantic(query_vec, node_vec) -> float` (clamped cosine in [0,1]); `load_embedding_index(kg_folder) -> dict | None`; new `--no-semantic` and `--query-embedding-fixture FILE` flags; a `semantic_score` field in each result's `score_breakdown`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_search_nodes_semantic.py`:

```python
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import search_nodes

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "search_nodes.py"))


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
    res = subprocess.run([sys.executable, SCRIPT, "epilepsy seizure", str(kg / "manifest.json")],
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
    res = subprocess.run([sys.executable, SCRIPT, "unrelated query", str(kg / "manifest.json"),
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
    res = subprocess.run([sys.executable, SCRIPT, "unrelated", str(kg / "manifest.json"),
                          "--no-semantic", "--query-embedding-fixture", str(qfix)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    # node is lexically irrelevant -> with semantic off, it should not match at all
    assert out["results"] == [] or out["results"][0]["score_breakdown"]["semantic_score"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_search_nodes_semantic.py -v`
Expected: FAIL — `score_semantic`/`load_embedding_index` not defined; no `semantic_score` in breakdown; `--query-embedding-fixture` unknown.

- [ ] **Step 3: Add the import**

In `scripts/search_nodes.py`, after the existing import block (the line `from collections import Counter, defaultdict`), add:

```python

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import embeddings
```

- [ ] **Step 4: Re-balance the weights**

In `scripts/search_nodes.py`, replace the weight constants block (currently `W_KEYWORD = 0.35` … `EVAL_BONUS = 0.05`) with:

```python
W_KEYWORD = 0.30
W_ENTITY = 0.25
W_SEMANTIC = 0.25
W_SUMMARY = 0.10
W_TAG = 0.10
EVAL_BONUS = 0.05
```

- [ ] **Step 5: Add the two new functions**

In `scripts/search_nodes.py`, immediately after `score_tags` (before the `Main` section), add:

```python
def score_semantic(query_vec, node_vec):
    """Clamped cosine similarity in [0, 1]; 0.0 if either vector is missing."""
    if not query_vec or not node_vec:
        return 0.0
    return max(0.0, min(1.0, embeddings.cosine(query_vec, node_vec)))


def load_embedding_index(kg_folder):
    """Load a usable _embeddings.json for kg_folder, else None (warn on stale/malformed)."""
    path = os.path.join(kg_folder, "_embeddings.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            idx = json.load(fh)
    except (json.JSONDecodeError, OSError):
        print(f"Warning: ignoring malformed embedding index {path}", file=sys.stderr)
        return None
    if idx.get("model") != embeddings.MODEL_NAME:
        print(f"Warning: ignoring stale embedding index {path} "
              f"(model {idx.get('model')!r} != {embeddings.MODEL_NAME!r})", file=sys.stderr)
        return None
    return idx
```

- [ ] **Step 6: Add the CLI flags**

In `main`, after the `--compact` argument, add:

```python
    parser.add_argument("--no-semantic", dest="no_semantic", action="store_true",
                        help="Disable embedding-based semantic scoring (lexical only)")
    parser.add_argument("--query-embedding-fixture", dest="query_embedding_fixture", default=None,
                        help="JSON file with a precomputed query vector (list[float]); test seam")
```

- [ ] **Step 7: Capture KG folder + load indices in the node-loading loop**

Replace the node-loading block (currently `all_nodes = []  # list of (kg_name, node_entry)` through the `for node in manifest.get("nodes", []): all_nodes.append((kg_name, node))` loop) with:

```python
    # Load all nodes from all manifests; track each node's KG folder + that folder's index
    all_nodes = []   # list of (kg_name, node_entry, kg_folder)
    indices = {}     # kg_folder -> embedding index dict or None
    for manifest_path in args.manifests:
        if not os.path.exists(manifest_path):
            print(f"Error: file not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in {manifest_path}: {e}", file=sys.stderr)
            sys.exit(1)

        kg_folder = os.path.dirname(os.path.abspath(manifest_path))
        kg_name = manifest.get("kg_name", os.path.basename(kg_folder))
        if kg_folder not in indices:
            indices[kg_folder] = None if args.no_semantic else load_embedding_index(kg_folder)
        for node in manifest.get("nodes", []):
            all_nodes.append((kg_name, node, kg_folder))
```

- [ ] **Step 8: Update the three pre-filters and the summary-token list to 3-tuples**

In `scripts/search_nodes.py`, update each comprehension that unpacks `all_nodes`:

Evidence-tier filter:
```python
    if min_tier_rank > 0:
        all_nodes = [(kg, n, f) for kg, n, f in all_nodes
                     if EVIDENCE_TIER_ORDER.get(n.get("evidence_tier", "unclassified"), 0) >= min_tier_rank]
```

Quarantine filter:
```python
    if not args.include_quarantined:
        all_nodes = [(kg, n, f) for kg, n, f in all_nodes
                     if not n.get("quarantined", False)]
```

Tag filter:
```python
    if args.tag_filter:
        tag_lower = args.tag_filter.lower()
        all_nodes = [(kg, n, f) for kg, n, f in all_nodes
                     if any(tag_lower == t.lower() for t in n.get("tags", []))]
```

Summary-token list:
```python
    all_summary_tokens = [tokenize(n.get("summary", "")) for _, n, _ in all_nodes]
```

- [ ] **Step 9: Compute the query embedding once (before the scoring loop)**

In `scripts/search_nodes.py`, immediately before `# Score each node` / `results = []`, add:

```python
    # Query embedding for semantic scoring (computed once), if any usable index loaded.
    query_vec = None
    if not args.no_semantic and any(indices.values()):
        if args.query_embedding_fixture:
            with open(args.query_embedding_fixture, "r", encoding="utf-8") as fh:
                query_vec = json.load(fh)
        else:
            try:
                query_vec = embeddings.embed_texts([args.query])[0]
            except embeddings.EmbeddingsUnavailable as e:
                print(f"Warning: semantic scoring disabled ({e})", file=sys.stderr)
                query_vec = None
```

- [ ] **Step 10: Blend semantic into the scoring loop**

Change the scoring-loop header to unpack the 3-tuple:
```python
    for idx, (kg_name, node, kg_folder) in enumerate(all_nodes):
```

After `tag_s = score_tags(query_tokens, tags)`, add:
```python
        node_vec = None
        if query_vec is not None:
            idx_for_kg = indices.get(kg_folder)
            if idx_for_kg:
                node_entry = idx_for_kg.get("nodes", {}).get(node_id)
                if node_entry:
                    node_vec = node_entry.get("vector")
        sem_score = score_semantic(query_vec, node_vec)
```

Replace the `final_score = (...)` expression with:
```python
        final_score = (W_KEYWORD * kw_score +
                       W_ENTITY * ent_score +
                       W_SEMANTIC * sem_score +
                       W_SUMMARY * sum_score +
                       W_TAG * tag_s +
                       eval_b + tier_b + quarantine_penalty)
```

In the `if not args.compact:` block that builds `entry["score_breakdown"]`, add a `semantic_score` entry (right after `keyword_score`):
```python
                entry["score_breakdown"] = {
                    "keyword_score": round(kw_score, 4),
                    "semantic_score": round(sem_score, 4),
                    "entity_score": round(ent_score, 4),
                    "summary_score": round(sum_score, 4),
                    "tag_score": round(tag_s, 4),
                    "eval_bonus": round(eval_b, 4),
                    "tier_bonus": round(tier_b, 4),
                }
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_search_nodes_semantic.py -v`
Expected: PASS (6 tests).

- [ ] **Step 12: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -q`
Expected: PASS (all).

```bash
git add scripts/search_nodes.py tests/unit/test_search_nodes_semantic.py
git commit -m "feat: blend semantic embedding signal into node search with lexical fallback"
```

---

## Task 4: Wiring — dependency, gitignore, build-kg refresh step

Add the dependency, ignore the cache, and refresh the index after each build/update. No unit tests (config/prose); verified by diff + a fallback smoke check.

**Files:**
- Modify: `requirements.txt` (add `fastembed`)
- Modify: `.gitignore` (ignore `_embeddings.json`)
- Modify: `.claude/commands/build-kg.md` (non-fatal refresh step in Phase 4)

**Interfaces:**
- Consumes: `scripts/build_embeddings.py` (Task 2).
- Produces: `fastembed` declared; `_embeddings.json` untracked; build/update auto-refreshes the index.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, add a line:

```
fastembed>=0.3
```

- [ ] **Step 2: Ignore the cache**

In `.gitignore`, under the `# Python` section, add:

```
_embeddings.json
```

- [ ] **Step 3: Add the refresh step to build-kg Phase 4**

In `.claude/commands/build-kg.md`, in Phase 4 immediately AFTER the digest-render step `1e` (the `python3 scripts/render_digest.py ...` block), add a new step:

````markdown
1f. Refresh the semantic embedding index (non-fatal). After the manifest is finalized, run:
   ```
   python3 scripts/build_embeddings.py {KG_FOLDER}
   ```
   This (re)builds the git-ignored `{KG_FOLDER}/_embeddings.json` cache used by semantic node search. It re-embeds only changed nodes. If `fastembed` is not installed or the model cannot load, this step exits non-zero — log it and continue; semantic search degrades to lexical until the index exists. Never fail the build over the embedding index.
````

- [ ] **Step 4: Verify the edits**

Run: `git diff requirements.txt .gitignore .claude/commands/build-kg.md`
Confirm: `fastembed>=0.3` in requirements; `_embeddings.json` ignored; a non-fatal `build_embeddings.py` step after the digest render in Phase 4.

- [ ] **Step 5: Confirm lexical fallback is intact (no index, no fastembed needed)**

Run:
```bash
python3 -m pytest tests/unit/test_search_nodes_semantic.py::test_baseline_lexical_ranking_without_index -v
```
Expected: PASS — search works with no index and `semantic_score` is 0.0.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .claude/commands/build-kg.md
git commit -m "feat: declare fastembed, ignore embedding cache, refresh index in build-kg"
```

---

## Task 5: End-to-end smoke gate

Verify the full suite is green and the graceful-fallback invariant holds with no model. The real-model path is exercised only if `fastembed` is installed (it downloads the model on first use), and is allowed to be skipped.

**Files:** none modified.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (embeddings lib + builder + hybrid search + all existing tests). No test loads the real model.

- [ ] **Step 2: Confirm `lib.embeddings` imports without fastembed present**

Run:
```bash
python3 -c "import sys, os; sys.path.insert(0, 'scripts'); from lib import embeddings; print('import OK', embeddings.MODEL_NAME)"
```
Expected: prints `import OK BAAI/bge-small-en-v1.5` — importing the module never imports fastembed (the lazy import is inside `_get_model`).

- [ ] **Step 3: Real index build + semantic search (only if fastembed installed)**

```bash
python3 -c "import fastembed" 2>/dev/null && HAVE_FE=1 || HAVE_FE=0
if [ "$HAVE_FE" = "1" ]; then
  TMP=$(mktemp -d); KG="$TMP/KG_Demo"; mkdir -p "$KG"
  cat > "$KG/manifest.json" <<'JSON'
{"kg_name":"KG_Demo","nodes":[
 {"id":"n1","title":"Epilepsy genetics","file":"nodes/n1.md","summary":"SCN1A variants cause seizure disorders.","keywords":["scn1a","epilepsy"],"tags":["neuro"],"evaluation_status":"passed","evidence_tier":"rct","entities":[]},
 {"id":"n2","title":"Cardiac arrhythmia","file":"nodes/n2.md","summary":"Heart rhythm abnormalities.","keywords":["cardiac"],"tags":["cardio"],"evaluation_status":"passed","evidence_tier":"rct","entities":[]}]}
JSON
  python3 scripts/build_embeddings.py "$KG" --json
  echo "--- index present? ---"; [ -f "$KG/_embeddings.json" ] && echo "INDEX OK"
  echo "--- semantic search for a paraphrase ('convulsion gene') ---"
  python3 scripts/search_nodes.py "convulsion gene" "$KG/manifest.json" --top 2 \
     | python3 -c "import json,sys; o=json.load(sys.stdin); print(o['results'][0]['node_id'], o['results'][0]['score_breakdown']['semantic_score'])"
  rm -rf "$TMP"
else
  echo "fastembed not installed — skipping live model check (lexical fallback verified by unit tests)."
fi
```

Expected: if fastembed is installed, `INDEX OK` prints and the top result for the paraphrase "convulsion gene" is `n1` with a non-zero `semantic_score` (semantic match without shared words). If not installed, the skip message prints — acceptable; the fallback path is already covered by unit tests.

- [ ] **Step 4: Final commit (only if verification produced artifacts)**

If Steps 1-3 produced no tracked file changes, no commit is needed (the `_embeddings.json` cache is git-ignored). Otherwise:
```bash
git add -A
git commit -m "test: smoke-verify semantic node search path"
```

---

## Self-Review Notes

- **Spec coverage:** embeddings seam + lazy fastembed → Task 1 (`embed_texts`, defensive import); pure cosine/hash/text → Task 1; `EmbeddingsUnavailable` → Task 1; index builder hash-keyed incremental + drop + atomic write → Task 2 (`compute_index`, `_write_atomic`); model-failure exits non-zero, no file → Task 2 (`test_main_exits_nonzero...`); hybrid blended weights → Task 3 (weight block + `final_score`); `score_semantic` clamp → Task 3; `load_embedding_index` absent/stale/malformed → Task 3; query embedded once + fixture seam → Task 3 (Step 9); per-node fallback to 0 → Task 3 (Step 10); `--no-semantic` → Task 3; `semantic_score` in breakdown → Task 3 (Step 10); regression/baseline lexical → Task 3 (`test_baseline_lexical_ranking_without_index`); requirements + gitignore + build-kg refresh → Task 4; module-imports-without-fastembed invariant → Task 5 Step 2.
- **Type consistency:** `node_embedding_text`/`text_hash`/`cosine`/`embed_texts` signatures identical across Tasks 1–3; `compute_index(nodes, existing_index, embed_fn) -> (index, stats)` with index shape `{"model","dim","nodes":{id:{"hash","vector"}}}` consumed by `load_embedding_index`/scoring in Task 3; `score_semantic(query_vec, node_vec)` matches its test and call site; all_nodes 3-tuple `(kg_name, node, kg_folder)` updated consistently in every filter, the summary-token list, and the scoring loop.
- **Out of scope (per spec):** embedding API backends; embedding node bodies/abstracts; cross-KG clustering; ANN indexing.
