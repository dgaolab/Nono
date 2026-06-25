# Retraction Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Periodically sweep the full cited PubMed corpus for retractions, flag retracted references, conditionally quarantine nodes that lose their last valid support, and surface findings in the run-record, digest, and log.

**Architecture:** A new deterministic script `scripts/check_retractions.py` (the `preflight.py` no-LLM/no-MCP contract) detects retractions via a PubMed esearch intersection, then mutates the ledger (`disposition: "retracted"`) and node frontmatter (`retracted: true`), routing zero-support nodes through `evaluation_status: "failed"` + `enforce_quarantine.py`. Findings flow into the run-record's new `retractions` field, the digest, and a `retraction` log op.

**Tech Stack:** Python 3.13, `pytest`, `jsonschema`; NCBI E-utilities (urllib); Markdown slash-command agents under `.claude/commands/`.

## Global Constraints

- Deterministic, no LLM, no MCP — the `preflight.py` contract. Copied from spec.
- **Never corrupt on failure:** a network/parse error exits non-zero and mutates nothing (all writes happen only after successful detection).
- Detection stays within **NCBI E-utilities** (no iCite, no second API).
- **PMIDs only** — NCT/ChEMBL references are never retraction-checked.
- Conditional quarantine: quarantine a node **only** when retraction leaves it with **zero valid support**, and only by setting `evaluation_status: "failed"` then running `enforce_quarantine.py` (preserves the linter `quarantine_drift` invariant `evaluation_status=="failed" ↔ quarantined==true`).
- Valid support of a node = count of its `pubmed_ids` entries with `verified: true` and not `retracted: true`, **plus** its `external_ids` count (NCT/ChEMBL refs still count).
- No remediation (replacement refs) — handled later by the existing `/evaluate-kg` flow.
- Run all tests from `/home/dadi/nono/libririan` with `python3 -m pytest tests/unit/ -v`.

---

## File Structure

- `schemas/pmid_ledger_schema.json` — **modify**; add `"retracted"` to the `disposition` enum.
- `scripts/pmid_ledger.py` — **modify**; add `"retracted"` to `DISPOSITIONS` and the `used → retracted` / `retracted → used` transitions.
- `tests/unit/test_pmid_ledger_retracted.py` — **create**; disposition/transition/schema tests.
- `schemas/run_record_schema.json` — **modify**; add optional `retractions` array.
- `scripts/render_digest.py` — **modify**; add a "Retractions" section.
- `tests/unit/test_run_record_schema.py` — **modify**; add retractions cases.
- `tests/unit/test_render_digest.py` — **modify**; add retractions-section cases.
- `scripts/check_retractions.py` — **create**; detection core (Task 3) + action/orchestration (Task 4).
- `scripts/append_log.py` — **modify**; add `"retraction"` op (Task 4).
- `tests/unit/test_check_retractions.py` — **create**; detection (Task 3) + sweep (Task 4) tests.
- `.claude/commands/schedule-kg.md`, `.claude/commands/build-kg.md` — **modify**; wiring prose (Task 5).

---

## Task 1: Add the "retracted" ledger disposition

Make `"retracted"` a first-class, schema-valid ledger disposition with the right transitions, so the sweep (Task 4) can mark retracted PMIDs and `pmid_ledger.py validate` accepts them.

**Files:**
- Modify: `schemas/pmid_ledger_schema.json` (the `disposition` enum)
- Modify: `scripts/pmid_ledger.py:36` (`DISPOSITIONS`) and `:39-44` (`_VALID_TRANSITIONS`)
- Test: `tests/unit/test_pmid_ledger_retracted.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `"retracted"` recognized in `pmid_ledger.DISPOSITIONS` and as a valid `used → retracted` transition; the ledger JSON Schema accepts `disposition: "retracted"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_pmid_ledger_retracted.py`:

```python
import json
import os
import sys

import jsonschema

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import pmid_ledger

SCHEMA_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "schemas", "pmid_ledger_schema.json"))


def test_retracted_in_dispositions():
    assert "retracted" in pmid_ledger.DISPOSITIONS


def test_used_to_retracted_transition_allowed():
    assert "retracted" in pmid_ledger._VALID_TRANSITIONS["used"]


def test_retracted_to_used_transition_allowed():
    # a retracted PMID can be re-validated later (recovery path)
    assert "used" in pmid_ledger._VALID_TRANSITIONS.get("retracted", set())


