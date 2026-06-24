# Retraction Monitoring — Design Spec

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-24
**Roadmap:** Phase two, item 3 (see `docs/superpowers/2026-06-24-phase-two-roadmap.md`)
**Builds on:** the run-record + digest (item 2, merged `af2a17e`) for surfacing, and the PMID ledger / `enforce_quarantine.py` for action.

## Problem

A KG cites PubMed articles that may be **retracted after** they were cited. Today
nothing detects this: "Retracted Publication" is a known PubMed publication type
but is explicitly *ignored* during tier classification, and no field, sweep, or
node action exists. A KG can therefore present claims backed by retracted
science indefinitely. Retraction monitoring closes that gap by periodically
checking the full cited corpus and acting on retractions.

## Goals

- Periodically detect, deterministically, which cited PMIDs have been retracted.
- Flag the retracted reference on its ledger entry and citing nodes.
- Quarantine a node only when retraction removes its last valid support
  (conditional quarantine).
- Surface every finding in the run digest and operation log.

## Non-goals

- Remediation (finding replacement references): handled by the existing
  `/evaluate-kg` remediation flow when a flagged/quarantined node is next
  evaluated.
- NCT/ChEMBL references: no equivalent retraction signal; PMIDs only.
- A second external API: detection stays within NCBI E-utilities (no iCite).
- LLM involvement: the sweep is a deterministic script (the `preflight.py`
  contract).

## Design decisions (resolved during brainstorming)

| Fork | Decision |
|------|----------|
| Action on retraction | **Flag + conditional quarantine** (quarantine only when no valid support remains) |
| Trigger / cadence | **Folded into the weekly scheduled run**, before the preflight proceed/skip branch (runs every week, incl. quiet weeks); also invokable manually |
| Scope | **Full cited corpus** each sweep (not just newly-added refs) |
| Detection source | **PubMed esearch intersection** (cited PMIDs ∩ `"Retracted Publication"[pt]`), reusing the `preflight.py` E-utilities pattern |
| Quarantine mechanism | Route through `evaluation_status: "failed"` + `enforce_quarantine.py`, preserving the linter's `quarantine_drift` invariant |

## Architecture

A new deterministic script `scripts/check_retractions.py` (no LLM, no MCP —
the `preflight.py` contract: no corrupt writes on failure). Given a KG folder it:

1. **Collect** all cited PMIDs from `_pmid_ledger.json` — entries with
   `disposition: "used"`.
2. **Detect** retractions: batch the PMIDs (~200 per query) and call NCBI
   esearch with `term=(pmid1 OR pmid2 …) AND "Retracted Publication"[Publication Type]`,
   reusing preflight's request/parse helper. The returned id list is the
   retracted subset. Article titles for reporting come from the ledger (no extra
   fetch).
3. **Update `last_checked`** on every swept entry (proof the sweep ran).
4. **Flag, act, and summarize** (Sections below).
5. **Return** a structured summary of this sweep's findings.

On any network/parse error the script exits non-zero **without** mutating the
ledger or node files — a failed sweep is a no-op, never a false "all clear".

## Ledger and node flagging

- **Ledger** (`schemas/pmid_ledger_schema.json`, `scripts/pmid_ledger.py`):
  add `"retracted"` to the `disposition` enum and the valid transition
  `used → retracted`. A newly-detected retracted PMID's entry is set to
  `disposition: "retracted"` with a `notes` stamp recording the detection date.
  `assigned_nodes` is the authoritative reverse index to the citing nodes.
