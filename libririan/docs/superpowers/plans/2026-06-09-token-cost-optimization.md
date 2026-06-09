# Token Cost Optimization (Phase One) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut a scheduled weekly `/build-kg` UPDATE run from ~$12 to ~$1–3 (near zero on quiet weeks) via a deterministic preflight early-exit, Haiku evaluation workers with strong-model escalation, manifest-only UPDATE loading, and per-run cost instrumentation.

**Architecture:** Two new deterministic Python scripts (`preflight.py`, `cost_report.py`), small additions to three existing scripts/schemas, and prompt edits to four `.claude/commands/*.md` files. A Stop hook in project `.claude/settings.json` appends per-session token/cost totals to `_cost_log.jsonl`. Spec: `docs/superpowers/specs/2026-06-09-token-cost-optimization-design.md`.

**Tech Stack:** Python 3 stdlib only (urllib for NCBI E-utilities — no new runtime deps), pytest for unit tests, Claude Code hooks/Agent-tool prompt conventions for the markdown command files.

**Conventions for all tasks:**
- Working directory for all commands: `/home/dadi/nono/libririan` (the git repo root is one level up at `/home/dadi/nono`; commit paths will show a `libririan/` prefix — that is expected).
- Run tests with `python3 -m pytest tests/unit -v`.
- Unit tests import scripts via: `sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))`.
- Existing scripts use atomic tempfile-write patterns; follow them.

---

### Task 1: Extract `append_entry()` from `append_log.py` and add the `preflight` op

`scripts/preflight.py` (Task 4) needs to append log entries by import, not subprocess. Currently all logic lives in `main()` (`scripts/append_log.py:25-77`).

**Files:**
- Modify: `scripts/append_log.py`
- Create: `tests/unit/__init__.py` (empty), `tests/unit/test_append_log.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Install pytest and register it as a dev requirement**

Replace the full contents of `requirements.txt` with:

```
PyYAML>=6.0
jsonschema>=4.0
pytest>=8.0
```

Run: `python3 -m pip install pytest --quiet` (skip if `python3 -m pytest --version` already succeeds).

- [ ] **Step 2: Write the failing test**

Create `tests/unit/__init__.py` (empty file). Create `tests/unit/test_append_log.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))

from append_log import VALID_OPS, append_entry


