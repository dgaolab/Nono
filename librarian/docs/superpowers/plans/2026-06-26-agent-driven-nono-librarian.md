# Agent-driven nono-librarian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite nono-librarian so the running agent (Claude or Hermes) performs all KG-curation reasoning, remove the served-local-model seam, and ship the toolkit as a pip-installable package living in a shared `~/.nono` uv venv.

**Architecture:** Reasoning leaves Python and moves into the agent (driven by SKILL.md). The toolkit becomes the `nono_librarian` package (src layout) exposing one `nono-librarian` console script (its own binary — the bare `nono` name is reserved for a future umbrella dispatcher across modules). Deterministic mechanics stay: retrieval (`nono gather`), structural assembly (`nono assemble`), the whole finish pipeline (`nono finalize`), and the verbatim-quote guardrail (`nono verify`). The agent hands structured JSON (`_nodes.json`, verdicts) to these CLIs.

**Tech Stack:** Python 3.14, uv, fastembed/onnxruntime, PyYAML, jsonschema, pytest. NCBI E-utilities (stdlib urllib). No model SDKs.

## Global Constraints

- Python pinned to **3.14** (newest with `onnxruntime` wheels). Bump only when a newer cpXX wheel exists.
- **No model/LLM code anywhere** after this plan — no `LLM_*` env, no OpenAI/Anthropic calls, no `lib/llm.py`.
- **No absolute paths hardcoded** — everything resolves via `NONO_HOME` (default `~/.nono`) or the installed console script / package resources.
- Dependencies live **only** in `pyproject.toml`. `requirements.txt` is removed.
- Single source of truth for the skill: the repo's `.claude/skills/nono-librarian/`; the global location is a symlink to it.
- The verbatim-quote guardrail is mandatory and deterministic: a claim can never be marked `verified`/supporting without a quote present verbatim in its source.
- The full test suite must run with **no network and no model** (fixtures/fakes only).
- Each task ends green; commit at the end of each task.
- Package import root is `nono_librarian`; lib modules at `nono_librarian.lib.*`, CLIs at `nono_librarian.cli.*`, packaged data at `nono_librarian.data`.

---

## Phase 1 — Package the toolkit (no behavior change)

### Task 1: Convert the repo to an installable `nono_librarian` package

Make the existing code an installed, importable package with a `nono-librarian` console entry point. **No behavior changes** — the goal is a green suite under the new layout. (`lib/llm.py` and the model orchestrators are kept here and removed in Phase 2.)

**Files:**
- Create: `pyproject.toml`
- Create: `src/nono_librarian/__init__.py`, `src/nono_librarian/cli/__init__.py`
- Move: `scripts/lib/*.py` → `src/nono_librarian/lib/`
- Move: `scripts/*.py` (all CLIs) → `src/nono_librarian/cli/`
- Move: `templates/`, `schemas/` → `src/nono_librarian/data/templates/`, `src/nono_librarian/data/schemas/`
- Create: `src/nono_librarian/data/__init__.py`, `src/nono_librarian/paths.py`
- Create: `src/nono_librarian/cli/__main__.py` (the `nono` dispatcher)
- Modify: every moved module's imports; every test's imports
- Delete: `requirements.txt`, stray `__pycache__/`

