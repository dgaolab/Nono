# Supporting-Quote Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the verbatim source sentence(s) the evaluator verified each reference against, stored per-PMID in node frontmatter, so claims carry audit-grade proof and the future digest can show evidence.

**Architecture:** Quotes are captured at *evaluate* time by whichever model verifies a reference (Haiku worker inline, strong model on escalation), written into the node's `pubmed_ids[].quotes` field via the existing `update_frontmatter.py` deep-merge. The manifest is untouched. Quote shape is validated by the linter (there is no node-frontmatter JSON schema). Existing nodes are not backfilled; the field is optional and absent on legacy nodes.

**Tech Stack:** Python 3.13, `pytest`, `pyyaml`, `jsonschema`; Markdown slash-command agents under `.claude/commands/`.

## Global Constraints

- Quotes live **only** in node frontmatter, per-PMID — never in `manifest.json` or `schemas/graph_schema.json`. Copied verbatim from spec.
- `quotes` is **optional**; absent means legacy/not-yet-evaluated and must never raise an error anywhere.
- When present, `quotes` holds **1–3** items; each item is `{text: <non-empty string>, source: <"abstract" | "full_text">}`.
- Quotes are stored **only** for `verified: true` references (verdict `supported`/`partially_supported`); never for failed/unrelated references.
- **Going-forward only** — no backfill of already-passed nodes in this plan.
- Run all Python tests with: `python3 -m pytest tests/unit/ -v` from the project root `/home/dadi/nono/libririan`.

---

## File Structure

- `scripts/lib/frontmatter.py` — **no change**; its existing flat-overwrite merge already replaces a matched PMID's `quotes` wholesale. Task 1 locks this with a test.
- `tests/unit/test_frontmatter_merge.py` — **create**; characterization tests for quote merge behavior.
- `scripts/linter_kg.py` — **modify**; add one check method + register it in `ALL_CHECKS`.
- `tests/unit/test_linter_quotes.py` — **create**; unit tests for the new linter check.
- `templates/node_template.md` — **modify**; add a commented `quotes` example.
- `tests/unit/test_node_template.py` — **create**; assert the template still parses.
- `.claude/commands/evaluate-kg-worker.md` — **modify**; E2 capture instruction + E6 write-back example.

---

## Task 1: Lock quote merge behavior in frontmatter.py

The spec requires that re-evaluating a node **replaces** a PMID's `quotes` wholesale (no stale accumulation) while leaving other PMIDs untouched. `_merge_list_of_dicts` already does a flat field overwrite, so this should pass with no production change — the test is a regression lock. If it fails, fix `_merge_list_of_dicts` per Step 4.

**Files:**
- Test: `tests/unit/test_frontmatter_merge.py` (create)
- Modify (only if Step 2 fails): `scripts/lib/frontmatter.py:226-239`

**Interfaces:**
- Consumes: `deep_merge(base: dict, updates: dict) -> dict` from `scripts/lib/frontmatter.py` (matches `pubmed_ids` entries by `pmid`).
- Produces: nothing for later tasks (behavioral lock only).

- [ ] **Step 1: Write the characterization tests**

Create `tests/unit/test_frontmatter_merge.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib.frontmatter import deep_merge


def _base_node():
    return {
        "id": "node_001",
        "pubmed_ids": [
            {"pmid": "111", "verified": True,
             "quotes": [{"text": "old quote", "source": "abstract"}]},
            {"pmid": "222", "verified": True,
             "quotes": [{"text": "keep me", "source": "abstract"}]},
        ],
    }


def test_quotes_replaced_wholesale_on_matching_pmid():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "111", "verified": True,
         "quotes": [{"text": "new quote", "source": "full_text"}]},
    ]}
    merged = deep_merge(base, updates)
    pmid_111 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "111")
    assert pmid_111["quotes"] == [{"text": "new quote", "source": "full_text"}]


def test_other_pmid_quotes_untouched():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "111", "quotes": [{"text": "new quote", "source": "full_text"}]},
    ]}
    merged = deep_merge(base, updates)
    pmid_222 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "222")
    assert pmid_222["quotes"] == [{"text": "keep me", "source": "abstract"}]


def test_new_pmid_with_quotes_is_appended():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "333", "verified": True,
         "quotes": [{"text": "brand new", "source": "abstract"}]},
    ]}
    merged = deep_merge(base, updates)
    pmids = [p["pmid"] for p in merged["pubmed_ids"]]
    assert pmids == ["111", "222", "333"]
    pmid_333 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "333")
    assert pmid_333["quotes"] == [{"text": "brand new", "source": "abstract"}]
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m pytest tests/unit/test_frontmatter_merge.py -v`
Expected: PASS (existing flat-overwrite merge already satisfies the contract).

