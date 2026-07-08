---
name: nono-pi
description: >-
  Front door for nono-pi, the harness-agnostic PI orchestrator. Whichever agent
  is running (Claude, another frontier model, or a local agent) does ALL the
  reasoning itself — subtopic decomposition, gap analysis, Significance &
  Innovation synthesis, and driving the writing skills. Use this whenever the
  user wants to turn a research goal (grant or paper) into KGs + a Significance &
  Innovation doc + a drafted/revised deliverable, on their own machine. The
  toolkit installs into the shared ~/.nono uv venv and is invoked as
  `$NONO_HOME/.venv/bin/nono-pi <command>`.
---

# nono-pi

The harness-agnostic front door to the PI orchestrator. Two jobs: **(1) guarantee
the shared `~/.nono` environment**, and **(2) run the PI program** — while **you,
the running agent, do all the reasoning**. The `nono-pi` CLI does only
deterministic work (folder scaffolding, intake, KG-build bookkeeping, doc
assembly, ledger reconciliation). No served model, no LLM calls in the CLI.

## Step 0 — Guarantee the environment (always first)

`~/.nono` is the shared research-assistant home. It holds ONE uv venv at
`~/.nono/.venv`; `nono-pi` is a module at `~/.nono/pi`, installed editable into
that venv (a sibling of `nono-librarian`).

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
test -d "$NONO_HOME/Nono" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/Nono"
ln -sfn "$NONO_HOME/Nono/pi" "$NONO_HOME/pi"
ln -sfn "$NONO_HOME/Nono/librarian" "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/pi" -e "$NONO_HOME/librarian"
```

Let `P="$NONO_HOME/.venv/bin/nono-pi"` and `N="$NONO_HOME/.venv/bin/nono-librarian"`.

## Step 0.5 — Resume or start

If the user points at an existing output folder, run `nono-pi status <out>`
first. It reconciles the ledger against disk and prints what is done vs pending.
**Skip completed work** (built KGs, an existing `Significance_and_Innovation.md`,
already-written sections) and resume at the first pending step. The user may
also expand the request (e.g. add sections) on resume — run `nono-pi route`
again with the new set and write only the newly-requested sections.

## Step 1 — Intake

Ask the user for: (a) the output folder, (b) a brief goal description, (c) any
additional input files (preliminary data, notes, prior papers), and (d) whether
they are providing an existing draft of the deliverable to improve.

```bash
nono-pi init <out>
```

Classify each provided file as *supporting material* vs *an existing deliverable
draft*. Auto-detect the run **mode** (`create` if no draft, `revise` if a draft
is present) and the **document type** (`grant` vs `paper`), then **ask the user
to confirm both**. Record intake (this copies supporting files into
`<out>/input/`, and in revise mode seeds the immutable baseline
`<out>/draft/v000.<ext>`):

```bash
nono-pi intake <out> --goal "<goal>" --doc-type <grant|paper> --mode <create|revise> \
  --file <path> [--file <path> ...] [--draft <path>]
```

## Step 2 — Deliverable depth

Ask which sections / what depth to produce, then record the choice and get the
skill plan back:

```bash
nono-pi route <out> --full          # whole document
nono-pi route <out> --sections specific_aims,approach   # a chosen subset
```

## Step 3 — Decompose subtopics

Reason out the subtopics underlying the goal yourself and write a
`subtopics.json` (`{"topic": ..., "subtopics": [{"title": ...}]}`). Then record
the KG build plan (subtopics + the overall topic):

```bash
nono-pi orchestrate-kg plan <out> --subtopics <out>/subtopics.json
```

## Step 4 — Build the KGs

For **each** planned KG (every subtopic and `_overall`), drive `nono-librarian`
end-to-end into that KG's folder under `<out>/kgs/<slug>/`, following the
`nono-librarian` skill (plan → gather → reason nodes → assemble → finalize). You
do the reasoning; the librarian CLI does the deterministic work. After each,
record the outcome:

```bash
nono-pi mark <out> --kg <slug> --kg-status built     # or failed
```

`nono-pi status <out>` also detects built KGs from disk (presence of
`manifest.json`), so a resumed run knows what remains.

## Step 5 — Logic / gap gate

Read the KGs and check the user's goal for logic mistakes or gaps, grounded in
KG evidence. Write your findings to `<out>/gaps_report.md`.

- **If a gap is closable by further analysis**, also emit a machine-readable
  plan for the future `nono-analyst` module:
  ```bash
  nono-pi analysis-plan <out> --input <analysis_input.json>
  ```
- **If gaps exist, HALT.** Present them and the suggested analysis plan, mark the
  gate, and wait for the user to revise the goal or explicitly confirm/override:
  ```bash
  nono-pi mark <out> --gate gaps        # gaps found; awaiting the user
  nono-pi mark <out> --gate confirmed   # user chose to proceed
  ```
- **If no blocking gaps**, mark the gate clear and continue:
  ```bash
  nono-pi mark <out> --gate clear
  ```

## Step 6 — Significance & Innovation

Establish Significance & Innovation from the goal, the input, and the KG
evidence. In `revise` mode, instead **evaluate the existing draft's** S&I claims
against the KG evidence (flag unsupported / overstated / already-published
claims). Write a `si_input.json` and render the doc:

```bash
nono-pi assemble-si <out> --input <si_input.json>
```

## Step 7 — Write or revise

Use the routing plan from Step 2. For each requested section, follow the mapped
skill(s) by reading their skill markdown, and:

- **create mode:** write the section to `<out>/draft/<section_key>.md`, then
  `nono-pi mark <out> --section <key> --section-status written`.
- **revise mode:** improve the current version into a new whole-document version
  `<out>/draft/v<NNN>.md` (never touch `v000`), addressing gap-gate findings and
  unsupported S&I claims while preserving strengths, then
  `nono-pi mark <out> --bump-draft`.

For an NSFC grant, also follow the `nsfc-grant-writer` skill in addition to the
routed grant skills.

Finish by printing `nono-pi status <out>` so the user sees the completed state.

## Scope (be honest)

- **In scope:** intake (incl. ingesting an existing draft), subtopic
  decomposition, KG orchestration via `nono-librarian`, the logic-gap gate +
  `nono-analyst` plan emission, Significance & Innovation authoring,
  depth-selected grant/paper drafting/revision, versioned drafts, and full
  resumability from the output folder.
- **Placeholder:** `nono-analyst` (consumes `analysis_plan.md`) is not built yet
  — `nono-pi` only emits the plan file.