**Interfaces:**
- Produces: package `nono_librarian` (editable-installed); console script `nono-librarian` → `nono_librarian.cli.__main__:main`; `nono_librarian.paths.data_file(*parts) -> pathlib.Path` and `nono_librarian.paths.schemas_dir() -> pathlib.Path`, `templates_dir() -> pathlib.Path`.
- Consumes: nothing (first task).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "nono-librarian"
version = "0.1.0"
description = "Agent-driven PubMed knowledge-graph toolkit (libririan)"
requires-python = ">=3.14"
dependencies = [
    "PyYAML>=6.0",
    "jsonschema>=4.0",
    "fastembed>=0.3",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
nono-librarian = "nono_librarian.cli.__main__:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"nono_librarian.data" = ["templates/*", "schemas/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Move the source tree with git**

```bash
cd "$(git rev-parse --show-toplevel)"
mkdir -p src/nono_librarian/data
git rm -r --cached scripts/lib/__pycache__ scripts/__pycache__ __pycache__ 2>/dev/null || true
rm -rf scripts/lib/__pycache__ scripts/__pycache__ __pycache__
git mv scripts/lib src/nono_librarian/lib
mkdir -p src/nono_librarian/cli
git mv scripts/*.py src/nono_librarian/cli/
git mv templates src/nono_librarian/data/templates
git mv schemas src/nono_librarian/data/schemas
rmdir scripts 2>/dev/null || true
git rm requirements.txt
: > src/nono_librarian/__init__.py
: > src/nono_librarian/cli/__init__.py
: > src/nono_librarian/data/__init__.py
git add src/nono_librarian/__init__.py src/nono_librarian/cli/__init__.py src/nono_librarian/data/__init__.py pyproject.toml
```

- [ ] **Step 3: Add the resource-path helper**

Create `src/nono_librarian/paths.py`:

```python
"""Locate packaged data (templates, schemas) regardless of CWD or install mode."""
from importlib import resources
import pathlib


def data_file(*parts):
    """Return a concrete filesystem Path to a file under nono_librarian/data."""
    root = resources.files("nono_librarian.data")
    p = root.joinpath(*parts)
    return pathlib.Path(str(p))


def schemas_dir():
    return data_file("schemas")


def templates_dir():
    return data_file("templates")
```

- [ ] **Step 4: Rewrite imports in moved modules**

```bash
cd "$(git rev-parse --show-toplevel)"
# package-internal imports
grep -rl 'from lib' src/nono_librarian | xargs sed -i \
  -e 's/^from lib\.frontmatter import/from nono_librarian.lib.frontmatter import/' \
  -e 's/^from lib import/from nono_librarian.lib import/' \
  -e 's/from lib\.frontmatter import/from nono_librarian.lib.frontmatter import/' \
  -e 's/from lib import/from nono_librarian.lib import/'
# drop the sys.path bootstrap hack lines (lib is now installed)
grep -rl 'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))' src/nono_librarian \
  | xargs sed -i '/sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))/d'
# cross-CLI module import in librarian_build / librarian_evaluate
grep -rl 'import librarian_evaluate as le' src/nono_librarian \
  | xargs sed -i 's/^import librarian_evaluate as le/from nono_librarian.cli import librarian_evaluate as le/'
```

Then manually verify each file still parses and that no `from lib` / bare `import <sibling>` remains:

```bash
grep -rn 'from lib\b\|^import librarian\|sys.path.insert' src/nono_librarian || echo "clean"
python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('src/nono_librarian/**/*.py', recursive=True)]" && echo "all parse"
```

- [ ] **Step 5: Convert sibling subprocess calls to `-m` module calls**

Find every place a CLI shells out to another script by path and switch to module form. The known sites are in `librarian_build.py` (`_run`, `_persist_and_classify`, `_finalize_kg`, `run_build`, `run_update`) and `librarian_evaluate.py` (`main`). Locate them all:

```bash
grep -rn 'scripts_dir\|os.path.join(.*\.py")\|sys.executable' src/nono_librarian/cli
```

For each `subprocess.run([sys.executable, os.path.join(scripts_dir, "X.py"), ...])` replace with `subprocess.run([sys.executable, "-m", "nono_librarian.cli.X", ...])` and delete now-unused `scripts_dir = os.path.dirname(...)` lines. Example transform in `librarian_evaluate.py`:

```python
# before
subprocess.run([sys.executable, os.path.join(scripts_dir, "update_frontmatter.py"),
                node_files[entry["node_id"]], "--updates-file", upd_path], check=True)
# after
subprocess.run([sys.executable, "-m", "nono_librarian.cli.update_frontmatter",
                node_files[entry["node_id"]], "--updates-file", upd_path], check=True)
```

Apply the same pattern to every match (`pmid_ledger`, `classify_evidence_tier`, `stamp_literature`, `enforce_quarantine`, `generate_index`, `update_manifest_stats`, `validate_manifest`, `build_embeddings`, `render_digest`, `append_log`). Re-grep to confirm none remain:

```bash
grep -rn 'os.path.join(scripts_dir' src/nono_librarian/cli || echo "clean"
```

- [ ] **Step 6: Point schema/template loaders at packaged data**

In `src/nono_librarian/cli/validate_manifest.py`, replace the walk-up `find-project-root` logic with the packaged default. Change the default schema resolution so that when no `--schema` is given it uses `nono_librarian.paths.data_file("schemas", "graph_schema.json")`:

```python
from nono_librarian.paths import data_file
# ...
DEFAULT_SCHEMA = data_file("schemas", "graph_schema.json")
# in main(): schema_path = args.schema or str(DEFAULT_SCHEMA)
```

In `src/nono_librarian/cli/generate_index.py`, load the index template via `data_file("templates", "index_template.md")` instead of any `__file__`/walk-up path. Grep for other template/schema lookups and route them through `nono_librarian.paths`:

```bash
grep -rn 'templates\|schemas\|graph_schema\|index_template\|node_template' src/nono_librarian/cli | grep -v data_file
```

- [ ] **Step 7: Add the `nono-librarian` dispatcher**

Create `src/nono_librarian/cli/__main__.py`. It maps a subcommand to the matching module's `main(argv)` and forwards the remaining args. Only modules whose `main` accepts an `argv` list are listed; all current CLIs already define `main(argv=None)`.

```python
"""`nono-librarian` — entry point dispatching to nono_librarian.cli.* subcommands."""
import importlib
import sys

# subcommand -> module under nono_librarian.cli
COMMANDS = {
    "search": "search_nodes",
    "lint": "linter_kg",
    "retractions": "check_retractions",
    "chase": "chase_citations",
    "ledger": "pmid_ledger",
    "digest": "render_digest",
    "embeddings": "build_embeddings",
    "index": "generate_index",
    "cross-index": "build_cross_indices",
    "preflight": "preflight",
    "cost-report": "cost_report",
    "evaluate": "librarian_evaluate",  # replaced by 'verify' in Phase 3
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: nono-librarian <command> [args]\n\ncommands:")
        for name in sorted(COMMANDS):
            print(f"  {name}")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"nono-librarian: unknown command {cmd!r}. Try 'nono-librarian --help'.", file=sys.stderr)
        return 2
    mod = importlib.import_module(f"nono_librarian.cli.{COMMANDS[cmd]}")
    return mod.main(rest)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Update test imports to the package**

In `tests/unit/`, replace the `sys.path.insert(... "scripts")` + `from lib import X` / `import <script>` pattern with package imports. Apply across the suite:

```bash
cd "$(git rev-parse --show-toplevel)"
grep -rl 'scripts' tests/unit | xargs sed -i \
  -e '/sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))/d' \
  -e 's/^from lib\./from nono_librarian.lib./' \
  -e 's/^from lib import/from nono_librarian.lib import/' \
  -e 's/^from lib\.frontmatter import/from nono_librarian.lib.frontmatter import/'
```

Then fix the remaining per-file specifics by hand:
- `test_librarian_build.py`: `import librarian_build as lb` → `from nono_librarian.cli import librarian_build as lb`; and the inner `sys.path.insert(..., "scripts"); from lib.frontmatter import parse` (lines ~296-297) → `from nono_librarian.lib.frontmatter import parse as parse_fm`.
- Any test importing a CLI module directly (`import search_nodes`, `import librarian_evaluate`, etc.) → `from nono_librarian.cli import <mod>`.
- Confirm none remain:

```bash
grep -rn 'from lib\b\|"scripts"\|^import librarian\|^import search_nodes' tests/unit || echo "clean"
```

- [ ] **Step 9: Create the venv, install editable, run the full suite**

```bash
cd "$(git rev-parse --show-toplevel)"
uv venv .venv-dev --python 3.14   # local dev venv; the ~/.nono/.venv bootstrap is Task 9
uv pip install --python .venv-dev -e ".[dev]"
.venv-dev/bin/python -m pytest -q
```

Expected: the **entire existing suite passes** (same tests as before, new import paths). If a test fails purely on a moved path/resource lookup, fix the lookup (route through `nono_librarian.paths`) — not the test's assertions.

- [ ] **Step 10: Ignore dev venvs; commit**

Add to `.gitignore`:
```
.venv-dev/
.venv/
```

```bash
git add -A
git commit -m "refactor: package toolkit as nono_librarian with 'nono' CLI (no behavior change)"
```

---

## Phase 2 — Remove the model seam

### Task 2: Delete `lib/llm.py` and the model-calling build/evaluate code

Strip every model call. Keep all deterministic helpers. Replace the model-driven evaluator with a deterministic `judge_node` that consumes the **agent's** verdict and enforces the guardrail.

**Files:**
- Delete: `src/nono_librarian/lib/llm.py`, `tests/unit/test_llm_lib.py`
- Modify: `src/nono_librarian/lib/build.py`, `src/nono_librarian/lib/evaluate.py`
- Modify: `tests/unit/test_build_lib.py`, `tests/unit/test_evaluate_lib.py`

**Interfaces:**
- Consumes: `apply_guardrail`, `node_verdict`, `quote_present` (unchanged, from `lib/evaluate.py`).
- Produces:
  - `lib/evaluate.py`: `parse_judgment(obj: dict) -> {"verdict","reasoning","quotes"}`; `judge_pmid(judgment: dict, *, source_text: str) -> guardrailed dict`; (kept) `apply_guardrail`, `quote_present`, `node_verdict`, `SUPPORTING`, `VERDICTS`, `EvaluationError`.
  - `lib/build.py`: (kept) `TIERS`, `select_candidates`, `slugify`, `assign_ids`, `render_node_markdown`, `apply_relationships`, `assemble_manifest`, `weak_spots`, `_shared_pmid_edges`, `RELATIONSHIPS`, `BuildError`. Removed: `_ask_json`, `plan_search`, `propose_skeleton`, `synthesize_node`, `propose_relationships`, `gap_fill_queries`, and `from nono_librarian.lib import llm`.

- [ ] **Step 1: Write failing tests for the deterministic evaluate core**

Replace the `chat`-based tests in `tests/unit/test_evaluate_lib.py` with these (keep the existing guardrail/`quote_present`/`node_verdict` tests; remove `verify_pmid`/`build_prompt` tests):

```python
from nono_librarian.lib import evaluate


def test_parse_judgment_accepts_agent_dict():
    j = evaluate.parse_judgment(
        {"verdict": "supported", "reasoning": "ok",
         "quotes": [{"text": "BRCA1 loss impairs repair", "source": "abstract"}]})
    assert j["verdict"] == "supported"
    assert j["quotes"][0]["source"] == "abstract"


def test_parse_judgment_rejects_unknown_verdict():
    import pytest
    with pytest.raises(evaluate.EvaluationError):
        evaluate.parse_judgment({"verdict": "definitely", "quotes": []})


def test_judge_pmid_forces_not_supported_without_verbatim_quote():
    src = "Cells with BRCA1 loss show impaired homologous recombination."
    out = evaluate.judge_pmid(
        {"verdict": "supported", "reasoning": "claimed",
         "quotes": [{"text": "totally fabricated sentence", "source": "abstract"}]},
        source_text=src)
    assert out["verdict"] == "not_supported"
    assert out["quotes"] == []
    assert out["guardrail_triggered"] is True


def test_judge_pmid_keeps_verbatim_quote():
    src = "Cells with BRCA1 loss show impaired homologous recombination."
    out = evaluate.judge_pmid(
        {"verdict": "supported", "reasoning": "ok",
         "quotes": [{"text": "impaired homologous recombination", "source": "abstract"}]},
        source_text=src)
    assert out["verdict"] == "supported"
    assert out["quotes"] and out["guardrail_triggered"] is False
```

- [ ] **Step 2: Run them — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_evaluate_lib.py -q`
Expected: FAIL (`parse_judgment` / `judge_pmid` undefined).

- [ ] **Step 3: Rewrite `lib/evaluate.py` deterministic core**

In `src/nono_librarian/lib/evaluate.py`: delete `build_prompt`, `verify_pmid`, the `_SYSTEM` constant, and the `from nono_librarian.lib import llm` import inside `parse_response`. Keep `_normalize`, `quote_present`, `apply_guardrail`, `node_verdict`, `VERDICTS`, `SUPPORTING`, `SOURCES`, `EvaluationError`. Replace `parse_response(text)` with `parse_judgment(obj)` and add `judge_pmid`:

```python
def parse_judgment(obj):
    """Validate an agent-supplied verdict dict into {verdict, reasoning, quotes}."""
    if not isinstance(obj, dict):
        raise EvaluationError("judgment was not an object")
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise EvaluationError(f"unknown verdict: {obj.get('verdict')!r}")
    quotes = obj.get("quotes") or []
    if not isinstance(quotes, list):
        quotes = []
    return {"verdict": verdict,
            "reasoning": str(obj.get("reasoning", "")).strip(),
            "quotes": quotes}


def judge_pmid(judgment, *, source_text):
    """Guardrail an agent judgment against the article text. No model call."""
    return apply_guardrail(parse_judgment(judgment), source_text)
```

- [ ] **Step 4: Run evaluate tests — expect pass**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_evaluate_lib.py -q`
Expected: PASS.

- [ ] **Step 5: Slim `lib/build.py`**

In `src/nono_librarian/lib/build.py`: delete `from nono_librarian.lib import llm`, `_ask_json`, `plan_search`, `propose_skeleton`, `synthesize_node`, `propose_relationships`, `gap_fill_queries`, and the now-orphaned system-prompt constants (`_PLAN_SYS`, `_SKELETON_SYS`, `_NODE_SYS`, `_REL_SYS`, `_GAP_SYS`, `_articles_blob`). Keep everything else. Confirm no `llm`/`chat` references remain:

```bash
grep -n 'llm\|chat\|_ask_json\|propose_\|synthesize_\|plan_search\|gap_fill' src/nono_librarian/lib/build.py || echo "clean"
```

- [ ] **Step 6: Trim `test_build_lib.py` to deterministic helpers**

Remove the tests covering the deleted `chat` functions (any test constructing a fake `chat`/calling `plan_search`, `propose_skeleton`, `synthesize_node`, `propose_relationships`, `gap_fill_queries`). Keep tests for `select_candidates`, `slugify`, `assign_ids`, `render_node_markdown`, `apply_relationships`, `assemble_manifest`, `weak_spots`, `_shared_pmid_edges`. Run:

Run: `.venv-dev/bin/python -m pytest tests/unit/test_build_lib.py -q`
Expected: PASS.

- [ ] **Step 7: Delete the model seam + its test**

```bash
git rm src/nono_librarian/lib/llm.py tests/unit/test_llm_lib.py
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: remove llm seam; deterministic agent-judgment guardrail"
```

### Task 3: Replace the model orchestrators with deterministic node evaluation

`librarian_build.py` and `librarian_evaluate.py` currently inject `chat`. Phase 3 introduces `gather`/`assemble`/`finalize`/`verify`. Here we delete `librarian_build.py` and convert `librarian_evaluate.py` into a deterministic module that judges from agent-supplied verdicts (no `chat`), so nothing imports `llm`.

**Files:**
- Delete: `src/nono_librarian/cli/librarian_build.py`, `tests/unit/test_librarian_build.py`
- Modify: `src/nono_librarian/cli/librarian_evaluate.py`, `tests/unit/test_librarian_evaluate.py`

**Interfaces:**
- Produces: `librarian_evaluate.judge_node(node_id, frontmatter, judgments_for_node, *, fetch_metadata, fetch_full_text) -> entry`; (kept) `frontmatter_updates(entry)`, `build_source_text(meta, full_text)`, `_node_files(kg_folder, only_ids)`. `judgments_for_node` is `{pmid: {"verdict","reasoning","quotes"}}`.
- Consumes: `lib.evaluate.judge_pmid`, `lib.evaluate.node_verdict`, `lib.pubmed.fetch_metadata/fetch_full_text`.

- [ ] **Step 1: Write the failing test for `judge_node`**

Replace `tests/unit/test_librarian_evaluate.py` model-call tests with a deterministic one (fakes for PubMed, agent verdict supplied):

```python
from nono_librarian.cli import librarian_evaluate as le


def _fake_meta(pmids):
    return {p: {"title": f"Article {p}", "abstract":
                "BRCA1 loss impairs homologous recombination repair.", "pmcid": None}
            for p in pmids}


def _fake_full_text(pmcid):
    return ""


def test_judge_node_passes_with_verbatim_quote():
    fm = {"title": "BRCA1 and HR",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR"}]}
    judg = {"111": {"verdict": "supported", "reasoning": "ok",
                    "quotes": [{"text": "impairs homologous recombination", "source": "abstract"}]}}
    entry = le.judge_node("node_001", fm, judg,
                          fetch_metadata=_fake_meta, fetch_full_text=_fake_full_text)
    assert entry["overall_status"] == "passed"
    assert entry["pmid_checks"][0]["verdict"] == "supported"


def test_judge_node_fails_when_quote_absent():
    fm = {"title": "BRCA1 and HR",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR"}]}
    judg = {"111": {"verdict": "supported", "reasoning": "claimed",
                    "quotes": [{"text": "fabricated", "source": "abstract"}]}}
    entry = le.judge_node("node_001", fm, judg,
                          fetch_metadata=_fake_meta, fetch_full_text=_fake_full_text)
    assert entry["overall_status"] == "failed"


def test_judge_node_marks_missing_pmid_unrelated():
    fm = {"title": "x", "pubmed_ids": [{"pmid": "999", "supports": "y"}]}
    entry = le.judge_node("node_001", fm, {"999": {"verdict": "supported", "quotes": []}},
                          fetch_metadata=lambda p: {}, fetch_full_text=_fake_full_text)
    assert entry["pmid_checks"][0]["verdict"] == "unrelated"
    assert entry["overall_status"] == "failed"
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_librarian_evaluate.py -q`
Expected: FAIL (`judge_node` undefined / old signature).

- [ ] **Step 3: Rewrite `librarian_evaluate.py`**

Replace `evaluate_node` (model) with `judge_node` (agent verdict). Drop `from nono_librarian.lib import ... llm` and `--attempts`. New `judge_node`:

```python
def judge_node(node_id, frontmatter, judgments_for_node, *,
               fetch_metadata, fetch_full_text):
    """Apply agent verdicts to one node's claims, guardrailed. No model call."""
    entries = frontmatter.get("pubmed_ids", []) or []
    pmids = [e["pmid"] for e in entries if e.get("pmid")]
    meta_map = fetch_metadata(pmids) if pmids else {}
    checks = []
    for e in entries:
        pmid = e.get("pmid")
        meta = meta_map.get(pmid)
        if not meta:
            checks.append({"pmid": pmid, "exists": False, "article_title": "",
                           "verdict": "unrelated",
                           "reasoning": "PMID not found in PubMed.", "quotes": []})
            continue
        full_text = ""
        if meta.get("pmcid"):
            try:
                full_text = fetch_full_text(meta["pmcid"])
            except pubmed.PubMedUnavailable:
                full_text = ""
        source_text = build_source_text(meta, full_text)
        judgment = judgments_for_node.get(pmid, {"verdict": "unrelated", "quotes": []})
        result = evaluate.judge_pmid(judgment, source_text=source_text)
        checks.append({"pmid": pmid, "exists": True, "article_title": meta["title"],
                       "verdict": result["verdict"],
                       "reasoning": result.get("reasoning", ""),
                       "quotes": result["quotes"]})
    status, note = evaluate.node_verdict(checks)
    return {"node_id": node_id, "pmid_checks": checks,
            "overall_status": status, "notes": note}
```

Keep `build_source_text`, `frontmatter_updates`, `_node_files`, `_now`. The module-level `main()` (CLI) is superseded by `nono-librarian verify` (Task 6) — delete `main()` and the `if __name__` block from this module now, and remove `"evaluate": "librarian_evaluate"` from the dispatcher `COMMANDS` (it returns in Task 6 as `verify`). Confirm no `llm` references remain anywhere:

```bash
grep -rn 'llm\|LLMUnavailable\|chat=' src/nono_librarian || echo "clean"
```

- [ ] **Step 4: Delete the build orchestrator + its test**

```bash
git rm src/nono_librarian/cli/librarian_build.py tests/unit/test_librarian_build.py
```

Remove any dispatcher reference to it (there is none in COMMANDS). Re-run the import sanity check:

```bash
python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('src/nono_librarian/**/*.py', recursive=True)]" && echo "all parse"
```

- [ ] **Step 5: Run the full suite — expect green**

Run: `.venv-dev/bin/python -m pytest -q`
Expected: PASS (no `llm`, no model orchestrators).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: deterministic judge_node from agent verdicts; drop model orchestrators"
```

---

## Phase 3 — New agent-facing CLIs and schemas

### Task 4: `nono gather` — deterministic retrieval

**Files:**
- Create: `src/nono_librarian/cli/gather.py`
- Create: `tests/unit/test_gather.py`
- Modify: `src/nono_librarian/cli/__main__.py` (add `"gather": "gather"`)

**Interfaces:**
- Consumes: `lib.pubmed.esearch/fetch_metadata/fetch_full_text`, `lib.build.TIERS`, `lib.build.select_candidates`.
- Produces: `gather.gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text, known_pmids, tier, mindate=None) -> list[article]` (lifted verbatim from the deleted `librarian_build.gather_articles`); `gather.main(argv) -> int` writing `_candidates.json`. Article shape: `{"pmid","title","abstract","metadata"}`.

- [ ] **Step 1: Write the failing test (fakes, no network)**

```python
import json
from nono_librarian.cli import gather