- [ ] **Step 3: If Step 2 passed, skip to Step 5**

No production change is needed. Proceed to commit.

- [ ] **Step 4: Only if a test FAILED — fix the merge**

In `scripts/lib/frontmatter.py`, inside `_merge_list_of_dicts`, the per-field loop must overwrite (not recurse into) list/dict field values. Confirm it reads:

```python
        if k is not None and k in index:
            # Merge into existing item — flat overwrite per field (replaces nested lists wholesale)
            existing = result[index[k]]
            for field, val in update_item.items():
                existing[field] = val
```

Re-run `python3 -m pytest tests/unit/test_frontmatter_merge.py -v` and confirm PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_frontmatter_merge.py scripts/lib/frontmatter.py
git commit -m "test: lock per-PMID quotes merge to wholesale replace"
```

---

## Task 2: Linter check for quote presence and shape

Add one check that reads the already-parsed node frontmatter (`self.node_fm`) and emits: **info** when a `verified: true` reference has no quotes, and **warning** when a `quotes` value is malformed (>3 items, empty `text`, or bad `source`).

**Files:**
- Modify: `scripts/linter_kg.py` (add to `ALL_CHECKS` list at lines 43-55; add method after `check_duplicate_entities`)
- Test: `tests/unit/test_linter_quotes.py` (create)

**Interfaces:**
- Consumes: `KGLinter(kg_folder)` constructor, `self.node_fm: dict[str, dict]` (node_id → frontmatter), and `self._add(check_id, severity, message, node_id=..., details=...)` from `scripts/linter_kg.py`.
- Produces: a `check_quote_health` method and `"quote_health"` entry in `ALL_CHECKS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_linter_quotes.py`:

```python
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from linter_kg import KGLinter


def _write_kg(tmp_path, node_frontmatter_blocks):
    """Create a minimal KG: manifest.json + one node file per frontmatter block."""
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    nodes_index = []
    for i, fm_yaml in enumerate(node_frontmatter_blocks, start=1):
        nid = f"node_{i:03d}"
        (kg / "nodes" / f"{nid}.md").write_text(f"---\n{fm_yaml}---\n\nbody\n", encoding="utf-8")
        nodes_index.append({"id": nid, "title": "t", "file": f"nodes/{nid}.md",
                            "tags": ["x"], "summary": "s", "keywords": ["k"],
                            "pubmed_ids": ["111"], "evaluation_status": "passed"})
    manifest = {"kg_name": "KG_Test", "topic": "t", "created": "2026-01-01",
                "updated": "2026-01-01", "version": 1, "nodes": nodes_index, "edges": [],
                "statistics": {"total_nodes": len(nodes_index), "total_edges": 0,
                               "total_unique_pmids": 1, "evaluation_passed": len(nodes_index),
                               "evaluation_failed": 0}}
    (kg / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(kg)


def _findings(kg, check="quote_health"):
    linter = KGLinter(kg)
    linter.check_quote_health()
    return [f for f in linter.findings if f["check_id"] == check]


def test_verified_ref_without_quotes_is_info(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"


def test_verified_ref_with_quotes_is_clean(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: "a real sentence"\n        source: "abstract"\n')
    kg = _write_kg(tmp_path, [fm])
    assert _findings(kg) == []


def test_unverified_ref_without_quotes_is_clean(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: false\n')
    kg = _write_kg(tmp_path, [fm])
    assert _findings(kg) == []


def test_too_many_quotes_is_warning(tmp_path):
    items = "".join(f'      - text: "q{n}"\n        source: "abstract"\n' for n in range(4))
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          f'    quotes:\n{items}')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)


def test_bad_source_is_warning(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: "x"\n        source: "wikipedia"\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)


def test_empty_text_is_warning(tmp_path):
    fm = ('id: "node_001"\n'
          'pubmed_ids:\n  - pmid: "111"\n    verified: true\n'
          '    quotes:\n      - text: ""\n        source: "abstract"\n')
    kg = _write_kg(tmp_path, [fm])
    findings = _findings(kg)
    assert any(f["severity"] == "warning" for f in findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_linter_quotes.py -v`
Expected: FAIL with `AttributeError: 'KGLinter' object has no attribute 'check_quote_health'`.

- [ ] **Step 3: Register the check**

In `scripts/linter_kg.py`, add `"quote_health"` to the `ALL_CHECKS` list (after `"duplicate_entities"`, lines 43-55):

```python
    ALL_CHECKS = [
        "orphan_nodes",
        "under_referenced",
        "dangling_edges",
        "file_manifest_drift",
        "stats_drift",
        "ledger_drift",
        "evaluation_gaps",
        "quarantine_drift",
        "evidence_tier_imbalance",
        "tag_coverage_gaps",
        "duplicate_entities",
        "quote_health",
    ]
```

- [ ] **Step 4: Implement the check method**

In `scripts/linter_kg.py`, add this method immediately after `check_duplicate_entities` (around line 455):

```python
    # ------------------------------------------------------------------
    # Check 12: Supporting-quote presence and shape
    # ------------------------------------------------------------------
    def check_quote_health(self):
        valid_sources = {"abstract", "full_text"}
        for nid, fm in self.node_fm.items():
            for ref in fm.get("pubmed_ids", []) or []:
                pmid = ref.get("pmid", "?")
                quotes = ref.get("quotes")

                # Shape validation (warning) — only when quotes is present.
                if quotes is not None:
                    problems = []
                    if not isinstance(quotes, list):
                        problems.append("quotes is not a list")
                    else:
                        if len(quotes) > 3:
                            problems.append(f"{len(quotes)} quotes (max 3)")
                        for q in quotes:
                            if not isinstance(q, dict):
                                problems.append("quote item is not a mapping")
                                continue
                            if not (q.get("text") or "").strip():
                                problems.append("quote has empty text")
                            if q.get("source") not in valid_sources:
                                problems.append(f"bad source {q.get('source')!r}")
                    if problems:
                        self._add("quote_health", "warning",
                                  f"Node {nid} PMID {pmid} has malformed quotes: "
                                  + "; ".join(problems),
                                  node_id=nid,
                                  details={"pmid": pmid, "problems": problems})
                        continue

                # Presence (info) — verified ref carrying no quotes.
                if ref.get("verified") is True and not quotes:
                    self._add("quote_health", "info",
                              f"Node {nid} PMID {pmid} is verified but has no supporting quote",
                              node_id=nid,
                              details={"pmid": pmid})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_linter_quotes.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all pre-existing tests plus the new ones).

- [ ] **Step 7: Commit**

```bash
git add scripts/linter_kg.py tests/unit/test_linter_quotes.py
git commit -m "feat: linter check for supporting-quote presence and shape"
```

---

## Task 3: Add commented quotes example to the node template

Show the `quotes` shape in the template without implying every node must have it (it is evaluator-populated), and prove the template still parses.

**Files:**
- Modify: `templates/node_template.md:6-9`
- Test: `tests/unit/test_node_template.py` (create)

**Interfaces:**
- Consumes: `parse(file_path) -> (dict, str)` from `scripts/lib/frontmatter.py`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_node_template.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib.frontmatter import parse

TEMPLATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                        "templates", "node_template.md"))


