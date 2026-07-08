# Design — nono-pi (agent-driven PI orchestrator)

**Date:** 2026-07-08
**Status:** Approved in shape (pending written-spec review)
**Repo:** dgaolab/Nono — pi module (new, sibling of `librarian/`)

## 1. Goal

Build a new Nono module, `nono-pi`, that plays the role of a **principal
investigator / orchestrator**. Given a research goal and supporting material, it:

1. Collects input from the user: an **output folder**, a **brief goal
   description**, and **additional input files** (including preliminary data).
2. Decides whether the request is to write a **grant** or a **scientific
   paper** (auto-detect, then confirm with the user).
3. Decomposes the goal into **subtopics** and drives `nono-librarian` to build a
   knowledge graph (KG) for each subtopic **and** for the overall topic.
4. Inspects the KGs for **logic mistakes / gaps** in the goal. If it finds any,
   it points them out and **halts for user confirmation**; where a gap is
   closable by further analysis, it also emits a brief further-analysis plan.
5. Otherwise establishes **Significance & Innovation** from the goal, the
   additional input, and the KG evidence, and saves it as a markdown file.
6. Uses the **corresponding writing skills** to draft the grant or paper, at a
   **depth the user chooses per run** (specific sections or a full draft).

If the user supplies an **existing draft** of the deliverable, `nono-pi` runs in
**revise mode**: it digests the current version and improves it against the KG
evidence rather than writing from scratch. If no draft is supplied, it runs in
**create mode**. The mode is auto-detected and confirmed with the user, never
guessed silently.

`nono-pi` MUST be **model-agnostic** — runnable by Claude, another frontier
model, or a locally-hosted agent — built the **same way as `nono-librarian`**.

## 2. Architecture — mirror nono-librarian

`nono-pi` reuses the librarian's proven split:

- A harness-agnostic **`SKILL.md` program** (the front door) that the **running
  agent** reads and executes, performing *all* reasoning itself: subtopic
  decomposition, gap analysis, Significance & Innovation synthesis, and driving
  the writing skills.
- A **thin, deterministic `nono-pi` Python CLI** that does only mechanical work
  (folder scaffolding, input intake, orchestrating the librarian, assembling
  output docs, and tracking/reconciling progress). **No LLM calls, no served
  model, no provider SDK** live in the CLI.

Scientific skills are referenced **by file path** so any file-reading agent can
follow their markdown, exactly as the librarian references deterministic
subcommands. This is what keeps `nono-pi` model-agnostic.

## 3. Environment layout

`nono-pi` is a sibling module under the shared `~/.nono` home and installs into
the **same** `~/.nono/.venv` as the librarian.

```
~/.nono/                       # NONO_HOME — assistant home (normal directory)
├── .venv/                     # ONE shared uv venv
├── librarian/                 # existing nono-librarian module
├── pi/                        # NEW — the nono-pi module (symlink to Nono/pi)
└── Nono/                      # cloned repo; pi/ and librarian/ are subdirs
```

Bootstrap adds, alongside the librarian steps:

```bash
ln -sfn "$NONO_HOME/Nono/pi" "$NONO_HOME/pi"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/pi"
```

Invoked as `"$NONO_HOME/.venv/bin/nono-pi" <command> …`. Python 3.14, matching
the librarian.

## 4. Package layout

```
pi/
  pyproject.toml                     # name = nono-pi; script: nono-pi = nono_pi.cli.__main__:main
                                     # dev extra: pytest>=8.0 ; testpaths = ["tests"]
  .claude/skills/nono-pi/SKILL.md    # the agent-driven "program" (front door)
  src/nono_pi/
    __init__.py
    paths.py
    cli/
      __main__.py                    # dispatch
      init.py                        # scaffold output folder skeleton
      intake.py                      # record goal + doc-type + depth; copy user files in
      orchestrate_kg.py              # loop subtopics + overall, drive nono-librarian per KG
      assemble_si.py                 # write Significance_and_Innovation.md from agent JSON
      analysis_plan.py               # write nono-analyst further-analysis plan md
      status.py                      # reconcile ledger vs disk; print done/pending
    lib/
      __init__.py
      ledger.py                      # read/write/reconcile pi_run.json
      routing.py                     # load + filter routing tables by depth
    data/
      templates/                     # significance_innovation, gaps_report, analysis_plan
      routing/                       # grant.json, paper.json (curated skill routing)
      schemas/                       # intake, subtopics, si_input, gaps_input schemas
  tests/
    unit/                            # pytest over deterministic CLI only
    fixtures/
  docs/superpowers/specs/            # this spec + future specs
```

