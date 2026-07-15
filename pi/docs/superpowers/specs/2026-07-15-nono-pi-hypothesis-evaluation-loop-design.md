# Design — nono-pi hypothesis-evaluation loops

**Date:** 2026-07-15
**Status:** Approved in shape (pending written-spec review)
**Repo:** dgaolab/Nono — pi module (extends the shipped `nono-pi`)
**Builds on:** `2026-07-08-nono-pi-design.md` (the base module)

## 1. Goal

Add iterative **hypothesis-evaluation loops** to `nono-pi`. Today the module is
single-pass: intake → KGs → one-shot gap gate → Significance & Innovation →
write. This adds two adversarial, evidence-grounded refinement loops, each
**halting for the user every round**:

1. **Aims loop** (pre-writing) — iterate on the hypothesis + Specific Aims until
   the scientific premise survives evaluation against the KGs. Replaces the
   single-pass gap gate.
2. **Draft loop** (post-writing) — iterate on the drafted deliverable via the
   routed review skills.

New flow:

```
intake → KGs → [AIMS LOOP] → S&I → write → [DRAFT LOOP] → done
```

The loops must stay **model-agnostic and agent-driven**, exactly like the rest
of `nono-pi`: the evaluation and revision are the running agent's reasoning; the
CLI does only deterministic bookkeeping and one deterministic computation
(evidence-strength scoring).

## 2. Judging strategy — reasoning-based, KG-grounded

Hypothesis validity here is a **reasoning judgment**, not a statistical test: the
evidence base is a literature knowledge graph (claims + PMIDs + evidence tiers),
not a dataset. Each round the agent produces an adversarial, multi-lens
evaluation:

- **Aims loop lenses:** *soundness/logic*, *novelty*, *significance*,
  *contradiction-check* (is the premise contradicted by the literature?).
- **Draft loop lenses:** reviewer-simulation + coherence via the routed skills
  (`grant-mock-reviewer` for grants; `scientific-manuscript-review` /
  `sci-paper-reviewer` for papers) — does the draft actually test the
  hypothesis, aims↔methods coherence, reviewer-flagged weaknesses.

**Every verdict must cite KG node IDs / PMIDs** — no unsupported "valid",
mirroring the librarian's verbatim-quote guardrail (a claim without a citable
basis cannot pass). This keeps the reasoning traceable and auditable.

**Quantitative grounding, not statistical testing:** a deterministic
evidence-strength score (§5) computed from the KG's existing fields is fed to
the aims-loop critic so its judgment is calibrated and reproducible. **Data-level
statistics stay out of `nono-pi`** — when validity actually hinges on data that
doesn't exist yet, that is a weakness the loop flags and emits as a
`nono-analyst` task via the existing `analysis_plan.md` handoff (§4).

## 3. The two loops (agent-driven control flow, in SKILL.md)

Both loops share one shape: **evaluate → HALT → user decides → (revise → repeat)
| stop**. Per round the agent (a) produces a structured evaluation, records it
with `nono-pi eval record`; (b) presents the verdict + proposed revision and
halts; (c) the user chooses **approve revision / edit / accept-as-is / stop**,
recorded with `nono-pi eval decide`; (d) on `approved`, the agent applies the
revision and starts the next round.

### 3.1 Aims loop (replaces the single-pass gap gate, old Step 5)

- **create mode:** round 0's artifact is the agent's first-draft hypothesis +
  Specific Aims (proposed from goal + input + KGs).
- **revise mode:** round 0's artifact is the **existing** hypothesis/aims
  *extracted from the ingested draft* — the loop **evaluates what's already
  there** rather than inventing a premise. Approved revisions edit the draft's
  aims (versioned, §3.3).
- **Gap-gate role is absorbed here.** A round whose verdict is
  "premise contradicted" or "gap closable only by further analysis" triggers the
  existing handoff: write `gaps_report.md`, and where a gap is analysis-closable,
  `nono-pi analysis-plan <out> --input <analysis_input.json>` for the future
  `nono-analyst`. The `gap_gate` ledger field is retained and set from within the
  loop; the standalone Step-5 gap gate is removed.
- Loop ends when the user **accepts** an evaluated round (premise sound) → proceed
  to S&I + write — or **stops**.

### 3.2 Draft loop — mode-agnostic, unified with the revise write step

The draft loop is the **single engine** that drives the routed review→revise
skills; its round-0 **seed** differs by mode, so there is never a separate loop
stacked on top of revise mode:

- **create mode:** the deliverable is written once (existing Step 8), then the
  draft loop reviews/revises it.
- **revise mode:** the seed is the ingested baseline `draft/v000.<ext>`. **Revise
  mode's write step *is* the draft loop** — there is no extra pass. This removes
  the double-reviewing risk: revise/resubmission tasks get one iterative
  halt-each-round loop instead of a one-shot revise plus a separate loop.

Each approved round applies the routed revise-column skills and writes the next
version (create: updates `draft/<section_key>.md`; revise: new
`draft/v<NNN>.md`, `v000` immutable). Loop ends on user **accept** or **stop**.

### 3.3 Review tasks — evaluate-then-stop, the report is the deliverable

A "review/assess" task (critique an existing grant/paper without rewriting)
needs **no new mode**. Because each round records its evaluation *before* any
revision, a review task = run the relevant loop, choose **accept/stop after
round 0**, and take the rendered evaluation report (§4) as the deliverable. No
draft is written.

## 4. Deterministic CLI additions (bookkeeping — no reasoning)

New command group `eval`:

