# nono-pi Hypothesis-Evaluation Loops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the shipped `nono-pi` module with two halt-for-user, KG-grounded adversarial refinement loops — an **aims loop** (replaces the single-pass gap gate) and a mode-agnostic **draft loop** — plus a deterministic **evidence-strength score** the aims critic consults.

**Architecture:** Same split as base `nono-pi`: the evaluation/revision reasoning stays with the running agent (driven by `SKILL.md`); the CLI only does deterministic bookkeeping (`eval record`/`eval decide`, ledger loop-state, `status` rendering) and one deterministic computation (`evidence-score`). Loop state lives in `pi_run.json` (additive, backward-compatible) so existing folders stay resumable.

**Tech Stack:** Python ≥3.14, argparse CLI (one module per subcommand, `main(argv)->int`), `jsonschema` Draft 2020-12, packaged data via `importlib.resources`, pytest. Dev venv already at `pi/.venv-dev`.

## Global Constraints

- Inherits all base-module constraints (from `2026-07-08-nono-pi-design.md`): model-agnostic (no LLM/served-model calls in any `nono_pi` module); runtime dep limited to `jsonschema>=4.0`; packaged data via `nono_pi.paths.data_file`; disk is source of truth for reconciled fields; CLI logic in importable functions, `main` a thin wrapper; tests import functions and use `tmp_path`.
- `schema_version` stays `1`; the two new ledger keys (`aims_loop`, `draft_loop`) are **optional/additive** so ledgers written by the base module still load and validate.
- Loop `status` ∈ `pending | in_progress | accepted | stopped`. Round `decision` ∈ `approved | accepted | stopped | null`.
- Loop state is **ledger-driven**; `reconcile()` must not touch `aims_loop`/`draft_loop`.
- New command modules (`eval`, `evidence_score`) are **registered in `__main__.COMMANDS` only in Task 5**, together with the `SKILL.md` rework — because the command-sync guard test (`test_skill_commands.py`) asserts COMMANDS and SKILL.md code-span references are equal in both directions, they must change in the same commit. Tasks 2–4 test their modules via direct import, leaving `COMMANDS`/`SKILL.md` untouched so the guard test stays green.
- `pyproject.toml` already globs `schemas/*` and `templates/*` in package-data — new schema/template files need no pyproject change.
- Two loop names only: `aims`, `draft`. Ledger key = `<loop>_loop`.
- Work is on branch `feat/nono-pi-hypothesis-loop`. Run tests with `cd pi && .venv-dev/bin/pytest`.

---

### Task 1: Ledger loop-state + schema extension

**Files:**
- Modify: `pi/src/nono_pi/lib/ledger.py` (`new_ledger`)
- Modify: `pi/src/nono_pi/data/schemas/pi_run_schema.json`
- Test: `pi/tests/unit/test_ledger_loops.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `new_ledger()` output now contains `aims_loop` and `draft_loop`, each `{"status": "pending", "rounds": []}`; `pi_run_schema.json` validates them and still validates a base-module ledger that lacks them.

- [ ] **Step 1: Write the failing test** — `pi/tests/unit/test_ledger_loops.py`

```python
from nono_pi.lib import ledger as L


def test_new_ledger_has_loop_state_and_validates():
    led = L.new_ledger("/out")
    assert led["aims_loop"] == {"status": "pending", "rounds": []}
    assert led["draft_loop"] == {"status": "pending", "rounds": []}
    L.validate_ledger(led)  # must not raise


def test_schema_backward_compatible_with_base_ledger():
    # A ledger written by the base module (no loop keys) must still validate.
    led = L.new_ledger("/out")
    del led["aims_loop"]
    del led["draft_loop"]
    L.validate_ledger(led)


