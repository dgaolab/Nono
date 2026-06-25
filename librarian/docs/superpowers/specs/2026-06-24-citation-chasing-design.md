# Citation Chasing — Design Spec

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-24
**Roadmap:** Phase two, item 4 (see `docs/superpowers/2026-06-24-phase-two-roadmap.md`)
**Builds on:** the E-utilities/fixture pattern of `preflight.py` and `check_retractions.py` (item 3) for discovery, and the run-record + digest (item 2) for surfacing.

## Problem

The graph cites a curated set of PubMed articles, but the foundational
literature those articles themselves rest on is invisible: nothing surfaces the
older, frequently-referenced works that the graph is implicitly built on but
does not yet cite. There is no deterministic way to discover high-value
expansion candidates from the existing corpus. Citation chasing closes that gap
by following backward references from the cited corpus and surfacing the most
central referenced works as a ranked discovery feed.

## Goals

- Deterministically discover candidate PMIDs by following **backward references**
  (`pubmed_pubmed_refs`) from the graph's cited corpus.
- Rank candidates by how central they are to the graph (co-citation frequency),
  with global impact (iCite RCR) as a tiebreak.
- Emit a bounded, deduplicated, ranked candidate feed — surfaced manually and,
  optionally, in the weekly digest.
- Stay read-only on the KG: the sweep never mutates the ledger or nodes.

## Non-goals

- **Ingestion of candidates.** Producing the feed is the whole job. Turning a
  candidate into a node is left to a human, or to a future `--seed-pmids` input
  to `/build-kg`. This keeps the feature integrity-first: growth is a separate,
  opt-in, cost-bearing step.
- **Forward citation chasing** (`pubmed_pubmed_citedin`): backward references
  only, for higher signal and lower volume.
- **LLM / MCP involvement:** the sweep is a deterministic script (the
  `preflight.py` contract).
- **Node or ledger schema changes:** only the reporting run-record schema gains a
  field; no whole-KG backfill.

## Design decisions (resolved during brainstorming)

| Fork | Decision |
|------|----------|
| Direction | **Backward references** (`pubmed_pubmed_refs`) — the foundational work the graph rests on |
| Output | **Discovery feed only** — ranked, deduped candidate PMID list; no auto-ingestion |
| Ranking | **Co-citation frequency primary, iCite RCR tiebreak** (iCite best-effort) |
| Bounding | **`--min-cocitation` floor (default 2) + `--top-n` cap (default 20)** |
| Seeds | All `disposition: "used"` PMIDs; dedup candidates against the **entire** ledger |
| Cadence | **Manual by default**, plus an opt-in `--with-citation-chase` scheduler flag |

## Architecture

A new deterministic script `scripts/chase_citations.py` (no LLM, no MCP, the
`preflight.py` contract). It is **read-only on the KG** — it writes nothing to
the ledger or node files; its only outputs are stdout JSON and an optional log
entry. Given a KG folder it:

1. **Collect seeds** — PMIDs whose ledger `disposition == "used"` (reuse
   `check_retractions.collect_used_pmids`).
