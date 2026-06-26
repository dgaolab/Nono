# Design — Agent-driven nono-librarian rewrite

**Date:** 2026-06-26
**Status:** Approved in shape (pending written-spec review)
**Repo:** dgaolab/Nono — librarian toolkit

## 1. Goal

Rewrite the `nono-librarian` skill + toolkit so that:

1. **Whichever agent is currently running** (Claude in Claude Code, or a local
   Hermes agent) performs the end-to-end KG curation reasoning itself. There is
   **no separately-served local model** to stand up, point at, or discover.
2. Python, the packaged toolkit, and its dependencies install into a **uv venv**
   that is part of a shared `~/.nono` research-assistant home, instead of a
   conda env named `nono`.
3. `SKILL.md` is rewritten to match (1) and (2).

The current toolkit was built on the opposite premise: a small open-weight model
served by vLLM did the in-loop reasoning, reached through one HTTP seam
(`scripts/lib/llm.py`) configured by `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`.
This rewrite removes that premise.

## 2. What makes this tractable

All model reasoning is already isolated behind a single injected `chat` callable,
defined in exactly one place (`scripts/lib/llm.py`). Every other piece — article
selection, node rendering, manifest assembly, the verbatim-quote guardrail, and
the entire post-build "finish" pipeline — is already deterministic, model-free
Python. So the rewrite is: **delete the one seam, lift the reasoning out into the
agent, and keep all the deterministic machinery**, repackaged.

## 3. Environment layout (replaces the conda env)

`~/.nono` becomes the research-assistant **home** — a normal directory, *not*
itself a venv. nono-librarian is one module under it; future modules are
siblings sharing one venv.

```
~/.nono/                       # NONO_HOME — normal directory (the assistant home)
├── .venv/                     # ONE shared uv venv (disposable; no source lives here)
├── librarian/                 # this repo (the nono-librarian module)
│   ├── pyproject.toml
│   ├── src/nono_librarian/    # the importable package (installed editable into ../.venv)
│   ├── tests/
│   └── ...
└── <future-module>/           # later modules, siblings, install into the SAME .venv
```

**Rules**

- The venv (`~/.nono/.venv`) is disposable and reproducible; **no source code is
  ever nested inside it**.
- The toolkit is installed **editable** (`uv pip install -e`) so dev edits are live.
- `NONO_HOME` env var anchors everything; default `~/.nono`. SKILL.md always
  invokes the installed CLI at `$NONO_HOME/.venv/bin/nono`, so it works from any
  working directory and on any machine after one bootstrap.

