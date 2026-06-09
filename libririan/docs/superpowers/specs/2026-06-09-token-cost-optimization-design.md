# Token Cost Optimization — Phase One Design

**Date:** 2026-06-09
**Status:** Draft, awaiting review
**Goal:** Reduce the cost of a scheduled weekly `/build-kg` UPDATE run from ~$12 to ~$1–3 on active weeks and to near zero on quiet weeks, without reducing what gets verified.

## Cost model (why these four changes)

In an agentic session the entire accumulated context is re-paid as input tokens on every tool round-trip, so cost ≈ context size × turn count. The current pipeline inflates both:

1. Every scheduled run pays the full pipeline even when the week produced no new literature.
2. Evaluation fans out to N parallel workers, each fetching metadata/abstracts into its own context — high-volume judgment work running on the session model.
3. UPDATE mode reads **all** node `.md` files into the builder's context (build-kg.md Phase 2 UPDATE step 1), and that text is re-paid on every subsequent turn. Cost grows linearly with KG size.
4. There is no per-run cost measurement, so optimizations cannot be verified and regressions go unnoticed.

Phase one addresses these four. Levers deferred to phase two are listed at the end.

## Component 1: Preflight early-exit (`scripts/preflight.py`)

A deterministic script (no LLM, no MCP) that answers "is there anything new this week?" before an agent session spends anything.

### Persisted search profile

`/build-kg` currently re-derives sub-queries from the topic on every run. To make preflight possible (and UPDATE searches reproducible), BUILD mode persists them in `manifest.json`:

```json
"search_profile": {
  "breadth": "narrow",
  "sub_queries": ["melatonin circadian rhythm molecular mechanism", "..."],
  "updated": "2026-06-09"
}
```

- Written in build-kg.md Phase 2 step 9 (manifest write) from the Phase 1b Step 1 sub-queries.
- UPDATE mode's Recent track (Phase 1b UPDATE Step 4) **reads** `search_profile.sub_queries` instead of re-deriving them. Gap-fill queries remain per-run and are not persisted.
- Added to `schemas/graph_schema.json` as an optional top-level property (the schema permits additional properties, so legacy manifests stay valid).

### Script behavior

```
python3 scripts/preflight.py {KG_FOLDER} [--threshold 3] [--since YYYY-MM-DD] [--log] [--esearch-fixture FILE]
```

1. Read `manifest.json`: `search_profile.sub_queries`, `since_date` from `schedule.last_run`, falling back to `updated`. Error (exit 2) if no `search_profile` — the KG predates this feature; one normal `/build-kg` run backfills it.
2. For each sub-query, call NCBI E-utilities `esearch` directly (`db=pubmed`, `datetype=edat`, `mindate={since}`, `retmax` per breadth tier). Rate-limit to 3 req/s (10 req/s if `NCBI_API_KEY` is set in the environment).
3. Deduplicate the union of returned PMIDs against `_pmid_ledger.json` (import the ledger-loading function from `pmid_ledger.py`; all dispositions count as "known").
4. Print a JSON report: `{kg, since_date, threshold, per_query: [{query, total_hits, novel}], novel_count, novel_pmids, proceed}` where `proceed = novel_count >= threshold`.
5. With `--log`, append a `preflight` op to `_log.md` by importing the append function from `append_log.py` (e.g., "Preflight: 1 novel PMID since 2026-06-02 — below threshold 3, skipping update.").
6. Preflight does **not** write to the ledger. Novel PMIDs are only counted; the real build run records dispositions properly. This avoids double-tracking and keeps preflight side-effect-free apart from the optional log line.
7. `--esearch-fixture` substitutes a fixture JSON for live E-utilities calls (same pattern as the existing `--test` modes) for testing.

Exit codes: 0 = ran successfully (read `proceed` from JSON), 2 = unusable manifest, 1 = network/parse error.

### Scheduler integration

The prompt template in `schedule-kg.md` Step 2 changes to:

```
1. Run: python3 scripts/preflight.py <KG_FolderName> --log
2. If the JSON output has "proceed": false, report a one-line summary
   ("Quiet week: N novel PMIDs, below threshold") and STOP. Do not load the KG.
3. Otherwise run /build-kg "<topic>" --output <KG_FolderName> (UPDATE mode).
```