2. **Discover references** — for each seed, call NCBI elink
   (`dbfrom=pubmed&db=pubmed&linkname=pubmed_pubmed_refs&id=<pmid>`), batched in
   chunks with NCBI rate etiquette (mirroring `preflight.py`'s inter-call sleep),
   to retrieve that seed's referenced PMIDs. Keep per-seed reference lists so
   co-citation can be computed.
3. **Dedup** — remove any candidate already present in the ledger under any
   disposition (reuse `preflight.load_known_pmids`). Previously-evaluated
   `irrelevant`/`failed`/`superseded`/`retracted` PMIDs therefore never resurface.
4. **Rank** — primary sort by co-citation frequency (number of distinct seeds
   referencing the candidate); tiebreak by iCite RCR (descending). A single
   batched call to `https://icite.od.nih.gov/api/pubs?pmids=<comma-list>` fetches
   RCR/citation_count for all surviving candidates. iCite is **best-effort**: on
   any iCite failure, RCR is `null` and ranking degrades to co-citation only
   (with PMID as the final stable tiebreak) — the sweep still succeeds.
5. **Bound** — drop candidates below `--min-cocitation` (default 2), then keep the
   top `--top-n` (default 20).
6. **Emit** — a structured JSON summary on stdout; log a one-line `citation` op.

On any **elink** network/parse error the script exits non-zero with no log
entry — a failed sweep is a no-op, never a misleadingly-empty feed. (Because the
sweep never mutates the KG, there is no corruption risk; the only guarantee
needed is "don't emit a false empty feed".)

## Output

stdout JSON (also the shape carried into the run-record):

```json
{
  "kg": "KG_Topic",
  "seed_count": 84,
  "candidate_count": 12,
  "icite_status": "ok",
  "candidates": [
    {"pmid": "12345678", "cocitation_count": 6, "rcr": 3.4, "referenced_by": ["111", "222", "333"]}
  ]
}
```

- `icite_status` ∈ `{"ok", "unavailable"}` — records whether RCR enrichment ran.
- `candidates` is sorted by the ranking rule and already bounded.
- `referenced_by` lists the seed PMIDs that reference the candidate (audit trail).

CLI:

```
python3 scripts/chase_citations.py <kg_folder> [--min-cocitation N] [--top-n N]
        [--json] [--elink-fixture FILE] [--icite-fixture FILE]
```

- `--elink-fixture FILE`: JSON `{"<seed_pmid>": ["<ref_pmid>", ...], ...}` replacing
  live elink (tests).
- `--icite-fixture FILE`: JSON `{"<pmid>": <rcr_float>, ...}` replacing live iCite
  (tests). Absent key → RCR `null` for that PMID.
- Without `--json`, prints a human-readable one-line summary to stderr.

## Logging

`scripts/append_log.py` gains a `"citation"` op. The sweep logs, e.g.
*"Citation chase: 12 candidates from 84 cited PMIDs (min co-citation 2, top 20);
iCite ok."* A sweep that finds nothing still logs (proof it ran).

## Surfacing (schedule integration)

- **Manual (default):** `python3 scripts/chase_citations.py <KG_FOLDER>` prints
  the human-readable summary; `--json` emits the structured feed.
- **Scheduled (opt-in):** the scheduler gains a `--with-citation-chase` flag,
  recorded as `schedule.citation_chase: true` in the manifest and substituted into
  the scheduled prompt. When enabled, the scheduled agent runs
  `chase_citations.py --json` and carries the `candidates` array into a new
  optional `citation_candidates` field of whichever run-record it writes that
  session (the build/update record, or the skip record on a quiet week). When the
  flag is unset, the scheduled prompt omits the step and the field. A manual
  `/build-kg` likewise omits it.
- **Digest:** `scripts/render_digest.py` gains a **"Citation candidates"** section,
  rendered only when `citation_candidates` is non-empty, listing each candidate
  PMID, its co-citation count, and RCR. Placed in the growth/reporting region of
  the digest (distinct from the audit-priority Failures/Retractions sections).

This mirrors exactly how item 3's `retractions` field flows from a deterministic
sweep through the run-record into the digest.

## Schema changes

- **Run-record** (`schemas/run_record_schema.json`): add an optional
  `citation_candidates` array. Each item:
  `{"pmid": "...", "cocitation_count": <int>, "rcr": <number|null>, "referenced_by": ["..."]}`.
  Absent/empty when no sweep ran or nothing was found.
- No node-schema or ledger-schema change; no backfill.

## Error handling

- elink network/parse failure → exit non-zero, no log entry, no feed.
- iCite failure → `icite_status: "unavailable"`, all `rcr: null`, ranking by
  co-citation + PMID; sweep still succeeds (exit 0).
- A seed with no `pubmed_pubmed_refs` linkset contributes no candidates (not an
  error — older/un-indexed references are simply absent).
- Empty seed set (no `used` PMIDs) → empty feed, exit 0.

## Testing

The deterministic core is unit-testable with injected elink/iCite fixtures, no
network:

- **Seed collection:** only `disposition: "used"` PMIDs are chased.
- **Reference discovery + co-citation:** given a fake elink map, a candidate
  referenced by multiple seeds gets the right `cocitation_count` and
  `referenced_by` list.
- **Ledger dedup:** a referenced PMID already in the ledger (any disposition) is
  excluded.
- **Ranking:** co-citation frequency orders candidates; iCite RCR breaks ties;
  PMID is the final stable tiebreak.
- **iCite degradation:** with iCite unavailable, ranking falls back to
  co-citation + PMID and `icite_status` is `"unavailable"`, exit 0.
- **Bounding:** `--min-cocitation` drops low-frequency candidates;
  `--top-n` caps the list.
- **No-op on elink failure:** a simulated elink error exits non-zero and emits no
  feed/log.
- **Read-only guarantee:** the ledger and node files are byte-identical before
  and after a sweep.
- **Run-record schema:** a record with a valid `citation_candidates` array
  validates; a malformed item (missing `pmid`/`cocitation_count`) is rejected.
- **Digest:** a run-record with `citation_candidates` renders the "Citation
  candidates" section; an empty/absent one renders nothing.

## Out of scope / follow-ups

- Candidate ingestion (a `--seed-pmids` input to `/build-kg`, or a manual add).
- Forward citation chasing (`pubmed_pubmed_citedin`).
- Per-seed or per-tier seed selection (e.g., chase only from high-evidence nodes).
- Recursive / multi-hop chasing (candidates of candidates).
