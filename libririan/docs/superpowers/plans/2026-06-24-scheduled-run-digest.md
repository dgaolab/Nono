# Scheduled-Run Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a deterministic, audit-readable markdown digest after every KG run (BUILD, UPDATE, and quiet-week skip), rendered from a new structured run-record plus existing artifacts.

**Architecture:** build-kg/schedule-kg emit a structured `runs/<run_id>.json` run-record; a new pure-Python script `scripts/render_digest.py` renders it (joined with `_evaluation_log.json` quotes, manifest `statistics`, and `_cost_log.jsonl`) into an immutable `digests/<run_id>.md` plus a `_digest.md` latest pointer. No LLM in the rendering path; rendering is a pure function of its inputs.

**Tech Stack:** Python 3.13, `pytest`, `jsonschema`; Markdown slash-command agents under `.claude/commands/`.

## Global Constraints

- Rendering is **deterministic**: no LLM, no `datetime.now()`, no `random` in the digest *content*. Same inputs → byte-identical digest body. Copied verbatim from spec.
- Quotes and verdicts are copied **verbatim** from `_evaluation_log.json` — never paraphrased.
- The digest must **never fail a run**: missing/malformed inputs degrade gracefully (see per-task error rules).
- Digest content lives **only** in `digests/<run_id>.md` and `_digest.md`; the run-record lives in `runs/<run_id>.json`. Manifest and node files are not modified by the digest path.
- `run_id` format: `<UTC-timestamp-no-colons>-v<version>`, e.g. `2026-06-24T080012Z-v7`.
- Run modes: `build` | `update` | `skip`.
- Detail depth: full per-node evidence (quote + verdict) for `update`; summary counts + node list for `build`; header + one-line message for `skip`.
- Cost is best-effort: footer shows actual cost if the session line exists, else "cost: pending" (session id known/absent) or "cost: unavailable" (no log file).
- Run all Python tests from `/home/dadi/nono/libririan` with `python3 -m pytest tests/unit/ -v`.

---

## File Structure

- `schemas/run_record_schema.json` — **create**; JSON Schema for the run-record.
- `tests/unit/test_run_record_schema.py` — **create**; schema validation tests.
- `scripts/render_digest.py` — **create**; pure rendering functions + IO/CLI.
- `tests/unit/test_render_digest.py` — **create**; rendering + IO tests.
- `scripts/append_log.py` — **modify**; add `"digest"` to `VALID_OPS` (lines 22 and the `--op` choices come from the same set).
- `.claude/commands/build-kg.md` — **modify**; Phase 4 emits the run-record and calls `render_digest.py`.
- `.claude/commands/schedule-kg.md` — **modify**; skip path emits a `skip` run-record and calls `render_digest.py`.

---

## Task 1: Run-record JSON schema

Define and validate the structured run-record that build-kg/schedule-kg will emit and the renderer will consume.

**Files:**
- Create: `schemas/run_record_schema.json`
- Test: `tests/unit/test_run_record_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `schemas/run_record_schema.json` — a JSON Schema (Draft 2020-12) describing the run-record object documented below.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_record_schema.py`:

```python
import json
import os

import jsonschema

SCHEMA_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "schemas", "run_record_schema.json"))


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def valid_update_record():
    return {
        "run_id": "2026-06-24T080012Z-v7",
        "kg_name": "KG_Topic",
        "mode": "update",
        "timestamp": "2026-06-24T08:00:12Z",
        "version": 7,
        "since_date": "2026-06-17",
        "preflight": {"novel_count": 9, "threshold": 3},
        "nodes_created": ["node_016"],
        "nodes_revised": ["node_003"],
        "refs_added": [{"pmid": "39876543", "nodes": ["node_003", "node_016"]}],
        "refs_failed": [{"pmid": "00000001", "node": "node_005", "reason": "verification failed"}],
        "eval_summary": {"evaluated": 3, "passed": 2, "failed": 1},
        "cost_session_id": "abc-123",
    }


def test_valid_update_record_passes():
    jsonschema.validate(valid_update_record(), load_schema())


def test_skip_record_passes():
    rec = valid_update_record()
    rec["mode"] = "skip"
    rec["since_date"] = "2026-06-17"
    rec["nodes_created"] = []
    rec["nodes_revised"] = []
    rec["refs_added"] = []
    rec["refs_failed"] = []
    rec["eval_summary"] = {"evaluated": 0, "passed": 0, "failed": 0}
    jsonschema.validate(rec, load_schema())


def test_build_record_allows_null_since_and_cost():
    rec = valid_update_record()
    rec["mode"] = "build"
    rec["since_date"] = None
    rec["preflight"] = None
    rec["cost_session_id"] = None
    jsonschema.validate(rec, load_schema())


def test_bad_mode_rejected():
    rec = valid_update_record()
    rec["mode"] = "rebuild"
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass


def test_missing_required_field_rejected():
    rec = valid_update_record()
    del rec["run_id"]
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py -v`
Expected: FAIL — `FileNotFoundError` (schema does not exist yet).