- **Node frontmatter:** on each citing node, the matching `pubmed_ids` entry
  (matched by `pmid` via `update_frontmatter.py`'s deep-merge) gets
  `verified: false` and a new `retracted: true` flag.

## Conditional quarantine

After flagging, for each affected node the script recomputes **valid support** =
the count of that node's `pubmed_ids` entries with `verified: true` and not
`retracted: true`.

- **≥1 valid support:** the node stays active but flagged — the retracted
  reference is visibly marked `retracted: true` in its frontmatter.
- **0 valid support:** set the node's `evaluation_status: "failed"` with a
  retraction note in the body, then run `scripts/enforce_quarantine.py` to set
  `quarantined: true` in the node and manifest.

Routing quarantine through `evaluation_status` is deliberate: the linter's
`quarantine_drift` check enforces `evaluation_status == "failed" ↔ quarantined
== true`. Setting `quarantined` directly would violate that invariant. The
slight semantic overload of `evaluation_status` ("failed evaluation" vs "support
retracted") is accepted in exchange for reusing the existing, linter-checked
machinery; the body note records the true reason.

## Surfacing

- **Run-record** (`schemas/run_record_schema.json`): add an optional
  `retractions` array. Each item:
  `{"pmid": "...", "nodes": ["node_003"], "action": "flagged" | "quarantined"}`.
  Absent/empty when the sweep found nothing.
- **Digest** (`scripts/render_digest.py`): add a **"Retractions"** section,
  rendered only when `retractions` is non-empty, listing each retracted PMID,
  its citing nodes, and the action taken. Placed near the failures section
  (high visibility, audit priority).
- **Log** (`scripts/append_log.py`): add a `"retraction"` op. The sweep logs,
  e.g. *"Retraction sweep: 2 PMIDs retracted; 1 node quarantined, 1 flagged
  (sweep of 84 cited PMIDs)."* A clean sweep with no findings still logs (proof
  it ran), e.g. *"Retraction sweep: 0 of 84 cited PMIDs retracted."*

## Scheduling and manual use

- **Scheduled:** `schedule-kg.md`'s scheduled prompt gains a **Step 0** that runs
  `check_retractions.py --json` over the full corpus **before** the preflight
  proceed/skip branch, so it runs every week including quiet weeks. The scheduled
  run is a single agent session: the agent observes the Step-0 `--json` summary
  and carries it into the `retractions` field of whichever run-record it writes
  later this session — the build/update record (build-kg Phase 4) when the update
  proceeds, or the skip record (schedule-kg) when preflight gates it. No separate
  handoff file is needed; the ledger (`disposition: "retracted"`) is the durable
  record, and `retractions` in the run-record is the per-run reporting echo. A
  **manual** `/build-kg` (not via the scheduler) runs no sweep, so its run-record
  simply omits `retractions`.
- **Manual:** `python3 scripts/check_retractions.py <KG_FOLDER>` runs the same
  sweep and prints a human-readable summary; `--json` emits the structured
  summary for piping into a run-record.

## Error handling

- Network/parse failure → exit non-zero, no mutations, and (in the scheduled
  flow) the week proceeds without retraction data rather than aborting the run.
- A PMID in the ledger but absent from PubMed results is simply not retracted
  (no action).
- The sweep never quarantines a node for any reason other than zero valid
  support after retraction flagging.

## Testing

The deterministic core is unit-testable by injecting a fake esearch result
(monkeypatching the E-utilities call), with no network:

- **Detection + flagging:** given a fake "retracted" subset, the cited PMIDs'
  ledger entries become `disposition: "retracted"` and the citing nodes'
  `pubmed_ids` entries get `verified: false` + `retracted: true`.
- **Conditional quarantine:** a node whose only support is retracted gets
  `evaluation_status: "failed"` and is quarantined; a node with another valid
  ref stays active and flagged.
- **Summary / run-record shape:** the returned summary lists each PMID with its
  nodes and `action` (`flagged`/`quarantined`).
- **No-op on failure:** a simulated network error leaves the ledger and nodes
  unmodified and exits non-zero.
- **last_checked:** every swept "used" entry has its `last_checked` advanced on
  a successful sweep.
- **Clean sweep:** zero retractions still logs and returns an empty
  `retractions` list (no node changes).
- **Digest:** a run-record with `retractions` renders the "Retractions" section;
  an empty/absent one renders nothing.
- **Schema:** a run-record with a valid `retractions` array validates; a bad
  `action` value is rejected.

## Out of scope / follow-ups

- Replacement-reference remediation (existing evaluate flow).
- Detecting expressions of concern / corrections (only retractions here).
- A separate retraction cadence (folded into the weekly run for now).