The threshold default is 3; `/schedule-kg` gains an optional `--threshold N` recorded as `schedule.threshold` in the manifest (added to `graph_schema.json` as an optional property) and substituted into the scheduled prompt's preflight command.

### Fix: `schedule.last_run` is never stamped

Preflight's `since_date` depends on `schedule.last_run`, which today is only updated if the scheduled agent remembers a trailing instruction in its prompt — manual UPDATE runs never touch it.

- `update_manifest_stats.py` gains a `--stamp-last-run` flag: if the manifest has a `schedule` block, set `schedule.last_run` to the current ISO timestamp; otherwise no-op.
- build-kg.md Phase 4 gains an explicit step (after validation, before logging): `python3 scripts/update_manifest_stats.py {KG_FOLDER} --stamp-last-run`.
- The trailing "update the schedule.last_run timestamp" instruction is removed from the `schedule-kg.md` prompt template.

## Component 2: Evaluation workers on Haiku

Per-PMID verification (read claim, read abstract, emit verdict) is high-volume, well-bounded judgment work — suited to a cheaper model at roughly a third of the price.

### Changes to `evaluate-kg.md`

- **Step 3a (parallel path):** the Agent tool calls that spawn workers set `model: haiku`.
- **Step 1 (direct path, N ≤ 5):** instead of invoking `/evaluate-kg-worker` inline (which forks context but inherits the session model), spawn a single worker via the Agent tool with `model: haiku`, using the same prompt template as Step 3a **with `--chunk-id 1`**. The direct path thereby converges with the parallel path: the orchestrator always runs the merge, ledger-sync, and manifest-stats steps itself. (Standalone invocation of `/evaluate-kg-worker` without `--chunk-id` keeps its current self-contained behavior.)

### Escalation guard against cheap-model false negatives

A wrongful **fail** verdict is the worst failure mode: it triggers remediation and quarantine. Pass verdicts (the common case) carry less risk. So fails get a second opinion from the stronger model:

- `evaluate-kg-worker.md` gains a `--no-remediate` flag. With it, Step E4 is skipped entirely: failed nodes are written to the chunk file with `overall_status: "failed"` and a note `"pending escalation"`, and node frontmatter is **not** updated for failed nodes (passed nodes are updated normally).
- The orchestrator passes `--no-remediate` to all Haiku workers.
- New orchestrator **Step 3.5 (Escalation pass):** after all waves, collect node IDs with `overall_status: "failed"` from the chunk files. If any, spawn ONE worker at the session model (no `--no-remediate`) with exactly those nodes and the next chunk ID. Its verdicts overwrite the Haiku verdicts at merge (merge_eval_chunks dedups by `node_id`, later entry wins — the escalation chunk gets the highest chunk number to guarantee ordering).
- Net effect: passes are cheap; fails cost one extra strong-model check on a small node set; remediation and quarantine decisions are always made by the strong model.

Direct path: the single Haiku worker also runs with `--no-remediate`; if it reports fails, the orchestrator spawns the escalation worker the same way.

## Component 3: Manifest-only UPDATE loading

### Changes to `build-kg.md`

- **Phase 2 UPDATE step 1** rewritten: read `manifest.json` only. Do NOT read node `.md` files at this stage. The manifest's per-node `summary`, `keywords`, `tags`, `pubmed_ids`, `evaluation_status`, and `evidence_tier` are sufficient for routing decisions.
- **Phase 1b UPDATE Step 2 (weak-spot scan)** explicitly operates on manifest node entries (PMID counts, `evaluation_status`, `quarantined`, tag distribution) — no node file reads.
- **Phase 2 UPDATE step 3 (compare new material)**: route each new fragment/PMID to candidate nodes by matching against manifest summaries and keywords. Optionally run `scripts/search_nodes.py "{fragment key terms}" {manifest} --compact --top 5` for routing — it already reads only manifests.
- **Phase 2 UPDATE step 4 (apply changes)**: read a node's full file (`parse_node.py`) only for nodes actually selected for modification, immediately before editing. Relationship design for new nodes uses manifest summaries.