- [ ] **Step 3: Write the schema**

Create `schemas/run_record_schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "KG Run Record",
  "type": "object",
  "required": ["run_id", "kg_name", "mode", "timestamp", "version",
               "nodes_created", "nodes_revised", "refs_added", "refs_failed",
               "eval_summary"],
  "properties": {
    "run_id": {"type": "string"},
    "kg_name": {"type": "string"},
    "mode": {"type": "string", "enum": ["build", "update", "skip"]},
    "timestamp": {"type": "string"},
    "version": {"type": "integer", "minimum": 1},
    "since_date": {"type": ["string", "null"]},
    "preflight": {
      "type": ["object", "null"],
      "properties": {
        "novel_count": {"type": "integer", "minimum": 0},
        "threshold": {"type": "integer", "minimum": 0}
      }
    },
    "nodes_created": {"type": "array", "items": {"type": "string"}},
    "nodes_revised": {"type": "array", "items": {"type": "string"}},
    "refs_added": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["pmid", "nodes"],
        "properties": {
          "pmid": {"type": "string"},
          "nodes": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "refs_failed": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["pmid", "node", "reason"],
        "properties": {
          "pmid": {"type": "string"},
          "node": {"type": "string"},
          "reason": {"type": "string"}
        }
      }
    },
    "eval_summary": {
      "type": "object",
      "required": ["evaluated", "passed", "failed"],
      "properties": {
        "evaluated": {"type": "integer", "minimum": 0},
        "passed": {"type": "integer", "minimum": 0},
        "failed": {"type": "integer", "minimum": 0}
      }
    },
    "cost_session_id": {"type": ["string", "null"]}
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add schemas/run_record_schema.json tests/unit/test_run_record_schema.py
git commit -m "feat: add run-record JSON schema for scheduled-run digest"
```

---

## Task 2: Pure digest rendering

The heart of the feature: pure functions that turn structured inputs into the digest markdown string. No file IO, no LLM, no clock.

**Files:**
- Create: `scripts/render_digest.py` (rendering functions only this task; IO/CLI added in Task 3)
- Test: `tests/unit/test_render_digest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `render(run_record: dict, eval_index: dict, node_titles: dict, stats: dict, cost: dict) -> str` — returns the digest markdown. `eval_index` maps `node_id -> eval entry` (an entry from `_evaluation_log.json`). `node_titles` maps `node_id -> title`. `cost` is `{"status": "ok", "est_cost_usd": float, "models": {...}}` | `{"status": "pending", "session_id": str|None}` | `{"status": "unavailable"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_render_digest.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from render_digest import render


def update_record():
    return {
        "run_id": "2026-06-24T080012Z-v7", "kg_name": "KG_Topic", "mode": "update",
        "timestamp": "2026-06-24T08:00:12Z", "version": 7, "since_date": "2026-06-17",
        "preflight": {"novel_count": 9, "threshold": 3},
        "nodes_created": ["node_016"], "nodes_revised": ["node_003"],
        "refs_added": [{"pmid": "39876543", "nodes": ["node_003"]}],
        "refs_failed": [{"pmid": "00000001", "node": "node_005", "reason": "verification failed"}],
        "eval_summary": {"evaluated": 3, "passed": 2, "failed": 1},
        "cost_session_id": "abc-123",
    }


def eval_index():
    return {
        "node_016": {"node_id": "node_016", "overall_status": "passed", "pmid_checks": [
            {"pmid": "39876543", "article_title": "A study", "verdict": "supported",
             "quotes": [{"text": "Effect size was 0.4 (p<0.01).", "source": "abstract"}]}]},
        "node_003": {"node_id": "node_003", "overall_status": "passed", "pmid_checks": [
            {"pmid": "39876543", "article_title": "A study", "verdict": "partially_supported",
             "quotes": [{"text": "Benefit seen in a subgroup.", "source": "full_text"}]}]},
        "node_005": {"node_id": "node_005", "overall_status": "failed", "pmid_checks": []},
    }


def titles():
    return {"node_016": "New concept", "node_003": "Existing concept", "node_005": "Bad node"}


def stats():
    return {"total_nodes": 17, "active_nodes": 16, "quarantined_nodes": 1,
            "evidence_tier_distribution": {"rct": 3, "cohort": 2}}


def test_update_shows_verbatim_quotes_and_verdicts():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "pending", "session_id": "abc-123"})
    assert "Effect size was 0.4 (p<0.01)." in out          # verbatim quote, created node
    assert "Benefit seen in a subgroup." in out             # verbatim quote, revised node
    assert "partially_supported" in out                     # per-ref verdict preserved
    assert "New concept" in out and "Existing concept" in out