def _esearch(q, **kw):
    return {"brca1 repair": ["111", "222"], "brca1 cancer": ["222", "333"]}[q]


def _fetch_metadata(pmids):
    return {p: {"title": f"T{p}", "abstract": f"abstract {p}", "pmcid": None}
            for p in pmids}


def _fetch_full_text(pmcid):
    return ""


def test_gather_articles_dedups_and_caps():
    arts = gather.gather_articles(
        ["brca1 repair", "brca1 cancer"], esearch=_esearch,
        fetch_metadata=_fetch_metadata, fetch_full_text=_fetch_full_text,
        known_pmids=set(), tier={"max_results": 10, "metadata": 10, "full_text": 0})
    pmids = [a["pmid"] for a in arts]
    assert pmids == ["111", "222", "333"]


def test_gather_main_writes_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(gather.pubmed, "esearch", _esearch)
    monkeypatch.setattr(gather.pubmed, "fetch_metadata", _fetch_metadata)
    monkeypatch.setattr(gather.pubmed, "fetch_full_text", _fetch_full_text)
    out = tmp_path / "_candidates.json"
    rc = gather.main(["brca1", "--query", "brca1 repair", "--query", "brca1 cancer",
                      "--breadth", "narrow", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert {a["pmid"] for a in data["articles"]} == {"111", "222", "333"}
    assert data["breadth"] == "narrow"
    assert data["sub_queries"] == ["brca1 repair", "brca1 cancer"]
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_gather.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `gather.py`**

```python
#!/usr/bin/env python3
"""`nono-librarian gather` — deterministic PubMed retrieval for agent-driven build."""
import argparse
import json
import sys

from nono_librarian.lib import build, pubmed


def gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text,
                    known_pmids, tier, mindate=None):
    per_query = [
        esearch(q, retmax=tier["max_results"], **({"mindate": mindate} if mindate else {}))
        for q in sub_queries
    ]
    pmids = build.select_candidates(per_query, known_pmids, cap=tier["metadata"])
    meta_map = fetch_metadata(pmids) if pmids else {}
    articles = []
    for p in pmids:
        meta = meta_map.get(p)
        if not meta:
            continue
        articles.append({"pmid": p, "title": meta.get("title", ""),
                         "abstract": meta.get("abstract", ""), "metadata": meta})
    for a in articles[:tier["full_text"]]:
        pmcid = a["metadata"].get("pmcid")
        if not pmcid:
            continue
        try:
            ft = fetch_full_text(pmcid)
        except pubmed.PubMedUnavailable:
            ft = ""
        if ft:
            a["abstract"] = (a["abstract"] + "\n\n" + ft).strip()
    return articles


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian gather",
                                     description="Retrieve PubMed candidate articles")
    parser.add_argument("topic")
    parser.add_argument("--query", action="append", dest="queries", required=True,
                        help="a planned sub-query (repeatable)")
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default="medium")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD or YYYY/MM/DD lower bound")
    parser.add_argument("--out", default="_candidates.json")
    args = parser.parse_args(argv)

    tier = build.TIERS[args.breadth]
    mindate = args.since.replace("-", "/") if args.since else None
    articles = gather_articles(
        args.queries, esearch=pubmed.esearch, fetch_metadata=pubmed.fetch_metadata,
        fetch_full_text=pubmed.fetch_full_text, known_pmids=set(), tier=tier,
        mindate=mindate)
    payload = {"topic": args.topic, "breadth": args.breadth,
               "sub_queries": args.queries, "articles": articles}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Gathered {len(articles)} articles → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — expect pass; register subcommand**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_gather.py -q` → PASS.
Add `"gather": "gather",` to `COMMANDS` in `__main__.py`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: nono gather (deterministic PubMed retrieval)"
```

### Task 5: `nono assemble` — structural node/manifest writer + input schema

**Files:**
- Create: `src/nono_librarian/data/schemas/nodes_input_schema.json`
- Create: `src/nono_librarian/cli/assemble.py`
- Create: `tests/unit/test_assemble.py`
- Modify: `src/nono_librarian/cli/__main__.py` (add `"assemble": "assemble"`)

**Interfaces:**
- Consumes: `lib.build.assign_ids/apply_relationships/render_node_markdown/assemble_manifest/_shared_pmid_edges/RELATIONSHIPS`, `lib.frontmatter.write`, `nono_librarian.paths.data_file`.
- Produces: `assemble.load_nodes_input(path) -> dict` (schema-validated); `assemble.build_nodes(raw_nodes, start_id) -> (nodes, edges)`; `assemble.write_kg(kg_folder, topic, breadth, sub_queries, raw, today, start_id) -> manifest`; `main(argv)`. Also writes `<KG>/_judgments.json` (`{node_id: {pmid: {verdict, reasoning, quotes}}}`) from the input's per-pmid verdicts for `finalize` to consume.

`_nodes.json` shape (one object): `{"sub_queries":[...], "nodes":[{"title","summary","detail","tags":[...],"keywords":[...],"entities":[{"name","type"}],"pmids":[...],"pubmed_ids":[{"pmid","supports","verdict","reasoning","quotes":[{"text","source"}]}],"related_to":[{"target_title"|"target_id","relationship"}]}]}`. Relationships are optional; when absent, `_shared_pmid_edges` supplies fallbacks.

- [ ] **Step 1: Write `nodes_input_schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "nono-librarian agent node input",
  "type": "object",
  "required": ["nodes"],
  "properties": {
    "sub_queries": {"type": "array", "items": {"type": "string"}},
    "nodes": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["title", "summary", "pubmed_ids"],
        "properties": {
          "title": {"type": "string", "minLength": 1},
          "summary": {"type": "string", "minLength": 1},
          "detail": {"type": "string"},
          "tags": {"type": "array", "items": {"type": "string"}},
          "keywords": {"type": "array", "items": {"type": "string"}},
          "entities": {
            "type": "array",
            "items": {"type": "object", "required": ["name", "type"],
                      "properties": {"name": {"type": "string"},
                                     "type": {"type": "string"}}}
          },
          "pmids": {"type": "array", "items": {"type": "string"}},
          "pubmed_ids": {
            "type": "array", "minItems": 1,
            "items": {
              "type": "object", "required": ["pmid", "supports"],
              "properties": {
                "pmid": {"type": "string", "minLength": 1},
                "supports": {"type": "string", "minLength": 1},
                "verdict": {"enum": ["supported", "partially_supported",
                                      "not_supported", "unrelated"]},
                "reasoning": {"type": "string"},
                "quotes": {
                  "type": "array",
                  "items": {"type": "object", "required": ["text", "source"],
                            "properties": {"text": {"type": "string"},
                                           "source": {"enum": ["abstract", "full_text"]}}}
              }
            }
          },
          "related_to": {
            "type": "array",
            "items": {"type": "object", "required": ["relationship"],
                      "properties": {"target_title": {"type": "string"},
                                     "target_id": {"type": "string"},
                                     "relationship": {"type": "string"}}}
          }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write the failing test**

```python
import json
from nono_librarian.cli import assemble
from nono_librarian.lib.frontmatter import parse as parse_node

RAW = {
    "sub_queries": ["brca1 repair"],
    "nodes": [
        {"title": "BRCA1 in HR repair", "summary": "BRCA1 enables HR.",
         "detail": "para", "tags": ["mechanism"], "keywords": ["brca1", "hr"],
         "entities": [{"name": "BRCA1", "type": "gene"}],
         "pmids": ["111"],
         "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 enables HR",
                         "verdict": "supported",
                         "quotes": [{"text": "BRCA1 enables HR", "source": "abstract"}]}]},
        {"title": "PARP synthetic lethality", "summary": "PARPi kills BRCA1-null.",
         "tags": ["therapy"], "pmids": ["111", "222"],
         "pubmed_ids": [{"pmid": "222", "supports": "PARPi is lethal in BRCA1-null"}]},
    ],
}


def test_assemble_writes_nodes_manifest_and_judgments(tmp_path):
    kg = tmp_path / "KG_Test"
    npath = tmp_path / "_nodes.json"
    npath.write_text(json.dumps(RAW))
    rc = assemble.main([str(kg), "--nodes", str(npath), "--topic", "BRCA1",
                        "--breadth", "narrow"])
    assert rc == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert [n["id"] for n in manifest["nodes"]] == ["node_001", "node_002"]
    # shared PMID 111 → fallback related_to edge between the two nodes
    assert any(e["source"] == "node_001" and e["target"] == "node_002"
               for e in manifest["edges"])
    fm, _ = parse_node(str(kg / "nodes" / manifest["nodes"][0]["file"].split("/")[-1]))
    assert fm["pubmed_ids"][0]["pmid"] == "111"
    judg = json.loads((kg / "_judgments.json").read_text())
    assert judg["node_001"]["111"]["verdict"] == "supported"


def test_assemble_rejects_invalid_input(tmp_path):
    import pytest, jsonschema
    bad = tmp_path / "_nodes.json"
    bad.write_text(json.dumps({"nodes": [{"title": "x"}]}))  # missing summary/pubmed_ids
    with pytest.raises(jsonschema.ValidationError):
        assemble.load_nodes_input(str(bad))


def test_assemble_start_id_appends(tmp_path):
    kg = tmp_path / "KG_Test"
    npath = tmp_path / "_nodes.json"
    npath.write_text(json.dumps(RAW))
    rc = assemble.main([str(kg), "--nodes", str(npath), "--topic", "BRCA1",
                        "--breadth", "narrow", "--start-id", "7"])
    assert rc == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["nodes"][0]["id"] == "node_007"
```

- [ ] **Step 3: Run — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_assemble.py -q`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement `assemble.py`**

```python
#!/usr/bin/env python3
"""`nono-librarian assemble` — turn agent _nodes.json into node files + manifest (no model)."""
import argparse
import datetime
import json
import os
import sys

import jsonschema

from nono_librarian.lib import build
from nono_librarian.lib.frontmatter import write as write_node
from nono_librarian.paths import data_file


def load_nodes_input(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    schema = json.loads(data_file("schemas", "nodes_input_schema.json").read_text())
    jsonschema.validate(raw, schema)
    return raw


def _node_seed(n):
    """Map one input node to the dict shape lib.build helpers expect."""
    supports = {pi["pmid"]: pi["supports"] for pi in n["pubmed_ids"]}
    return {
        "title": n["title"], "summary": n["summary"], "detail": n.get("detail", ""),
        "tags": n.get("tags") or ["general"], "category": (n.get("tags") or ["general"])[0],
        "keywords": n.get("keywords", []), "entities": n.get("entities", []),
        "pmids": n.get("pmids") or list(supports.keys()), "supports": supports,
        "related_nodes": [], "relationships": {},
    }


def build_nodes(raw_nodes, start_id):
    seeds = [_node_seed(n) for n in raw_nodes]
    nodes = build.assign_ids(seeds, start=start_id)
    title_to_id = {n["title"]: n["id"] for n in nodes}
    edges = []
    for raw, node in zip(raw_nodes, nodes):
        for rel in raw.get("related_to", []) or []:
            tgt = rel.get("target_id") or title_to_id.get(rel.get("target_title", ""))
            r = rel["relationship"]
            if tgt and tgt in title_to_id.values() and tgt != node["id"] \
                    and r in build.RELATIONSHIPS:
                edges.append({"source": node["id"], "target": tgt, "relationship": r})
    if not edges:
        edges = build._shared_pmid_edges(nodes)
    build.apply_relationships(nodes, edges)
    return nodes, edges


def judgments_from_input(raw_nodes, nodes):
    out = {}
    for raw, node in zip(raw_nodes, nodes):
        per = {}
        for pi in raw["pubmed_ids"]:
            if "verdict" in pi:
                per[pi["pmid"]] = {"verdict": pi["verdict"],
                                   "reasoning": pi.get("reasoning", ""),
                                   "quotes": pi.get("quotes", [])}
        if per:
            out[node["id"]] = per
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian assemble",
                                     description="Write node files + manifest from _nodes.json")
    parser.add_argument("kg_folder")
    parser.add_argument("--nodes", required=True, help="agent _nodes.json")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default="medium")
    parser.add_argument("--start-id", type=int, default=1)
    args = parser.parse_args(argv)

    raw = load_nodes_input(args.nodes)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    nodes, edges = build_nodes(raw["nodes"], args.start_id)

    nodes_dir = os.path.join(args.kg_folder, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)
    for n in nodes:
        fm, body = build.render_node_markdown(n, today)
        write_node(os.path.join(nodes_dir, n["file"]), fm, body)

    kg_name = os.path.basename(os.path.normpath(args.kg_folder))
    manifest = build.assemble_manifest(kg_name, args.topic, args.breadth,
                                       raw.get("sub_queries", []), nodes, edges, today)
    with open(os.path.join(args.kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(args.kg_folder, "_judgments.json"), "w", encoding="utf-8") as fh:
        json.dump(judgments_from_input(raw["nodes"], nodes), fh, indent=2)
    print(f"Assembled {len(nodes)} nodes → {args.kg_folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run — expect pass; register subcommand**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_assemble.py -q` → PASS.
Add `"assemble": "assemble",` to `COMMANDS`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: nono assemble + nodes_input schema (structural KG writer)"
```

### Task 6: `nono verify` — shared guardrail writeback engine

`verify` is the deterministic evaluation writeback used both standalone (re-evaluate an existing KG from agent verdicts) and inside `finalize`. It reuses `librarian_evaluate.judge_node` + `frontmatter_updates`.

**Files:**
- Create: `src/nono_librarian/cli/verify.py`
- Create: `tests/unit/test_verify.py`
- Modify: `src/nono_librarian/cli/__main__.py` (add `"verify": "verify"`)

**Interfaces:**
- Consumes: `librarian_evaluate.judge_node/frontmatter_updates/_node_files`, `lib.pubmed`, `lib.frontmatter.parse`.
- Produces: `verify.verify_kg(kg_folder, judgments, *, only_ids=None, fetch_metadata, fetch_full_text) -> list[entry]` (writes node frontmatter via `update_frontmatter` module, writes `_evaluation_log.json`, runs `update_manifest_stats`); `verify.load_judgments(kg_folder, path) -> dict`; `main(argv)`.

- [ ] **Step 1: Write the failing test (fakes; verdicts from a file)**

```python
import json
from nono_librarian.cli import verify
from nono_librarian.lib.frontmatter import write as write_node, parse as parse_node


def _meta(pmids):
    return {p: {"title": f"T{p}",
                "abstract": "BRCA1 loss impairs homologous recombination.",
                "pmcid": None} for p in pmids}


def _ft(_pmcid):
    return ""


def _seed_kg(tmp_path):
    kg = tmp_path / "KG"
    (kg / "nodes").mkdir(parents=True)
    fm = {"id": "node_001", "title": "BRCA1",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR",
                          "verified": False}],
          "evaluation_status": "pending"}
    write_node(str(kg / "nodes" / "node_001_brca1.md"), fm, "# BRCA1\n")
    (kg / "manifest.json").write_text(json.dumps(
        {"kg_name": "KG", "nodes": [{"id": "node_001", "title": "BRCA1",
         "file": "nodes/node_001_brca1.md", "pubmed_ids": ["111"],
         "evaluation_status": "pending"}], "edges": [], "statistics": {}}))
    return kg


def test_verify_marks_verified_with_quote(tmp_path):
    kg = _seed_kg(tmp_path)
    judg = {"node_001": {"111": {"verdict": "supported",
            "quotes": [{"text": "impairs homologous recombination", "source": "abstract"}]}}}
    entries = verify.verify_kg(str(kg), judg, fetch_metadata=_meta, fetch_full_text=_ft)
    assert entries[0]["overall_status"] == "passed"
    fm, _ = parse_node(str(kg / "nodes" / "node_001_brca1.md"))
    assert fm["pubmed_ids"][0]["verified"] is True
    assert fm["evaluation_status"] == "passed"


def test_verify_forces_fail_without_quote(tmp_path):
    kg = _seed_kg(tmp_path)
    judg = {"node_001": {"111": {"verdict": "supported",
            "quotes": [{"text": "not in source", "source": "abstract"}]}}}
    entries = verify.verify_kg(str(kg), judg, fetch_metadata=_meta, fetch_full_text=_ft)
    assert entries[0]["overall_status"] == "failed"
    fm, _ = parse_node(str(kg / "nodes" / "node_001_brca1.md"))
    assert fm["quarantined"] is True
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_verify.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `verify.py`**

```python
#!/usr/bin/env python3
"""`nono-librarian verify` — apply agent verdicts to a KG behind the verbatim-quote guardrail."""
import argparse
import datetime
import json
import os
import subprocess
import sys

from nono_librarian.cli import librarian_evaluate as le
from nono_librarian.lib import pubmed
from nono_librarian.lib.frontmatter import parse as parse_node


def load_judgments(kg_folder, path):
    p = path or os.path.join(kg_folder, "_judgments.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def verify_kg(kg_folder, judgments, *, only_ids=None,
              fetch_metadata=pubmed.fetch_metadata,
              fetch_full_text=pubmed.fetch_full_text):
    node_files = le._node_files(kg_folder, only_ids)
    entries = []
    for node_id, path in node_files.items():
        fm, _body = parse_node(path)
        entry = le.judge_node(node_id, fm, judgments.get(node_id, {}),
                              fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text)
        entry["timestamp"] = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append(entry)

    with open(os.path.join(kg_folder, "_evaluation_log.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)
    for entry in entries:
        updates = le.frontmatter_updates(entry)
        upd_path = os.path.join(kg_folder, f"_eval_upd_{entry['node_id']}.json")
        with open(upd_path, "w", encoding="utf-8") as fh:
            json.dump(updates, fh)
        subprocess.run([sys.executable, "-m", "nono_librarian.cli.update_frontmatter",
                        node_files[entry["node_id"]], "--updates-file", upd_path], check=True)
        os.remove(upd_path)
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.update_manifest_stats",
                    kg_folder], check=True)
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian verify",
                                     description="Guardrailed evaluation writeback from agent verdicts")
    parser.add_argument("kg_folder")
    parser.add_argument("--verdicts", default=None,
                        help="agent verdicts JSON (default: <KG>/_judgments.json)")
    parser.add_argument("--nodes", default=None, help="comma-separated node IDs")
    args = parser.parse_args(argv)
    only_ids = set(args.nodes.split(",")) if args.nodes else None
    judgments = load_judgments(args.kg_folder, args.verdicts)
    entries = verify_kg(args.kg_folder, judgments, only_ids=only_ids)
    passed = sum(1 for e in entries if e["overall_status"] == "passed")
    print(f"Verified {len(entries)} nodes: {passed} passed, {len(entries) - passed} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — expect pass; register subcommand**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_verify.py -q` → PASS.
Add `"verify": "verify",` to `COMMANDS`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: nono verify (shared guardrail writeback engine)"
```

### Task 7: `nono finalize` — the one finish command

Chains the full deterministic finish over an assembled KG, including the `verify` guardrail step. Lifts the deterministic helpers from the deleted `librarian_build.py` (`build_run_record`, `_persist_and_classify`, `_finalize_kg`, `ledger_batch_for_used`, `next_node_number`) into this module, with subprocess calls in `-m` form.

**Files:**
- Create: `src/nono_librarian/cli/finalize.py`
- Create: `tests/unit/test_finalize.py`
- Modify: `src/nono_librarian/cli/__main__.py` (add `"finalize": "finalize"`)

**Interfaces:**
- Consumes: `verify.verify_kg`, `lib.frontmatter`, the maintenance CLI modules (via `-m`), `nono_librarian.paths`.
- Produces: `finalize.build_run_record(...)` (copied verbatim from old `librarian_build`); `finalize.finalize_kg(kg_folder, *, mode, version, candidates_path=None, run_subprocess=True) -> summary`; `main(argv)`. Reads `<KG>/_candidates.json` (if present) for the ledger "used" batch and `<KG>/_judgments.json` for verdicts.

- [ ] **Step 1: Write the failing end-to-end test (fake PubMed, real scripts)**

```python
import json
from nono_librarian.cli import assemble, finalize

RAW = {"sub_queries": ["brca1"], "nodes": [
    {"title": "BRCA1 in HR", "summary": "BRCA1 enables HR.", "detail": "p",
     "tags": ["mechanism"], "keywords": ["brca1"], "pmids": ["111"],
     "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 enables HR",
                     "verdict": "supported",
                     "quotes": [{"text": "BRCA1 enables HR", "source": "abstract"}]}]}]}


def _meta(pmids):
    return {p: {"title": f"T{p}", "abstract": "BRCA1 enables HR repair.",
                "pmcid": None, "authors": ["A"], "journal": "J", "year": "2024",
                "publication_types": ["Journal Article"]} for p in pmids}


def _ft(_):
    return ""


def test_finalize_runs_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kg = tmp_path / "KG_BRCA1"
    npath = tmp_path / "_nodes.json"
    npath.write_text(json.dumps(RAW))
    assemble.main([str(kg), "--nodes", str(npath), "--topic", "BRCA1", "--breadth", "narrow"])
    # candidates feed the ledger "used" batch
    (kg / "_candidates.json").write_text(json.dumps({"articles": [
        {"pmid": "111", "metadata": _meta(["111"])["111"]}]}))
    monkeypatch.setattr(finalize.pubmed, "fetch_metadata", _meta)
    monkeypatch.setattr(finalize.pubmed, "fetch_full_text", _ft)
    summary = finalize.finalize_kg(str(kg), mode="build", version=1)
    assert summary["passed"] == 1 and summary["failed"] == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["statistics"]            # stats populated
    assert (kg / "runs").exists()            # run-record written
    assert (kg / "_log.md").exists() or (kg / "_log.json").exists()
```

(Adjust the final log-file assertion to whatever `append_log` writes — confirm by reading `src/nono_librarian/cli/append_log.py` before implementing.)

- [ ] **Step 2: Run — expect failure**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_finalize.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `finalize.py`**

Port the deterministic pipeline from the old `librarian_build.py`. Copy `build_run_record` verbatim. Re-express `_persist_and_classify` and `_finalize_kg` with `-m` subprocess calls and a `verify_kg` step replacing the old in-loop evaluator:

```python
#!/usr/bin/env python3
"""`nono-librarian finalize` — deterministic finish pipeline for an assembled KG (no model)."""
import argparse
import datetime
import json
import os
import subprocess
import sys

from nono_librarian.cli import verify
from nono_librarian.lib import pubmed


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(*args):
    subprocess.run([sys.executable, "-m", *args], check=True)


def build_run_record(*, kg_name, mode, version, timestamp, nodes_created,
                     refs_added, passed, failed, since_date=None):
    run_id = timestamp.replace(":", "") + f"-v{version}"
    return {"run_id": run_id, "kg_name": kg_name, "mode": mode, "timestamp": timestamp,
            "version": version, "since_date": since_date,
            "nodes_created": nodes_created, "nodes_revised": [],
            "refs_added": refs_added, "refs_failed": [],
            "eval_summary": {"evaluated": len(nodes_created), "passed": passed, "failed": failed}}


def _ledger_used_batch(candidates_path):
    if not candidates_path or not os.path.exists(candidates_path):
        return []
    with open(candidates_path, encoding="utf-8") as fh:
        cands = json.load(fh)
    batch = []
    for a in cands.get("articles", []):
        m = a.get("metadata", {})
        batch.append({"pmid": a["pmid"], "disposition": "used", "title": m.get("title"),
                      "authors": m.get("authors", []), "journal": m.get("journal"),
                      "year": m.get("year"),
                      "publication_types": m.get("publication_types", [])})
    return batch


def _refs_added(kg_folder, node_ids):
    """Group node IDs by the PMIDs they cite, from the manifest."""
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    refs = {}
    for n in manifest["nodes"]:
        if node_ids and n["id"] not in node_ids:
            continue
        for pmid in n.get("pubmed_ids", []):
            refs.setdefault(pmid, set()).add(n["id"])
    return [{"pmid": p, "nodes": sorted(ns)}
            for p, ns in sorted(refs.items(), key=lambda kv: int(kv[0]))]


def finalize_kg(kg_folder, *, mode, version, candidates_path=None,
                since_date=None, overview_text=None, run_subprocess=True):
    candidates_path = candidates_path or os.path.join(kg_folder, "_candidates.json")
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    kg_name = manifest["kg_name"]
    node_ids = [n["id"] for n in manifest["nodes"]]

    # 1. ledger: record used PMIDs
    if not os.path.exists(os.path.join(kg_folder, "_pmid_ledger.json")):
        _run("nono_librarian.cli.pmid_ledger", "init", kg_folder, "--kg-name", kg_name)
    batch = _ledger_used_batch(candidates_path)
    if batch:
        bpath = os.path.join(kg_folder, "_build_ledger_batch.json")
        with open(bpath, "w", encoding="utf-8") as fh:
            json.dump(batch, fh)
        _run("nono_librarian.cli.pmid_ledger", "batch-add", kg_folder, "--input", bpath)
        os.remove(bpath)

    # 2. evidence tiers + literature stamping
    _run("nono_librarian.cli.classify_evidence_tier", kg_folder, "--update-ledger")
    _run("nono_librarian.cli.stamp_literature", kg_folder)

    # 3. guardrailed evaluation writeback from agent verdicts
    judgments = verify.load_judgments(kg_folder, None) if os.path.exists(
        os.path.join(kg_folder, "_judgments.json")) else {}
    entries = verify.verify_kg(kg_folder, judgments,
                               fetch_metadata=pubmed.fetch_metadata,
                               fetch_full_text=pubmed.fetch_full_text)
    passed = sum(1 for e in entries if e["overall_status"] == "passed")
    failed = len(entries) - passed

    # 4. quarantine, index, stats, validate, embeddings (non-fatal)
    _run("nono_librarian.cli.enforce_quarantine", kg_folder)
    if overview_text is not None:
        _run("nono_librarian.cli.generate_index", kg_folder, "--overview-text", overview_text)
    else:
        _run("nono_librarian.cli.generate_index", kg_folder)
    _run("nono_librarian.cli.update_manifest_stats", kg_folder)
    _run("nono_librarian.cli.validate_manifest", os.path.join(kg_folder, "manifest.json"))
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.build_embeddings", kg_folder],
                   check=False)

    # 5. run-record + digest (digest non-fatal), then log
    run_record = build_run_record(
        kg_name=kg_name, mode=mode, version=version, timestamp=_now_iso(),
        nodes_created=node_ids, refs_added=_refs_added(kg_folder, node_ids),
        passed=passed, failed=failed, since_date=since_date)
    runs_dir = os.path.join(kg_folder, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    rr_path = os.path.join(runs_dir, run_record["run_id"] + ".json")
    with open(rr_path, "w", encoding="utf-8") as fh:
        json.dump(run_record, fh, indent=2)
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.render_digest",
                    kg_folder, "--run-record", rr_path], check=False)
    _run("nono_librarian.cli.append_log", kg_folder, "--op", mode,
         "--summary", f"Local {mode.upper()}: {len(node_ids)} nodes, {passed} passed, {failed} failed.")
    return {"nodes": len(node_ids), "passed": passed, "failed": failed,
            "kg_folder": kg_folder, "run_id": run_record["run_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian finalize",
                                     description="Run the deterministic KG finish pipeline")
    parser.add_argument("kg_folder")
    parser.add_argument("--mode", choices=["build", "update"], default="build")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--since", default=None)
    parser.add_argument("--overview-text", default=None)
    args = parser.parse_args(argv)
    summary = finalize_kg(args.kg_folder, mode=args.mode, version=args.version,
                          since_date=args.since, overview_text=args.overview_text)
    print(f"{args.mode.upper()} finalized: {summary['nodes']} nodes "
          f"({summary['passed']} passed, {summary['failed']} failed) → {args.kg_folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Confirm exact CLI flags before running**

Read these to confirm the subcommand flags used above match (fix any mismatch in `finalize.py`): `pmid_ledger.py` (`init --kg-name`, `batch-add --input`; confirm the ledger filename used in the `init`-guard, e.g. `_pmid_ledger.json`), `classify_evidence_tier.py` (`--update-ledger`), `generate_index.py` (`--overview-text`), `append_log.py` (`--op`, `--summary`, and the log filename for the test assertion), `render_digest.py` (`--run-record`).

```bash
grep -n 'add_argument\|_ledger.json\|_log' src/nono_librarian/cli/pmid_ledger.py src/nono_librarian/cli/append_log.py src/nono_librarian/cli/classify_evidence_tier.py src/nono_librarian/cli/generate_index.py src/nono_librarian/cli/render_digest.py
```

- [ ] **Step 5: Run — expect pass; register subcommand**

Run: `.venv-dev/bin/python -m pytest tests/unit/test_finalize.py -q` → PASS.
Add `"finalize": "finalize",` to `COMMANDS`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: nono finalize (one-command deterministic finish pipeline)"
```

---

## Phase 4 — SKILL.md, bootstrap, and packaging cleanup

### Task 8: Rewrite SKILL.md for the agent-driven flow

**Files:**
- Modify: `.claude/skills/nono-librarian/SKILL.md`

**Interfaces:** none (documentation). Must reflect: `$NONO_HOME/.venv/bin/nono` invocation, agent-is-the-reasoner, and the gather→agent→assemble→finalize / verify flows.

- [ ] **Step 1: Replace SKILL.md frontmatter + body**

Write the new `.claude/skills/nono-librarian/SKILL.md`:

```markdown
---
name: nono-librarian
description: >-
  Front door for the libririan PubMed knowledge-graph toolkit. Whichever agent
  is running (Claude or a local Hermes agent) drives the end-to-end KG curation
  itself — there is NO separate local model to set up, serve, or find. Use this
  whenever the user wants to build, update, query, evaluate, lint, or maintain a
  libririan KG (folders with manifest.json + nodes/) on their own machine, or
  mentions running the librarian "locally", "offline", "in the nono env", or
  "without the Claude PubMed MCP". The toolkit installs into a shared ~/.nono uv
  venv and is invoked as `$NONO_HOME/.venv/bin/nono-librarian <command>`.
---

# nono-librarian

The harness-agnostic front door to the libririan toolkit. Two jobs: **(1)
guarantee the shared `~/.nono` environment**, and **(2) dispatch** a request to
the right deterministic `nono-librarian` subcommand — while **you, the running
agent, do all the reasoning**. No Claude PubMed MCP, no served model.

## Step 1 — Guarantee the environment (always first)

`~/.nono` is the research-assistant home (a normal directory). It holds ONE
shared uv venv at `~/.nono/.venv`; nono-librarian is the module at
`~/.nono/librarian`, installed editable into that venv. Everything runs through
`$NONO_HOME/.venv/bin/nono-librarian`.

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
test -d "$NONO_HOME/librarian" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
mkdir -p "$HOME/.claude/skills"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$HOME/.claude/skills/nono-librarian"
```

Invoke the toolkit as `"$NONO_HOME/.venv/bin/nono-librarian" <command> …`.
Python 3.14 is the newest the embedding runtime (onnxruntime) ships wheels for.
`requirements` live in `pyproject.toml`; there is no `requirements.txt` and no conda.

## Step 2 — You are the reasoner (no model to find)

There is **no model discovery, no `LLM_*` env, no OpenAI/Anthropic endpoint**.
The agent reading this skill performs every reasoning step itself: search
planning, node design, per-node synthesis, relationship calls, and the
claim↔evidence judgment. The `nono-librarian` subcommands do only deterministic work —
retrieval, structural writes, the finish pipeline, and the verbatim-quote
guardrail. You hand them structured JSON.

The guardrail is enforced in Python regardless of what you decide: a claim you
mark `supported`/`partially_supported` is kept only if at least one of your
quotes appears **verbatim** in the article text; otherwise it is forced to
`not_supported`. Quote exactly.

## Step 3 — Dispatch by intent

Let `N="$NONO_HOME/.venv/bin/nono-librarian"`.

### Query / search a KG
`$N search "<query>" <KG>/manifest.json --top 10` ranks nodes (semantic +
lexical, no model). To answer in prose, read the top nodes and write the answer
yourself, citing PMIDs — do not fabricate beyond the returned summaries/quotes.
Refresh the index after node changes: `$N embeddings <KG>`.

### Maintain a KG
Deterministic, no reasoning: `$N lint <KG>`, `$N retractions <KG>`,
`$N chase <KG>`, `$N ledger <subcommand> <KG>`, `$N digest <KG>`,
`$N index <KG>`, `$N cross-index …`. Run any with `--help` first.

### Build or update a KG
1. **Plan** sub-queries for the topic yourself (breadth: narrow=3, medium=4,
   broad=6 sub-queries).
2. **Gather:** `$N gather "<topic>" --query "<q1>" --query "<q2>" … --breadth <b> [--since YYYY-MM-DD] --out _candidates.json`. For an UPDATE, pass `--since` and copy `_candidates.json` into the KG folder.
3. **Reason:** read `_candidates.json` and write `_nodes.json` — design nodes
   (each one citable claim), synthesize each from its articles, set relationships,
   and for every cited PMID give a `verdict` plus verbatim `quotes`. Conform to
   `~/.nono/librarian/src/nono_librarian/data/schemas/nodes_input_schema.json`.
4. **Assemble:** `$N assemble <KG> --nodes _nodes.json --topic "<topic>" --breadth <b>` (UPDATE: add `--start-id <next node number>`).
5. **Finalize:** `$N finalize <KG> --mode build` (UPDATE: `--mode update --version <v> --since <date>`). This runs ledger → tiers → stamping → guardrailed verdict writeback → quarantine → index → stats → validation → embeddings → run-record → digest → log.

### Evaluate / fact-check an existing KG
Read each node's claims and cited sources, judge support, and write a verdicts
file `{node_id: {pmid: {verdict, reasoning, quotes:[{text,source}]}}}`. Then
`$N verify <KG> --verdicts <file>`. The guardrail + writeback are identical to
finalize's evaluation step; a claim never passes without a verbatim quote.

## Scope (be honest)

- **Local, agent-driven, no model server:** build, update, query, prose answers,
  evaluation/fact-check, and all maintenance.
- **Not implemented:** ClinicalTrials.gov and ChEMBL as sources, entity-ID
  normalization across nodes, user-provided `--source` materials. PubMed is the
  only source.

Quality now tracks **whichever agent runs this** rather than a fixed local
model. The Python guardrail bounds the failure mode (no quote → no pass) but
cannot make a weak agent's synthesis strong.
```

- [ ] **Step 2: Sanity-check there are no stale references**

```bash
grep -n 'conda\|LLM_\|vLLM\|llm.py\|requirements.txt\|librarian_build\|librarian_evaluate\|LLMUnavailable' .claude/skills/nono-librarian/SKILL.md || echo "clean"
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/nono-librarian/SKILL.md
git commit -m "docs: rewrite SKILL.md for agent-driven, ~/.nono venv flow"
```

### Task 9: Bootstrap script, gitignore, and final verification

**Files:**
- Create: `scripts-bootstrap/bootstrap.sh` (a checked-in copy of the Step-1 bootstrap, for new machines)
- Modify: `.gitignore`
- Modify: `docs/superpowers/plans/2026-06-26-agent-driven-nono-librarian.md` (check off completed)

**Interfaces:** none.

- [ ] **Step 1: Add the bootstrap script**

Create `scripts-bootstrap/bootstrap.sh` (mirrors SKILL.md Step 1 so a fresh machine can run one command):

```bash
#!/usr/bin/env bash
set -euo pipefail
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
test -d "$NONO_HOME/librarian" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
mkdir -p "$HOME/.claude/skills"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$HOME/.claude/skills/nono-librarian"
echo "nono-librarian ready: $NONO_HOME/.venv/bin/nono-librarian --help"
```

```bash
chmod +x scripts-bootstrap/bootstrap.sh
```

- [ ] **Step 2: Update `.gitignore`**

Ensure it contains (remove now-stale `scripts/lib/__pycache__/`):

```
# Test output (regenerated each run)
tests/output/

# Python
__pycache__/
*.pyc
src/nono_librarian/**/__pycache__/

# venvs (never tracked; live outside the repo in ~/.nono, plus local dev)
.venv/
.venv-dev/

# Generated KG artifacts
_cost_log.jsonl
_embeddings.json
_candidates.json
_nodes.json
_judgments.json
```

- [ ] **Step 3: Full suite + smoke test from a clean install**

```bash
cd "$(git rev-parse --show-toplevel)"
rm -rf .venv-dev && uv venv .venv-dev --python 3.14
uv pip install --python .venv-dev -e ".[dev]"
.venv-dev/bin/python -m pytest -q
.venv-dev/bin/nono-librarian --help
.venv-dev/bin/nono-librarian lint --help
```

Expected: all tests PASS; `nono-librarian --help` lists `assemble, chase, cost-report, cross-index, digest, embeddings, finalize, gather, index, ledger, lint, preflight, retractions, search, verify`; `nono-librarian lint --help` prints linter usage. Confirm no `llm`/model references survive anywhere:

```bash
grep -rn 'LLMUnavailable\|LLM_BASE_URL\|lib.llm\|import llm\|chat=llm' src tests || echo "clean"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: bootstrap script, gitignore, final cleanup"
```

---

## Self-Review

**Spec coverage:**
- §3 env layout → Task 8 (SKILL.md Step 1), Task 9 (bootstrap).
- §4 packaging (pyproject, src, `nono-librarian` CLI, packaged data) → Task 1.
- §5 reasoning-seam removal → Tasks 2, 3.
- §6 curation flow (gather/assemble/finalize/verify, UPDATE via start-id) → Tasks 4–7, SKILL.md.
- §7 `nodes_input_schema.json` (+ verdicts shape, folded into `_judgments.json`) → Task 5.
- §8 SKILL.md rewrite → Task 8.
- §9 tests (remove model tests, port deterministic, add new) → Tasks 2, 3 (removals), 4–7 (new).
- §10 migration/no-hardcoded-paths → `nono_librarian.paths`, `NONO_HOME` (Tasks 1, 8, 9).
- §11 out-of-scope → respected (no new sources, no model path).
- §12 net change → matches Tasks 1–9.
- Decisions: requirements.txt removed (Task 1 Step 2); global skill symlink (Tasks 8, 9).

**Placeholder scan:** Two deliberate "confirm exact flags by reading X" steps (Task 7 Step 4, Task 7 Step 1 log-file assertion) — these are verification steps against existing code, not unfilled requirements; the surrounding code is complete. No TBD/TODO elsewhere.

**Type consistency:** `judge_node(node_id, frontmatter, judgments_for_node, *, fetch_metadata, fetch_full_text)` used identically in Tasks 3, 6. `verify_kg(kg_folder, judgments, *, only_ids, fetch_metadata, fetch_full_text)` consistent across Tasks 6, 7. `judgments` shape `{node_id: {pmid: {verdict, reasoning, quotes}}}` consistent across assemble (producer), verify, finalize. `gather_articles` signature matches the lifted original. `_nodes.json` schema matches `_node_seed`/`build_nodes` consumption.
