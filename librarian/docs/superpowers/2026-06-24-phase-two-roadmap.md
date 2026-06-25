# Libririan — Phase Two Roadmap

**Status:** Approved order, not yet specced
**Date:** 2026-06-24
**Predecessor:** Phase one (token-cost optimization) — merged 2026-06-09 (`dc85b49`)

## Purpose

Phase one cut the cost of scheduled KG updates. Phase two grows the graph's
coverage, integrity, and usability. This document locks the **order** in which
the six phase-two items are tackled. Each item still needs its own design spec
and implementation plan (see `specs/` and `plans/`) before work begins.

## Sequencing principles

The order is governed by two rules, applied in priority order:

1. **Schema-affecting changes first** — anything that changes how nodes store
   data is cheap to adopt going forward but expensive to retrofit (it forces a
   backfill of the entire KG). Do these before accumulating more ingestion debt.
2. **Integrity and reporting before growth and new sources** — establish the
   tooling to audit and report on the graph before features that inflate its
   size, cost, or quality risk.

## The locked order

### 1. Supporting-quote storage
*Schema/data-model change — do first to avoid a whole-KG backfill.*
Changes node schema and ingestion to capture supporting quotes for claims.
Every BUILD/UPDATE run done without it is debt. Also strengthens every later
item: the digest can cite quotes, evaluation can verify against them, and
retraction monitoring can show exactly what is affected.

### 2. Human-readable digest for scheduled runs
*User-critical; the visible payoff. Low coupling, high value.*
A reporting layer over existing manifest/changelog data plus the new quotes.
Building it second also creates the reporting surface that items 3 and 4 plug
their output into.

### 3. Retraction monitoring
*Integrity before growth.*
Checks PubMed for retractions of cited PMIDs and flags affected nodes. Builds
the elink/efetch plumbing (reused by item 4) and surfaces naturally in the
digest. Contained, and protects graph correctness before the graph grows.

### 4. Citation chasing (elink/iCite)
*Reuses item 3's elink plumbing, but is a growth feature that re-inflates cost.*
Expands the graph by following citations. Sequenced after the digest and
retraction scaffolding so growth is auditable and integrity-checked as it lands.

### 5. Semantic node search
*Standalone infra; better once the graph is larger.*
Embedding-based node search over the existing keyword `search_nodes.py`.
Improves routing/query but blocks nothing; benefits from the larger graph that
item 4 produces.

### 6. Preprint track
*Last — highest quality risk.*
Ingests preprints (e.g. bioRxiv/medRxiv) as a separate track with its own
provenance and evidence-tier handling. Best added once quote storage,
retraction monitoring, and the digest are all in place to contain and report
on lower-confidence material.

## Open decision

The genuine fork is **item 1 vs item 2**. The digest is the user-flagged
critical deliverable and could go first for a faster visible win. Trade-off:

- **Quotes first (chosen):** avoids an expensive whole-KG backfill; the digest
  later folds in quotes with only minor rework.
- **Digest first (alternative):** faster visible payoff; accepts minor digest
  rework once quotes land, but keeps accruing ingestion debt until quotes ship.

Quotes-first is the locked default. Revisit only if the digest is needed sooner
than the next ingestion cycle.

## Next step

Pick item 1 (supporting-quote storage) — or the digest, if the fork is flipped —
and run brainstorming → design spec → implementation plan before coding.