def test_template_parses_and_documents_quotes():
    fm, body = parse(TEMPLATE)
    # Template still parses as valid frontmatter with a pubmed_ids list.
    assert isinstance(fm.get("pubmed_ids"), list)
    # The quotes shape is documented somewhere in the template text.
    raw = open(TEMPLATE, encoding="utf-8").read()
    assert "quotes:" in raw
    assert "source:" in raw and "abstract" in raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_node_template.py -v`
Expected: FAIL on `assert "quotes:" in raw` (template has no quotes example yet).

- [ ] **Step 3: Edit the template**

In `templates/node_template.md`, replace the `pubmed_ids` block (lines 6-9):

```yaml
pubmed_ids:
  - pmid: "XXXXXXXX"
    supports: "Specific claim this PMID backs"
    verified: true
    evidence_tier: "unclassified"
```

with (adds a commented `quotes` example — populated by the evaluator, 1-3 verbatim excerpts):

```yaml
pubmed_ids:
  - pmid: "XXXXXXXX"
    supports: "Specific claim this PMID backs"
    verified: true
    evidence_tier: "unclassified"
    # quotes added by the evaluator for verified refs — 1-3 verbatim excerpts:
    # quotes:
    #   - text: "Verbatim sentence from the source that backs the claim."
    #     source: "abstract"   # abstract | full_text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_node_template.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/node_template.md tests/unit/test_node_template.py
git commit -m "docs: document quotes field in node template"
```

---

## Task 4: Teach the evaluate worker to capture and write quotes

Prose changes to the worker command: capture quotes in E2, write them back in E6. These are LLM instructions (no unit test); verify by reading and by a `--test` smoke run.

**Files:**
- Modify: `.claude/commands/evaluate-kg-worker.md:128` (end of E2 verdict section) and `:206-217` (E6 update example + deep-merge note)

**Interfaces:**
- Consumes: the `update_frontmatter.py` per-PMID deep-merge contract documented at E6.
- Produces: node frontmatter `pubmed_ids[].quotes` consumed by Task 2's linter check.

- [ ] **Step 1: Add the capture instruction to E2**

In `.claude/commands/evaluate-kg-worker.md`, immediately after line 128 (`Assign a verdict per PMID: ...`), insert:

```markdown