Result: UPDATE context cost stops scaling with total KG size and scales with the size of the week's changes instead.

## Component 4: Cost instrumentation

### `scripts/cost_report.py`

Parses Claude Code transcript JSONL files. Assistant messages carry `message.usage` (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) and `message.model`. The script:

- Sums token usage by model across a transcript, including any sibling subagent transcripts for the same session that are discoverable in the transcript directory.
- Computes estimated USD cost from a static built-in price table (per-model input/output/cache-write/cache-read rates, with a header comment noting it must be kept current; `--price-file` overrides).
- Modes:
  - `--hook`: read the Stop-hook JSON from stdin (`transcript_path`, `session_id`), compute totals, append one JSON line to `_cost_log.jsonl` at the repo root: `{session_id, ts, models: {…}, totals: {input, output, cache_read, cache_write}, est_cost_usd}`. A session that stops multiple times appends multiple lines; consumers take the last line per `session_id` (totals are cumulative, so the last line is authoritative).
  - `--summary [--last N]`: print a readable table of the latest entry per session (timestamp, tokens, est. cost) for audits.

### Hook registration

Project `.claude/settings.json` (created — currently only `settings.local.json` exists) registers a Stop hook running `python3 scripts/cost_report.py --hook`. It fires for every session in this project, including interactive ones — acceptable, since `_cost_log.jsonl` is an audit log keyed by session. `_cost_log.jsonl` is added to `.gitignore`.

### Known limitation

The Stop hook fires when a session ends, so a run's cost entry lands *after* any in-run reporting. Per-run cost therefore appears in `_cost_log.jsonl` / `--summary`, not in the run's own terminal output. The phase-two digest can quote the previous run's cost. Per-phase breakdown is out of scope for phase one; per-run totals are enough to verify each optimization's impact.

## Error handling

- **Preflight network failure:** exit 1 with an error JSON; the scheduler prompt instructs the agent to fall through to a normal `/build-kg` run (fail open — a wasted run is better than a silently skipped update).
- **Missing `search_profile` (legacy KG):** preflight exits 2 with a clear message; the scheduled agent falls through to a normal run, which backfills the profile.
- **Haiku worker quality drift:** bounded by the escalation pass; quarantine decisions never rest on a cheap-model verdict alone.
- **cost_report on malformed transcript lines:** skip unparseable lines, never fail the hook (hook errors must not disrupt sessions).

## Testing

- `preflight.py`: fixture-driven test using `--esearch-fixture` plus the existing `tests/output/KG_Melatonin_Circadian` manifest (after adding `search_profile` to the test-mode manifest write); assert novel-count math, threshold logic, ledger dedup, and exit codes. One live smoke test against E-utilities.
- `cost_report.py`: fixture transcript JSONL in `tests/fixtures/`; assert per-model sums, price math, hook-mode append, and last-line-per-session semantics.
- `update_manifest_stats.py --stamp-last-run`: unit test on a manifest with and without a `schedule` block.
- Prompt changes (build-kg, evaluate-kg, worker, schedule-kg): `/build-kg --test` must still pass end-to-end (test mode is BUILD-only, so it exercises the manifest `search_profile` write and the evaluator's Haiku + escalation path). UPDATE-mode loading changes are verified by one real UPDATE run, comparing `_cost_log.jsonl` before/after.
- `validate_test_output.py` extended to assert `search_profile` presence in the test manifest.

## Verification of the goal

Run one real weekly UPDATE before merging the prompt changes and one after, compare `_cost_log.jsonl` entries. Success criteria: active-week cost reduced by ≥60%; a quiet-week scheduled run (preflight exit) costs only the preflight-wrapper session (~$0.05–0.2).

## Out of scope (phase two candidates)

- Deterministic harvest script replacing Phase 1b MCP round-trips (lever 3 from discussion)
- Full-text fetch-to-disk and selective section reading (lever 5)
- Verify-only-new-PMIDs evaluation scope on UPDATE (lever 6)
- Human-readable digest for scheduled runs; retraction monitoring; supporting-quote storage
- Per-phase cost attribution