def test_append_entry_creates_log_and_prepends(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()

    ts1 = append_entry(str(kg), "build", "first entry")
    ts2 = append_entry(str(kg), "preflight", "second entry", details="2 novel PMIDs")

    content = (kg / "_log.md").read_text()
    assert content.index("second entry") < content.index("first entry")  # newest on top
    assert f"## [{ts1}] build | first entry" in content
    assert f"## [{ts2}] preflight | second entry" in content
    assert "2 novel PMIDs" in content


def test_preflight_is_a_valid_op():
    assert "preflight" in VALID_OPS


def test_append_entry_rejects_bad_op_and_missing_dir(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    with pytest.raises(ValueError):
        append_entry(str(kg), "bogus", "x")
    with pytest.raises(FileNotFoundError):
        append_entry(str(tmp_path / "nope"), "build", "x")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_append_log.py -v`
Expected: FAIL with `ImportError: cannot import name 'append_entry'`

- [ ] **Step 4: Refactor `append_log.py`**

Replace everything from the `VALID_OPS = ...` line through the end of `main()` in `scripts/append_log.py` with:

```python
VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight"}


def append_entry(kg_folder: str, op: str, summary: str, details: str = "") -> str:
    """Prepend an operation entry to <kg_folder>/_log.md. Returns the timestamp used."""
    kg_folder = os.path.abspath(kg_folder)
    log_path = os.path.join(kg_folder, "_log.md")

    if not os.path.isdir(kg_folder):
        raise FileNotFoundError(f"directory not found: {kg_folder}")
    if op not in VALID_OPS:
        raise ValueError(f"invalid op: {op!r} (valid: {sorted(VALID_OPS)})")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_entry = f"## [{timestamp}] {op} | {summary}\n"
    if details:
        new_entry += details.rstrip("\n") + "\n"
    new_entry += "\n"

    existing = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as fh:
            existing = fh.read()

    content = new_entry + existing

    fd, tmp_path = tempfile.mkstemp(dir=kg_folder, suffix=".md.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, log_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return timestamp


def main():
    parser = argparse.ArgumentParser(description="Append an operation entry to _log.md.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--op", required=True, choices=sorted(VALID_OPS),
                        help="Operation type")
    parser.add_argument("--summary", required=True,
                        help="One-line summary of what was done")
    parser.add_argument("--details", default="",
                        help="Optional multi-line details (newlines preserved)")
    args = parser.parse_args()

    try:
        timestamp = append_entry(args.kg_folder, args.op, args.summary, args.details)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Logged: [{timestamp}] {args.op}", file=sys.stderr)
```

Keep the module docstring and imports unchanged (they already import `argparse`, `os`, `sys`, `tempfile`, `datetime`/`timezone`). Update the docstring's `Operations:` line to read: `Operations: build, update, evaluate, link, query, lint, schedule, preflight`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_append_log.py -v`
Expected: 3 PASS

Also smoke-test the CLI path is unbroken:
`mkdir -p /tmp/kg_smoke && python3 scripts/append_log.py /tmp/kg_smoke --op preflight --summary "smoke" && grep -q "preflight | smoke" /tmp/kg_smoke/_log.md && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add scripts/append_log.py tests/unit/__init__.py tests/unit/test_append_log.py requirements.txt
git commit -m "refactor: expose append_entry() and add preflight op to append_log"
```

---

### Task 2: `--stamp-last-run` flag on `update_manifest_stats.py`

Fixes the never-stamped `schedule.last_run` (spec §Component 1). Stamps only when a `schedule` block exists.

**Files:**
- Modify: `scripts/update_manifest_stats.py`
- Create: `tests/unit/test_stamp_last_run.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_stamp_last_run.py`:

```python
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def make_kg(tmp_path, schedule=None):
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    manifest = {
        "kg_name": "KG_Test", "topic": "test topic",
        "created": "2026-01-01", "updated": "2026-01-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }
    if schedule is not None:
        manifest["schedule"] = schedule
    (kg / "manifest.json").write_text(json.dumps(manifest))
    return kg


def run_stats(kg, *extra):
    return subprocess.run(
        [sys.executable, "scripts/update_manifest_stats.py", str(kg), *extra],
        cwd=PROJECT_ROOT, capture_output=True, text=True)


def test_stamps_last_run_when_schedule_exists(tmp_path):
    kg = make_kg(tmp_path, schedule={"cron": "0 8 * * 1", "last_run": None,
                                     "trigger_name": "kg-update-test"})
    result = run_stats(kg, "--stamp-last-run")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["schedule"]["last_run"] is not None
    assert manifest["schedule"]["last_run"].endswith("Z")


def test_no_op_without_schedule_block(tmp_path):
    kg = make_kg(tmp_path)
    result = run_stats(kg, "--stamp-last-run")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert "schedule" not in manifest


def test_default_run_does_not_stamp(tmp_path):
    kg = make_kg(tmp_path, schedule={"cron": "0 8 * * 1", "last_run": None,
                                     "trigger_name": "kg-update-test"})
    result = run_stats(kg)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["schedule"]["last_run"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_stamp_last_run.py -v`
Expected: first test FAILS (`unrecognized arguments: --stamp-last-run`, returncode 2); the other two may pass — that's fine.

- [ ] **Step 3: Implement**

In `scripts/update_manifest_stats.py`:

1. Add to the imports block (after `import tempfile`):

```python
from datetime import datetime, timezone
```

2. Add after the `--dry-run` argument definition:

```python
    parser.add_argument("--stamp-last-run", action="store_true",
                        help="Set schedule.last_run to the current UTC timestamp "
                             "(no-op when the manifest has no schedule block)")
```

3. Immediately after the `stats = {...}` dict literal is closed (before the ledger-stats block), add:

```python
    if args.stamp_last_run and isinstance(manifest.get("schedule"), dict):
        manifest["schedule"]["last_run"] = datetime.now(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

(In `--dry-run` mode the manifest is never written, so the stamp is naturally a no-op there too.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_stamp_last_run.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/update_manifest_stats.py tests/unit/test_stamp_last_run.py
git commit -m "feat: add --stamp-last-run to update_manifest_stats"
```

---

### Task 3: Schema additions — `search_profile` and `schedule.threshold`

**Files:**
- Modify: `schemas/graph_schema.json`
- Create: `tests/unit/test_graph_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_graph_schema.py`:

```python
import json
import os

import jsonschema

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schemas", "graph_schema.json")


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def minimal_manifest():
    return {
        "kg_name": "KG_Test", "topic": "t",
        "created": "2026-01-01", "updated": "2026-01-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }


def test_search_profile_is_a_declared_property():
    schema = load_schema()
    assert "search_profile" in schema["properties"]
    manifest = minimal_manifest()
    manifest["search_profile"] = {
        "breadth": "narrow",
        "sub_queries": ["melatonin circadian rhythm molecular mechanism"],
        "updated": "2026-06-09",
    }
    jsonschema.validate(manifest, schema)  # must not raise


def test_search_profile_rejects_bad_breadth():
    schema = load_schema()
    manifest = minimal_manifest()
    manifest["search_profile"] = {"breadth": "huge", "sub_queries": ["x"]}
    try:
        jsonschema.validate(manifest, schema)
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass


def test_schedule_threshold_is_declared():
    schema = load_schema()
    assert "threshold" in schema["properties"]["schedule"]["properties"]
    manifest = minimal_manifest()
    manifest["schedule"] = {"cron": "0 8 * * 1", "trigger_name": "kg-update-t",
                            "last_run": None, "threshold": 3}
    jsonschema.validate(manifest, schema)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_graph_schema.py -v`
Expected: FAIL on the `assert "search_profile" in schema["properties"]` line.

- [ ] **Step 3: Edit the schema**

In `schemas/graph_schema.json`, replace:

```json
      "description": "Data sources used in building this KG"
    },
    "schedule": {
```

with:

```json
      "description": "Data sources used in building this KG"
    },
    "search_profile": {
      "type": "object",
      "description": "Persisted PubMed sub-queries — consumed by scripts/preflight.py and reused by UPDATE-mode Recent-track searches",
      "required": ["breadth", "sub_queries"],
      "properties": {
        "breadth": {
          "type": "string",
          "enum": ["narrow", "medium", "broad"],
          "description": "Topic breadth tier assigned in build-kg Phase 1b Step 0"
        },
        "sub_queries": {
          "type": "array",
          "items": { "type": "string" },
          "minItems": 1,
          "description": "Exact PubMed sub-query strings used by the BUILD search"
        },
        "updated": {
          "type": "string",
          "format": "date",
          "description": "Date the profile was last written"
        }
      }
    },
    "schedule": {
```

Then replace:

```json
        "trigger_name": {
          "type": "string",
          "description": "Name of the Claude Code remote trigger"
        }
      },
```

with:

```json
        "trigger_name": {
          "type": "string",
          "description": "Name of the Claude Code remote trigger"
        },
        "threshold": {
          "type": "integer",
          "minimum": 0,
          "description": "Minimum novel PMIDs from preflight required to run a scheduled update (default 3)"
        }
      },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_graph_schema.py -v`
Expected: 3 PASS

Also confirm the test-mode expected output still validates (regression):
`python3 scripts/validate_manifest.py tests/output/KG_Melatonin_Circadian/manifest.json; echo "exit: $?"`
Expected: exit 0 if that folder exists (it lacks `search_profile`, which is optional — must still validate). If the folder doesn't exist, skip this check.

- [ ] **Step 5: Commit**

```bash
git add schemas/graph_schema.json tests/unit/test_graph_schema.py
git commit -m "feat: add search_profile and schedule.threshold to graph schema"
```

---

### Task 4: `scripts/preflight.py`

Deterministic early-exit check: runs the persisted sub-queries against NCBI E-utilities, dedups against the PMID ledger, reports `proceed: true/false`. No MCP, no LLM, no ledger writes. Exit codes: 0 = ran, 1 = network/parse error, 2 = unusable manifest.

**Files:**
- Create: `scripts/preflight.py`
- Create: `tests/fixtures/mock_esearch.json`, `tests/unit/test_preflight.py`

- [ ] **Step 1: Create the test fixture**

Create `tests/fixtures/mock_esearch.json`:

```json
{
  "melatonin circadian rhythm molecular mechanism": {
    "count": 4,
    "idlist": ["99000001", "99000002", "99000101", "99000102"]
  },
  "melatonin sleep disorder treatment": {
    "count": 2,
    "idlist": ["99000002", "99000103"]
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_preflight.py`:

```python
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(PROJECT_ROOT, "tests", "fixtures", "mock_esearch.json")

SUB_QUERIES = [
    "melatonin circadian rhythm molecular mechanism",
    "melatonin sleep disorder treatment",
]


def make_kg(tmp_path, *, with_profile=True, known_pmids=(), schedule=None):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    manifest = {
        "kg_name": "KG_Test", "topic": "melatonin",
        "created": "2026-01-01", "updated": "2026-05-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }
    if with_profile:
        manifest["search_profile"] = {"breadth": "narrow", "sub_queries": SUB_QUERIES,
                                      "updated": "2026-05-01"}
    if schedule is not None:
        manifest["schedule"] = schedule
    (kg / "manifest.json").write_text(json.dumps(manifest))
    if known_pmids:
        ledger = {"kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-05-01",
                  "version": 1, "statistics": {},
                  "entries": {p: {"disposition": "used"} for p in known_pmids}}
        (kg / "_pmid_ledger.json").write_text(json.dumps(ledger))
    return kg


def run_preflight(kg, *extra):
    return subprocess.run(
        [sys.executable, "scripts/preflight.py", str(kg),
         "--esearch-fixture", FIXTURE, *extra],
        cwd=PROJECT_ROOT, capture_output=True, text=True)


def test_counts_novel_pmids_and_dedups_ledger(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"])
    result = run_preflight(kg)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    # union of fixture idlists = 5 unique; 2 known in ledger -> 3 novel
    assert report["novel_count"] == 3
    assert sorted(report["novel_pmids"]) == ["99000101", "99000102", "99000103"]
    assert report["proceed"] is True       # default threshold 3
    assert len(report["per_query"]) == 2


def test_threshold_blocks_proceed(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"])
    result = run_preflight(kg, "--threshold", "4")
    report = json.loads(result.stdout)
    assert report["proceed"] is False


def test_threshold_from_manifest_schedule(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"],
                 schedule={"cron": "0 8 * * 1", "trigger_name": "t",
                           "last_run": "2026-06-01T08:00:00Z", "threshold": 4})
    result = run_preflight(kg)
    report = json.loads(result.stdout)
    assert report["threshold"] == 4
    assert report["proceed"] is False
    assert report["since_date"] == "2026/06/01"  # from schedule.last_run


def test_since_falls_back_to_manifest_updated(tmp_path):
    kg = make_kg(tmp_path)
    report = json.loads(run_preflight(kg).stdout)
    assert report["since_date"] == "2026/05/01"


def test_missing_search_profile_exits_2(tmp_path):
    kg = make_kg(tmp_path, with_profile=False)
    result = run_preflight(kg)
    assert result.returncode == 2
    assert "search_profile" in result.stderr


def test_log_flag_appends_preflight_entry(tmp_path):
    kg = make_kg(tmp_path)
    result = run_preflight(kg, "--log")
    assert result.returncode == 0, result.stderr
    log = (kg / "_log.md").read_text()
    assert "preflight |" in log
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_preflight.py -v`
Expected: all FAIL (`No such file or directory: scripts/preflight.py` style errors via returncode 2 mismatch / FileNotFoundError).

- [ ] **Step 4: Implement `scripts/preflight.py`**

```python
#!/usr/bin/env python3
"""Deterministic pre-check for scheduled KG updates.

Runs the sub-queries persisted in manifest.json's search_profile against
NCBI E-utilities (esearch), dedups the returned PMIDs against the PMID
ledger, and reports whether enough novel literature exists to justify a
full /build-kg UPDATE run. No MCP, no LLM, no ledger writes.

Usage:
    python3 scripts/preflight.py <kg_folder> [--threshold N] [--since YYYY-MM-DD]
                                 [--log] [--esearch-fixture FILE]

Exit codes:
    0  ran successfully (read "proceed" from the JSON on stdout)
    1  network / parse error talking to E-utilities
    2  unusable manifest (missing manifest.json or search_profile)

Set NCBI_API_KEY in the environment to lift the rate limit from 3 to 10 req/s.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from append_log import append_entry

EUTILS_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
# Mirrors the per-tier max_results table in build-kg.md Phase 1b Step 0.
RETMAX_BY_BREADTH = {"narrow": 10, "medium": 20, "broad": 30}
DEFAULT_THRESHOLD = 3


def load_known_pmids(kg_folder: str) -> set[str]:
    """All PMIDs in the ledger, any disposition. Empty set if no ledger."""
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    return set(ledger.get("entries", {}).keys())


def resolve_since(manifest: dict, override: str | None) -> str:
    """Resolve the search window start as YYYY/MM/DD (E-utilities format)."""
    if override:
        return override.replace("-", "/")
    last_run = (manifest.get("schedule") or {}).get("last_run")
    if last_run:
        return last_run[:10].replace("-", "/")
    return manifest["updated"].replace("-", "/")


def esearch(query: str, since: str, retmax: int, api_key: str | None) -> dict:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "json",
        "datetype": "edat",
        "mindate": since,
        "maxdate": date.today().strftime("%Y/%m/%d"),
    }
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ESEARCH + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    result = data.get("esearchresult", {})
    return {"count": int(result.get("count", 0)),
            "idlist": [str(p) for p in result.get("idlist", [])]}


def main():
    parser = argparse.ArgumentParser(
        description="Deterministic preflight check for scheduled KG updates.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--threshold", type=int, default=None,
                        help="Minimum novel PMIDs to recommend a full run "
                             "(default: schedule.threshold in manifest, else 3)")
    parser.add_argument("--since", default=None,
                        help="Override the window start (YYYY-MM-DD); default derives "
                             "from schedule.last_run, then manifest.updated")
    parser.add_argument("--log", action="store_true",
                        help="Append a preflight entry to the KG's _log.md")
    parser.add_argument("--esearch-fixture", default=None,
                        help="JSON file mapping query -> {count, idlist}; replaces "
                             "live E-utilities calls (used by tests)")
    args = parser.parse_args()

    manifest_path = os.path.join(args.kg_folder, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Error: manifest.json not found in {args.kg_folder}", file=sys.stderr)
        sys.exit(2)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    profile = manifest.get("search_profile")
    if not isinstance(profile, dict) or not profile.get("sub_queries"):
        print("Error: manifest has no search_profile — run /build-kg once to "
              "backfill it; preflight will work from the next run on.", file=sys.stderr)
        sys.exit(2)

    threshold = args.threshold
    if threshold is None:
        threshold = (manifest.get("schedule") or {}).get("threshold", DEFAULT_THRESHOLD)

    since = resolve_since(manifest, args.since)
    retmax = RETMAX_BY_BREADTH.get(profile.get("breadth", "medium"), 20)
    api_key = os.environ.get("NCBI_API_KEY")

    fixture = None
    if args.esearch_fixture:
        with open(args.esearch_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)

    per_query = []
    all_pmids: set[str] = set()
    for i, query in enumerate(profile["sub_queries"]):
        if fixture is not None:
            result = fixture.get(query, {"count": 0, "idlist": []})
        else:
            if i > 0:
                time.sleep(0.11 if api_key else 0.34)  # NCBI rate etiquette
            try:
                result = esearch(query, since, retmax, api_key)
            except Exception as e:
                print(f"Error: esearch failed for {query!r}: {e}", file=sys.stderr)
                sys.exit(1)
        all_pmids.update(str(p) for p in result.get("idlist", []))
        per_query.append({"query": query,
                          "total_hits": result.get("count", 0),
                          "returned": len(result.get("idlist", []))})

    novel = sorted(all_pmids - load_known_pmids(args.kg_folder))

    report = {
        "kg": os.path.basename(os.path.abspath(args.kg_folder)),
        "since_date": since,
        "threshold": threshold,
        "per_query": per_query,
        "novel_count": len(novel),
        "novel_pmids": novel,
        "proceed": len(novel) >= threshold,
    }
    json.dump(report, sys.stdout, indent=2)
    print()

    if args.log:
        decision = ("proceeding with update" if report["proceed"]
                    else "below threshold, skipping update")
        append_entry(args.kg_folder, "preflight",
                     f"{len(novel)} novel PMIDs since {since} "
                     f"(threshold {threshold}) — {decision}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_preflight.py -v`
Expected: 6 PASS

- [ ] **Step 6: Live smoke test (one real E-utilities call)**

Build a throwaway KG manifest and hit the live API once:

```bash
mkdir -p /tmp/kg_preflight_smoke && cat > /tmp/kg_preflight_smoke/manifest.json <<'EOF'
{"kg_name": "KG_Smoke", "topic": "melatonin", "created": "2026-01-01",
 "updated": "2026-05-01", "version": 1, "nodes": [], "edges": [],
 "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                "evaluation_passed": 0, "evaluation_failed": 0},
 "search_profile": {"breadth": "narrow",
                    "sub_queries": ["melatonin circadian rhythm"],
                    "updated": "2026-05-01"}}
EOF
python3 scripts/preflight.py /tmp/kg_preflight_smoke
```

Expected: JSON report on stdout with `novel_count` > 0 and `proceed: true` (melatonin publishes weekly; no ledger means all returned PMIDs are novel). If the network is unavailable, expect exit 1 with a clear stderr message — that also confirms the error path.

- [ ] **Step 7: Commit**

```bash
git add scripts/preflight.py tests/fixtures/mock_esearch.json tests/unit/test_preflight.py
git commit -m "feat: add deterministic preflight script for scheduled KG updates"
```

---

### Task 5: `scripts/cost_report.py`

Parses Claude Code transcript JSONL (assistant messages carry `message.usage` and `message.model`), sums tokens per model (dedup streamed partials by message id, last wins), prices them, and appends to `_cost_log.jsonl`. Three modes: `--hook` (Stop-hook stdin), `--summary`, and a direct `<transcript>` mode for tests/ad-hoc use.

**Files:**
- Create: `scripts/cost_report.py`
- Create: `tests/fixtures/mock_transcript.jsonl`, `tests/unit/test_cost_report.py`

- [ ] **Step 1: Create the fixture transcript**

Create `tests/fixtures/mock_transcript.jsonl` with exactly these 6 lines (line 3 repeats msg_01 with higher output_tokens — the dedup must keep the later one; line 5 is deliberately malformed):

```
{"type":"user","sessionId":"sess-1","message":{"role":"user","content":"hi"}}
{"type":"assistant","sessionId":"sess-1","message":{"id":"msg_01","model":"claude-opus-4-8","usage":{"input_tokens":1000,"output_tokens":50,"cache_read_input_tokens":2000,"cache_creation_input_tokens":500}}}
{"type":"assistant","sessionId":"sess-1","message":{"id":"msg_01","model":"claude-opus-4-8","usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":2000,"cache_creation_input_tokens":500}}}
{"type":"assistant","sessionId":"sess-1","message":{"id":"msg_02","model":"claude-haiku-4-5-20251001","usage":{"input_tokens":500,"output_tokens":100,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
this line is not json
{"type":"system","sessionId":"sess-1","content":"reminder"}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_cost_report.py`:

```python
import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(PROJECT_ROOT, "tests", "fixtures", "mock_transcript.jsonl")

# opus 4.8: (1000*5 + 200*25 + 2000*0.5 + 500*6.25) / 1e6 = 0.014125
# haiku 4.5 (dated id, prefix-matched): (500*1 + 100*5) / 1e6 = 0.001
EXPECTED_COST = 0.014125 + 0.001


def test_direct_mode_sums_dedups_and_prices():
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", FIXTURE, "--session-id", "sess-1"],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)
    opus = entry["models"]["claude-opus-4-8"]
    assert opus["output"] == 200          # dedup by message id, last wins
    assert opus["input"] == 1000
    assert opus["cache_read"] == 2000 and opus["cache_write"] == 500
    haiku = entry["models"]["claude-haiku-4-5-20251001"]
    assert haiku["input"] == 500 and haiku["output"] == 100
    assert entry["est_cost_usd"] == pytest.approx(EXPECTED_COST, abs=1e-4)


def test_hook_mode_appends_to_log_and_never_fails(tmp_path):
    log_file = tmp_path / "_cost_log.jsonl"
    payload = json.dumps({"session_id": "sess-1", "transcript_path": FIXTURE,
                          "hook_event_name": "Stop"})
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--hook",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, input=payload, capture_output=True, text=True)
    assert result.returncode == 0
    entry = json.loads(log_file.read_text().strip())
    assert entry["session_id"] == "sess-1"
    assert entry["est_cost_usd"] == pytest.approx(EXPECTED_COST, abs=1e-4)

    # garbage stdin must not break the hook
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--hook",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, input="not json", capture_output=True, text=True)
    assert result.returncode == 0


def test_summary_mode_takes_last_entry_per_session(tmp_path):
    log_file = tmp_path / "_cost_log.jsonl"
    log_file.write_text(
        json.dumps({"session_id": "s1", "ts": "2026-06-09T10:00:00Z",
                    "models": {}, "est_cost_usd": 1.0}) + "\n" +
        json.dumps({"session_id": "s1", "ts": "2026-06-09T11:00:00Z",
                    "models": {}, "est_cost_usd": 2.5}) + "\n")
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--summary",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "2.5" in result.stdout
    assert result.stdout.count("s1") == 1   # one row per session


def test_unknown_model_is_flagged_not_priced(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(
        {"type": "assistant", "sessionId": "x",
         "message": {"id": "m1", "model": "future-model-9",
                     "usage": {"input_tokens": 10, "output_tokens": 10}}}) + "\n")
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", str(transcript)],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    entry = json.loads(result.stdout)
    assert entry["est_cost_usd"] == 0
    assert entry["unpriced_models"] == ["future-model-9"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_cost_report.py -v`
Expected: all FAIL (script missing).

- [ ] **Step 4: Implement `scripts/cost_report.py`**

```python
#!/usr/bin/env python3
"""Token and cost accounting from Claude Code transcript JSONL files.

Modes:
    --hook                read a Stop-hook JSON payload from stdin
                          ({session_id, transcript_path}), compute totals,
                          append one JSON line to the cost log. NEVER fails
                          (exit 0 always) — a hook must not disrupt sessions.
    --summary [--last N]  print a table of the latest entry per session.
    <transcript> [--session-id S]
                          direct mode: print the computed entry JSON to stdout
                          without writing the log (tests / ad-hoc use).

Assistant transcript lines carry message.usage (input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens) and message.model.
Streamed partials repeat the same message.id with growing usage — we dedupe
by id, last occurrence wins. Sibling agent-*.jsonl files in the transcript
directory whose first line carries the same sessionId are summed in too
(subagent / evaluation-worker usage).
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

# USD per million tokens. KEEP CURRENT with platform.claude.com pricing.
# cache_write assumes the default 5-minute TTL (1.25x input);
# cache_read is 0.1x input. Override with --price-file.
PRICES = {
    "claude-fable-5":   {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 1.0},
    "claude-opus-4-8":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-opus-4-7":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-opus-4-6":  {"input": 5.0,  "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
    "claude-haiku-4-5": {"input": 1.0,  "output": 5.0,  "cache_write": 1.25, "cache_read": 0.1},
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG = os.path.join(REPO_ROOT, "_cost_log.jsonl")


def price_for(model: str, prices: dict) -> dict | None:
    """Longest-prefix match so dated/suffixed IDs (claude-haiku-4-5-20251001,
    claude-fable-5[1m]) resolve to their base price row."""
    best = None
    for key in prices:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return prices[best] if best else None


def parse_transcript(path: str) -> dict[str, dict]:
    """Sum usage per model for one transcript file."""
    by_msg: dict[str, tuple[str, dict]] = {}
    anon = 0
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError:
        return {}
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            msg_id = msg.get("id")
            if not msg_id:
                anon += 1
                msg_id = f"_anon_{anon}"
            by_msg[msg_id] = (msg.get("model", "unknown"), usage)

    totals: dict[str, dict] = {}
    for model, usage in by_msg.values():
        t = totals.setdefault(model, {"input": 0, "output": 0,
                                      "cache_read": 0, "cache_write": 0})
        t["input"] += usage.get("input_tokens") or 0
        t["output"] += usage.get("output_tokens") or 0
        t["cache_read"] += usage.get("cache_read_input_tokens") or 0
        t["cache_write"] += usage.get("cache_creation_input_tokens") or 0
    return totals


def merge_totals(into: dict, frm: dict) -> None:
    for model, t in frm.items():
        dest = into.setdefault(model, {"input": 0, "output": 0,
                                       "cache_read": 0, "cache_write": 0})
        for k in dest:
            dest[k] += t[k]


def find_agent_transcripts(transcript_path: str, session_id: str) -> list[str]:
    """Sibling agent-*.jsonl files whose first line carries this sessionId."""
    if not session_id:
        return []
    out = []
    pattern = os.path.join(os.path.dirname(os.path.abspath(transcript_path)),
                           "agent-*.jsonl")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                first = json.loads(fh.readline())
        except (OSError, json.JSONDecodeError):
            continue
        if first.get("sessionId") == session_id:
            out.append(path)
    return out


def build_entry(transcript: str, session_id: str, prices: dict) -> dict:
    totals = parse_transcript(transcript)
    for agent_path in find_agent_transcripts(transcript, session_id):
        merge_totals(totals, parse_transcript(agent_path))

    cost = 0.0
    unpriced = []
    for model, t in totals.items():
        p = price_for(model, prices)
        if p is None:
            unpriced.append(model)
            continue
        cost += (t["input"] * p["input"] + t["output"] * p["output"]
                 + t["cache_read"] * p["cache_read"]
                 + t["cache_write"] * p["cache_write"]) / 1_000_000

    entry = {
        "session_id": session_id,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transcript": transcript,
        "models": totals,
        "est_cost_usd": round(cost, 4),
    }
    if unpriced:
        entry["unpriced_models"] = sorted(unpriced)
    return entry


def run_hook(log_file: str, prices: dict) -> int:
    try:
        payload = json.load(sys.stdin)
        transcript = payload.get("transcript_path", "")
        session_id = payload.get("session_id", "")
        if not transcript or not os.path.exists(transcript):
            return 0
        entry = build_entry(transcript, session_id, prices)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # a hook must never disrupt the session
    return 0


def run_summary(log_file: str, last_n: int) -> int:
    if not os.path.exists(log_file):
        print("No cost log found.", file=sys.stderr)
        return 0
    latest: dict[str, dict] = {}
    with open(log_file, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = entry.get("session_id", "?")
            latest[sid] = entry  # totals are cumulative; last line is authoritative
    rows = sorted(latest.values(), key=lambda e: e.get("ts", ""))[-last_n:]
    print(f"{'timestamp':<22} {'session':<38} {'input':>10} {'output':>9} "
          f"{'cache_rd':>10} {'cache_wr':>9} {'est_usd':>8}")
    for e in rows:
        t = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for m in (e.get("models") or {}).values():
            for k in t:
                t[k] += m.get(k, 0)
        print(f"{e.get('ts', '?'):<22} {e.get('session_id', '?'):<38} "
              f"{t['input']:>10} {t['output']:>9} {t['cache_read']:>10} "
              f"{t['cache_write']:>9} {e.get('est_cost_usd', 0):>8.4f}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Token/cost accounting from Claude Code transcripts.")
    parser.add_argument("transcript", nargs="?", default=None,
                        help="Transcript JSONL path (direct mode)")
    parser.add_argument("--hook", action="store_true",
                        help="Stop-hook mode: read payload JSON from stdin, append to log")
    parser.add_argument("--summary", action="store_true",
                        help="Print the latest entry per session from the cost log")
    parser.add_argument("--last", type=int, default=20,
                        help="Rows to show in --summary (default 20)")
    parser.add_argument("--session-id", default="",
                        help="Session id for direct mode (enables agent-file discovery)")
    parser.add_argument("--log-file", default=DEFAULT_LOG,
                        help=f"Cost log path (default {DEFAULT_LOG})")
    parser.add_argument("--price-file", default=None,
                        help="JSON file overriding the built-in price table")
    args = parser.parse_args()

    prices = PRICES
    if args.price_file:
        with open(args.price_file, "r", encoding="utf-8") as fh:
            prices = json.load(fh)

    if args.hook:
        sys.exit(run_hook(args.log_file, prices))
    if args.summary:
        sys.exit(run_summary(args.log_file, args.last))
    if not args.transcript:
        parser.error("provide a transcript path, or use --hook / --summary")
    entry = build_entry(args.transcript, args.session_id, prices)
    json.dump(entry, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_cost_report.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/cost_report.py tests/fixtures/mock_transcript.jsonl tests/unit/test_cost_report.py
git commit -m "feat: add cost_report script for per-session token accounting"
```

---

### Task 6: Stop hook registration and gitignore

**Files:**
- Create: `.claude/settings.json` (does not currently exist — only `settings.local.json` does; do NOT touch `settings.local.json`)
- Modify: `.gitignore`

- [ ] **Step 1: Create `.claude/settings.json`**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/cost_report.py --hook"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Append to `.gitignore`**

Add this line to the end of `.gitignore`:

```
_cost_log.jsonl
```

- [ ] **Step 3: Verify the hook command works end-to-end**

Simulate a Stop-hook firing (uses the test fixture as the transcript, writes to a temp log to avoid polluting the real one):

```bash
echo '{"session_id":"hook-smoke","transcript_path":"tests/fixtures/mock_transcript.jsonl"}' \
  | python3 scripts/cost_report.py --hook --log-file /tmp/cost_smoke.jsonl \
  && tail -1 /tmp/cost_smoke.jsonl
```

Expected: exit 0 and one JSON line containing `"session_id": "hook-smoke"` and a nonzero `est_cost_usd`.

Note: the real hook takes effect for NEW sessions in this project once `settings.json` exists; from then on every session appends to `_cost_log.jsonl` at the repo's `libririan/` root.

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json .gitignore
git commit -m "feat: register Stop hook for per-session cost logging"
```

---

### Task 7: `build-kg.md` — search_profile, manifest-only UPDATE, last_run stamp

All edits are exact-string replacements in `.claude/commands/build-kg.md`. After each edit, verify with the grep shown.

**Files:**
- Modify: `.claude/commands/build-kg.md`

- [ ] **Step 1: Persist `search_profile` at BUILD manifest write (Phase 2 BUILD step 9)**

Replace:

```
9. **Write manifest.json**: Create the Tier 1 index with all nodes, edges, summaries, keywords, and statistics. Follow the schema at `schemas/graph_schema.json`. The `summary` field should be exactly one sentence. The `keywords` field should contain 3-8 search terms that would help match this node to future queries.
```

with:

```
9. **Write manifest.json**: Create the Tier 1 index with all nodes, edges, summaries, keywords, and statistics. Follow the schema at `schemas/graph_schema.json`. The `summary` field should be exactly one sentence. The `keywords` field should contain 3-8 search terms that would help match this node to future queries. Also write the `search_profile` field: `{"breadth": "<tier from Phase 1b Step 0>", "sub_queries": [<the exact sub-query strings used in Phase 1b Step 1>], "updated": "<today>"}` — it is consumed by `scripts/preflight.py` (scheduled-run early exit) and reused by UPDATE-mode Recent-track searches. In test mode, record the two fixed test sub-queries.
```

Verify: `grep -c "search_profile" .claude/commands/build-kg.md` → at least 1.

- [ ] **Step 2: UPDATE Recent track reads the persisted profile (Phase 1b UPDATE Step 4)**

Replace:

```
**Step 4: Recent track.** Use the same facet decomposition as the original BUILD. Fire all sub-query `search_articles` calls **in parallel** (batch up to 5 concurrent calls). For each sub-query, call `search_articles` with `date_from` = `since_date`, `datetype: "edat"`, and `max_results` per tier.
```

with:

```
**Step 4: Recent track.** Use the sub-queries persisted in `manifest.json` → `search_profile.sub_queries`. (Legacy KG without `search_profile`: re-derive the facet decomposition as in BUILD Step 1, then persist it in Phase 2 UPDATE Step 5.) Fire all sub-query `search_articles` calls **in parallel** (batch up to 5 concurrent calls). For each sub-query, call `search_articles` with `date_from` = `since_date`, `datetype: "edat"`, and `max_results` per tier.
```

- [ ] **Step 3: Weak-spot scan is manifest-only (Phase 1b UPDATE Step 2)**

Replace:

```
**Step 2: Identify weak spots.** Scan the existing KG for gap-fill targets:
```

with:

```
**Step 2: Identify weak spots.** Scan the node entries in `manifest.json` — do NOT read node `.md` files; the manifest carries `pubmed_ids`, `evaluation_status`, `quarantined`, and `tags` — for gap-fill targets:
```

- [ ] **Step 4: Manifest-only graph loading (Phase 2 UPDATE step 1)**

Replace:

```
1. **Load existing graph**: Read `manifest.json` and all node `.md` files listed in it.
```

with:

```
1. **Load the manifest only**: Read `manifest.json`. Do NOT read node `.md` files at this stage — the per-node `summary`, `keywords`, `tags`, `pubmed_ids`, `evaluation_status`, and `evidence_tier` in the manifest are sufficient for all routing decisions. Node files are opened later, one at a time, only for nodes actually selected for modification (Step 4). This keeps UPDATE context cost proportional to the week's changes instead of total KG size.
```

- [ ] **Step 5: Routing via manifest summaries (Phase 2 UPDATE step 3)**

Replace:

```
3. **Compare new material against existing nodes**:
   - Identify existing nodes that need updated/additional references
   - Identify entirely new knowledge that warrants new nodes
   - Identify relationships that should be added or revised
```

with:

```
3. **Compare new material against existing nodes** (using manifest summaries and keywords only):
   - Identify existing nodes that need updated/additional references — match each new article against the manifest node summaries and keywords. For fragments that are hard to place, run `python3 scripts/search_nodes.py "<fragment key terms>" {KG_FOLDER}/manifest.json --top 5 --compact` to rank candidate nodes deterministically.
   - Identify entirely new knowledge that warrants new nodes
   - Identify relationships that should be added or revised — design relationships for new nodes from the manifest summaries; do not read other node files for this
```

- [ ] **Step 6: Read node files only at edit time (Phase 2 UPDATE step 4)**

Replace:

```
4. **Apply changes**:
   - For existing nodes gaining new references: append new PMIDs and update the Detail/Evidence sections
```

with:

```
4. **Apply changes**:
   - For existing nodes gaining new references: read the node's full file first (`python3 scripts/parse_node.py {node_path}` or the Read tool) — only now, immediately before editing — then append new PMIDs and update the Detail/Evidence sections
```

- [ ] **Step 7: Refresh/backfill search_profile on UPDATE (Phase 2 UPDATE step 5)**

Replace:

```
5. **Update manifest.json**: Merge new entries, increment `version`, update `statistics`
```

with:

```
5. **Update manifest.json**: Merge new entries, increment `version`, update `statistics`. Refresh `search_profile.updated`; if `search_profile` was absent (legacy KG), write it now from the sub-queries used in this run's Recent track.
```

- [ ] **Step 8: Stamp `schedule.last_run` in Phase 4**

Replace:

```
1c. Validate the PMID ledger:
   ```
   python3 scripts/pmid_ledger.py validate {KG_FOLDER}
   ```
   If validation fails, investigate and fix. Warnings about ledger-manifest drift should be addressed by running `python3 scripts/pmid_ledger.py sync {KG_FOLDER}`.
```

with:

```
1c. Validate the PMID ledger:
   ```
   python3 scripts/pmid_ledger.py validate {KG_FOLDER}
   ```
   If validation fails, investigate and fix. Warnings about ledger-manifest drift should be addressed by running `python3 scripts/pmid_ledger.py sync {KG_FOLDER}`.

1d. Stamp the schedule timestamp (writes `schedule.last_run`; silently a no-op if this KG has no `schedule` block):
   ```
   python3 scripts/update_manifest_stats.py {KG_FOLDER} --stamp-last-run
   ```
```

- [ ] **Step 9: Verify and commit**

Run: `grep -n "search_profile\|stamp-last-run\|Load the manifest only" .claude/commands/build-kg.md`
Expected: hits in Phase 1b Step 4, Phase 2 step 9 (BUILD), Phase 2 UPDATE steps 1/3/5, Phase 4 step 1d.

```bash
git add .claude/commands/build-kg.md
git commit -m "feat: persist search_profile, manifest-only UPDATE loading, stamp last_run in build-kg"
```

---

### Task 8: Evaluator on Haiku with strong-model escalation

Workers run on Haiku with remediation disabled; any fail verdict is re-checked (and remediated/quarantined) by one session-model worker. `merge_eval_chunks.py` already merges chunks in **numeric** chunk-id order with later-wins-per-node_id semantics (`scripts/merge_eval_chunks.py:23-26, 68-88`), so the escalation chunk just needs the highest numeric chunk id.

**Files:**
- Modify: `.claude/commands/evaluate-kg.md`
- Modify: `.claude/commands/evaluate-kg-worker.md`

- [ ] **Step 1: Worker — document the `--no-remediate` flag**

In `.claude/commands/evaluate-kg-worker.md`, replace:

```
- **--test** (optional flag): Run in test mode using mock PubMed fixtures. When set, read article metadata and full text from `tests/fixtures/` instead of calling MCP tools or curl. See "Test Mode" sections in Steps E1, E2, and E4.
```

with:

```
- **--test** (optional flag): Run in test mode using mock PubMed fixtures. When set, read article metadata and full text from `tests/fixtures/` instead of calling MCP tools or curl. See "Test Mode" sections in Steps E1, E2, and E4.
- **--no-remediate** (optional flag): Skip Step E4 entirely. Failed nodes are written to the results array with `overall_status: "failed"` and `notes: "pending escalation"`, and their node files are NOT modified (no quarantine, no frontmatter update). The orchestrator passes this to cheap-model workers so that remediation and quarantine decisions are made only by a stronger escalation worker.
```

- [ ] **Step 2: Worker — gate Step E4**

In `.claude/commands/evaluate-kg-worker.md`, replace:

```
## Step E4: Remediation
```

with:

```
## Step E4: Remediation

**If `--no-remediate` was passed, skip this entire step.** Record each failed node in the Step E5 results array with `overall_status: "failed"` and `notes: "pending escalation"`. Do NOT search for replacement references, do NOT edit the node file, and do NOT set `quarantined` — the orchestrator escalates failed nodes to a stronger worker that runs full remediation.
```

- [ ] **Step 3: Worker — gate Step E6 frontmatter updates for failed nodes**

In `.claude/commands/evaluate-kg-worker.md`, replace:

```
**Always** (both chunk and direct modes):
Use the frontmatter update script to set evaluation results on each node. For each evaluated node, run:
```

with:

```
**Always** (both chunk and direct modes):
Use the frontmatter update script to set evaluation results on each node. **Exception: if `--no-remediate` was passed, only update nodes that passed — leave failed nodes' frontmatter untouched (the escalation worker sets their final status).** For each node to update, run:
```

- [ ] **Step 4: Orchestrator — direct path becomes a single Haiku worker**

In `.claude/commands/evaluate-kg.md`, replace:

```
**If N <= 5** — Direct evaluation (no parallelization overhead):
- Invoke `/evaluate-kg-worker` with the same `--kg`, `--nodes`, and `--sources` arguments. Do NOT pass `--chunk-id`. **If `--test` was passed, include `--test` in the worker invocation.**
- The worker handles everything: evaluation, writing `_evaluation_log.json`, updating node files, and updating manifest statistics.
- Skip to Step 6 (Report) after the worker completes.
```

with:

```
**If N <= 5** — Single worker (still spawned via the Agent tool so it runs on the cheaper model):
- Use the Agent tool to spawn ONE worker with the Agent tool's `model` parameter set to `haiku`, using the Step 3a prompt template with `--chunk-id 1` and `--no-remediate`. **If `--test` was passed, include `--test`.**
- After it completes, verify `_eval_chunk_1.json` exists (retry once as in Step 3b), then continue at Step 3.5 (escalation) and Steps 4-6 exactly as in parallel mode — the orchestrator owns merging, ledger sync, and manifest statistics in both paths.
```

- [ ] **Step 5: Orchestrator — Haiku model + `--no-remediate` in the wave template (Step 3a)**

In `.claude/commands/evaluate-kg.md`, replace:

```
For every chunk in the current wave, use the **Agent tool** to spawn a worker agent. Issue **all Agent calls for the wave in a single response** to enable parallel execution.

Each Agent call should use this prompt template (fill in the actual values). **If `--test` was passed, include `--test` in the worker invocation:**

```
You are a Knowledge Graph evaluation worker. Invoke the /evaluate-kg-worker skill with these exact arguments:

/evaluate-kg-worker --kg {KG_FOLDER} --nodes {CHUNK_NODE_IDS} --sources {SOURCES} --chunk-id {N} {--test if test mode}
```

with:

```
For every chunk in the current wave, use the **Agent tool** to spawn a worker agent **with the Agent tool's `model` parameter set to `haiku`** — per-PMID verification is high-volume, well-bounded judgment work suited to the cheaper model, and every fail verdict gets a stronger-model second opinion in Step 3.5. Issue **all Agent calls for the wave in a single response** to enable parallel execution.

Each Agent call should use this prompt template (fill in the actual values). **If `--test` was passed, include `--test` in the worker invocation:**

```
You are a Knowledge Graph evaluation worker. Invoke the /evaluate-kg-worker skill with these exact arguments:

/evaluate-kg-worker --kg {KG_FOLDER} --nodes {CHUNK_NODE_IDS} --sources {SOURCES} --chunk-id {N} --no-remediate {--test if test mode}
```

- [ ] **Step 6: Orchestrator — insert Step 3.5 (escalation pass)**

In `.claude/commands/evaluate-kg.md`, replace:

```
## Step 4: Merge Results
```

with:

```
## Step 3.5: Escalation Pass (strong-model second opinion on fails)

Haiku workers run with `--no-remediate`, so a failed node has NOT been remediated or quarantined yet — it is only recorded as failed in its chunk file. A wrongful quarantine is the worst failure mode, so fails get a second opinion:

1. Read all `_eval_chunk_*.json` files and collect the node IDs with `overall_status: "failed"`.
2. If there are none, continue to Step 4.
3. Otherwise, spawn ONE worker via the Agent tool **without a `model` override** (it inherits the session model), using the Step 3a prompt template with:
   - `--nodes` = the failed node IDs (comma-separated)
   - `--chunk-id` = (highest chunk ID used so far) + 1
   - NO `--no-remediate` flag — this worker performs full Step E4 remediation and quarantine
   - `--test` if test mode
4. After it completes, verify its chunk file exists (retry once, as in Step 3b). `merge_eval_chunks.py` merges chunks in numeric chunk-id order and later chunks win per node_id, so the escalation verdicts overwrite the Haiku fail verdicts at merge time.

Net effect: passes are accepted from the cheap model; every fail — and all remediation and quarantine decisions — is confirmed by the stronger model.

---

## Step 4: Merge Results
```

- [ ] **Step 7: Verify and commit**

Run: `grep -n "no-remediate\|haiku\|Escalation" .claude/commands/evaluate-kg.md .claude/commands/evaluate-kg-worker.md`
Expected: hits in orchestrator Steps 1, 3a, 3.5 and worker Input/E4/E6 sections.

```bash
git add .claude/commands/evaluate-kg.md .claude/commands/evaluate-kg-worker.md
git commit -m "feat: run eval workers on haiku with strong-model escalation on fails"
```

---

### Task 9: `schedule-kg.md` — preflight-gated scheduled prompt and `--threshold`

**Files:**
- Modify: `.claude/commands/schedule-kg.md`

- [ ] **Step 1: Add `--threshold` to the create-schedule inputs**

Replace:

```
- **topic** (required): The research topic
- **--cron** (optional): Cron expression. Defaults to `0 8 * * 1` (every Monday 8am)
- **--output** (optional): Target KG folder name
```

with:

```
- **topic** (required): The research topic
- **--cron** (optional): Cron expression. Defaults to `0 8 * * 1` (every Monday 8am)
- **--output** (optional): Target KG folder name
- **--threshold <N>** (optional): Minimum novel PMIDs (per the preflight check) required to run a scheduled update. Defaults to 3. Recorded as `schedule.threshold` in the manifest and substituted into the scheduled prompt.
```

- [ ] **Step 2: Replace the scheduled-agent prompt template**

Replace:

```
Run /build-kg "<topic>" --output <KG_FolderName>

This is a scheduled update run. The KG already exists — run in UPDATE mode.
UPDATE mode automatically filters PubMed searches to articles added since the last run (via schedule.last_run in manifest.json).
Focus on finding new research that adds to or revises existing knowledge nodes.
After the build completes, update the schedule.last_run timestamp in manifest.json.
```

with:

```
This is a scheduled KG update run for <KG_FolderName>.

1. First run the deterministic preflight check (no MCP tools, no KG loading):
   python3 scripts/preflight.py <KG_FolderName> --threshold <threshold> --log
2. If the JSON output has "proceed": false, report exactly one line — "Quiet week: {novel_count} novel PMIDs since {since_date}, below threshold {threshold} — skipped update." — and STOP. Do not load the KG and do not call any MCP tools.
3. If preflight exits non-zero (network error, or a legacy manifest without search_profile), fall through to step 4 anyway — a wasted full run is better than a silently skipped update.
4. Otherwise run: /build-kg "<topic>" --output <KG_FolderName>
   The KG already exists, so this runs in UPDATE mode: it derives its date window from schedule.last_run and stamps schedule.last_run when it finishes (Phase 4 step 1d). Focus on new research that adds to or revises existing knowledge nodes.
```

- [ ] **Step 3: Record the threshold in the manifest schedule block (Step 3)**

Replace:

```json
{
  "schedule": {
    "cron": "0 8 * * 1",
    "last_run": null,
    "trigger_name": "kg-update-<slugified-topic>"
  }
}
```

with:

```json
{
  "schedule": {
    "cron": "0 8 * * 1",
    "last_run": null,
    "trigger_name": "kg-update-<slugified-topic>",
    "threshold": 3
  }
}
```

- [ ] **Step 4: Verify and commit**

Run: `grep -n "preflight\|threshold" .claude/commands/schedule-kg.md`
Expected: hits in the input list, the prompt template, and the manifest snippet. Also verify the stale instruction is gone: `grep -c "update the schedule.last_run timestamp" .claude/commands/schedule-kg.md` → `0`.

```bash
git add .claude/commands/schedule-kg.md
git commit -m "feat: gate scheduled KG updates behind preflight with configurable threshold"
```

---

### Task 10: `validate_test_output.py` — assert `search_profile` in test manifests

**Files:**
- Modify: `scripts/validate_test_output.py`

- [ ] **Step 1: Add the check function**

In `scripts/validate_test_output.py`, insert after the end of `check_all_nodes_have_references` (after its closing `)` and before `def check_ledger_integrity`):

```python
def check_search_profile(manifest: dict) -> CheckResult:
    """BUILD must persist search_profile (consumed by preflight.py and UPDATE searches)."""
    profile = manifest.get("search_profile")
    if not isinstance(profile, dict):
        return CheckResult("search_profile", False, "search_profile missing from manifest")
    missing = [k for k in ("breadth", "sub_queries") if not profile.get(k)]
    if missing:
        return CheckResult("search_profile", False,
                           f"search_profile missing fields: {missing}")
    return CheckResult("search_profile", True)
```

- [ ] **Step 2: Wire it into main()**

Replace:

```python
        all_results.append(check_all_nodes_have_references(manifest))
```

with:

```python
        all_results.append(check_all_nodes_have_references(manifest))
        all_results.append(check_search_profile(manifest))
```

- [ ] **Step 3: Verify it runs (and fails on the legacy test output, if present)**

Run: `python3 scripts/validate_test_output.py --help` (or with no args) to confirm no syntax error — Expected: usage text, exit cleanly. If `tests/output/KG_Melatonin_Circadian/` exists from a pre-change run, running the validator against it should now report the `search_profile` check as FAILED — that's expected until the next `/build-kg --test` run regenerates it.

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_test_output.py
git commit -m "test: require search_profile in build-kg test output manifest"
```

---

### Task 11: Full verification

- [ ] **Step 1: Run the entire unit suite**

Run: `python3 -m pytest tests/unit -v`
Expected: all tests PASS (append_log: 3, stamp_last_run: 3, graph_schema: 3, preflight: 6, cost_report: 4 — 19 total).

- [ ] **Step 2: Cross-file consistency greps**

```bash
grep -c "preflight.py" .claude/commands/schedule-kg.md          # expect >= 1
grep -c "search_profile" .claude/commands/build-kg.md           # expect >= 4
grep -c "stamp-last-run" .claude/commands/build-kg.md           # expect 1
grep -c "no-remediate" .claude/commands/evaluate-kg.md          # expect >= 2
grep -c "no-remediate" .claude/commands/evaluate-kg-worker.md   # expect >= 3
grep -c "preflight" scripts/append_log.py                       # expect >= 1
```

- [ ] **Step 3: Commit any stragglers and report**

```bash
git status --short
```

Expected: clean (everything committed in Tasks 1-10).

- [ ] **Step 4: Flag the two human-run E2E verifications (do not run them as part of this plan)**

Report to the user that two verifications remain that consume real tokens / need a real KG:

1. **`/build-kg --test`** — exercises the full BUILD pipeline against mock fixtures, including the new `search_profile` manifest write (now asserted by `validate_test_output.py`) and the Haiku-worker + escalation evaluator path.
2. **One real weekly UPDATE run** on an existing KG, before/after comparing entries in `_cost_log.jsonl` (the Stop hook starts populating it for new sessions). Success criteria from the spec: ≥60% cost reduction on an active week; a quiet-week scheduled run that stops at preflight costs only the wrapper session.

---

## Spec coverage map

| Spec section | Tasks |
|---|---|
| Component 1: preflight script + search_profile + scheduler integration | 3, 4, 7 (steps 1-2, 7), 9 |
| Component 1: `schedule.last_run` fix | 2, 7 (step 8), 9 (step 2 removes the stale instruction) |
| Component 2: Haiku workers + escalation guard | 8 |
| Component 3: manifest-only UPDATE loading | 7 (steps 3-6) |
| Component 4: cost_report + Stop hook + gitignore | 5, 6 |
| Testing section | 1-5, 10, 11 |
| Error handling (fail-open scheduler, hook never fails) | 4 (exit codes), 5 (run_hook), 9 (prompt step 3) |
