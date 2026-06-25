# Claude-Free Local Librarian — Design Spec

**Date:** 2026-06-24
**Status:** Phase 0 implemented; Phases 1–3 designed, not yet planned.
**Topic:** Make the libririan PubMed knowledge-graph toolkit runnable end-to-end
without Claude or the Claude PubMed MCP — driven by a local open-weight model
through an OpenAI-compatible endpoint, inside the `nono` conda env.

## Goal

Today the librarian's reasoning *is* Claude: every `.claude/commands/*.md` is an
agent prompt Claude executes, and retrieval goes through the Claude PubMed MCP
(`mcp__claude_ai_PubMed__*`). The goal is a parallel execution path that needs
neither — so the toolkit runs on a user's machine against a local model
(vLLM / llama.cpp / LM Studio / Ollama) with PubMed reached directly via NCBI
E-utilities.

The `nono-librarian` skill (`.claude/skills/nono-librarian/SKILL.md`) is the
front door over this path: guarantee the `nono` env, then dispatch.

## Non-goals

- Not removing or changing the existing Claude-based commands. The Claude path
  remains the high-quality default; this is an additive, local alternative.
- Not matching Claude's KG quality on the local path. Output tracks the local
  model, which is materially weaker at long-horizon synthesis and evidence
  judgment. We optimize for "runs locally and is useful", not parity.
- Not shipping/operating a model server. We target a *protocol*
  (OpenAI-compatible `/chat/completions`); the user runs whatever server.

## Locked decisions

1. **LLM transport: OpenAI-compatible `/chat/completions`**, configured by env
   (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). Implemented with stdlib
   `urllib` → **no new pip dependency**. One seam: `scripts/lib/llm.py`.
2. **Environment: conda env `nono`.** Use if present; else create with
   `python=3.14` (newest onnxruntime wheel) + `pip install -r requirements.txt`.
   The skill enforces this; all Python runs via `conda run -n nono`.
3. **Graceful degradation is invariant.** `LLMUnavailable` (model down) and the
   embeddings fallback (model absent) must never hard-fail a request — degrade
   to the deterministic layer and say so. Mirrors the semantic-search fallback.
4. **No live model or network in CI.** Every seam takes an injection point
   (`llm.chat(..., _opener=)`, pubmed fixtures, embeddings fakes); the unit
   suite exercises logic with fakes only.

## Architecture

```
nono-librarian (SKILL.md)         ← front door: env + dispatch  [Phase 0 done]
        │
        ├── query/search ───────► search_nodes.py (+embeddings)  [deterministic]
        │                          └─ optional answer prose ───► lib/llm.py
        ├── maintain ───────────► linter/retraction/citation/... [deterministic]
        └── build  ─────────────► librarian_build.py (orchestrator)  [Phase 3]
                                     ├─ reasoning ──► lib/llm.py
                                     └─ retrieval ─► lib/pubmed.py  [Phase 1]
```

Two seams isolate the external worlds, each the single place that touches it:

- **`lib/llm.py`** — local model. *(Implemented in Phase 0.)*
- **`lib/pubmed.py`** — NCBI E-utilities (esearch / efetch / esummary, plus PMC
  full text). Consolidates the ad-hoc E-utilities calls already living in
  `chase_citations.py`, `check_retractions.py`, `preflight.py`,
  `classify_evidence_tier.py`. Honors `NCBI_API_KEY`; fixture-injectable.

## Phase breakdown

### Phase 0 — Front door + LLM seam  *(DONE)*
- `.claude/skills/nono-librarian/SKILL.md` — env bootstrap + intent dispatch.
- `scripts/lib/llm.py` + `tests/unit/test_llm_lib.py` (5 tests).
- Query/search and all maintenance scripts already run Claude-free via the env;
  build path explicitly reports "not yet local" rather than falling back to
  Claude silently.

