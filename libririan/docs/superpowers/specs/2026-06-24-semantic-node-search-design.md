# Semantic Node Search — Design Spec

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-24
**Roadmap:** Phase two, item 5 (see `docs/superpowers/2026-06-24-phase-two-roadmap.md`)
**Builds on:** the deterministic lexical ranker `scripts/search_nodes.py` (consumed by `/query-kg`) and the BUILD/UPDATE manifest pipeline.

## Problem

`search_nodes.py` ranks nodes purely lexically — keyword/entity overlap, TF-IDF
summary cosine, tag matching. It cannot connect a query to a node that uses
different words for the same concept (e.g. "seizure disorder" vs a node titled
"epilepsy phenotype"). As the graph grows (item 4 adds breadth), this lexical
gap widens and query routing degrades. Semantic node search adds an
embedding-based similarity signal so conceptually-related nodes surface even
without shared vocabulary.

## Goals

- Add an embedding-based `semantic_score` signal to node ranking, blended into
  the existing weighted scorer ("search over the existing search").
- Compute node embeddings offline and cheaply (no per-query API cost), cached
  per KG and refreshed incrementally.
- Degrade gracefully to pure lexical ranking whenever embeddings are
  unavailable, so `/query-kg` never breaks.

## Non-goals

- Replacing the lexical scorer (semantic is an added signal, not a substitute).
- An embedding API or any network/runtime cost at query time (local model only).
- Embedding node *bodies* or PMID abstracts (node title + summary + keywords
  only — the same fields lexical search already trusts).
- Committing the embedding index to git (it is a derived cache).
- Cross-KG semantic clustering / dedup (a possible later item; out of scope here).

## Design decisions (resolved during brainstorming)

| Fork | Decision |
|------|----------|
| Embedding source | **Local ONNX model via `fastembed`** (`BAAI/bge-small-en-v1.5`, 384-dim) — no API key, no per-query cost, offline & deterministic |
| Integration | **Hybrid** — `semantic_score` blended as a new weighted signal in `search_nodes.py`, with lexical fallback |
| Index storage | **`_embeddings.json` per KG, git-ignored derived cache**, hash-keyed incremental, rebuilt on demand + refreshed at build/update |

## Architecture

Three units with clear boundaries:

### 1. `scripts/lib/embeddings.py` — the embedding seam

The single place that touches the model. No file I/O.

- `embed_texts(texts: list[str]) -> list[list[float]]` — lazily loads a
  module-level `fastembed.TextEmbedding(MODEL_NAME)` on first call and returns
  one vector per input text. The lazy singleton avoids re-loading the model
  per call.
- `node_embedding_text(node: dict) -> str` — the text embedded for a node:
  `title`, `summary`, and `keywords` (space-joined), e.g.
  `"<title>. <summary> <kw1> <kw2> ..."`.
- `text_hash(text: str) -> str` — stable hash (sha256 hex) of the embedding
  text, used as the cache key for incremental rebuilds.
- `cosine(a: list[float], b: list[float]) -> float` — cosine similarity of two
  equal-length vectors; returns 0.0 if either has zero magnitude.
- Constants `MODEL_NAME = "BAAI/bge-small-en-v1.5"` and `DIM = 384`.

`fastembed` is added to `requirements.txt`, but the `fastembed` import inside
`embed_texts` is wrapped: an `ImportError` or model-load failure raises a
clear, catchable `EmbeddingsUnavailable` exception that callers handle as
"fall back to lexical".

### 2. `scripts/build_embeddings.py` — the index builder

CLI: `python3 scripts/build_embeddings.py <kg_folder> [--json]`.

- Reads `<kg_folder>/manifest.json` nodes.
- Loads any existing `<kg_folder>/_embeddings.json`.
- For each manifest node, computes `text_hash(node_embedding_text(node))`;
  reuses the cached vector when the hash is unchanged, otherwise re-embeds
  (batched via one `embed_texts` call for all changed nodes).
- Drops index entries for nodes no longer in the manifest.
- Writes `_embeddings.json` atomically:
  `{"model": MODEL_NAME, "dim": DIM, "nodes": {node_id: {"hash": ..., "vector": [...]}}}`.
- On `EmbeddingsUnavailable`, exits non-zero with a clear message (this command's
  whole job is to build the index, so failure must be visible, not silent).
- Prints a one-line summary (or `--json`: `{kg, embedded, reused, dropped, total}`).

### 3. `scripts/search_nodes.py` — hybrid scoring (modify)

- For each manifest argument, attempt to load its sibling `_embeddings.json`.
  An index is **usable** only if its `model` header equals `embeddings.MODEL_NAME`;
  a mismatch (stale model) is treated as absent (and a one-line stderr warning).