**Bootstrap (idempotent, run by SKILL.md Step 1 and on a new machine):**

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
# 1. shared venv
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
# 2. toolkit source present at $NONO_HOME/librarian (clone on a fresh machine)
test -d "$NONO_HOME/librarian" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/librarian"
# 3. install the toolkit (editable) + deps into the shared venv
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
# 4. make the skill globally available via a symlink to the repo (single source of truth)
mkdir -p "$HOME/.claude/skills"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$HOME/.claude/skills/nono-librarian"
```

Python 3.14 is pinned because it is the newest the embedding runtime
(`onnxruntime`, via `fastembed`) ships wheels for; bump only when a newer cpXX
wheel exists. `.gitignore` gains `.venv/` (and `.nono/` is irrelevant now since
the venv is outside the repo, but we keep ignoring stray local venvs).

## 4. Packaging

Add `pyproject.toml` defining the distribution `nono-librarian` with package
`nono_librarian` in a `src/` layout. Dependencies move from `requirements.txt`
into `pyproject.toml` (`fastembed`, `PyYAML`, `jsonschema`; `pytest` as a dev
extra). **`requirements.txt` is removed** — `pyproject.toml` is the single
dependency source, and bootstrap installs via `uv pip install -e .`.

**Module restructure (file moves, imports updated):**

| Today | After |
|-------|-------|
| `scripts/lib/*.py` | `src/nono_librarian/lib/*.py` |
| `scripts/*.py` (CLIs) | `src/nono_librarian/cli/*.py` |
| `templates/`, `schemas/` | `src/nono_librarian/data/` (packaged resources) |

The `sys.path.insert(...); from lib import X` hack is replaced by proper package
imports (`from nono_librarian.lib import build`). Templates/schemas are loaded
via `importlib.resources` so they resolve regardless of CWD.

**One console entry point — `nono` — with subcommands** (a single argparse
dispatcher), future-proof for the larger assistant:

- Curation: `nono gather`, `nono assemble`, `nono finalize`, `nono verify`
- Query/maintenance: `nono search`, `nono lint`, `nono retractions`,
  `nono chase`, `nono ledger`, `nono digest`, `nono embeddings`, `nono index`,
  `nono cross-index`, `nono preflight`, `nono cost-report`
- Internal helpers that `finalize` chains (classify-evidence-tier,
  stamp-literature, enforce-quarantine, update-manifest-stats, validate-manifest,
  generate-index, append-log, parse-node, update-frontmatter, merge-eval-chunks)
  remain as modules; `finalize` calls them in-process or via
  `python -m nono_librarian.cli.<x>`. They need not all be public subcommands.

## 5. Reasoning seam — removed

- **Delete** `scripts/lib/llm.py` and `tests/unit/test_llm_lib.py`.
- **`lib/build.py`** — drop the `chat`-calling functions (`_ask_json`,
  `plan_search`, `propose_skeleton`, `synthesize_node`, `propose_relationships`,
  `gap_fill_queries`). **Keep** all deterministic helpers: `TIERS`,
  `select_candidates`, `slugify`, `assign_ids`, `render_node_markdown`,
  `apply_relationships`, `assemble_manifest`, `weak_spots`, `_shared_pmid_edges`,
  `BuildError`.
- **`lib/evaluate.py`** — drop `build_prompt`, `verify_pmid`, and the
  `llm.extract_json_object` import. **Keep** the deterministic guardrail:
  `parse_response` (adapted to accept an already-parsed dict instead of model
  text), `quote_present`, `apply_guardrail`, `node_verdict`, `_normalize`,
  `EvaluationError`.
- **Delete** the model-orchestrator entrypoints `scripts/librarian_build.py` and
  the `chat`-driven body of `scripts/librarian_evaluate.py`; their deterministic
  pieces (`build_run_record`, `_persist_and_classify`, `_finalize_kg`,
  `gather_articles`, `_evaluate_and_writeback` minus the model call,
  `frontmatter_updates`, `build_source_text`) move into the new CLIs below.

The agent itself is now the reasoner. SKILL.md tells it to do the narrow steps
(search planning, node skeletons, per-node synthesis, relationships, claim
verdicts) directly — the same steps the Claude `/build-kg` and `/evaluate-kg`
skills already describe, but harness-agnostic and over E-utilities rather than
the Claude PubMed MCP.

## 6. Curation flow — agent reasons, deterministic CLIs do the mechanics

### Build / update

1. **`nono gather "<topic>" --query Q1 --query Q2 … [--since YYYY-MM-DD] [--breadth narrow|medium|broad] [--out _candidates.json]`**
   Mechanical retrieval over `lib/pubmed.py` (`esearch` → `fetch_metadata` →
   `fetch_full_text`) + `select_candidates`. Writes `_candidates.json`
   (PMID, title, abstract, optional full text, metadata). The agent supplies the
   sub-queries it planned; tiers come from `breadth`. No model call.

2. *(agent step)* The agent reads `_candidates.json`, designs the graph
   (skeleton → per-node synthesis → relationships → per-claim candidate quotes),
   and emits **`_nodes.json`**, validated against the new
   `schemas/nodes_input_schema.json` (§7).

3. **`nono assemble <KG> --nodes _nodes.json --topic "<topic>" --breadth <b> [--start-id N]`**
   Deterministic: `assign_ids` → `apply_relationships` → `render_node_markdown` →
   `assemble_manifest`; writes node markdown files + `manifest.json`. `--start-id`
   (from `next_node_number`) makes this serve UPDATE by appending.

4. **`nono finalize <KG>`** — the one finish command (requirement: "keep as one
   finalize command"). Chains, in order:
   ledger batch-add → classify evidence tiers → stamp literature →
   **quote guardrail + eval writeback** (fetch each cited source, drop quotes
   not present verbatim via `quote_present`/`apply_guardrail`, force unsupported
   claims to `not_supported`, recompute `node_verdict` + quarantine, write back
   node frontmatter) → enforce quarantine → generate index → update manifest
   stats → validate manifest → build embeddings (non-fatal) → write run-record →
   render digest (non-fatal) → append log.

UPDATE is the same scripts: `gather --since`, agent emits new `_nodes.json`,
`assemble --start-id`, `finalize`. (The current code only partially wired the
finish into UPDATE; routing both modes through the same `assemble`+`finalize`
closes that gap.)

### Evaluate an existing KG

The standalone counterpart to the Claude `/evaluate-kg` command:

1. *(agent step)* The agent reads node claims, reads each cited source,
   judges support, and emits **`_verdicts.json`** (`{node_id: {pmid: {verdict,
   quotes[]}}}`).
2. **`nono verify <KG> [--nodes id1,id2] [--verdicts _verdicts.json]`** — applies
   the **same deterministic guardrail + writeback** used inside `finalize`
   (shared code path), updating node frontmatter, quarantine, and manifest stats.

The guarantee that **a claim can never pass without a verbatim quote** stays in
Python and is independent of which agent reasoned.

## 7. New schema — `schemas/nodes_input_schema.json`

Validates the agent's `_nodes.json` hand-off so `assemble`/`finalize` never
ingest malformed reasoning output. Minimum per node: `title`, `summary`, `body`,
`pubmed_ids` (each: `pmid`, `supports` claim text, optional candidate `quotes`
with `source` ∈ {abstract, full_text}), and relationship hints
(`related_nodes` / `relationships`). Aligns with what `render_node_markdown` and
`apply_relationships` already consume. A companion (lighter) shape covers
`_verdicts.json` for `verify`.

## 8. SKILL.md rewrite

- **Frontmatter description** — drop "WITHOUT Claude / on a local open-weight
  model via an OpenAI-compatible endpoint" and the conda guarantee; replace with:
  the front door to the libririan toolkit that **whichever agent is running
  (Claude or Hermes) drives end-to-end**, using a shared `~/.nono` uv venv; no
  model to set up or find.
- **Step 1 — Guarantee the environment**: the `NONO_HOME` / `~/.nono/.venv`
  bootstrap from §3; invoke everything as `$NONO_HOME/.venv/bin/nono <cmd>`.
- **Step 2 — You are the reasoner**: state plainly that the running agent does the
  curation reasoning; there is no model discovery, no `LLM_*` env, no
  `LLMUnavailable` fallback. Remove the entire old "scheduling agent supplies the
  model" / vLLM / endpoint table section.
- **Step 3 — Dispatch by intent**: query (`nono search`, agent writes the
  natural-language answer itself), maintenance (the `nono` maintenance
  subcommands), evaluate (agent judges → `nono verify`), build/update
  (`nono gather` → agent `_nodes.json` → `nono assemble` → `nono finalize`).
- **Scope** — update honestly: still PubMed-only; ClinicalTrials.gov, ChEMBL,
  entity-ID normalization, and `--source` materials remain unimplemented. Quality
  now tracks **whichever agent runs it** rather than a fixed local model.

**Single source of truth:** the repo's `.claude/skills/nono-librarian/`. The
stale global copy at `~/.claude/skills/nono-librarian/` is **replaced by a
symlink** to the repo's skill directory
(`ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" ~/.claude/skills/nono-librarian`),
so the globally-available skill and the repo never drift. This symlink step is
part of the §3 bootstrap.

## 9. Tests

- **Remove:** `test_llm_lib.py`; the `chat`-injection tests in `test_build_lib.py`,
  `test_evaluate_lib.py`, `test_librarian_build.py`, `test_librarian_evaluate.py`.
- **Keep/port:** all deterministic-helper tests (selection, rendering, manifest
  assembly, guardrail/`quote_present`/`node_verdict`, run-record, digest, ledger,
  embeddings, search), updated to the new package import paths.
- **Add:** unit tests for `nono gather` (fixture E-utilities `_opener`),
  `nono assemble` (`_nodes.json` → files/manifest), `nono finalize` (end-to-end
  over a temp KG with fake PubMed), `nono verify` (guardrail forces-down a
  non-verbatim quote), and `nodes_input_schema.json` validation.
- The whole suite must run with **no network and no model** — fixtures only.

## 10. Migration / ops notes

- The current working clone lives at `~/nono/librarian` (no dot). The new home is
  `~/.nono/librarian`. Relocating (or re-cloning) is an ops step; **no code may
  hardcode an absolute path** — everything resolves via `NONO_HOME` or the
  installed console script, so the toolkit works wherever it is checked out.
- `_cost_log.jsonl`, `_embeddings.json` handling is unchanged.
- No data migration for existing KGs: their on-disk format (manifest + nodes) is
  unchanged; only the tooling that produces/maintains them changes.

## 11. Out of scope (YAGNI)

- No new article sources (ClinicalTrials.gov, ChEMBL, `--source`).
- No entity-ID normalization.
- No re-introduction of any served-model / OpenAI-endpoint path.
- No build of the broader research assistant — only the `~/.nono` layout it will
  live in.

## 12. Net change summary

- **Deleted:** `lib/llm.py`, `librarian_build.py`, model body of
  `librarian_evaluate.py`, `test_llm_lib.py`, model-call tests.
- **Slimmed:** `lib/build.py`, `lib/evaluate.py` (deterministic parts only).
- **Added:** `pyproject.toml`, `src/` package layout + `nono` CLI dispatcher,
  `cli/gather.py`, `cli/assemble.py`, `cli/finalize.py`, `cli/verify.py`,
  `schemas/nodes_input_schema.json`, new tests.
- **Rewritten:** `SKILL.md` (repo copy; global location symlinked to it).
- **Removed:** `requirements.txt` (deps now in `pyproject.toml`).