### Phase 1 — PubMed retrieval seam
- `scripts/lib/pubmed.py`: `search(query, ...) -> [pmid]`, `summaries(pmids)`,
  `fetch_abstracts(pmids)`, `fetch_full_text(pmid)` (PMC when available), all
  fixture-injectable; rate-limit aware (`NCBI_API_KEY`), defensive on failure.
- Tests: fixture-driven parsing of esearch/efetch/esummary payloads; failure
  modes return empty/raise predictably, never partial garbage.
- Optional: refactor the existing scattered E-utilities callers onto this seam
  (de-duplication; out of scope if it risks churn — decide in the plan).

### Phase 2 — Claude-free integrity ops
- Re-express the *reasoning* in `evaluate-kg(-worker)` and `linter-kg` so it can
  run via `lib/llm.py` + `lib/pubmed.py` instead of Claude + MCP. Linter is
  already deterministic; evaluate needs model-driven judgment (claim ↔ evidence)
  — define a strict, schema-validated prompt with a deterministic guardrail
  (e.g. require a verbatim supporting quote; reject if absent).

### Phase 3 — Claude-free build orchestrator  *(the hard part)*
- `scripts/librarian_build.py`: a deterministic state machine over the
  `build-kg.md` phases (breadth classification → sub-query generation → search
  → retrieve → evidence eval → node synthesis → dedup → manifest/ledger →
  embeddings → digest), calling `llm.py` for each reasoning step and
  `pubmed.py` for retrieval. Each LLM step uses a constrained, schema-checked
  prompt with retry, because small models drift; the orchestrator — not the
  model — owns control flow, file writes, and validation.
- Reuses every existing deterministic component (`pmid_ledger.py`,
  `validate_manifest.py`, `build_embeddings.py`, `render_digest.py`, …).

## Resolved decisions (settled 2026-06-24, ready for Phase 3 plan)

1. **Local-model reliability for agentic build.** 7B-class models are weak at
   long-horizon tool use. Mitigation: orchestrator owns control flow; each LLM
   call is a single narrow task with schema validation + retry, not free-roam agency.
   **Decision: no minimum model bar and no gate.** The user is responsible for
   pointing `nono` at a model good enough to run the build; the orchestrator
   does not inspect or refuse based on model size/quality. Schema-validation +
   retry per call remains the only quality guard.
2. **Full-text access.** PMC covers only part of PubMed; many abstracts only.
   **Decision: require full text.** The local build pulls full text (PMC) as the
   evidence basis rather than settling for abstract-only. The pubmed seam
   (Phase 1) must expose full-text retrieval; PMIDs without accessible full text
   are handled by the orchestrator (deferred/flagged), not silently downgraded.
3. **Evidence-tier judgment without Claude.** **Decision: use whatever the
   current Claude-dependent approach uses — which is already fully
   deterministic.** `classify_evidence_tier.py` derives tiers from PubMed
   `publication_types` metadata (title-keyword fallback); the Claude path never
   asks the model for tiers. So the local build reuses the same script verbatim
   and makes **zero model calls** for evidence-tier classification.
4. **Determinism/repeatability.** Local sampling is nondeterministic.
   **Decision: accept run-to-run variance.** No requirement to pin
   `temperature=0`/seed. (We may still set `temperature=0` opportunistically
   where a server honors it, but repeatability is not guaranteed or tested for.)
5. **Cost/latency.** Local build over many PMIDs × multiple LLM calls is slow.
   **Decision: cap concurrency at 3** (hard ceiling — the user's local model
   cannot serve more than 3 concurrent requests). Per-PMID reasoning caching is
   left to my discretion in the Phase 3 plan; default to caching per-PMID LLM
   outputs keyed by PMID + prompt hash so reruns/escalations don't re-pay.

## Testing strategy

- Unit: fakes/fixtures only, no live model/network (Phase 0 already conforms).
- Integration (manual, opt-in): a tiny end-to-end build against a real local
  endpoint + live E-utilities, gated behind an env flag, mirroring the
  fastembed live-smoke approach (skipped when the endpoint is down).