def test_schema_accepts_retracted_disposition():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    entry = {
        "disposition": "retracted",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_checked": "2026-06-24T00:00:00+00:00",
        "assigned_nodes": ["node_001"],
    }
    # Validate a single entry against the entries' additionalProperties subschema.
    entry_schema = schema["properties"]["entries"]["additionalProperties"]
    jsonschema.validate(entry, entry_schema)


def test_schema_rejects_unknown_disposition():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    entry_schema = schema["properties"]["entries"]["additionalProperties"]
    bad = {"disposition": "bogus", "first_seen": "2026-01-01T00:00:00+00:00",
           "last_checked": "2026-01-01T00:00:00+00:00", "assigned_nodes": []}
    try:
        jsonschema.validate(bad, entry_schema)
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_pmid_ledger_retracted.py -v`
Expected: FAIL — `"retracted" not in DISPOSITIONS` and schema accepts-retracted assertion fails.

- [ ] **Step 3: Update the ledger schema enum**

In `schemas/pmid_ledger_schema.json`, find the `disposition` property's `enum` (currently `["used", "irrelevant", "failed", "superseded"]`) and add `"retracted"`:

```json
          "enum": ["used", "irrelevant", "failed", "superseded", "retracted"],
```

- [ ] **Step 4: Update DISPOSITIONS and transitions**

In `scripts/pmid_ledger.py`, line 36:

```python
DISPOSITIONS = {"used", "irrelevant", "failed", "superseded", "retracted"}
```

And the `_VALID_TRANSITIONS` dict (lines 39-44):

```python
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "used": {"failed", "superseded", "retracted"},
    "irrelevant": {"used"},
    "failed": {"used"},        # re-found and re-verified in a later cycle
    "superseded": {"used"},    # re-assigned in a later cycle
    "retracted": {"used"},     # re-validated in a later cycle (recovery)
}
```

- [ ] **Step 5: Run tests + full suite**

Run: `python3 -m pytest tests/unit/test_pmid_ledger_retracted.py -v`
Expected: PASS (5 tests).
Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add schemas/pmid_ledger_schema.json scripts/pmid_ledger.py tests/unit/test_pmid_ledger_retracted.py
git commit -m "feat: add 'retracted' ledger disposition and transitions"
```

---

## Task 2: Run-record `retractions` field + digest section

Add the optional `retractions` array to the run-record schema and render it in the digest, so a sweep's findings surface in the per-run digest.

**Files:**
- Modify: `schemas/run_record_schema.json` (add `retractions` to `properties`)
- Modify: `scripts/render_digest.py` (add a `_retractions` helper, call it in `render`)
- Test: `tests/unit/test_run_record_schema.py` (add cases), `tests/unit/test_render_digest.py` (add cases)

**Interfaces:**
- Consumes: `render(run_record, eval_index, node_titles, stats, cost) -> str` (existing).
- Produces: run-record `retractions` array of `{pmid, nodes:[...], action}` where `action` ∈ `{"flagged", "quarantined"}`; a "## Retractions" markdown section emitted when `run_record["retractions"]` is non-empty.

- [ ] **Step 1: Write the failing schema tests**

Add to `tests/unit/test_run_record_schema.py`:

```python
def test_retractions_array_is_valid():
    rec = valid_update_record()
    rec["retractions"] = [{"pmid": "111", "nodes": ["node_003"], "action": "quarantined"}]
    jsonschema.validate(rec, load_schema())


def test_retractions_bad_action_rejected():
    rec = valid_update_record()
    rec["retractions"] = [{"pmid": "111", "nodes": ["node_003"], "action": "deleted"}]
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
```

- [ ] **Step 2: Write the failing digest tests**

Add to `tests/unit/test_render_digest.py`:

```python
def test_retractions_section_renders_when_present():
    rec = update_record()
    rec["retractions"] = [{"pmid": "99999", "nodes": ["node_003"], "action": "quarantined"}]
    out = render(rec, eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "Retractions" in out
    assert "99999" in out and "node_003" in out and "quarantined" in out


def test_no_retractions_section_when_absent():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "## Retractions" not in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py -v`
Expected: FAIL — schema lacks `retractions` constraints (bad-action test fails) and digest has no Retractions section.