def test_reconcile_leaves_loops_untouched(tmp_path):
    led = L.new_ledger(str(tmp_path))
    led["aims_loop"]["status"] = "in_progress"
    led["aims_loop"]["rounds"].append({"round": 0, "verdicts": {}, "weaknesses": [],
                                        "proposed_revision": "x", "decision": None})
    L.reconcile(str(tmp_path), led)
    assert led["aims_loop"]["status"] == "in_progress"
    assert len(led["aims_loop"]["rounds"]) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_ledger_loops.py -v`
Expected: FAIL — `KeyError: 'aims_loop'` in the first test.

- [ ] **Step 3: Add the two keys to `new_ledger`** in `pi/src/nono_pi/lib/ledger.py`

Insert after the `"sections": {},` line (last entry in the returned dict), keeping it inside the dict:

```python
        "sections": {},
        "aims_loop": {"status": "pending", "rounds": []},
        "draft_loop": {"status": "pending", "rounds": []},
```

- [ ] **Step 4: Extend `pi_run_schema.json`** — add the two optional properties and shared `$defs`.

In `pi/src/nono_pi/data/schemas/pi_run_schema.json`, add these two properties inside `"properties"` (after `"sections"`):

```json
    "sections": {
      "type": "object",
      "additionalProperties": {"type": "string", "enum": ["requested", "written"]}
    },
    "aims_loop": {"$ref": "#/$defs/loop"},
    "draft_loop": {"$ref": "#/$defs/loop"}
```

Then add a top-level `"$defs"` key (sibling of `"properties"`, before the closing brace):

```json
  "$defs": {
    "loop": {
      "type": "object",
      "required": ["status", "rounds"],
      "properties": {
        "status": {"type": "string", "enum": ["pending", "in_progress", "accepted", "stopped"]},
        "rounds": {"type": "array", "items": {"$ref": "#/$defs/round"}}
      }
    },
    "round": {
      "type": "object",
      "required": ["round", "decision"],
      "properties": {
        "round": {"type": "integer", "minimum": 0},
        "verdicts": {"type": "object"},
        "weaknesses": {"type": "array"},
        "proposed_revision": {"type": "string"},
        "decision": {"type": ["string", "null"], "enum": ["approved", "accepted", "stopped", null]},
        "note": {"type": ["string", "null"]}
      }
    }
  }
```

(The ledger schema keeps round validation light — deep content validation of a round *input* lives in `eval_round_schema.json`, Task 2. `aims_loop`/`draft_loop` are NOT added to the top-level `"required"` array, preserving backward compatibility.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_ledger_loops.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full suite**

Run: `cd pi && .venv-dev/bin/pytest -q`
Expected: PASS (all base-module tests + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /home/dadi/nono
git add pi/src/nono_pi/lib/ledger.py pi/src/nono_pi/data/schemas/pi_run_schema.json pi/tests/unit/test_ledger_loops.py
git commit -m "feat(pi): add aims_loop/draft_loop state to ledger + schema"
```

---

### Task 2: `eval` command (record + decide) + round schema + report template

**Files:**
- Create: `pi/src/nono_pi/cli/eval.py`
- Create: `pi/src/nono_pi/data/schemas/eval_round_schema.json`
- Create: `pi/src/nono_pi/data/templates/evaluation_report.md`
- Test: `pi/tests/unit/test_eval.py`

**Interfaces:**
- Consumes: `nono_pi.lib.ledger` (`read_ledger`, `write_ledger`), `nono_pi.paths.data_file`.
- Produces:
  - `nono_pi.cli.eval.record_round(out_dir, loop, round_input) -> dict` — validates `round_input` against `eval_round_schema.json`, appends a round (auto-numbered, `decision=None`) to `<loop>_loop.rounds`, sets loop `status="in_progress"`, renders `<out>/<loop>_evaluation.md`, returns the stored round.
  - `nono_pi.cli.eval.decide_round(out_dir, loop, decision, note=None) -> dict` — sets the latest round's `decision`/`note`; `accepted`/`stopped` set loop `status` accordingly, `approved` keeps `in_progress`; re-renders the report.
  - `LOOPS = ("aims", "draft")`; `main(argv=None) -> int` with `record` and `decide` subactions.
  - `round_input` shape (validated): `{"verdicts": {dim: {"verdict","rationale","citations",(score)}}, "weaknesses": [{"issue","fix",(dimension),(closable_by_analysis)}], "proposed_revision": str}`.