## 5. Control flow (what SKILL.md tells the agent)

- **Step 0 — Guarantee env.** Same block as the librarian, plus the `pi/`
  symlink + editable install above.
- **Step 0.5 — Resume-or-start.** If invoked against an existing output folder,
  run `nono-pi status <out>` first (see §8) and resume; otherwise start fresh.
- **Step 1 — Intake.** Agent asks the user for: (a) output folder, (b) brief
  goal description, (c) additional input files incl. preliminary data, and
  optionally an existing draft of the deliverable. Deterministic: `nono-pi init
  <out>` scaffolds the folder; `nono-pi intake` records `intake.json` and copies
  the user's files into `<out>/input/` (originals preserved, untouched).
- **Step 1.5 — Mode.** Agent classifies each provided file as *supporting
  material* vs *an existing draft of the deliverable*, auto-detects the run
  **mode** (`create` if no draft, `revise` if a draft is present), and **asks
  the user to confirm**; recorded in `intake.json`. In `revise` mode the ingested
  draft is copied to `<out>/draft/v000.<ext>` as the immutable baseline.
- **Step 2 — Document type.** Agent auto-detects grant vs paper from the goal
  (or from the provided draft in `revise` mode), **asks the user to confirm**;
  recorded in `intake.json`.
- **Step 3 — Deliverable depth.** Agent asks which sections / what depth to
  write (e.g. Specific Aims only, Results only, or full draft); recorded.
- **Step 4 — Subtopic decomposition.** Agent reads goal + input and writes
  `subtopics.json`.
- **Step 5 — Build KGs.** For each subtopic **and** the overall topic, the agent
  drives `nono-librarian` end-to-end (plan → gather → reason nodes → assemble →
  finalize) — the reasoning steps (planning sub-queries, designing/synthesizing
  nodes) are the agent's, exactly as in the librarian's own program. `nono-pi
  orchestrate-kg` is the per-KG **driver/bookkeeper**: it resolves each KG's
  target folder under `<out>/kgs/<slug>/` (and `<out>/kgs/_overall/`), sequences
  the librarian invocations, and records per-KG status in the ledger. It does
  **not** perform any reasoning.
- **Step 6 — Logic / gap gate.** Agent inspects the KGs for logic mistakes /
  gaps in the goal, grounded in KG evidence.
  - **If gaps:** write `<out>/gaps_report.md`; if a gap is closable by further
    analysis, also write `<out>/analysis_plan.md` in a form the future
    **`nono-analyst`** module can consume (placeholder; not built yet). **Halt**,
    present the issues, and wait for the user to revise the goal or explicitly
    confirm/override. Record the decision in the ledger so the halt is not
    re-triggered on resume.
  - **Else / after user confirms:** continue.
- **Step 7 — Significance & Innovation.** Agent synthesizes S&I from goal +
  input + KG evidence; `nono-pi assemble-si` writes
  `<out>/Significance_and_Innovation.md`. In `revise` mode the agent instead
  *evaluates the existing draft's* significance/innovation claims against the KG
  evidence — flagging claims that are unsupported, overstated, or already
  published — rather than inventing fresh ones.
- **Step 8 — Write / revise.** Load the hard-coded routing table for the doc
  type, filter to the chosen depth/sections, and select the column for the mode:
  - **`create`:** the agent follows each section skill's markdown (by path) to
    write sections into `<out>/draft/` (`vNNN` files).
  - **`revise`:** the agent runs the revise-oriented skills — critique/plan then
    targeted edit — improving the baseline (`v000`) into a new version
    (`v001`, …), addressing gap-gate findings and unsupported S&I claims while
    preserving the draft's strengths. Only the chosen sections are touched.

## 6. Skill routing tables (hard-coded, `data/routing/`)

Each table maps **section → { create: skill, revise: skill(s) }**, so the Step 3
depth choice selects which entries run and the Step 1.5 mode selects the column.