def test_update_has_failures_section():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "Failures" in out
    assert "00000001" in out and "verification failed" in out   # failed ref
    assert "node_005" in out                                    # failed-eval node


def test_outcome_line_counts():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "9 novel" in out
    assert "2/3 passed" in out


def test_cost_ok_renders_dollar_amount():
    cost = {"status": "ok", "est_cost_usd": 0.1234, "models": {"claude-opus-4-8": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0}}}
    out = render(update_record(), eval_index(), titles(), stats(), cost)
    assert "0.1234" in out


def test_cost_pending_and_unavailable_text():
    pend = render(update_record(), eval_index(), titles(), stats(), {"status": "pending", "session_id": "abc-123"})
    assert "pending" in pend and "abc-123" in pend
    unav = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "unavailable" in unav


def test_skip_mode_is_one_liner():
    rec = update_record()
    rec["mode"] = "skip"
    out = render(rec, {}, {}, stats(), {"status": "unavailable"})
    assert "below threshold 3" in out
    assert "Effect size" not in out          # no audit body
    assert "What changed" not in out


def test_build_mode_is_summary_no_quotes():
    rec = update_record()
    rec["mode"] = "build"
    rec["since_date"] = None
    rec["preflight"] = None
    out = render(rec, eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "node_016" in out                 # node listed
    assert "Effect size was 0.4 (p<0.01)." not in out   # no per-quote dump in build mode


def test_missing_eval_entry_does_not_crash():
    out = render(update_record(), {}, titles(), stats(), {"status": "unavailable"})
    assert "evaluation pending" in out.lower()


def test_render_is_deterministic():
    a = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    b = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert a == b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_render_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'render_digest'`.

- [ ] **Step 3: Write the rendering functions**

Create `scripts/render_digest.py`:

```python
#!/usr/bin/env python3
"""Render a deterministic, audit-readable digest for a KG run.

Rendering is a pure function of its inputs (no LLM, no clock, no randomness):
the same inputs always produce a byte-identical digest body.

CLI and file IO are added below the rendering functions.
"""


def _outcome_line(rr: dict) -> str:
    novel = (rr.get("preflight") or {}).get("novel_count")
    ev = rr.get("eval_summary", {})
    parts = []
    if novel is not None:
        parts.append(f"{novel} novel PMIDs")
    parts.append(f"{len(rr.get('nodes_created', []))} added, "
                 f"{len(rr.get('nodes_revised', []))} revised")
    parts.append(f"{ev.get('passed', 0)}/{ev.get('evaluated', 0)} passed evaluation")
    return " → ".join([parts[0], "; ".join(parts[1:])]) if novel is not None \
        else "; ".join(parts)


def _header(rr: dict) -> list[str]:
    run_date = rr.get("timestamp", "")[:10]
    return [
        f"# Digest — {rr.get('kg_name', '')} — {run_date}",
        "",
        f"**Run:** {rr.get('run_id', '')} · **Mode:** {rr.get('mode', '')} "
        f"· **Version:** v{rr.get('version', '')}",
        f"**Outcome:** {_outcome_line(rr)}",
        "",
    ]


def _node_block(node_id: str, title: str, refs_for_node: list, eval_index: dict) -> list[str]:
    entry = eval_index.get(node_id)
    if entry is None:
        return [f"### {node_id} — {title}  [evaluation pending]", ""]
    lines = [f"### {node_id} — {title}  [{entry.get('overall_status', '?')}]"]
    checks = {c.get("pmid"): c for c in entry.get("pmid_checks", [])}
    for pmid in refs_for_node:
        c = checks.get(pmid)
        if c is None:
            lines.append(f"- **PMID {pmid}** [no evaluation record]")
            continue
        cite = c.get("article_title") or ""
        lines.append(f"- **PMID {pmid}** [{c.get('verdict', '?')}]: {cite}".rstrip())
        for q in c.get("quotes", []) or []:
            lines.append(f"  > {q.get('text', '')}  _({q.get('source', '')})_")
    lines.append("")
    return lines


def _refs_added_for(node_id: str, rr: dict) -> list[str]:
    return [r["pmid"] for r in rr.get("refs_added", []) if node_id in r.get("nodes", [])]


def _all_refs_for(node_id: str, eval_index: dict) -> list[str]:
    entry = eval_index.get(node_id)
    if not entry:
        return []
    return [c.get("pmid") for c in entry.get("pmid_checks", []) if c.get("pmid")]


def _cost_line(cost: dict) -> str:
    status = cost.get("status")
    if status == "ok":
        m = cost.get("models", {})
        tin = sum(v.get("input", 0) for v in m.values())
        tout = sum(v.get("output", 0) for v in m.values())
        return f"- Cost: ${cost.get('est_cost_usd', 0):.4f} ({tin} in / {tout} out tokens)"
    if status == "pending":
        sid = cost.get("session_id") or "unknown"
        return f"- Cost: pending — session {sid}, see _cost_log.jsonl"
    return "- Cost: unavailable"


def _totals(stats: dict, cost: dict) -> list[str]:
    lines = ["## Run totals"]
    if stats:
        lines.append(f"- Nodes: {stats.get('total_nodes', '?')} total, "
                     f"{stats.get('active_nodes', '?')} active, "
                     f"{stats.get('quarantined_nodes', '?')} quarantined")
        tiers = stats.get("evidence_tier_distribution") or {}
        if tiers:
            lines.append("- Evidence tiers: "
                         + ", ".join(f"{k} {v}" for k, v in sorted(tiers.items())))
    lines.append(_cost_line(cost))
    lines.append("")
    return lines


def _failures(rr: dict, eval_index: dict, node_titles: dict) -> list[str]:
    failed_nodes = [nid for nid in rr.get("nodes_created", []) + rr.get("nodes_revised", [])
                    if (eval_index.get(nid) or {}).get("overall_status") == "failed"]
    refs_failed = rr.get("refs_failed", [])
    if not failed_nodes and not refs_failed:
        return []
    lines = ["## Failures & quarantines"]
    for nid in failed_nodes:
        lines.append(f"- {nid} — {node_titles.get(nid, nid)}: FAILED evaluation")
    for r in refs_failed:
        lines.append(f"- PMID {r.get('pmid')} ({r.get('node')}) — {r.get('reason')}")
    lines.append("")
    return lines


def render(run_record: dict, eval_index: dict, node_titles: dict,
           stats: dict, cost: dict) -> str:
    """Render the digest markdown. Pure function of its inputs."""
    mode = run_record.get("mode")
    lines = _header(run_record)

    if mode == "skip":
        pf = run_record.get("preflight") or {}
        lines.append(
            f"Quiet week: {pf.get('novel_count', 0)} novel PMIDs since "
            f"{run_record.get('since_date', '?')}, below threshold "
            f"{pf.get('threshold', '?')} — no update.")
        lines.append("")
        return "\n".join(lines)

    if mode == "build":
        lines.append("## Summary")
        ev = run_record.get("eval_summary", {})
        lines.append(f"- {len(run_record.get('nodes_created', []))} nodes created; "
                     f"{ev.get('passed', 0)}/{ev.get('evaluated', 0)} passed evaluation")
        lines.append("- Nodes: " + ", ".join(
            f"{nid} ({node_titles.get(nid, nid)})" for nid in run_record.get("nodes_created", [])))
        lines.append("")
        lines.extend(_failures(run_record, eval_index, node_titles))
        lines.extend(_totals(stats, cost))
        return "\n".join(lines)

    # update mode — full audit body
    lines.append("## What changed")
    lines.append("")
    for nid in run_record.get("nodes_created", []):
        lines.append(f"**New:**")
        lines.extend(_node_block(nid, node_titles.get(nid, nid),
                                 _all_refs_for(nid, eval_index), eval_index))
    for nid in run_record.get("nodes_revised", []):
        lines.append(f"**Revised:**")
        lines.extend(_node_block(nid, node_titles.get(nid, nid),
                                 _refs_added_for(nid, run_record), eval_index))
    lines.extend(_failures(run_record, eval_index, node_titles))
    lines.extend(_totals(stats, cost))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_render_digest.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/render_digest.py tests/unit/test_render_digest.py
git commit -m "feat: pure digest rendering from run-record and eval log"
```

---

## Task 3: Digest IO, CLI, and outputs

Add the file-reading, cost-lookup, output-writing, and logging layer around the pure renderer, and the CLI entry point. Also enable the `digest` log op.

**Files:**
- Modify: `scripts/render_digest.py` (append IO/CLI below the rendering functions)
- Modify: `scripts/append_log.py:22` (add `"digest"` to `VALID_OPS`)
- Test: `tests/unit/test_render_digest.py` (add IO tests)

**Interfaces:**
- Consumes: `render(...)` from Task 2; `append_entry(kg_folder, op, summary, details="")` from `scripts/append_log.py`.
- Produces:
  - `load_cost(log_path: str, session_id: str | None) -> dict` — returns a `cost` dict (`status` ok/pending/unavailable) as defined in Task 2.
  - `generate(kg_folder: str, run_record_path: str, cost_log_path: str, do_log: bool = True) -> str` — reads inputs, renders, writes `digests/<run_id>.md` and `_digest.md`, optionally logs the `digest` op, returns the digest path.

- [ ] **Step 1: Write the failing IO tests**

Append to `tests/unit/test_render_digest.py`:

```python
import json


def _make_kg(tmp_path, run_record, eval_log, manifest_stats, manifest_nodes):
    kg = tmp_path / "KG_Topic"
    kg.mkdir()
    (kg / "runs").mkdir()
    rr_path = kg / "runs" / (run_record["run_id"] + ".json")
    rr_path.write_text(json.dumps(run_record), encoding="utf-8")
    (kg / "_evaluation_log.json").write_text(json.dumps(eval_log), encoding="utf-8")
    (kg / "manifest.json").write_text(json.dumps(
        {"kg_name": "KG_Topic", "nodes": manifest_nodes, "statistics": manifest_stats}),
        encoding="utf-8")
    return str(kg), str(rr_path)


def test_generate_writes_digest_and_pointer(tmp_path):
    from render_digest import generate
    nodes = [{"id": "node_016", "title": "New concept"}, {"id": "node_003", "title": "Existing concept"}]
    kg, rr = _make_kg(tmp_path, update_record(),
                      list(eval_index().values()), stats(), nodes)
    cost_log = tmp_path / "_cost_log.jsonl"   # absent on purpose
    out_path = generate(kg, rr, str(cost_log), do_log=False)
    assert out_path.endswith("digests/2026-06-24T080012Z-v7.md")
    digest_text = open(out_path, encoding="utf-8").read()
    pointer_text = open(os.path.join(kg, "_digest.md"), encoding="utf-8").read()
    assert digest_text == pointer_text                  # latest pointer is a copy
    assert "Effect size was 0.4 (p<0.01)." in digest_text
    assert "Cost: unavailable" in digest_text           # no cost log file


def test_load_cost_statuses(tmp_path):
    from render_digest import load_cost
    missing = tmp_path / "nope.jsonl"
    assert load_cost(str(missing), "abc")["status"] == "unavailable"
    log = tmp_path / "_cost_log.jsonl"
    log.write_text(json.dumps({"session_id": "abc", "est_cost_usd": 0.5,
                               "models": {"m": {"input": 1, "output": 2}}}) + "\n", encoding="utf-8")
    assert load_cost(str(log), "abc")["status"] == "ok"
    assert load_cost(str(log), "other")["status"] == "pending"     # file present, no match
    assert load_cost(str(log), None)["status"] == "pending"        # no session id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_render_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate'` (and `load_cost`).

- [ ] **Step 3: Add `"digest"` to append_log VALID_OPS**

In `scripts/append_log.py`, line 22, change:

```python
VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight"}
```

to:

```python
VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight", "digest"}
```

- [ ] **Step 4: Append the IO/CLI layer to render_digest.py**

Append to `scripts/render_digest.py`:

```python
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from append_log import append_entry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COST_LOG = os.path.join(REPO_ROOT, "_cost_log.jsonl")


def load_cost(log_path: str, session_id) -> dict:
    """Return a cost dict: ok (with totals) / pending / unavailable."""
    if not os.path.exists(log_path):
        return {"status": "unavailable"}
    if not session_id:
        return {"status": "pending", "session_id": session_id}
    found = None
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("session_id") == session_id:
                found = entry          # last match wins
    if found is None:
        return {"status": "pending", "session_id": session_id}
    return {"status": "ok", "est_cost_usd": found.get("est_cost_usd", 0.0),
            "models": found.get("models", {})}


def generate(kg_folder: str, run_record_path: str,
             cost_log_path: str = DEFAULT_COST_LOG, do_log: bool = True) -> str:
    """Render the digest for a run-record and write digests/<run_id>.md + _digest.md.
    Returns the path to the per-run digest file."""
    with open(run_record_path, "r", encoding="utf-8") as fh:
        run_record = json.load(fh)

    eval_log = []
    eval_path = os.path.join(kg_folder, "_evaluation_log.json")
    if os.path.exists(eval_path):
        try:
            with open(eval_path, "r", encoding="utf-8") as fh:
                eval_log = json.load(fh)
        except (json.JSONDecodeError, OSError):
            eval_log = []
    eval_index = {e["node_id"]: e for e in eval_log if isinstance(e, dict) and "node_id" in e}

    node_titles, stats = {}, {}
    manifest_path = os.path.join(kg_folder, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            node_titles = {n["id"]: n.get("title", n["id"])
                           for n in manifest.get("nodes", []) if "id" in n}
            stats = manifest.get("statistics", {})
        except (json.JSONDecodeError, OSError):
            pass

    cost = load_cost(cost_log_path, run_record.get("cost_session_id"))

    digest_md = render(run_record, eval_index, node_titles, stats, cost)

    digests_dir = os.path.join(kg_folder, "digests")
    os.makedirs(digests_dir, exist_ok=True)
    digest_path = os.path.join(digests_dir, run_record["run_id"] + ".md")
    with open(digest_path, "w", encoding="utf-8") as fh:
        fh.write(digest_md)
    with open(os.path.join(kg_folder, "_digest.md"), "w", encoding="utf-8") as fh:
        fh.write(digest_md)

    if do_log:
        try:
            append_entry(kg_folder, "digest",
                         f"Digest written for {run_record['run_id']} (mode {run_record.get('mode')}).")
        except (FileNotFoundError, ValueError):
            pass        # never fail the run over logging

    return digest_path


def main():
    parser = argparse.ArgumentParser(description="Render a KG run digest.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--run-record", required=True, help="Path to runs/<run_id>.json")
    parser.add_argument("--cost-log", default=DEFAULT_COST_LOG, help="Path to _cost_log.jsonl")
    parser.add_argument("--no-log", action="store_true", help="Do not append a digest entry to _log.md")
    args = parser.parse_args()
    try:
        path = generate(args.kg_folder, args.run_record, args.cost_log, do_log=not args.no_log)
    except Exception as e:          # digest must never fail the run
        print(f"Warning: digest generation failed: {e}", file=sys.stderr)
        sys.exit(0)
    print(f"Digest written: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_render_digest.py tests/unit/test_append_log.py -v`
Expected: PASS (Task 2 tests + the 2 new IO tests + existing append_log tests).

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add scripts/render_digest.py scripts/append_log.py tests/unit/test_render_digest.py
git commit -m "feat: digest IO/CLI, cost-log lookup, and digest log op"
```

---

## Task 4: Wire digest generation into the commands

Prose edits to the two command files so runs emit a run-record and call the renderer. No unit tests (LLM-instruction prose); verified by reading the diffs.

**Files:**
- Modify: `.claude/commands/build-kg.md` (Phase 4, around lines 567-581)
- Modify: `.claude/commands/schedule-kg.md` (scheduled-prompt skip path, around lines 48-53)

**Interfaces:**
- Consumes: `scripts/render_digest.py` CLI (`<kg_folder> --run-record <path> [--cost-log <path>]`) and the run-record schema from Task 1.
- Produces: a `runs/<run_id>.json` file per run and a generated digest.

- [ ] **Step 1: Add run-record + digest step to build-kg Phase 4**

In `.claude/commands/build-kg.md`, immediately after step `1d` (the `update_manifest_stats.py --stamp-last-run` block ending at line 570) and before step `2` (Log the operation), insert a new step `1e`:

````markdown
1e. Write the run-record and render the digest. Build `runs/<run_id>.json` where `run_id` is `<UTC-timestamp-with-colons-removed>Z-v<version>` (e.g. `2026-06-24T080012Z-v7`), conforming to `schemas/run_record_schema.json`. Populate it from this run's changelog buffer (Phase 2):
   - `mode`: `"build"` for an initial build, `"update"` otherwise.
   - `since_date`: the UPDATE window start, or `null` for an initial build.
   - `preflight`: `{novel_count, threshold}` if this run came from a scheduled preflight, else `null`.
   - `nodes_created`, `nodes_revised`: node IDs touched this run.
   - `refs_added`: `[{pmid, nodes:[...]}]`; `refs_failed`: `[{pmid, node, reason}]`.
   - `eval_summary`: `{evaluated, passed, failed}` from Phase 3.
   - `cost_session_id`: this session's id if known, else `null`.

   Then render the digest:
   ```
   python3 scripts/render_digest.py {KG_FOLDER} --run-record {KG_FOLDER}/runs/{run_id}.json
   ```
   This writes `digests/{run_id}.md` and refreshes `_digest.md`. Digest generation never fails the run — if it warns, continue.
````

- [ ] **Step 2: Add the validation reference to build-kg Phase 4 file list**

In `.claude/commands/build-kg.md`, in step `1` (the "Ensure all files are written" list, lines 546-553), add two lines after the `_changelog.md` entry:

```markdown
   - `runs/<run_id>.json` — structured run-record (one per run)
   - `digests/<run_id>.md` + `_digest.md` — audit-readable run digest (latest pointer)
```

- [ ] **Step 3: Add skip-path run-record + digest to schedule-kg**

In `.claude/commands/schedule-kg.md`, replace the scheduled-prompt step `2` (the skip case, line 50) with:

````markdown
2. If the JSON output has "proceed": false, write a skip run-record and digest, then report one line and STOP. Build `<KG_FolderName>/runs/<run_id>.json` with `run_id` = `<UTC-timestamp-no-colons>Z-v<current manifest version>`, `mode: "skip"`, `since_date` and `preflight: {novel_count, threshold}` from the preflight JSON, empty `nodes_created`/`nodes_revised`/`refs_added`/`refs_failed`, and `eval_summary: {evaluated: 0, passed: 0, failed: 0}`. Then run:
   ```
   python3 scripts/render_digest.py <KG_FolderName> --run-record <KG_FolderName>/runs/<run_id>.json
   ```
   Then report exactly one line — "Quiet week: {novel_count} novel PMIDs since {since_date}, below threshold {threshold} — skipped update." — and STOP. Do not load the KG and do not call any MCP tools.
````

- [ ] **Step 4: Verify the edits**

Run: `git diff .claude/commands/build-kg.md .claude/commands/schedule-kg.md`
Confirm: build-kg Phase 4 has step `1e` (run-record + render call) and the file-list additions; schedule-kg skip path writes a `skip` run-record and calls the renderer before the one-line report. Confirm the `run_id` format text matches `schemas/run_record_schema.json` and Task 1.

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/build-kg.md .claude/commands/schedule-kg.md
git commit -m "feat: emit run-record and render digest from build-kg and schedule-kg"
```

---

## Task 5: End-to-end smoke gate

Verify the whole path with no production code: full suite green, and a real `render_digest.py` invocation against a temp KG produces the expected files.

**Files:** none modified.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (run-record schema + render_digest rendering + IO + existing tests).

- [ ] **Step 2: Real CLI invocation against a temp KG**

Create a temp KG by hand and run the actual CLI (no pytest), confirming files appear and contain a verbatim quote:

```bash
TMP=$(mktemp -d)
mkdir -p "$TMP/KG_Demo/runs"
cat > "$TMP/KG_Demo/manifest.json" <<'JSON'
{"kg_name":"KG_Demo","nodes":[{"id":"node_001","title":"Demo node"}],
 "statistics":{"total_nodes":1,"active_nodes":1,"quarantined_nodes":0,
 "evidence_tier_distribution":{"rct":1}}}
JSON
cat > "$TMP/KG_Demo/_evaluation_log.json" <<'JSON'
[{"node_id":"node_001","overall_status":"passed","pmid_checks":[
 {"pmid":"123","article_title":"Demo","verdict":"supported",
  "quotes":[{"text":"A verbatim finding.","source":"abstract"}]}]}]
JSON
cat > "$TMP/KG_Demo/runs/2026-06-24T080012Z-v1.json" <<'JSON'
{"run_id":"2026-06-24T080012Z-v1","kg_name":"KG_Demo","mode":"update",
 "timestamp":"2026-06-24T08:00:12Z","version":1,"since_date":"2026-06-17",
 "preflight":{"novel_count":4,"threshold":3},
 "nodes_created":["node_001"],"nodes_revised":[],
 "refs_added":[{"pmid":"123","nodes":["node_001"]}],"refs_failed":[],
 "eval_summary":{"evaluated":1,"passed":1,"failed":0},"cost_session_id":null}
JSON
touch "$TMP/KG_Demo/_log.md"
python3 scripts/render_digest.py "$TMP/KG_Demo" --run-record "$TMP/KG_Demo/runs/2026-06-24T080012Z-v1.json"
echo "--- digest ---"; cat "$TMP/KG_Demo/digests/2026-06-24T080012Z-v1.md"
echo "--- pointer present ---"; test -f "$TMP/KG_Demo/_digest.md" && echo OK
echo "--- log op present ---"; grep -q "digest |" "$TMP/KG_Demo/_log.md" && echo OK
rm -rf "$TMP"
```

Expected: digest prints with "A verbatim finding." present, `_digest.md` exists ("OK"), and `_log.md` has a `digest |` entry ("OK").

- [ ] **Step 3: Confirm run-record validates against its schema**

Run:
```bash
python3 -c "import json,jsonschema; s=json.load(open('schemas/run_record_schema.json')); jsonschema.validate({'run_id':'x','kg_name':'k','mode':'skip','timestamp':'t','version':1,'nodes_created':[],'nodes_revised':[],'refs_added':[],'refs_failed':[],'eval_summary':{'evaluated':0,'passed':0,'failed':0}}, s); print('valid')"
```
Expected: prints `valid`.

- [ ] **Step 4: Final commit (only if verification produced artifacts)**

If Steps 1-3 produced no file changes, no commit is needed. Otherwise:
```bash
git add -A
git commit -m "test: smoke-verify scheduled-run digest path"
```

---

## Self-Review Notes

- **Spec coverage:** deterministic renderer → Task 2; run-record → Tasks 1 & 4; eval-log quotes/verdicts in audit body → Task 2; manifest stats + cost footer → Tasks 2 & 3; cost best-effort timing (pending/unavailable) → Tasks 2 & 3; per-run files + `_digest.md` pointer → Task 3; `digest` log op → Task 3; all-runs + skip wiring → Task 4; BUILD summary / UPDATE full / skip one-liner → Task 2; graceful degradation → Tasks 2 & 3; testing → Tasks 1-3 & 5.
- **Out of scope (per spec):** push notification; cross-run analytics.