- [ ] **Step 1: Create `pi/src/nono_pi/data/schemas/eval_round_schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "nono-pi evaluation round input",
  "type": "object",
  "required": ["verdicts", "weaknesses", "proposed_revision"],
  "properties": {
    "verdicts": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {
        "type": "object",
        "required": ["verdict", "rationale", "citations"],
        "properties": {
          "verdict": {"type": "string", "enum": ["sound", "weak", "contradicted", "unclear"]},
          "score": {"type": ["number", "null"]},
          "rationale": {"type": "string", "minLength": 1},
          "citations": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "weaknesses": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["issue", "fix"],
        "properties": {
          "issue": {"type": "string"},
          "dimension": {"type": "string"},
          "fix": {"type": "string"},
          "closable_by_analysis": {"type": "boolean"}
        }
      }
    },
    "proposed_revision": {"type": "string"}
  }
}
```

- [ ] **Step 2: Create `pi/src/nono_pi/data/templates/evaluation_report.md`**

```markdown
# {loop} evaluation — status: {status}

{rounds}
```

- [ ] **Step 3: Write the failing test** — `pi/tests/unit/test_eval.py`

```python
import json

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.eval import record_round, decide_round


def _round():
    return {
        "verdicts": {"soundness": {"verdict": "weak", "rationale": "thin support",
                                   "citations": ["node_1", "12345"]}},
        "weaknesses": [{"issue": "aim 2 unsupported", "fix": "add mechanism",
                        "closable_by_analysis": True}],
        "proposed_revision": "Tighten aim 2 around the mechanism.",
    }


def test_record_round_appends_numbers_and_renders(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    r0 = record_round(str(out), "aims", _round())
    r1 = record_round(str(out), "aims", _round())
    assert r0["round"] == 0 and r1["round"] == 1
    assert r0["decision"] is None
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["status"] == "in_progress"
    assert len(led["aims_loop"]["rounds"]) == 2
    report = (out / "aims_evaluation.md").read_text()
    assert "# aims evaluation" in report
    assert "Round 0" in report and "Tighten aim 2" in report


def test_decide_transitions_status(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "approved")
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["rounds"][-1]["decision"] == "approved"
    assert led["aims_loop"]["status"] == "in_progress"
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "accepted", note="premise sound")
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["status"] == "accepted"
    assert led["aims_loop"]["rounds"][-1]["note"] == "premise sound"


def test_decide_without_rounds_raises(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(ValueError):
        decide_round(str(out), "draft", "accepted")


def test_record_rejects_bad_round(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(Exception):
        record_round(str(out), "aims", {"weaknesses": [], "proposed_revision": "x"})  # no verdicts
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nono_pi.cli.eval'`.

- [ ] **Step 5: Implement `pi/src/nono_pi/cli/eval.py`**