- **grant.json**
  - *create* → `grant-specific-aims-writer`, `grant-proposal-assistant`,
    `research-proposal-generator` (+ `nsfc-grant-writer` when NSFC).
  - *revise* → `grant-mock-reviewer` → `revision-strategy-planner` →
    `grant-specific-aims-writer` (edit mode) / `grant-proposal-assistant`.
- **paper.json**
  - *create* → `introduction-section-writer`, `methods-section-writer`,
    `results-section-writer`, `discussion-composer`, `abstract-summarizer`
    (+ figure/reference helpers).
  - *revise* → `scientific-manuscript-review` / `sci-paper-reviewer` →
    `revision-strategy-planner` → the section writers in edit mode.

Tables are data, not code, so the curated set can be edited without touching the
CLI. `lib/routing.py` loads a table and returns the subset matching the requested
sections **and** the selected mode column.

## 7. Output folder layout (the durable memory)

```
<out>/
  intake.json                  # goal, doc type, requested sections/depth
  input/                       # copied user files (preliminary data, etc.)
  subtopics.json
  kgs/<subtopic-slug>/         # librarian KGs (manifest.json + nodes/)
  kgs/_overall/
  Significance_and_Innovation.md
  gaps_report.md               # only if gaps found
  analysis_plan.md             # only if further analysis suggested (nono-analyst input)
  draft/                       # versioned grant/paper sections/drafts (v000, v001, …)
                               #   revise mode: v000 = ingested original (immutable)
  pi_run.json                  # progress ledger (plan + state)
```

## 8. Resumability & continuation

**Principle:** the output folder *is* the durable memory, and **disk is the
source of truth**. Nothing required to continue lives in the session.

- **`pi_run.json` is a progress ledger** recording the plan and state: intake,
  confirmed doc type, **run mode (`create`/`revise`) and current draft version**,
  requested sections + chosen depth, subtopics, per-KG build status, gap-gate
  outcome (including the user's confirm/override decision), Significance &
  Innovation status, and **per-section draft status** (`requested` / `written`).
- **`nono-pi status <out>`** reconciles the ledger against what actually exists
  on disk — which `kgs/<slug>/` folders are built, whether
  `Significance_and_Innovation.md` exists, which files are present in `draft/` —
  and prints done vs pending. If the ledger is missing or stale, disk wins and
  the ledger is rebuilt from it, so any folder is resumable even if it was
  created by a different agent/session.
- **Continuation path** (e.g. Specific Aims today, remaining sections tomorrow):
  a fresh session runs `status`, sees KGs + S&I + `specific_aims.md` already
  done, and writes only the remaining `requested`-but-not-`written` sections
  from the same KG/S&I grounding. The user may **expand the request on resume**
  ("now also write Approach and Results"); `nono-pi` appends those to the ledger
  and writes just those, reusing the existing KGs and S&I.

Resume needs no new memory type — it reads the KGs, the S&I / gaps /
analysis-plan markdown, and the ledger already present in `<out>/`.

## 9. Error handling

- **Gap gate** halts as in §5 Step 6; the recorded decision prevents re-halting.
- **Per-subtopic librarian failures** are isolated: a failed KG is logged in the
  ledger and does not abort the other subtopics; the run can be resumed to retry
  just the failed KGs.
- **Missing/invalid input** (nonexistent output folder parent, unreadable input
  files) fails fast in `nono-pi intake` with a clear message.

## 10. Testing

Mirror the librarian: **pytest unit tests over the deterministic CLI only** —
`init`, `intake`, `assemble-si`, routing selection, `analysis-plan` emission,
and `status`/ledger reconciliation — using fixtures and schema validation.
Agent-reasoning steps (decomposition, gap analysis, S&I synthesis, writing) are
**not** unit-tested, the same stance as the librarian; the deterministic
scaffolding around them is fully covered.

## 11. Scope (be honest)

- **In scope:** intake (incl. ingesting an existing draft), subtopic
  decomposition, KG orchestration via the librarian, the logic-gap gate +
  further-analysis-plan emission, Significance & Innovation authoring,
  depth-selected grant/paper drafting via a curated skill table in both `create`
  and `revise` modes, versioned drafts, and full resumability from the output
  folder.
- **Out of scope / placeholder:** `nono-analyst` (consumes `analysis_plan.md`)
  is **not built** — `nono-pi` only emits the plan file. KG quality is bounded by
  `nono-librarian` and by whichever agent runs the program.