For each PMID you rate `supported` or `partially_supported`, capture **1-3 verbatim excerpts** (each ≤ ~2 sentences) from the article text you just read — the exact sentence(s) your verdict rests on. Record each excerpt with its source section: `abstract` or `full_text`. Copy text verbatim (no paraphrasing). You already have the article text in context, so this requires no additional fetch. Do **not** capture quotes for `not_supported` or `unrelated` references.
```

- [ ] **Step 2: Carry quotes into the E5 results (optional audit mirror)**

In `.claude/commands/evaluate-kg-worker.md`, update the E5 `pmid_checks` example (lines 174-181) so the per-PMID object shows the captured quotes alongside `reasoning`:

```json
      {
        "pmid": "35486828",
        "exists": true,
        "article_title": "...",
        "verdict": "supported",
        "reasoning": "The abstract states X, which directly supports the node's claim about X.",
        "quotes": [
          {"text": "At 12 weeks, 40.2% of patients achieved a response versus 11.1% with placebo.", "source": "abstract"}
        ]
      }
```

- [ ] **Step 3: Add quotes to the E6 write-back example**

In `.claude/commands/evaluate-kg-worker.md`, replace the "Passed evaluation" example (lines 208-210) with one that includes `quotes` in the per-PMID update:

```bash
# Passed evaluation — ensure quarantined is false (un-quarantine on re-eval)
python3 scripts/update_frontmatter.py KG_X/nodes/node_001_foo.md \
  '{"evaluation_status": "passed", "quarantined": false, "pubmed_ids": [{"pmid": "35486828", "verified": true, "quotes": [{"text": "At 12 weeks, 40.2% of patients achieved a response versus 11.1% with placebo.", "source": "abstract"}]}]}'
```

- [ ] **Step 4: Update the deep-merge note for quotes**

In `.claude/commands/evaluate-kg-worker.md`, replace the deep-merge note (line 217):

```markdown
The script deep-merges the updates into the existing frontmatter — it matches PMID entries by their `pmid` value, so the `verified` field and the `quotes` list are set on matching entries. On re-evaluation the `quotes` list is replaced wholesale (the latest verification wins), not appended. New entries are appended.
```

- [ ] **Step 5: Verify the edits read correctly**

Run: `git diff .claude/commands/evaluate-kg-worker.md`
Confirm: E2 has the capture instruction, E5 shows `quotes` in `pmid_checks`, E6 shows `quotes` in the passed-node update example, and the deep-merge note mentions wholesale replacement. Confirm no instruction tells the worker to write quotes for failed/unrelated refs.

- [ ] **Step 6: Commit**

```bash
git add .claude/commands/evaluate-kg-worker.md
git commit -m "feat: capture and persist supporting quotes in evaluate worker"
```

---

## Task 5: Smoke-test the end-to-end capture path

Verify the worker change does not break the existing `--test` evaluation flow and that a quote, once written, survives merge + lint cleanly. This task has no production code; it is a verification gate.

**Files:**
- None modified. Uses existing `tests/fixtures/` and `scripts/`.

- [ ] **Step 1: Confirm the full unit suite is green**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (Tasks 1-3 tests plus all pre-existing tests).

- [ ] **Step 2: Hand-verify merge + lint on a quoted node**

Create a throwaway node file with a verified PMID and a valid quote, then run the linter against a minimal KG containing it (reuse the harness from `tests/unit/test_linter_quotes.py` interactively, or build a temp KG by hand). Confirm: a well-formed quote yields **no** `quote_health` finding; removing the quote yields exactly one **info** finding; adding a 4th quote yields a **warning**.

Run (example, against any KG folder that has quoted nodes):
```bash
python3 scripts/linter_kg.py <kg_folder> --severity info | python3 -m json.tool
```
Expected: `quote_health` findings appear only as described; `errors` and `warnings` counts are unaffected by info-level quote findings.

- [ ] **Step 3: Confirm the manifest is untouched by quotes**

Run: `git grep -n "quotes" schemas/ ; echo "exit: $?"`
Expected: no matches in `schemas/` (exit 1) — quotes never entered the manifest schema.

- [ ] **Step 4: Final commit (if any verification artifacts were added)**

If Steps 1-3 produced no file changes, no commit is needed. Otherwise:
```bash
git add -A
git commit -m "test: smoke-verify supporting-quote capture path"
```

---

## Self-Review Notes

- **Spec coverage:** data model → Tasks 3/4; capture (E2) → Task 4; write-back (E6) → Task 4; merge semantics → Task 1; no-schema-change → Global Constraints + Task 5 Step 3; linter info+warning → Task 2; template → Task 3; going-forward-only → Global Constraints (no backfill task by design).
- **Out of scope (per spec):** the digest (roadmap item 2) and any backfill of existing nodes.