```python
"""`nono-pi eval` — record hypothesis/draft evaluation rounds and user decisions."""
import argparse
import json
import os

from nono_pi.lib import ledger as L
from nono_pi.paths import data_file

_SCHEMA = data_file("schemas", "eval_round_schema.json")
_TEMPLATE = data_file("templates", "evaluation_report.md")
LOOPS = ("aims", "draft")


def _loop_key(loop):
    return f"{loop}_loop"


def _validate_round(doc):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(doc)


def _render_report(out_dir, loop, lp):
    tpl = open(_TEMPLATE, encoding="utf-8").read()
    blocks = []
    for rnd in lp["rounds"]:
        vlines = []
        for dim, v in rnd.get("verdicts", {}).items():
            cites = ", ".join(v.get("citations", [])) or "—"
            vlines.append(f"- **{dim}:** {v['verdict']} — {v['rationale']} ({cites})")
        wlines = [f"- {w['issue']} → {w['fix']}" for w in rnd.get("weaknesses", [])]
        blocks.append(
            f"## Round {rnd['round']} — decision: {rnd.get('decision') or 'pending'}\n\n"
            f"### Verdicts\n" + ("\n".join(vlines) or "_none_") + "\n\n"
            f"### Weaknesses\n" + ("\n".join(wlines) or "_none_") + "\n\n"
            f"### Proposed revision\n{rnd['proposed_revision']}\n"
        )
    md = tpl.format(loop=loop, status=lp["status"], rounds="\n".join(blocks))
    with open(os.path.join(out_dir, f"{loop}_evaluation.md"), "w", encoding="utf-8") as fh:
        fh.write(md)


def record_round(out_dir, loop, round_input):
    _validate_round(round_input)
    led = L.read_ledger(out_dir)
    lp = led.setdefault(_loop_key(loop), {"status": "pending", "rounds": []})
    rnd = {
        "round": len(lp["rounds"]),
        "verdicts": round_input["verdicts"],
        "weaknesses": round_input["weaknesses"],
        "proposed_revision": round_input["proposed_revision"],
        "decision": None,
        "note": None,
    }
    lp["rounds"].append(rnd)
    lp["status"] = "in_progress"
    L.write_ledger(out_dir, led)
    _render_report(out_dir, loop, lp)
    return rnd


def decide_round(out_dir, loop, decision, note=None):
    led = L.read_ledger(out_dir)
    lp = led.get(_loop_key(loop))
    if not lp or not lp.get("rounds"):
        raise ValueError(f"no {loop} rounds to decide on; run 'nono-pi eval record' first")
    rnd = lp["rounds"][-1]
    rnd["decision"] = decision
    if note is not None:
        rnd["note"] = note
    lp["status"] = decision if decision in ("accepted", "stopped") else "in_progress"
    L.write_ledger(out_dir, led)
    _render_report(out_dir, loop, lp)
    return rnd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi eval")
    sub = ap.add_subparsers(dest="action", required=True)
    r = sub.add_parser("record", help="append an evaluation round")
    r.add_argument("out_dir")
    r.add_argument("--loop", choices=LOOPS, required=True)
    r.add_argument("--input", required=True, help="path to a round JSON")
    d = sub.add_parser("decide", help="record the user's decision on the latest round")
    d.add_argument("out_dir")
    d.add_argument("--loop", choices=LOOPS, required=True)
    d.add_argument("--decision", choices=["approved", "accepted", "stopped"], required=True)
    d.add_argument("--note", default=None)
    args = ap.parse_args(argv)

    if args.action == "record":
        with open(args.input, encoding="utf-8") as fh:
            doc = json.load(fh)
        rnd = record_round(args.out_dir, args.loop, doc)
        print(f"Recorded {args.loop} round {rnd['round']} "
              f"→ {os.path.join(args.out_dir, f'{args.loop}_evaluation.md')}")
    else:
        rnd = decide_round(args.out_dir, args.loop, args.decision, note=args.note)
        print(f"{args.loop} round {rnd['round']} decision: {rnd['decision']}")
    return 0
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_eval.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
cd /home/dadi/nono
git add pi/src/nono_pi/cli/eval.py pi/src/nono_pi/data/schemas/eval_round_schema.json pi/src/nono_pi/data/templates/evaluation_report.md pi/tests/unit/test_eval.py
git commit -m "feat(pi): eval record/decide commands + round schema + report template"
```

---

### Task 3: `evidence-score` command

**Files:**
- Create: `pi/src/nono_pi/cli/evidence_score.py`
- Test: `pi/tests/unit/test_evidence_score.py`

