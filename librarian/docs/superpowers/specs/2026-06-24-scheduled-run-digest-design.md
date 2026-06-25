# Scheduled-Run Digest — Design Spec

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-24
**Roadmap:** Phase two, item 2 (see `docs/superpowers/2026-06-24-phase-two-roadmap.md`)
**Depends on:** Phase-two item 1 (supporting-quote storage, merged `7f4dbff`) — the digest renders the verbatim quotes that item stores in `_evaluation_log.json`.

## Problem

A scheduled KG update runs unattended (cron → `/build-kg` in UPDATE mode). Today
it leaves only scattered machine artifacts: a one-line `_log.md` entry, an
LLM-authored `_changelog.md` section, `_evaluation_log.json`, the manifest
`statistics`, and a `_cost_log.jsonl` line. There is no single human-readable,
audit-grade report of what a run did. The user has called audit-readable
reporting the critical gap.

## Goals

The digest's primary job, in priority order **A = B > C**:

- **A — Audit trust.** For each new/changed claim, show its supporting quote and
  evaluation verdict so a human can confirm new knowledge is faithful to its
  sources without opening node files. The digest is defensible evidence.
- **B — What's new.** Read as a "what changed this week" briefing: nodes created,
  nodes revised, notable new references.
- **C — Operational health.** A lighter footer: run totals, pass/fail tallies,
  quarantines, tokens/$.

## Non-goals

- Push delivery (email/Slack). **Files only** this spec. The generator is a
  deterministic cron-run script, and the available Gmail/Slack integrations need
  interactive auth that may be absent in a headless run; notification is a clean
  follow-up once the content is proven.
- Any LLM in the digest-generation path (see Generation).
- Cross-KG or multi-run rollups/analytics (the structured run-record this spec
  introduces enables them later).
- Retraction/citation features (separate roadmap items).

## Design decisions (resolved during brainstorming)

| Fork | Decision |
|------|----------|
| Primary purpose | Audit trust **and** what's-new (tied), then operational/cost — **A = B > C** |
| Generation | **Deterministic Python script**, no LLM — verbatim facts/quotes/verdicts, reproducible |
| Run-scope source | A structured **run-record JSON** emitted by build-kg, **not** parsing `_changelog.md` prose or matching node dates |
| Output/history | **Per-run immutable files** `digests/<run_id>.md` + a `_digest.md` latest pointer |
| Run scope | **All runs** — initial BUILD, every UPDATE (scheduled or manual), and a minimal **skip digest** for gated quiet weeks |
| Detail depth | **Full evidence for all changed nodes** (claim + quote + verdict); initial BUILD falls back to summary counts |
| Delivery | **Files only** (notify later) |

## Architecture

A new deterministic script, `scripts/render_digest.py`, renders a digest by
reading structured inputs only. It never calls an LLM and never parses prose.

Inputs and their roles:

- **run-record** (`runs/<run_id>.json`, new — see below) → *scope*: which nodes
  and references changed this run, the run's mode/version/novelty, and the cost
  session id.
- **`_evaluation_log.json`** → *evidence*: per-reference verdict, reasoning, and
  the verbatim `quotes` (from phase-two item 1) for the changed nodes.
- **manifest `statistics`** → *totals*: node/edge counts, evidence-tier
  distribution, active vs quarantined.
- **`_cost_log.jsonl`** → *operational footer*: this run's tokens and estimated
  cost, joined on the run-record's `cost_session_id`.

Rendering is a pure function of these inputs: same inputs → byte-identical
digest.

### Why a run-record (not date-derivation or prose-parsing)

Identifying "what changed this run" deterministically is the crux. Node
`updated` dates are day-granular and would misattribute two runs on the same
day; `_changelog.md` is LLM-authored prose and fragile to parse. A structured
run-record makes digest rendering a pure transform and doubles as a reusable
audit artifact for later roadmap items. Cost: one small additional file written
per run.

## Run-record

build-kg Phase 4 gains a step that writes `runs/<run_id>.json`, where
`run_id = "<UTC-timestamp>-v<version>"` (e.g. `2026-06-24T080012Z-v7`). It is the
machine-readable twin of the changelog buffer build-kg already maintains in
Phase 2. A JSON Schema at `schemas/run_record_schema.json` governs it.

```json
{
  "run_id": "2026-06-24T080012Z-v7",
  "kg_name": "KG_Topic",
  "mode": "update",
  "timestamp": "2026-06-24T08:00:12Z",
  "version": 7,
  "since_date": "2026-06-17",
  "preflight": {"novel_count": 9, "threshold": 3},
  "nodes_created": ["node_016"],
  "nodes_revised": ["node_003"],
  "refs_added":  [{"pmid": "39876543", "nodes": ["node_003", "node_016"]}],
  "refs_failed": [{"pmid": "00000001", "node": "node_005", "reason": "verification failed"}],
  "eval_summary": {"evaluated": 3, "passed": 2, "failed": 1},
  "cost_session_id": "uuid-or-null"
}
```

Field rules:

