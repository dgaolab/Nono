---
name: nono-librarian
description: >-
  Front door for the libririan PubMed knowledge-graph toolkit that runs WITHOUT
  Claude — on a local open-weight model via an OpenAI-compatible endpoint. Use
  this whenever the user wants to query, search, lint, or maintain a libririan
  KG (folders with manifest.json + nodes/) on their own machine, or mentions
  running the librarian "locally", "offline", "on a local model", "without
  Claude/the API", or "in the nono env". This skill ALWAYS guarantees the conda
  env named `nono` (using it if present, creating it with the project's
  dependencies if not) and runs every Python through it, so semantic search
  (fastembed) and the deterministic scripts work the same everywhere.
---

# nono-librarian

The Claude-free entry point to the libririan toolkit. Its two jobs: **(1)
guarantee the `nono` conda environment**, and **(2) dispatch** a request to the
right deterministic script or local-model step — never assuming the Claude
harness or the Claude PubMed MCP is present.

Run from the repo root (the folder containing `scripts/`, `requirements.txt`).

## Step 1 — Guarantee the `nono` environment (always do this first)

The whole point of this skill is reproducible local execution, so before
running anything, make sure the env exists and then run *everything* through it.

```bash
# Use `nono` if it exists; otherwise create it and install the project deps.
if conda env list | awk '{print $1}' | grep -qx nono; then
  echo "nono env present — using it."
else
  echo "nono env missing — creating it."
  # Python 3.14 is the newest the embedding runtime (onnxruntime) ships wheels
  # for; bump this only when onnxruntime publishes a newer cpXX wheel.
  conda create -n nono python=3.14 -y
  conda run -n nono python -m pip install -r requirements.txt
fi
```

After this, invoke project Python as `conda run -n nono python <script> ...`
(or tell the user to `conda activate nono` first). Do **not** call a bare
`python3` — that resolves to the base interpreter, which lacks `fastembed` and
will silently degrade semantic search to lexical-only.

`requirements.txt` already pins everything needed: `fastembed` (pulls
`onnxruntime`), `PyYAML`, `jsonschema`, `pytest`. No Anthropic / Claude
packages are involved.

## Step 2 — Reach the local model only when reasoning is needed

LLM reasoning goes through one seam, `scripts/lib/llm.py`, which targets any
**OpenAI-compatible** `/chat/completions` server (vLLM, llama.cpp, LM Studio,
Ollama's OpenAI shim) using stdlib `urllib` — no extra dependency. Configure it
by environment:

| Variable | Default | Meaning |
|----------|---------|---------|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible server base URL |
| `LLM_MODEL` | `qwen2.5:7b-instruct` | model name to request |
| `LLM_API_KEY` | `not-needed` | bearer token if the server wants one |

`llm.chat(messages)` returns the reply text or raises `LLMUnavailable`. Treat
that exception as a signal to **degrade gracefully** — e.g. return the ranked
nodes themselves instead of a prose answer — exactly as semantic search falls
back to lexical when the model is absent. A request that needs the model should
never hard-fail just because the endpoint is down; say so and return what the
deterministic layer produced.

## Step 3 — Dispatch by intent

Classify the request and route it. The query and maintenance paths are
**fully Claude-free today**; build is not yet (see "Scope" below).

### Query / search a KG  — Claude-free now

`scripts/search_nodes.py` ranks nodes by a blend of semantic (fastembed) and
lexical signals — **no LLM at all**. This is the primary local query tool.

```bash
conda run -n nono python scripts/search_nodes.py "<query>" <KG>/manifest.json --top 10
```

Optionally refresh the embedding index first if nodes changed:
`conda run -n nono python scripts/build_embeddings.py <KG>`.

To return a *natural-language answer* rather than a ranked list, feed the top
results to the local model via `lib/llm.py` and ask it to answer **only from
the supplied node summaries/quotes, with PMIDs cited**. If `LLMUnavailable`,
return the ranked nodes and note the model was unreachable.

### Maintain a KG — Claude-free now

These are all deterministic Python (KG files and/or NCBI E-utilities directly —
no MCP). Run any with `--help` first to confirm flags before using.

| Task | Script |
|------|--------|
| Lint / health check | `scripts/linter_kg.py` |
| Retraction sweep | `scripts/check_retractions.py` |
| Citation-chase discovery feed | `scripts/chase_citations.py` |
| Rebuild embedding index | `scripts/build_embeddings.py` |
| Ledger ops / stats | `scripts/pmid_ledger.py <subcommand>` |
| Render run digest | `scripts/render_digest.py` |
| Cross-KG indices | `scripts/build_cross_indices.py`, `scripts/generate_index.py` |

### Evaluate / fact-check a KG — Claude-free now (uses the local model)

`scripts/librarian_evaluate.py` re-verifies each node's claims against PubMed
(via `lib/pubmed.py`) and the local model (via `lib/llm.py`), then writes
`_evaluation_log.json` and updates node frontmatter + manifest stats. Every
supporting verdict must rest on a **verbatim quote** found in the source, or it
is forced down to `not_supported` (the deterministic guardrail in
`lib/evaluate.py`). If the model endpoint is unavailable the run aborts and
writes **nothing** — it never half-evaluates.

```bash
conda run -n nono python scripts/librarian_evaluate.py <KG> [--nodes id1,id2]
```

Quality of the claim↔evidence judgment tracks the local model; the guardrail
bounds the failure mode (it cannot pass a claim it cannot quote) but a weak
model may still be over-skeptical. This is the local counterpart to the
Claude `/evaluate-kg` command, which remains the higher-quality default.

### Build / ingest a new KG — NOT yet Claude-free

KG construction currently lives in the Claude agent prompt `.claude/commands/
build-kg.md` and the Claude PubMed MCP. Running it on a local model requires a
new retrieval seam (`lib/pubmed.py`, direct E-utilities) and a deterministic
build orchestrator — a separate, spec'd effort (Phases 1–3), not this skill.

If the user asks to build locally, do **not** silently fall back to Claude.
Explain that local build isn't implemented yet, point to the design spec at
`docs/superpowers/specs/` (the Claude-free librarian re-architecture), and offer
the Claude-based `/build-kg` only if they explicitly accept using Claude.

## Scope (be honest about the boundary)

- **Works locally with zero Claude today:** env bootstrap, query/search,
  natural-language answers (when a local endpoint is up), evidence
  evaluation/fact-checking (when a local endpoint is up), and all maintenance
  scripts.
- **Not yet local:** KG building/ingestion. Tracked as a separate
  re-architecture; this skill is Phase 0 (front door + seams).

Quality on the local paths that *use* the model (answer synthesis, and later
build) tracks the local model — a small open-weight model is materially weaker
than Claude at synthesis and evidence judgment. Prefer the deterministic output
when the two disagree.