**Interfaces:**
- Consumes: KG `manifest.json` node fields (`evidence_tier`, `pubmed_ids`, `evaluation_status`, `quarantined` — from the librarian's `graph_schema.json`).
- Produces:
  - `score_node(node) -> (float, dict)` — deterministic strength score in [0,1] + a `factors` dict.
  - `score_kg(kg_dir) -> dict` — `{node_id: {"score", "factors"}}` for one KG folder.
  - `write_scores(out_dir, slug) -> (dict, float)` — writes `<out>/kgs/<slug>/_evidence_score.json`, returns scores + mean.
  - `TIER_WEIGHT`, `EVAL_FACTOR` maps; `main(argv=None) -> int` (`--kg <slug>`; default all built KGs).

- [ ] **Step 1: Write the failing test** — `pi/tests/unit/test_evidence_score.py`

```python
import json

from nono_pi.cli.evidence_score import score_node, write_scores, _kg_slugs


def test_score_node_strong_vs_weak():
    strong, _ = score_node({"id": "node_1", "evidence_tier": "meta_analysis",
                            "pubmed_ids": ["1", "2", "3"], "evaluation_status": "passed"})
    weak, _ = score_node({"id": "node_2", "evidence_tier": "opinion",
                          "pubmed_ids": ["9"], "evaluation_status": "failed"})
    assert strong > weak
    assert 0.0 <= weak <= strong <= 1.0
    assert strong == 1.0  # meta_analysis * passed * not-quarantined * (0.5+0.5*1)


def test_score_node_quarantine_penalty():
    base, _ = score_node({"id": "n", "evidence_tier": "rct", "pubmed_ids": ["1", "2", "3"],
                          "evaluation_status": "passed"})
    quar, factors = score_node({"id": "n", "evidence_tier": "rct", "pubmed_ids": ["1", "2", "3"],
                                "evaluation_status": "passed", "quarantined": True})
    assert quar < base
    assert factors["quarantined"] is True


def test_score_node_missing_fields_use_defaults():
    s, factors = score_node({"id": "node_x"})
    assert factors["evidence_tier"] == "unclassified"
    assert factors["evaluation_status"] == "pending"
    assert factors["n_pmids"] == 0
    assert 0.0 <= s <= 1.0


def test_write_scores_and_slug_discovery(tmp_path):
    out = tmp_path / "proj"
    kg = out / "kgs" / "sub-a"
    kg.mkdir(parents=True)
    manifest = {"nodes": [
        {"id": "node_1", "evidence_tier": "rct", "pubmed_ids": ["1", "2"], "evaluation_status": "passed"},
        {"id": "node_2", "evidence_tier": "opinion", "pubmed_ids": [], "evaluation_status": "pending"},
    ]}
    (kg / "manifest.json").write_text(json.dumps(manifest))
    scores, mean = write_scores(str(out), "sub-a")
    assert set(scores) == {"node_1", "node_2"}
    assert (kg / "_evidence_score.json").exists()
    assert 0.0 <= mean <= 1.0
    assert _kg_slugs(str(out)) == ["sub-a"]  # only dirs with manifest.json
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_evidence_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nono_pi.cli.evidence_score'`.

- [ ] **Step 3: Implement `pi/src/nono_pi/cli/evidence_score.py`**

```python
"""`nono-pi evidence-score <out>` — deterministic per-node evidence-strength scores.

A reproducible proxy for evidence robustness (NOT a statistical hypothesis test),
computed purely from fields the librarian populates in each KG's manifest. Fed to
the aims-loop critic as quantitative grounding.
"""
import argparse
import json
import os
import sys

# Base weight by highest evidence tier among a node's references.
TIER_WEIGHT = {
    "meta_analysis": 1.0, "rct": 0.9, "cohort": 0.7, "case_series": 0.5,
    "case_report": 0.4, "review": 0.5, "opinion": 0.3, "unclassified": 0.3,
}
EVAL_FACTOR = {"passed": 1.0, "pending": 0.7, "failed": 0.3}


def score_node(node):
    """Return (score in [0,1], factors dict) for one manifest node."""
    tier = node.get("evidence_tier", "unclassified")
    tier_w = TIER_WEIGHT.get(tier, 0.3)
    n_pmids = len(node.get("pubmed_ids", []) or [])
    sources_factor = min(n_pmids, 3) / 3
    eval_status = node.get("evaluation_status", "pending")
    eval_f = EVAL_FACTOR.get(eval_status, 0.7)
    quar = bool(node.get("quarantined", False))
    quar_f = 0.1 if quar else 1.0
    score = round(tier_w * eval_f * quar_f * (0.5 + 0.5 * sources_factor), 3)
    return score, {
        "evidence_tier": tier, "tier_weight": tier_w,
        "n_pmids": n_pmids, "sources_factor": round(sources_factor, 3),
        "evaluation_status": eval_status, "eval_factor": eval_f,
        "quarantined": quar,
    }


def score_kg(kg_dir):
    with open(os.path.join(kg_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    scores = {}
    for node in manifest.get("nodes", []):
        s, factors = score_node(node)
        scores[node["id"]] = {"score": s, "factors": factors}
    return scores


def write_scores(out_dir, slug):
    kg_dir = os.path.join(out_dir, "kgs", slug)
    scores = score_kg(kg_dir)
    with open(os.path.join(kg_dir, "_evidence_score.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
    mean = round(sum(v["score"] for v in scores.values()) / len(scores), 3) if scores else 0.0
    return scores, mean


def _kg_slugs(out_dir):
    kgs_dir = os.path.join(out_dir, "kgs")
    if not os.path.isdir(kgs_dir):
        return []
    return sorted(d for d in os.listdir(kgs_dir)
                  if os.path.exists(os.path.join(kgs_dir, d, "manifest.json")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi evidence-score")
    ap.add_argument("out_dir")
    ap.add_argument("--kg", default=None, help="one KG slug; default = all built KGs")
    args = ap.parse_args(argv)
    slugs = [args.kg] if args.kg else _kg_slugs(args.out_dir)
    if not slugs:
        print("nono-pi evidence-score: no built KGs found", file=sys.stderr)
        return 2
    for slug in slugs:
        scores, mean = write_scores(args.out_dir, slug)
        print(f"{slug}: {len(scores)} nodes, mean strength {mean} "
              f"→ kgs/{slug}/_evidence_score.json")
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_evidence_score.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/dadi/nono
git add pi/src/nono_pi/cli/evidence_score.py pi/tests/unit/test_evidence_score.py
git commit -m "feat(pi): evidence-score command (deterministic KG evidence strength)"
```

---

### Task 4: `status` loops section

**Files:**
- Modify: `pi/src/nono_pi/cli/status.py`
- Test: `pi/tests/unit/test_status_loops.py` (new)

**Interfaces:**
- Consumes: ledger `aims_loop`/`draft_loop` (may be absent in old ledgers — use `.get`).
- Produces: `status_report` output gains an aims/draft loops section when present; add helper `_fmt_loop(led, key, label) -> str | None`. `status_report`'s return signature is unchanged (`(report_str, ledger)`).

- [ ] **Step 1: Write the failing test** — `pi/tests/unit/test_status_loops.py`

```python
from nono_pi.cli.init import scaffold
from nono_pi.cli.intake import record_intake
from nono_pi.cli.eval import record_round, decide_round
from nono_pi.cli.status import status_report


def _round():
    return {"verdicts": {"soundness": {"verdict": "sound", "rationale": "ok", "citations": ["node_1"]}},
            "weaknesses": [], "proposed_revision": "none"}


def test_status_shows_loop_rounds(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="grant", mode="create")
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "accepted")
    report, led = status_report(str(out))
    assert "aims loop: accepted" in report
    assert "round 0" in report
    assert "soundness=sound" in report


def test_status_ok_when_loops_absent(tmp_path):
    # An old-style ledger without loop keys must not break status.
    out = tmp_path / "proj"
    scaffold(str(out))
    import json
    p = out / "pi_run.json"
    led = json.loads(p.read_text())
    led.pop("aims_loop"); led.pop("draft_loop")
    p.write_text(json.dumps(led))
    report, _ = status_report(str(out))
    assert "nono-pi status" in report  # no crash
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_status_loops.py -v`
Expected: FAIL — assertion error (`"aims loop: accepted"` not in report).

- [ ] **Step 3: Extend `pi/src/nono_pi/cli/status.py`**

Add this helper after `_fmt_kgs`:

```python
def _fmt_loop(led, key, label):
    lp = led.get(key)
    if not lp:
        return None
    lines = [f"  {label}: {lp.get('status', 'pending')}"]
    for rnd in lp.get("rounds", []):
        dims = ", ".join(f"{d}={v.get('verdict')}"
                         for d, v in rnd.get("verdicts", {}).items())
        lines.append(f"    round {rnd.get('round')}: [{dims}] decision={rnd.get('decision')}")
    return "\n".join(lines)
```

In `status_report`, after the section/draft-version block builds `lines`, append the loop sections just before `return`:

```python
    for key, label in (("aims_loop", "aims loop"), ("draft_loop", "draft loop")):
        block = _fmt_loop(led, key, label)
        if block:
            lines.append(block)
    return "\n".join(lines), led
```

(Remove the old bare `return "\n".join(lines), led` so there is exactly one return.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_status_loops.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `cd pi && .venv-dev/bin/pytest -q`
Expected: PASS (all tests through Task 4; `test_skill_commands.py` still green — COMMANDS/SKILL untouched).

- [ ] **Step 6: Commit**

```bash
cd /home/dadi/nono
git add pi/src/nono_pi/cli/status.py pi/tests/unit/test_status_loops.py
git commit -m "feat(pi): status shows aims/draft loop rounds"
```

---

### Task 5: Register commands + rework SKILL.md into the loops

**Files:**
- Modify: `pi/src/nono_pi/cli/__main__.py` (`COMMANDS`)
- Modify: `pi/.claude/skills/nono-pi/SKILL.md`
- Test: `pi/tests/unit/test_skill_commands.py` (existing guard — no change; must stay green)

**Interfaces:**
- Consumes: all commands built in Tasks 2–3.
- Produces: `eval` and `evidence-score` dispatchable via `nono-pi`; `SKILL.md` documents the aims loop (replacing Step 5), the draft loop (new Step 8.5), the review-then-stop path, and every command in code spans so the guard test's both-direction equality holds.

- [ ] **Step 1: Register the two commands** in `pi/src/nono_pi/cli/__main__.py`

Add to the `COMMANDS` dict (after `"mark": "mark",`):

```python
    "mark": "mark",
    "eval": "eval",
    "evidence-score": "evidence_score",
```

- [ ] **Step 2: Run the guard test to verify it FAILS**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_skill_commands.py -v`
Expected: FAIL — `test_skill_documents_every_command` fails: `{'eval', 'evidence-score'}` are in COMMANDS but not yet referenced in SKILL.md.

- [ ] **Step 3: Rework `SKILL.md`** — replace the Step 5 gap-gate section and add the draft loop.

In `pi/.claude/skills/nono-pi/SKILL.md`, **replace the entire "## Step 5 — Logic / gap gate" section** (from that heading up to but not including "## Step 6 — Significance & Innovation") with:

````markdown
## Step 4.5 — Score KG evidence strength

Before evaluating the premise, compute deterministic evidence-strength scores so
your judgment is calibrated (not hand-wavy):

```bash
nono-pi evidence-score <out>          # all built KGs → kgs/<slug>/_evidence_score.json
```

Read the scores; weight weakly-supported claims (low tier, single source,
quarantined) accordingly in the aims loop below.

## Step 5 — Aims loop (evaluate the hypothesis until it is sound)

Iterate on the hypothesis + Specific Aims, **halting for the user every round**.
In `revise` mode, evaluate the **existing** premise extracted from the ingested
draft rather than inventing one.

Each round:

1. **Evaluate** the current hypothesis/aims against the KGs across four lenses —
   *soundness/logic*, *novelty*, *significance*, *contradiction-check* — grounded
   in the KG nodes and the evidence-strength scores. Every verdict MUST cite KG
   node IDs / PMIDs; a claim with no citable basis cannot pass. Write a round
   JSON (`{"verdicts": {lens: {"verdict","rationale","citations",…}}, "weaknesses":
   [...], "proposed_revision": "..."}`) and record it:
   ```bash
   nono-pi eval record <out> --loop aims --input <round.json>
   ```
2. **Gap handoff:** if a weakness is closable only by further analysis, also emit
   the `nono-analyst` plan and note the gap gate:
   ```bash
   nono-pi analysis-plan <out> --input <analysis_input.json>
   nono-pi mark <out> --gate gaps
   ```
   Write `<out>/gaps_report.md` when the premise is contradicted or gapped.
3. **HALT.** Present the round's verdicts + proposed revision (they are rendered
   in `<out>/aims_evaluation.md`) and wait. Record the user's choice:
   ```bash
   nono-pi eval decide <out> --loop aims --decision approved   # apply revision, loop again
   nono-pi eval decide <out> --loop aims --decision accepted    # premise sound → continue
   nono-pi eval decide <out> --loop aims --decision stopped     # stop here
   ```
   On `approved`, revise the hypothesis/aims and start the next round. On
   `accepted` (mark the gate clear: `nono-pi mark <out> --gate clear`) continue to
   Step 6. A **review-only task** stops here: the rendered `aims_evaluation.md` is
   the deliverable.
````

- [ ] **Step 4: Add the draft loop** — insert a new section AFTER "## Step 8 — Write / revise" (before "## Scope").

````markdown
## Step 8.5 — Draft loop (review and refine the deliverable)

Refine the drafted deliverable with the same halt-each-round loop, driven by the
routed review skills. This loop is **mode-agnostic**: in `create` mode its seed
is the freshly written draft from Step 8; in `revise` mode its seed is the
ingested `draft/v000.<ext>` — i.e. revise mode's improvement *is* this loop, not
a separate pass.

Each round:

1. **Review** the current draft with the routed skills (grants:
   `grant-mock-reviewer`; papers: `scientific-manuscript-review` /
   `sci-paper-reviewer`) — reviewer simulation + coherence (does it test the
   hypothesis, aims↔methods coherence), grounded in the KGs and S&I. Record it:
   ```bash
   nono-pi eval record <out> --loop draft --input <round.json>
   ```
2. **HALT** and record the decision:
   ```bash
   nono-pi eval decide <out> --loop draft --decision approved   # apply, loop again
   nono-pi eval decide <out> --loop draft --decision accepted    # done
   nono-pi eval decide <out> --loop draft --decision stopped
   ```
   On `approved`, apply the routed revise-column skills and write the next version
   (`create`: update `draft/<section_key>.md`; `revise`: new `draft/v<NNN>.md`,
   never touching `v000`), then `nono-pi mark <out> --bump-draft` and loop again.

Finish by printing `nono-pi status <out>`.
````

- [ ] **Step 5: Run the guard test to verify it PASSES**

Run: `cd pi && .venv-dev/bin/pytest tests/unit/test_skill_commands.py -v`
Expected: PASS (3 passed) — every COMMANDS entry (`init`, `intake`, `route`, `orchestrate-kg`, `assemble-si`, `analysis-plan`, `status`, `mark`, `eval`, `evidence-score`) is now referenced in a SKILL.md code span, and SKILL.md references no non-command.

- [ ] **Step 6: Run the full suite**

Run: `cd pi && .venv-dev/bin/pytest -q`
Expected: PASS (all tests across the base module + Tasks 1–5).

- [ ] **Step 7: Commit**

```bash
cd /home/dadi/nono
git add pi/src/nono_pi/cli/__main__.py pi/.claude/skills/nono-pi/SKILL.md
git commit -m "feat(pi): wire eval/evidence-score + rework SKILL.md into aims+draft loops"
```

---

## Self-Review Notes

- **Spec coverage:** aims loop replacing gap gate (Task 5 SKILL Step 5 + Task 2 eval); draft loop mode-agnostic & unified with revise write step (Task 5 Step 8.5); halt-for-user each round (eval record→decide, Task 2); reasoning-based KG-grounded judging with mandatory citations (SKILL Step 5 + eval_round_schema `citations` required, Task 2); evidence-strength score (Task 3); data-stats deferred to nono-analyst via analysis-plan (SKILL Step 5 gap handoff, reuses base command); ledger loop-state + backward-compatible resumability (Task 1); status loops view (Task 4); review-as-evaluate-then-stop (SKILL Step 5, deliverable = aims_evaluation.md, Task 2 renders it). All spec §s map to tasks.
- **Placeholder scan:** none. `_evidence_score.json` field names verified against librarian `graph_schema.json` (`evidence_tier` enum, `pubmed_ids`, `evaluation_status`, `quarantined`).
- **Type consistency:** loop key `<loop>_loop` and `LOOPS=("aims","draft")` consistent across ledger (Task 1), eval (Task 2), status (Task 4); round shape (`round`/`verdicts`/`weaknesses`/`proposed_revision`/`decision`/`note`) identical in `new_ledger` seed, `eval.record_round`, `pi_run_schema.$defs.round`, and `eval_round_schema` (input subset); decision enum `approved|accepted|stopped(|null)` identical in eval CLI choices, `decide_round`, and the schema; `status_report` return signature `(str, dict)` unchanged.