- If any usable index loaded and semantic is enabled, embed the query once via
  `embeddings.embed_texts([query])[0]` (or read `--query-embedding-fixture`).
- New pure signal `score_semantic(query_vec, node_vec) -> float = cosine(...)`,
  clamped to `[0, 1]` (negative cosine → 0).
- **Re-balanced weights** (main signals sum to 1.0):
  `W_KEYWORD = 0.30`, `W_ENTITY = 0.25`, `W_SEMANTIC = 0.25`, `W_SUMMARY = 0.10`,
  `W_TAG = 0.10`; plus the existing `EVAL_BONUS = 0.05`, tier bonus (≤0.05), and
  quarantine penalty (−0.10), unchanged.
- A node absent from the index, or any node when semantic is unavailable, gets
  `semantic_score = 0.0` — lexical ranking proceeds unchanged. The
  `score_breakdown` (non-compact output) gains a `semantic_score` field.

## Graceful degradation (the core invariant)

`search_nodes.py` must produce a sensible lexical ranking in every failure mode:

- `fastembed` not installed / model download fails → `EmbeddingsUnavailable`
  caught, semantic disabled for the whole run, lexical proceeds.
- No `_embeddings.json` beside a manifest → that KG's nodes get `semantic_score 0`.
- Stale index (model header ≠ current `MODEL_NAME`) → ignored + stderr warning.
- A manifest node missing from the index → `semantic_score 0` for that node.
- `--no-semantic` → skip all embedding work, identical to today's behavior.

Because weights are fixed (not renormalized when semantic is absent), scores
stay comparable across nodes within a run; a run with no semantic data simply
reproduces the pre-existing lexical ranking shape.

## Index storage & freshness

- `_embeddings.json` lives in each KG folder and is **git-ignored** (added to
  `.gitignore`). It is a derived cache, never source of truth.
- A fresh checkout has no index → semantic is silently off until
  `build_embeddings.py` runs; this is acceptable given graceful fallback.
- The index is refreshed automatically: `build-kg.md` Phase 4 gains a non-fatal
  step running `build_embeddings.py <KG_FOLDER>` after the manifest is written
  (both BUILD and UPDATE). It is also runnable manually to backfill any existing KG.

## Wiring

- **`requirements.txt`:** add `fastembed`.
- **`.gitignore`:** add `_embeddings.json` (and any fastembed model cache dir, if
  it lands in-repo).
- **`build-kg.md`:** Phase 4, after manifest write — non-fatal
  `python3 scripts/build_embeddings.py <KG_FOLDER>` (a failed/absent model must
  not fail the build; log and continue).
- **`query-kg.md`:** no change required — `search_nodes.py` auto-detects the
  index. (`--no-semantic` remains available if a caller wants pure lexical.)

## Error handling

- `build_embeddings.py`: model unavailable → exit non-zero, clear message, no
  partial/corrupt index write (atomic write only after all vectors computed).
- `search_nodes.py`: never fails due to embeddings — every embedding error path
  degrades to lexical. Malformed `_embeddings.json` (bad JSON / missing `model`)
  → treated as absent + stderr warning.
- A query/node vector dimension mismatch with `DIM` → that node's
  `semantic_score` is 0 (defensive; should not happen with a consistent index).

## Testing

No test runs the real model; `embeddings.embed_texts` is the single seam,
monkeypatched in-process, and the CLI semantic path uses fixtures.

- **`lib/embeddings.py`:** `cosine` correctness (orthogonal→0, identical→1,
  zero-vector→0); `node_embedding_text` composition from title/summary/keywords;
  `text_hash` stability and sensitivity to text change.
- **`build_embeddings.py`** (monkeypatched `embed_texts`): builds an index from a
  manifest; an unchanged node's vector is **reused** (embedder not called for it)
  on a second run; a changed node is **re-embedded**; a removed node is **dropped**;
  `EmbeddingsUnavailable` → non-zero exit, no index file written.
- **`search_nodes.py` hybrid:** `score_semantic` blends correctly; a node ranks
  higher when its vector is close to the query vector than a lexically-equal node
  whose vector is far; a node absent from the index scores `semantic_score 0`;
  `--no-semantic` reproduces the pre-change lexical output; a stale-model index is
  ignored (lexical fallback); the CLI semantic path works via a prebuilt fixture
  `_embeddings.json` + `--query-embedding-fixture`.
- **Regression:** all existing `search_nodes` tests pass unchanged when no index
  is present (graceful-fallback guarantee).

## Out of scope / follow-ups

- Embedding API backends (local-only for now).
- Embedding node bodies / abstracts; per-PMID semantic search.
- Cross-KG semantic clustering, dedup, or link suggestions.
- Approximate-nearest-neighbor indexing (linear cosine scan is fine at current
  graph sizes; revisit only if node counts reach the thousands).