- **`nono-pi eval record <out> --loop aims|draft --input <round.json>`**
  Validates the agent's round JSON against `eval_round_schema.json`, assigns the
  round number (`len(rounds)`), sets the loop `status="in_progress"`, appends the
  round (with `decision=null`), and (re)renders a human-readable
  `<out>/<loop>_evaluation.md` from all rounds (via a packaged template). Prints
  a one-round summary.
- **`nono-pi eval decide <out> --loop aims|draft --decision approved|accepted|stopped [--note <text>]`**
  Sets the latest round's `decision` (+ optional note). `accepted`/`stopped` set
  the loop `status` accordingly; `approved` leaves it `in_progress` (a next round
  is expected).

Extend existing:

- **`status`** gains a loops section: for `aims_loop` and `draft_loop`, print the
  status and a compact rounds table (round #, per-dimension verdicts, decision).
- **`__main__.COMMANDS`** += `eval`, `evidence-score`.

`analysis-plan` and the `gaps_report.md` handoff are reused unchanged from the
base module.

## 5. Evidence-strength score (deterministic, `evidence-score` command)

**`nono-pi evidence-score <out> [--kg <slug>]`** reads each KG's manifest + nodes
and computes a per-node **strength score in [0,1]** purely from fields the
librarian already populates, then writes `<out>/kgs/<slug>/_evidence_score.json`
(`{node_id: {score, factors}}`) and prints a per-KG summary. Consumed by the
aims-loop critic as grounding.

Score = product/blend of deterministic factors (exact librarian field names
confirmed against `graph_schema.json` at implementation time):

- **Evidence tier** → base weight (higher tier ⇒ higher base).
- **Independent-source count** → `min(n_pmids, 3)/3` factor (more independent
  PMIDs ⇒ stronger).
- **Retraction flag** → heavy penalty if any cited PMID is retracted.
- (Optional, if trivially available) supporting-vs-contradicting edge balance.

The formula is intentionally simple, transparent, and reproducible — it is a
proxy for *evidence robustness*, not a hypothesis test. When a field is missing
from a node, a documented conservative default is used.

## 6. Ledger extension & resumability

`new_ledger()` gains two additive keys:

```json
"aims_loop":  {"status": "pending", "rounds": []},
"draft_loop": {"status": "pending", "rounds": []}
```

Loop `status` ∈ `pending | in_progress | accepted | stopped`. Each round:

```json
{
  "round": 0,
  "verdicts": { "<dimension>": {"verdict": "sound|weak|contradicted|unclear",
                                "score": 0.0, "rationale": "…",
                                "citations": ["<node_id or PMID>", …]} },
  "weaknesses": [ {"issue": "…", "dimension": "…", "fix": "…",
                   "closable_by_analysis": false} ],
  "proposed_revision": "…",
  "decision": null,
  "note": null
}
```

- **Additive & backward-compatible:** `schema_version` stays `1`; the two keys
  are optional in `pi_run_schema.json`; readers use `.get(..., default)` so
  ledgers written by the base module still load. Existing output folders remain
  resumable.
- **Loop state is ledger-driven** (decisions aren't disk-derivable), so
  `reconcile()` leaves `aims_loop`/`draft_loop` untouched. `status` reflects
  exactly where each loop paused, so a fresh session resumes mid-loop at the
  correct round.

## 7. Files touched

**New:**
- `src/nono_pi/cli/eval.py` — `record` + `decide` subactions.
- `src/nono_pi/cli/evidence_score.py` — the scoring command.
- `src/nono_pi/data/schemas/eval_round_schema.json` — validates a round input.
- `src/nono_pi/data/templates/evaluation_report.md` — rendered `<loop>_evaluation.md`.
- `tests/unit/test_eval.py`, `tests/unit/test_evidence_score.py`.

**Extend:**
- `src/nono_pi/lib/ledger.py` — `new_ledger` (+ loop keys); `reconcile` unchanged
  but tolerant of missing keys.
- `src/nono_pi/data/schemas/pi_run_schema.json` — optional `aims_loop`/`draft_loop`.
- `src/nono_pi/cli/status.py` — loops section (tolerant `.get`).
- `src/nono_pi/cli/__main__.py` — `COMMANDS += eval, evidence-score`.
- `.claude/skills/nono-pi/SKILL.md` — rework Step 5 into the aims loop; add
  Step 8.5 draft loop; document create/revise seeding, the review-then-stop path,
  and the new commands (keeps the command-sync guard test green).
- `tests/unit/test_status_mark.py` / `test_ledger.py` — extend for loop state.

## 8. Testing

Pytest over the deterministic parts only, mirroring the base module: round
recording (numbering, status transitions, report render), decision recording
(status transitions for approved/accepted/stopped), ledger loop-state defaults +
backward-compat load of a base-module ledger, evidence-score computation from a
fixture KG (tier/sources/retraction factors, missing-field defaults), and
status loop rendering. The evaluation/revision **reasoning is the agent's and is
not unit-tested** (same stance as the rest of `nono-pi`). Halt-for-user each
round is the runaway-iteration safety valve; `stopped` ends a loop cleanly and
resumably.

## 9. Scope (be honest)

- **In scope:** the two halt-each-round loops (agent-driven), `eval
  record`/`decide` bookkeeping + rendered evaluation reports, the deterministic
  evidence-strength score, ledger loop-state + resumability, mode-agnostic draft
  loop unified with the revise write step, aims loop evaluating the *existing*
  premise in revise mode, and review-as-evaluate-then-stop.
- **Out of scope / unchanged:** data-level statistical validation (remains a
  `nono-analyst` task via `analysis_plan.md`; `nono-analyst` is still not built);
  automatic convergence/threshold stopping (deferred — stopping is
  halt-for-user by design); KG building, routing tables, and intake are unchanged
  from the base module.