- [ ] **Step 4: Add `retractions` to the run-record schema**

In `schemas/run_record_schema.json`, add to the `properties` object (it is optional — not in `required`):

```json
    "retractions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["pmid", "nodes", "action"],
        "properties": {
          "pmid": {"type": "string"},
          "nodes": {"type": "array", "items": {"type": "string"}},
          "action": {"type": "string", "enum": ["flagged", "quarantined"]}
        }
      }
    }
```

- [ ] **Step 5: Render the Retractions section**

In `scripts/render_digest.py`, add this helper next to `_failures` (above `render`):

```python
def _retractions(rr: dict) -> list[str]:
    items = rr.get("retractions") or []
    if not items:
        return []
    lines = ["## Retractions"]
    for r in items:
        nodes = ", ".join(r.get("nodes", []))
        lines.append(f"- PMID {r.get('pmid')} — {r.get('action')} — affects {nodes}")
    lines.append("")
    return lines
```

Then call it in `render`, in BOTH the `update` and `build` branches, immediately before `lines.extend(_totals(stats, cost))`:

```python
    lines.extend(_retractions(run_record))
    lines.extend(_totals(stats, cost))
```

(The `skip` branch returns early and does not show a totals/retractions body; skip-run retractions still appear because the skip path is rare and a skip digest is a one-liner — retractions on a skip week are captured in the log and ledger. Do NOT add retractions to the skip branch.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add schemas/run_record_schema.json scripts/render_digest.py tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py
git commit -m "feat: run-record retractions field + digest Retractions section"
```

---

## Task 3: Retraction detection core (`check_retractions.py`)

The deterministic detection layer: collect cited PMIDs, query PubMed for which are retracted, with a fixture hook for tests. No mutations yet.

**Files:**
- Create: `scripts/check_retractions.py`
- Test: `tests/unit/test_check_retractions.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `collect_used_pmids(kg_folder: str) -> list[str]` — sorted PMIDs whose ledger `disposition == "used"`.
  - `find_retracted(used_pmids: list[str], query_fn) -> set[str]` — chunks the PMIDs (200/chunk), calls `query_fn(chunk: list[str]) -> set[str]` (which returns the retracted subset of that chunk), unions the results.
  - `esearch_retracted(pmids: list[str], api_key: str | None) -> set[str]` — one live esearch returning the retracted subset of `pmids`.
  - CLI: `python3 scripts/check_retractions.py <kg_folder> [--esearch-fixture FILE] [--json]` printing the detected retracted PMIDs (no mutations in this task).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_check_retractions.py`:

```python
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import check_retractions

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check_retractions.py"))


def _write_ledger(kg, entries):
    (kg / "_pmid_ledger.json").write_text(json.dumps({
        "kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "entries": entries,
        "statistics": {"total": len(entries), "used": 0, "irrelevant": 0, "failed": 0, "superseded": 0},
    }), encoding="utf-8")


def test_collect_used_pmids_only_used(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "irrelevant", "first_seen": "x", "last_checked": "x", "assigned_nodes": []},
        "333": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    assert check_retractions.collect_used_pmids(str(kg)) == ["111", "333"]


def test_find_retracted_unions_chunks():
    # query_fn echoes the retracted subset; here pretend 222 and 555 are retracted
    retracted = {"222", "555"}
    def query_fn(chunk):
        return {p for p in chunk if p in retracted}
    got = check_retractions.find_retracted(["111", "222", "333", "444", "555"], query_fn)
    assert got == {"222", "555"}


def test_find_retracted_empty_when_none():
    assert check_retractions.find_retracted(["111", "222"], lambda chunk: set()) == set()


def test_cli_detection_with_fixture(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "999": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    fixture = tmp_path / "retracted.json"
    fixture.write_text(json.dumps({"retracted": ["999"]}), encoding="utf-8")
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--esearch-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["retracted_pmids"] == ["999"]
    assert out["checked_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_check_retractions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_retractions'`.

- [ ] **Step 3: Write the detection core**

Create `scripts/check_retractions.py`:

```python
#!/usr/bin/env python3
"""Deterministic retraction sweep for a KG's cited PubMed corpus.

Detects which cited PMIDs (ledger disposition "used") have been retracted, by
intersecting them with PubMed's "Retracted Publication" publication type via
NCBI esearch. No MCP, no LLM. On any network/parse error the script exits
non-zero WITHOUT mutating the ledger or node files.

Usage:
    python3 scripts/check_retractions.py <kg_folder> [--esearch-fixture FILE] [--json]

The --esearch-fixture FILE is a JSON object {"retracted": ["pmid", ...]} that
replaces live E-utilities calls (used by tests); a cited PMID is treated as
retracted iff it appears in that list.

Set NCBI_API_KEY in the environment to lift the rate limit.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EUTILS_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CHUNK_SIZE = 200
RETRACTED_PT = '"Retracted Publication"[Publication Type]'


def collect_used_pmids(kg_folder: str) -> list[str]:
    """Sorted PMIDs whose ledger disposition is 'used'."""
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    return sorted(p for p, e in ledger.get("entries", {}).items()
                  if e.get("disposition") == "used")


def esearch_retracted(pmids: list[str], api_key: str | None) -> set[str]:
    """Return the subset of `pmids` PubMed reports as retracted (one query)."""
    if not pmids:
        return set()
    term = "(" + " OR ".join(f"{p}[uid]" for p in pmids) + ") AND " + RETRACTED_PT
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": str(len(pmids))}
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ESEARCH + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    idlist = data.get("esearchresult", {}).get("idlist", [])
    return {str(p) for p in idlist}


def find_retracted(used_pmids: list[str], query_fn) -> set[str]:
    """Chunk `used_pmids` and union query_fn(chunk) across chunks."""
    retracted: set[str] = set()
    for i in range(0, len(used_pmids), CHUNK_SIZE):
        chunk = used_pmids[i:i + CHUNK_SIZE]
        retracted |= query_fn(chunk)
    return retracted


def _build_query_fn(args, api_key):
    """Return query_fn(chunk)->set, live or fixture-backed."""
    if args.esearch_fixture:
        with open(args.esearch_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        retracted_set = {str(p) for p in fixture.get("retracted", [])}
        return lambda chunk: {p for p in chunk if p in retracted_set}

    sleep = 0.11 if api_key else 0.34
    state = {"first": True}

    def live(chunk):
        if not state["first"]:
            time.sleep(sleep)  # NCBI rate etiquette between chunks
        state["first"] = False
        return esearch_retracted(chunk, api_key)
    return live


def main():
    parser = argparse.ArgumentParser(description="Deterministic retraction sweep for a KG.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--esearch-fixture", default=None,
                        help='JSON {"retracted": [pmid,...]} replacing live E-utilities (tests)')
    parser.add_argument("--json", action="store_true", help="Emit the structured summary as JSON")
    args = parser.parse_args()

    used = collect_used_pmids(args.kg_folder)
    api_key = os.environ.get("NCBI_API_KEY")
    query_fn = _build_query_fn(args, api_key)

    try:
        retracted = sorted(find_retracted(used, query_fn))
    except Exception as e:
        print(f"Error: retraction esearch failed: {e}", file=sys.stderr)
        sys.exit(1)

    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "checked_count": len(used), "retracted_pmids": retracted}
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_check_retractions.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

```bash
git add scripts/check_retractions.py tests/unit/test_check_retractions.py
git commit -m "feat: retraction detection core (esearch intersection)"
```

---

## Task 4: Retraction action — flag, conditional quarantine, log

Extend `check_retractions.py` to act on detected retractions: mark the ledger, flag node references, conditionally quarantine zero-support nodes, log a `retraction` op, and emit a `retractions` summary.

**Files:**
- Modify: `scripts/check_retractions.py` (add action functions; extend `main`)
- Modify: `scripts/append_log.py:22` (add `"retraction"` to `VALID_OPS`) and `:7` (docstring)
- Test: `tests/unit/test_check_retractions.py` (add sweep tests)

**Interfaces:**
- Consumes: `collect_used_pmids`, `find_retracted` (Task 3); `lib.frontmatter.parse`/`write`; `enforce_quarantine.py` (subprocess); `append_log.append_entry`.
- Produces:
  - `apply_retractions(kg_folder: str, retracted: set[str], swept: list[str]) -> list[dict]` — mutates ledger + nodes, runs quarantine enforcement, returns the `retractions` summary list `[{pmid, nodes, action}]` (`action` ∈ `flagged`/`quarantined`).
  - The CLI, after detection, performs the mutations and includes `retractions` in its `--json` summary.

- [ ] **Step 1: Write the failing sweep tests**

Add to `tests/unit/test_check_retractions.py`:

```python
from lib.frontmatter import parse as parse_node


def _node(kg, nid, pmids, eval_status="passed", external=0):
    pubmed = "\n".join(
        f'  - pmid: "{p}"\n    supports: "s"\n    verified: true' for p in pmids)
    ext = ""
    if external:
        ext = "external_ids:\n" + "".join(
            f'  - source: "clinicaltrials"\n    id: "NCT{n:08d}"\n' for n in range(external))
    fm = (f'id: "{nid}"\n'
          f'pubmed_ids:\n{pubmed}\n'
          f'{ext}'
          f'evaluation_status: "{eval_status}"\nquarantined: false\n')
    (kg / "nodes" / f"{nid}.md").write_text(f"---\n{fm}---\n\nbody\n", encoding="utf-8")


def _full_kg(tmp_path):
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_002"]},
        "333": {"disposition": "used", "first_seen": "x", "last_checked": "old", "assigned_nodes": ["node_002"]},
    })
    (kg / "manifest.json").write_text(json.dumps({
        "kg_name": "KG_Test", "topic": "t", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "nodes": [
            {"id": "node_001", "title": "Solo", "file": "nodes/node_001.md", "tags": ["x"],
             "summary": "s", "keywords": ["k"], "pubmed_ids": ["111"], "evaluation_status": "passed"},
            {"id": "node_002", "title": "Multi", "file": "nodes/node_002.md", "tags": ["x"],
             "summary": "s", "keywords": ["k"], "pubmed_ids": ["222", "333"], "evaluation_status": "passed"}],
        "edges": [], "statistics": {"total_nodes": 2, "total_edges": 0, "total_unique_pmids": 3,
                                    "evaluation_passed": 2, "evaluation_failed": 0}}), encoding="utf-8")
    _node(kg, "node_001", ["111"])               # single ref -> will lose all support
    _node(kg, "node_002", ["222", "333"])         # two refs -> keeps support
    return kg


def _run_sweep(kg, retracted_pmids, tmp_path):
    fixture = tmp_path / "r.json"
    fixture.write_text(json.dumps({"retracted": retracted_pmids}), encoding="utf-8")
    return subprocess.run([sys.executable, SCRIPT, str(kg), "--esearch-fixture", str(fixture), "--json"],
                          capture_output=True, text=True)


def test_single_ref_node_quarantined(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, ["111"], tmp_path)
    assert res.returncode == 0, res.stderr
    fm, _ = parse_node(str(kg / "nodes" / "node_001.md"))
    assert fm["evaluation_status"] == "failed"
    assert fm["quarantined"] is True
    ref = next(r for r in fm["pubmed_ids"] if r["pmid"] == "111")
    assert ref["retracted"] is True and ref["verified"] is False
    ledger = json.loads((kg / "_pmid_ledger.json").read_text())
    assert ledger["entries"]["111"]["disposition"] == "retracted"
    summary = json.loads(res.stdout)
    assert {"pmid": "111", "nodes": ["node_001"], "action": "quarantined"} in summary["retractions"]


def test_multi_ref_node_flagged_not_quarantined(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, ["222"], tmp_path)
    assert res.returncode == 0, res.stderr
    fm, _ = parse_node(str(kg / "nodes" / "node_002.md"))
    assert fm["evaluation_status"] == "passed"      # still has 333
    assert fm.get("quarantined", False) is False
    ref = next(r for r in fm["pubmed_ids"] if r["pmid"] == "222")
    assert ref["retracted"] is True and ref["verified"] is False
    summary = json.loads(res.stdout)
    assert {"pmid": "222", "nodes": ["node_002"], "action": "flagged"} in summary["retractions"]


def test_clean_sweep_no_changes_but_advances_last_checked(tmp_path):
    kg = _full_kg(tmp_path)
    res = _run_sweep(kg, [], tmp_path)
    assert res.returncode == 0, res.stderr
    ledger = json.loads((kg / "_pmid_ledger.json").read_text())
    assert all(e["disposition"] == "used" for e in ledger["entries"].values())
    assert all(e["last_checked"] != "old" for e in ledger["entries"].values())
    summary = json.loads(res.stdout)
    assert summary["retractions"] == []


def test_malformed_fixture_exits_nonzero_no_mutation(tmp_path):
    kg = _full_kg(tmp_path)
    before = (kg / "_pmid_ledger.json").read_text()
    fixture = tmp_path / "bad.json"
    fixture.write_text("{not json", encoding="utf-8")
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--esearch-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode != 0
    assert (kg / "_pmid_ledger.json").read_text() == before   # untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_check_retractions.py -v`
Expected: FAIL — `apply_retractions` not defined / main does not mutate.

- [ ] **Step 3: Add `"retraction"` to append_log**

In `scripts/append_log.py` line 22:

```python
VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight", "digest", "retraction"}
```

And line 7 docstring — add `retraction` to the listed operations:

```python
Operations: build, update, evaluate, link, query, lint, schedule, preflight, digest, retraction
```

- [ ] **Step 4: Add the action layer to check_retractions.py**

In `scripts/check_retractions.py`, add these imports near the top (after the existing `sys.path.insert`):

```python
import datetime
import subprocess

from lib.frontmatter import parse as parse_node, write as write_node
from append_log import append_entry
```

Add these functions above `main`:

```python
def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _valid_support(fm: dict) -> int:
    """Count references that still support the node: verified, non-retracted PMIDs + all external_ids."""
    pmid_support = sum(1 for r in fm.get("pubmed_ids", []) or []
                       if r.get("verified") is True and r.get("retracted") is not True)
    return pmid_support + len(fm.get("external_ids", []) or [])


def _flag_ledger(kg_folder: str, retracted: set[str], swept: list[str]) -> None:
    """Mark retracted entries and advance last_checked on all swept entries. Atomic write."""
    import tempfile
    path = os.path.join(kg_folder, "_pmid_ledger.json")
    with open(path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    now = _now_iso()
    entries = ledger.get("entries", {})
    for pmid in swept:
        if pmid in entries:
            entries[pmid]["last_checked"] = now
    for pmid in retracted:
        if pmid in entries:
            entries[pmid]["disposition"] = "retracted"
            entries[pmid]["notes"] = f"Retraction detected by sweep on {now[:10]}."
    ledger["updated"] = datetime.date.today().isoformat()
    ledger["version"] = ledger.get("version", 0) + 1
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_retractions(kg_folder: str, retracted: set[str], swept: list[str]) -> list[dict]:
    """Flag ledger + nodes, conditionally quarantine, return the retractions summary."""
    # Reverse index PMID -> nodes, from the ledger.
    with open(os.path.join(kg_folder, "_pmid_ledger.json"), "r", encoding="utf-8") as fh:
        entries = json.load(fh).get("entries", {})

    _flag_ledger(kg_folder, retracted, swept)

    summary: list[dict] = []
    any_quarantine = False
    for pmid in sorted(retracted):
        nodes = entries.get(pmid, {}).get("assigned_nodes", [])
        action = "flagged"
        for nid in nodes:
            node_path = os.path.join(kg_folder, "nodes", f"{nid}.md")
            # node files may be named <id>_<slug>.md; resolve by glob if exact name missing
            if not os.path.exists(node_path):
                import glob as _glob
                matches = _glob.glob(os.path.join(kg_folder, "nodes", f"{nid}_*.md"))
                if not matches:
                    continue
                node_path = matches[0]
            fm, body = parse_node(node_path)
            for r in fm.get("pubmed_ids", []) or []:
                if r.get("pmid") == pmid:
                    r["retracted"] = True
                    r["verified"] = False
            if _valid_support(fm) == 0:
                fm["evaluation_status"] = "failed"
                body = body.rstrip() + (
                    f"\n\n> [!warning] Retraction\n> Reference PMID {pmid} was retracted; "
                    f"this node lost its last valid supporting reference and was quarantined "
                    f"pending re-evaluation.\n")
                action = "quarantined"
                any_quarantine = True
            write_node(node_path, fm, body)
        summary.append({"pmid": pmid, "nodes": list(nodes), "action": action})

    # Sync quarantined flags + manifest from evaluation_status (only if a node changed).
    if any_quarantine:
        enforce = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enforce_quarantine.py")
        subprocess.run([sys.executable, enforce, kg_folder], capture_output=True)
    return summary
```

Then replace the bottom of `main` (everything after the `try/except` that computes `retracted`) with:

```python
    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "checked_count": len(used), "retracted_pmids": retracted}

    retractions = apply_retractions(args.kg_folder, set(retracted), used)
    summary["retractions"] = retractions

    n_q = sum(1 for r in retractions if r["action"] == "quarantined")
    n_f = sum(1 for r in retractions if r["action"] == "flagged")
    try:
        append_entry(args.kg_folder, "retraction",
                     f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted; "
                     f"{n_q} node(s) quarantined, {n_f} flagged.")
    except Exception:
        pass  # never fail the sweep over logging

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Retraction sweep: {len(retracted)} of {len(used)} cited PMIDs retracted; "
              f"{n_q} quarantined, {n_f} flagged.", file=sys.stderr)
```

(`apply_retractions` with an empty `retracted` set still advances `last_checked` via `_flag_ledger` and returns `[]`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_check_retractions.py tests/unit/test_append_log.py -v`
Expected: PASS (Task 3 tests + 4 new sweep tests + existing append_log tests).

- [ ] **Step 6: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

```bash
git add scripts/check_retractions.py scripts/append_log.py tests/unit/test_check_retractions.py
git commit -m "feat: retraction action — flag, conditional quarantine, log op"
```

---

## Task 5: Wire the sweep into the scheduled run

Prose edits: run the sweep before preflight in the scheduled prompt, and carry its findings into the run-record's `retractions` field. No unit tests (LLM-instruction prose); verified by diff.

**Files:**
- Modify: `.claude/commands/schedule-kg.md` (scheduled prompt: add Step 0; skip path populates `retractions`)
- Modify: `.claude/commands/build-kg.md` (Phase 4 step 1e run-record: include `retractions`)

**Interfaces:**
- Consumes: `scripts/check_retractions.py --json`; the run-record schema's `retractions` field (Task 2).
- Produces: scheduled runs invoke the sweep and record `retractions` in the run-record.

- [ ] **Step 1: Add Step 0 to the scheduled prompt**

In `.claude/commands/schedule-kg.md`, in the scheduled-agent prompt block, insert a new step BEFORE the current step 1 (the preflight call):

````markdown
0. First run the deterministic retraction sweep over the whole KG (no MCP, no LLM):
   ```
   python3 scripts/check_retractions.py <KG_FolderName> --json
   ```
   Keep the JSON `retractions` array from its output. It must be carried into the
   `retractions` field of whichever run-record you write later this session (the
   build/update record if the update proceeds, or the skip record if preflight
   gates it). The sweep mutates the ledger/nodes itself; you only carry its summary.
   If the sweep exits non-zero, proceed anyway with an empty `retractions` list.
````

Renumber the existing steps (the preflight call becomes step 1, etc.) — they may already be numbered 1-4; keep their order, just ensure the retraction step is first.

- [ ] **Step 2: Populate `retractions` in the skip run-record**

In `.claude/commands/schedule-kg.md`, in the skip-path step that writes the `mode: "skip"` run-record (the "proceed": false branch), add `retractions` to the listed fields: "...and `cost_session_id: null`, and `retractions` (the array from the Step 0 sweep output, or `[]` if none), per `schemas/run_record_schema.json`."

- [ ] **Step 3: Populate `retractions` in the build/update run-record**

In `.claude/commands/build-kg.md`, Phase 4 step `1e` (the run-record field list), add one bullet:

```markdown
   - `retractions`: the `retractions` array from the scheduled run's retraction sweep (Step 0 of the scheduled prompt), or omit/`[]` for a manual run with no sweep.
```

- [ ] **Step 4: Verify the edits**

Run: `git diff .claude/commands/schedule-kg.md .claude/commands/build-kg.md`
Confirm: scheduled prompt has a Step 0 running `check_retractions.py --json` before preflight; both the skip record and the build/update run-record include `retractions`; a manual build omits it.

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/schedule-kg.md .claude/commands/build-kg.md
git commit -m "feat: run retraction sweep in scheduled flow, record in run-record"
```

---

## Task 6: End-to-end smoke gate

Verify the whole path with no production code: full suite green, and a real `check_retractions.py` sweep against a temp KG flips a ledger entry and quarantines the right node.

**Files:** none modified.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (ledger disposition + run-record/digest + detection + sweep + existing tests).

- [ ] **Step 2: Real CLI sweep against a temp KG**

```bash
TMP=$(mktemp -d); KG="$TMP/KG_Demo"; mkdir -p "$KG/nodes"
cat > "$KG/_pmid_ledger.json" <<'JSON'
{"kg_name":"KG_Demo","created":"2026-01-01","updated":"2026-01-01","version":1,
 "entries":{"55555":{"disposition":"used","first_seen":"x","last_checked":"old","assigned_nodes":["node_001"]}},
 "statistics":{"total":1,"used":1,"irrelevant":0,"failed":0,"superseded":0}}
JSON
cat > "$KG/manifest.json" <<'JSON'
{"kg_name":"KG_Demo","topic":"t","created":"2026-01-01","updated":"2026-01-01","version":1,
 "nodes":[{"id":"node_001","title":"Solo","file":"nodes/node_001.md","tags":["x"],"summary":"s",
 "keywords":["k"],"pubmed_ids":["55555"],"evaluation_status":"passed"}],
 "edges":[],"statistics":{"total_nodes":1,"total_edges":0,"total_unique_pmids":1,"evaluation_passed":1,"evaluation_failed":0}}
JSON
cat > "$KG/nodes/node_001.md" <<'MD'
---
id: "node_001"
pubmed_ids:
  - pmid: "55555"
    supports: "s"
    verified: true
evaluation_status: "passed"
quarantined: false
---

body
MD
touch "$KG/_log.md"
echo '{"retracted":["55555"]}' > "$TMP/fix.json"
python3 scripts/check_retractions.py "$KG" --esearch-fixture "$TMP/fix.json" --json
echo "--- node ---"; cat "$KG/nodes/node_001.md"
echo "--- ledger disposition ---"; python3 -c "import json;print(json.load(open('$KG/_pmid_ledger.json'))['entries']['55555']['disposition'])"
echo "--- log ---"; grep -q "retraction |" "$KG/_log.md" && echo "LOG OK"
rm -rf "$TMP"
```

Expected: JSON shows `"action": "quarantined"` for PMID 55555; the node has `evaluation_status: "failed"`, `quarantined: true`, and the `55555` ref `retracted: true`; ledger disposition is `retracted`; `_log.md` has a `retraction |` entry ("LOG OK").

- [ ] **Step 3: Confirm a clean sweep is a safe no-op**

```bash
TMP=$(mktemp -d); KG="$TMP/KG_Demo2"; mkdir -p "$KG/nodes"
cat > "$KG/_pmid_ledger.json" <<'JSON'
{"kg_name":"KG_Demo2","created":"2026-01-01","updated":"2026-01-01","version":1,
 "entries":{"123":{"disposition":"used","first_seen":"x","last_checked":"old","assigned_nodes":["node_001"]}},
 "statistics":{"total":1,"used":1,"irrelevant":0,"failed":0,"superseded":0}}
JSON
echo '{"retracted":[]}' > "$TMP/fix.json"
python3 scripts/check_retractions.py "$KG" --esearch-fixture "$TMP/fix.json" --json
python3 -c "import json;e=json.load(open('$KG/_pmid_ledger.json'))['entries']['123'];print('disp',e['disposition'],'checked_advanced',e['last_checked']!='old')"
rm -rf "$TMP"
```

Expected: `retractions` is `[]`; disposition stays `used`; `checked_advanced True`.

- [ ] **Step 4: Final commit (only if verification produced artifacts)**

If Steps 1-3 produced no file changes, no commit is needed. Otherwise:
```bash
git add -A
git commit -m "test: smoke-verify retraction monitoring path"
```

---

## Self-Review Notes

- **Spec coverage:** deterministic detection → Task 3; esearch intersection → Task 3; ledger `retracted` disposition → Task 1; node `retracted` flag → Task 4; conditional quarantine via `evaluation_status`+`enforce_quarantine` → Task 4; valid-support incl. external_ids → Task 4 (`_valid_support`); run-record `retractions` → Task 2; digest section → Task 2; `retraction` log op → Task 4; scheduled Step 0 (pre-preflight) + run-record carry → Task 5; manual omit → Task 5; never-corrupt-on-failure → Task 3 (detect-before-mutate) + Task 4 malformed-fixture test; last_checked advance → Task 4.
- **Out of scope (per spec):** remediation; NCT/ChEMBL retraction; expressions of concern; separate cadence.
