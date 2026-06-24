# Supporting-Quote Storage — Design Spec

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-24
**Roadmap:** Phase two, item 1 (see `docs/superpowers/2026-06-24-phase-two-roadmap.md`)

## Problem

Knowledge-graph nodes today identify their evidence only by reference ID plus a
free-text `supports` description (e.g. *"Phase III trial showing 40% response
rate"*). The actual sentence from the source article is never stored. The
evaluator (`evaluate-kg-worker`, step E2) *does* fetch article text and compare
each claim against it, but discards that text after producing a verdict.

This leaves two gaps:

1. **Audit/verification trust** — there is no readable proof, attached to a
   claim, that the node is faithful to its source. A human (or a re-run) cannot
   see the exact sentence the verdict rested on.
2. **Reporting** — the phase-two human-readable digest (roadmap item 2) wants to
   show the sentence behind each new/changed claim, not just a citation.

Storing supporting quotes serves both. This spec is **item 1** specifically
because it changes the node schema: capturing quotes from now on is cheap, while
retrofitting them later would force a re-evaluation pass over the whole KG.

## Goals

- Persist the verbatim source sentence(s) that justify each verified reference.
- Capture them as a byproduct of verification, so stored quotes are exactly what
  the evaluator stood on (audit grade) and only verified quotes are kept.
- Add zero manifest bloat, preserving the phase-one UPDATE cost wins.
- Make the data the digest (item 2) will later read, without building the digest
  here.

## Non-goals

- The human-readable digest itself (roadmap item 2).
- Backfilling quotes onto already-passed nodes (going-forward only).
- Hard verbatim validation against source text (the text is not persisted, so a
  substring check is impossible — verbatim is instruction-enforced).
- Storing quotes for `not_supported` / `unrelated` references.

## Design decisions (resolved during brainstorming)

| Fork | Decision |
|------|----------|
| Primary purpose | Audit/verification trust **and** richer digest (not a cost play) |
| Capture point | At **evaluate** time, by whichever model verifies |
| Storage location | **Node frontmatter, per-PMID** (single source of truth) |
| Quote shape | **1–3 verbatim excerpts** per PMID, each ≤ ~2 sentences, with a per-excerpt `source` marker; no char offsets |
| Who extracts | The **Haiku worker inline** for all passing PMIDs; the strong model likewise on escalation/remediation |
| Existing nodes | **Going-forward only**; `quotes` optional, missing = legacy, handled gracefully |
| Linter | **Info-level** note when a passing PMID lacks quotes (not a warning/error) |

## Data model

Add an optional `quotes` field to each entry in a node's `pubmed_ids` array
(frontmatter only):

```yaml
pubmed_ids:
  - pmid: "35486828"
    supports: "Phase III trial showing 40% response rate at 12 weeks"
    verified: true
    evidence_tier: "rct"
    quotes:                          # NEW — optional; when present, 1–3 items
      - text: "At 12 weeks, 40.2% of patients in the treatment arm achieved a clinical response versus 11.1% with placebo (p<0.001)."
        source: "abstract"           # enum: abstract | full_text
```

Rules:

- **1–3 excerpts** per PMID when the field is present, each a verbatim copy of
  ≤ ~2 sentences from the fetched article text. The field is omitted entirely
  rather than written as an empty array (an absent `quotes` means legacy or
  not-yet-evaluated).
- Each excerpt carries its own `source` (`abstract` | `full_text`), because
  different excerpts may come from different sections.
- No character offsets — article text is not persisted, so offsets carry no
  value.
- `quotes` is present only on `supported` / `partially_supported` references.
  `not_supported` / `unrelated` references get none.
- The field is optional everywhere. A missing `quotes` means the node is legacy
  or has not yet been (re)evaluated, and every consumer treats that gracefully.

The **manifest is unchanged**: it carries PMID *strings* only (not the per-PMID
objects), so quotes add zero manifest bloat and the phase-one manifest-only
UPDATE loading is unaffected.

## Capture — `evaluate-kg-worker` step E2

In E2 the worker already fetches the article and compares each claim against the
abstract (and full text when available). When it assigns a `supported` or
`partially_supported` verdict, it additionally copies 1–3 verbatim sentences it
verified against and tags each with `abstract` or `full_text`.

- **No extra fetch** — the text is already in context at that moment; the cost is
  a few additional output tokens.
- The **same instruction applies to both models**: the Haiku worker captures
  quotes for nodes it passes, and the strong model captures them for
  escalated/remediated nodes. Every passing node therefore carries quotes
  regardless of whether it escalated.
- Excerpts must be copied verbatim (no paraphrase, no ellipsis-editing beyond
  trimming to sentence boundaries).

## Write-back — worker step E5

Quotes ride the existing `update_frontmatter.py` deep-merge path
(`evaluate-kg-worker` lines 203–217). The per-PMID update JSON simply gains
`quotes`:

```json
{"pmid": "35486828", "verified": true,
 "quotes": [{"text": "...", "source": "abstract"}]}
```

`update_frontmatter.py` matches `pubmed_ids` entries by `pmid` (per
`scripts/lib/frontmatter.py` `_IDENTITY_KEYS`), so the update lands on the right
reference.

**Merge semantics (the one shared-code change):** on re-evaluation, the `quotes`
list on a matched PMID entry must be **replaced wholesale** (latest verification
wins), *not* set-unioned. Without this, re-runs would accumulate stale quotes.
This requires an explicit rule in `frontmatter.py`'s merge logic so that, within
a matched `pubmed_ids` entry, the nested `quotes` list is overwritten rather than
recursively merged or unioned. Quotes on *other* PMID entries must remain
untouched.

## Schema and template

- **`schemas/graph_schema.json`** — add `quotes` to the `pubmed_ids` items
  definition: optional `array`, `minItems: 1`, `maxItems: 3`, each item an object with
  `text` (string, required) and `source` (enum: `abstract`, `full_text`,
  required).
- **`templates/node_template.md`** — add a commented `quotes` example under
  `pubmed_ids` so node authors and the worker see the shape.
- **Manifest schema** — no change.

## Linter — `linter-kg`

Add an **info-level** check: for any reference with `verified: true` that has no
`quotes`, emit an informational note (e.g. "passing reference has no supporting
quote"). This is **not** a warning or error — legacy and not-yet-re-evaluated
nodes legitimately lack quotes. The note exists to track digest-coverage growth,
not to fail lint.

## Edge cases

- **Re-evaluation:** quotes replaced wholesale on the matched PMID (see merge
  semantics); other references untouched.
- **Failed/quarantined nodes:** no quotes stored; failing references are
  `not_supported`/`unrelated` by definition.
- **Legacy nodes:** absent `quotes` is valid; no consumer errors on it.
- **Verbatim fidelity:** instruction-enforced only; not validated against source
  text (text not persisted).
- **full_text unavailable:** quotes come from the abstract, marked
  `source: abstract` — the common case.

## Testing

- **Schema:** accepts a valid `quotes` array; rejects `maxItems > 3` and an
  invalid `source` value.
- **Merge (`frontmatter.py`):** re-evaluating a node **replaces** the matched
  PMID's quotes (no accumulation across runs) and leaves other PMIDs' quotes
  untouched.
- **Worker output:** extend the existing eval-worker test expectations so a
  passing node's frontmatter carries `quotes` with `text` + `source`.
- **Linter:** a passing reference without quotes produces an info-level note and
  does not fail lint.

## Out of scope / follow-ups

- Digest consumption of quotes — roadmap item 2.
- Optional one-time backfill of quote-less passing nodes — could be added later
  if digest coverage needs to be complete sooner than natural re-evaluation
  provides.