- `mode`: `build` | `update` | `skip`.
- `since_date`: `null` for an initial BUILD.
- `preflight`: present for scheduled runs; may be `null` for a manual run.
- `nodes_created` / `nodes_revised`: node IDs touched this run (empty for `skip`).
- `refs_added` / `refs_failed`: references added or failed this run (empty for
  `skip`).
- `eval_summary`: counts from this run's evaluation (zeros for `skip`).
- `cost_session_id`: join key into `_cost_log.jsonl`; `null` if unknown.

For a **skip** run, the schedule path writes a minimal record (`mode: "skip"`,
populated `preflight`, empty change arrays, zeroed `eval_summary`) so quiet weeks
still leave a dated digest.

## Digest content & layout

`render_digest.py` renders the run-record (plus the other inputs) to markdown,
ordered by the A = B > C priority.

**Real run (build/update):**

1. **Header** — KG name, run date, version, mode; a one-line outcome, e.g.
   "9 novel PMIDs → 1 node added, 1 revised; 2/3 passed evaluation".
2. **What changed (audit body)** — for each node in `nodes_created` then
   `nodes_revised`: title + link and the node's eval verdict; then, for each of
   that node's references, the PMID + citation, the per-reference verdict, and
   the **verbatim supporting quote(s)** copied from `_evaluation_log.json`.
   Quotes and verdicts are never paraphrased. The references shown for a node
   are: for a created node, all of its references; for a revised node, the
   references in the run-record's `refs_added` that map to that node (the ones
   this run touched).
3. **Failures & quarantines** — nodes that failed evaluation or are quarantined,
   and entries from `refs_failed`, each with its reason. Always a distinct
   section; never buried in the audit body.
4. **Operational footer (C)** — run totals from manifest `statistics`
   (total/active/quarantined nodes, evidence-tier distribution) and this run's
   tokens + estimated cost from `_cost_log.jsonl`, joined on `cost_session_id`.
   **Cost timing:** the current session's cost line is appended by the Stop hook
   at session end — *after* Phase 4 renders the digest — so it is normally not
   present at render time. When the `cost_session_id` is not yet found in
   `_cost_log.jsonl`, the footer shows "cost: pending — session `<id>`, see
   `_cost_log.jsonl`". The recorded `cost_session_id` keeps the cost joinable
   later. Live cost is therefore best-effort by design; the audit body (A/B)
   never depends on it.

**Skip run:** header plus the one-line quiet-week message
("9 novel PMIDs since 2026-06-17, below threshold 3 — no update"). No audit body.

**Initial BUILD:** header plus summary counts and a node list (no per-quote
dump), because "this run" is the whole graph and a full quote dump would be
huge.

## Outputs & invocation

- Writes `digests/<run_id>.md` (immutable; never overwritten).
- Overwrites `_digest.md` with a copy of the most recent run's digest (latest
  pointer).
- Logs a `digest` operation to `_log.md` via `append_log.py` (a new valid op).
- **Invocation:**
  - build-kg Phase 4 gains a final step (after manifest stats, changelog, and
    the build/update log entry) that writes the run-record and then calls
    `render_digest.py`.
  - The schedule-kg skip path (when preflight returns `proceed: false`) writes a
    `skip` run-record from the preflight JSON and calls `render_digest.py`,
    so quiet weeks produce a digest.

## Error handling

The digest must never fail a run. Missing or malformed inputs degrade
gracefully:

- Missing `_cost_log.jsonl` entirely → footer shows "cost: unavailable". A
  present log with no line yet for this `cost_session_id` (the normal Stop-hook
  timing) → footer shows "cost: pending" (see Cost timing above).
- A changed node absent from `_evaluation_log.json` → list the node with
  "evaluation pending / not found" instead of crashing.
- Missing manifest `statistics` fields → omit those lines.
- `render_digest.py` returning non-zero must not abort the build; the calling
  step treats digest failure as a warning, mirroring the cost hook's
  never-fail contract.

## Testing

Because generation is deterministic, it is unit-testable with fixtures:

- **Real-run rendering:** a sample run-record + `_evaluation_log.json` (with
  quotes) + `_cost_log.jsonl` → assert the rendered markdown contains the
  verbatim quotes, the correct per-node verdicts, the run-outcome tallies, a
  failures section for the failed ref, and the cost footer.
- **Skip mode:** a `skip` run-record renders header + one-line message only, no
  audit body.
- **BUILD summary mode:** a `build` run-record renders counts + node list, no
  per-quote dump.
- **Graceful degradation:** missing cost log → "cost: unavailable"; a changed
  node missing from the eval log → "evaluation pending" line, no crash.
- **Determinism:** rendering the same inputs twice yields byte-identical output.
- **Schema:** a valid run-record validates against `schemas/run_record_schema.json`;
  an invalid `mode` is rejected.
- **Latest pointer:** after a run, `_digest.md` equals the newest
  `digests/<run_id>.md`.

## Out of scope / follow-ups

- Push notification (email/Slack) once content is proven.
- Cross-run / cross-KG analytics built on the accumulated run-records.
