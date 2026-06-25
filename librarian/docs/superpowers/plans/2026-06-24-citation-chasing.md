# Citation Chasing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, read-only discovery feed that follows backward references from the cited corpus, ranks candidates by co-citation frequency (iCite RCR tiebreak), and surfaces a bounded candidate list in the log, run-record, and digest.

**Architecture:** A new deterministic script `scripts/chase_citations.py` (the `preflight.py` no-LLM/no-MCP, read-only-on-the-KG contract) collects `used` seed PMIDs, fetches each seed's `pubmed_pubmed_refs` via NCBI elink, dedups candidates against the ledger, ranks them (co-citation primary, iCite RCR tiebreak), bounds the list, and emits a JSON feed + a `citation` log op. The feed flows into a new optional `citation_candidates` run-record field and a digest section, exactly mirroring item 3's `retractions` flow.

**Tech Stack:** Python 3.13, `pytest`, `jsonschema`; NCBI E-utilities (elink) + NIH iCite (urllib); Markdown slash-command agents under `.claude/commands/`.

## Global Constraints

- Deterministic, no LLM, no MCP — the `preflight.py` contract. Copied from spec.
- **Read-only on the KG:** the sweep writes nothing to the ledger or node files. Its only outputs are stdout JSON and an optional `_log.md` entry.
- **No false empty feed:** an elink network/parse error exits non-zero and emits no feed and no log entry.
- Discovery stays within **NCBI elink** for links; **iCite** is a best-effort enrichment only.
- **iCite degradation:** any iCite failure sets `icite_status: "unavailable"`, all `rcr: null`, ranking falls back to co-citation then PMID, and the sweep still exits 0.
- **Backward references only** (`pubmed_pubmed_refs`); no forward (`citedin`) chasing.
- **Seeds** = ledger entries with `disposition == "used"`. **Dedup** candidates against the entire ledger (any disposition) so previously-rejected PMIDs never resurface.
- **No node-schema or ledger-schema change** — only the run-record reporting schema gains a field; no backfill.
- **Candidate ingestion is out of scope** — the feed is the deliverable.
- Run all tests from `/home/dadi/nono/libririan` with `python3 -m pytest tests/unit/ -v`.

---

## File Structure

- `schemas/run_record_schema.json` — **modify**; add optional `citation_candidates` array.
- `scripts/render_digest.py` — **modify**; add a `_citation_candidates` helper + a "Citation candidates" section.
- `tests/unit/test_run_record_schema.py` — **modify**; add citation_candidates cases.
- `tests/unit/test_render_digest.py` — **modify**; add citation-candidates-section cases.
- `scripts/chase_citations.py` — **create**; detection core (Task 2) + ranking/bounding/output (Task 3).
- `scripts/append_log.py` — **modify**; add `"citation"` op (Task 3).
- `tests/unit/test_chase_citations.py` — **create**; detection (Task 2) + ranking/CLI (Task 3) tests.
- `.claude/commands/schedule-kg.md`, `.claude/commands/build-kg.md` — **modify**; wiring prose (Task 4).

---

## Task 1: Run-record `citation_candidates` field + digest section

Add the optional `citation_candidates` array to the run-record schema and render it in the digest, so a sweep's findings surface in the per-run digest. (Schema-affecting, so first — mirrors item 3 Task 2.)

**Files:**
- Modify: `schemas/run_record_schema.json` (add `citation_candidates` to `properties`)
- Modify: `scripts/render_digest.py` (add `_citation_candidates` helper; call it in both `build`/`update` branches)
- Test: `tests/unit/test_run_record_schema.py` (add cases), `tests/unit/test_render_digest.py` (add cases)

**Interfaces:**
- Consumes: `render(run_record, eval_index, node_titles, stats, cost) -> str` (existing).
- Produces: run-record `citation_candidates` array of `{pmid, cocitation_count, rcr, referenced_by:[...]}` where `rcr` is a number or null; a "## Citation candidates" markdown section emitted when `run_record["citation_candidates"]` is non-empty.

- [ ] **Step 1: Write the failing schema tests**

Add to `tests/unit/test_run_record_schema.py`:

```python
def test_citation_candidates_array_is_valid():
    rec = valid_update_record()
    rec["citation_candidates"] = [
        {"pmid": "777", "cocitation_count": 3, "rcr": 2.1, "referenced_by": ["111", "222", "333"]},
        {"pmid": "888", "cocitation_count": 2, "rcr": None, "referenced_by": ["111", "222"]},
    ]
    jsonschema.validate(rec, load_schema())


def test_citation_candidates_missing_pmid_rejected():
    rec = valid_update_record()
    rec["citation_candidates"] = [{"cocitation_count": 3, "referenced_by": ["111"]}]
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
```

- [ ] **Step 2: Write the failing digest tests**

Add to `tests/unit/test_render_digest.py`:

```python
def test_citation_candidates_section_renders_when_present():
    rec = update_record()
    rec["citation_candidates"] = [
        {"pmid": "777", "cocitation_count": 3, "rcr": 2.1, "referenced_by": ["node_003"]}]
    out = render(rec, eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "Citation candidates" in out
    assert "777" in out and "3" in out and "2.1" in out


def test_no_citation_candidates_section_when_absent():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "## Citation candidates" not in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py -v`
Expected: FAIL — schema lacks `citation_candidates` constraints (missing-pmid test fails) and digest has no Citation candidates section.

- [ ] **Step 4: Add `citation_candidates` to the run-record schema**

In `schemas/run_record_schema.json`, the `properties` object currently ends with the `retractions` block (after `cost_session_id`). Add a comma after the `retractions` block's closing brace and append (it is optional — not in `required`):

```json
    "citation_candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["pmid", "cocitation_count", "referenced_by"],
        "properties": {
          "pmid": {"type": "string"},
          "cocitation_count": {"type": "integer", "minimum": 1},
          "rcr": {"type": ["number", "null"]},
          "referenced_by": {"type": "array", "items": {"type": "string"}}
        }
      }
    }
```

The tail of `properties` becomes:

```json
    "retractions": {
      ...
    },
    "citation_candidates": {
      ...
    }
  }
}
```

- [ ] **Step 5: Render the Citation candidates section**

In `scripts/render_digest.py`, add this helper immediately after `_retractions` (above `render`):

```python
def _citation_candidates(rr: dict) -> list[str]:
    items = rr.get("citation_candidates") or []
    if not items:
        return []
    lines = ["## Citation candidates"]
    for c in items:
        rcr = c.get("rcr")
        rcr_str = f", RCR {rcr}" if rcr is not None else ""
        lines.append(f"- PMID {c.get('pmid')} — cited by {c.get('cocitation_count')} node ref(s){rcr_str}")
    lines.append("")
    return lines
```

Then call it in `render`, in BOTH the `build` and `update` branches, immediately AFTER `lines.extend(_totals(stats, cost))` (the growth/reporting region trails the audit region). In the `build` branch:

```python
        lines.extend(_retractions(run_record))
        lines.extend(_totals(stats, cost))
        lines.extend(_citation_candidates(run_record))
        return "\n".join(lines)
```

In the `update` branch (note the 4-space indent):

```python
    lines.extend(_retractions(run_record))
    lines.extend(_totals(stats, cost))
    lines.extend(_citation_candidates(run_record))
    return "\n".join(lines)
```

(The `skip` branch returns early and shows no body; citation candidates on a quiet week still appear because the skip path is rare and skip-week candidates are captured in the log — do NOT add the section to the skip branch.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add schemas/run_record_schema.json scripts/render_digest.py tests/unit/test_run_record_schema.py tests/unit/test_render_digest.py
git commit -m "feat: run-record citation_candidates field + digest section"
```

---

## Task 2: Citation discovery core (`chase_citations.py`)

The deterministic discovery layer: collect seed PMIDs, fetch each seed's backward references via elink (with a fixture hook), dedup against the ledger, and compute co-citation. No ranking/iCite/output yet.

**Files:**
- Create: `scripts/chase_citations.py`
- Test: `tests/unit/test_chase_citations.py` (create)

**Interfaces:**
- Consumes: `check_retractions.collect_used_pmids(kg_folder) -> list[str]` and `preflight.load_known_pmids(kg_folder) -> set[str]` (both existing, both on `scripts/` sys.path).
- Produces:
  - `elink_references(pmid: str, api_key: str | None) -> list[str]` — one live elink call returning the `pubmed_pubmed_refs` PMIDs for one seed.
  - `fetch_references(seeds: list[str], link_fn) -> dict[str, list[str]]` — calls `link_fn(seed: str) -> list[str]` per seed, returns `{seed: [ref_pmid, ...]}`.
  - `build_candidates(refs_by_seed: dict[str, list[str]], known: set[str]) -> dict[str, dict]` — returns `{candidate_pmid: {"cocitation_count": int, "referenced_by": [seed, ...]}}`, excluding any candidate in `known` and any candidate that is itself a seed.
  - CLI: `python3 scripts/chase_citations.py <kg_folder> [--elink-fixture FILE] [--json]` printing the deduped candidates with co-citation counts (no ranking/bounding/iCite in this task).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chase_citations.py`:

```python
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import chase_citations

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "chase_citations.py"))


def _write_ledger(kg, entries):
    (kg / "_pmid_ledger.json").write_text(json.dumps({
        "kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-01-01",
        "version": 1, "entries": entries,
        "statistics": {"total": len(entries), "used": 0, "irrelevant": 0, "failed": 0, "superseded": 0},
    }), encoding="utf-8")


def test_fetch_references_maps_each_seed():
    calls = {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"]}
    got = chase_citations.fetch_references(["111", "222"], lambda s: calls[s])
    assert got == {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"]}


def test_build_candidates_counts_cocitation_and_dedups():
    refs_by_seed = {"111": ["aaa", "bbb"], "222": ["bbb", "ccc"], "333": ["bbb"]}
    # 'aaa' already in ledger (known) -> excluded; '333' is a seed referenced by none here
    known = {"111", "222", "333", "aaa"}
    cand = chase_citations.build_candidates(refs_by_seed, known)
    assert "aaa" not in cand                       # known -> excluded
    assert cand["bbb"]["cocitation_count"] == 3
    assert sorted(cand["bbb"]["referenced_by"]) == ["111", "222", "333"]
    assert cand["ccc"]["cocitation_count"] == 1


def test_build_candidates_excludes_seeds_themselves():
    refs_by_seed = {"111": ["222", "zzz"]}   # 111 references another seed 222
    known = {"111", "222"}
    cand = chase_citations.build_candidates(refs_by_seed, known)
    assert "222" not in cand                       # a seed is never its own candidate
    assert "zzz" in cand


def test_cli_discovery_with_fixture(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
        "old": {"disposition": "irrelevant", "first_seen": "x", "last_checked": "x", "assigned_nodes": []},
    })
    fixture = tmp_path / "elink.json"
    fixture.write_text(json.dumps({"111": ["aaa", "old"], "222": ["aaa", "bbb"]}), encoding="utf-8")
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--elink-fixture", str(fixture), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["seed_count"] == 2
    pmids = {c["pmid"]: c for c in out["candidates"]}
    assert "old" not in pmids                       # already in ledger
    assert pmids["aaa"]["cocitation_count"] == 2
    assert pmids["bbb"]["cocitation_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_chase_citations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chase_citations'`.

- [ ] **Step 3: Write the discovery core**

Create `scripts/chase_citations.py`:

```python
#!/usr/bin/env python3
"""Deterministic citation-chasing discovery feed for a KG.

Follows backward references (pubmed_pubmed_refs) from the cited corpus
(ledger disposition "used"), dedups candidates against the ledger, ranks them
by co-citation frequency (iCite RCR tiebreak, best-effort), bounds the list,
and emits a JSON candidate feed. No MCP, no LLM, and READ-ONLY on the KG: it
writes nothing to the ledger or node files. On an elink network/parse error it
exits non-zero and emits no feed and no log entry.

Usage:
    python3 scripts/chase_citations.py <kg_folder> [--min-cocitation N] [--top-n N]
            [--json] [--elink-fixture FILE] [--icite-fixture FILE]

--elink-fixture FILE: JSON {"<seed_pmid>": ["<ref_pmid>", ...], ...} replacing
  live elink (tests).
--icite-fixture FILE: JSON {"<pmid>": <rcr_float>, ...} replacing live iCite
  (tests); a missing key yields rcr null for that PMID.

Set NCBI_API_KEY in the environment to lift the E-utilities rate limit.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_retractions import collect_used_pmids
from preflight import load_known_pmids

EUTILS_ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
REFS_LINKNAME = "pubmed_pubmed_refs"


def elink_references(pmid: str, api_key: str | None) -> list[str]:
    """Return the pubmed_pubmed_refs PMIDs for one seed (one elink call)."""
    params = {"dbfrom": "pubmed", "db": "pubmed", "linkname": REFS_LINKNAME,
              "id": pmid, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    url = EUTILS_ELINK + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    refs: list[str] = []
    for linkset in data.get("linksets", []):
        for db in linkset.get("linksetdbs", []):
            if db.get("linkname") == REFS_LINKNAME:
                refs.extend(str(p) for p in db.get("links", []))
    return refs


def fetch_references(seeds: list[str], link_fn) -> dict[str, list[str]]:
    """Map each seed to its referenced PMIDs via link_fn(seed)->list."""
    return {seed: list(link_fn(seed)) for seed in seeds}


def build_candidates(refs_by_seed: dict[str, list[str]], known: set[str]) -> dict[str, dict]:
    """Candidate PMID -> {cocitation_count, referenced_by}, excluding known/seed PMIDs."""
    seeds = set(refs_by_seed)
    candidates: dict[str, dict] = {}
    for seed, refs in refs_by_seed.items():
        for ref in dict.fromkeys(refs):          # dedup within a single seed's list
            if ref in known or ref in seeds:
                continue
            entry = candidates.setdefault(ref, {"cocitation_count": 0, "referenced_by": []})
            entry["cocitation_count"] += 1
            entry["referenced_by"].append(seed)
    return candidates


def _build_link_fn(args, api_key):
    """Return link_fn(seed)->list, live or fixture-backed."""
    if args.elink_fixture:
        with open(args.elink_fixture, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        return lambda seed: [str(p) for p in fixture.get(seed, [])]

    sleep = 0.11 if api_key else 0.34
    state = {"first": True}

    def live(seed):
        if not state["first"]:
            time.sleep(sleep)  # NCBI rate etiquette between elink calls
        state["first"] = False
        return elink_references(seed, api_key)
    return live


def main():
    parser = argparse.ArgumentParser(description="Deterministic citation-chasing discovery feed.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--min-cocitation", type=int, default=2,
                        help="Drop candidates referenced by fewer than N seeds (default 2)")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Keep at most N candidates after ranking (default 20)")
    parser.add_argument("--elink-fixture", default=None,
                        help='JSON {"seed": [ref,...]} replacing live elink (tests)')
    parser.add_argument("--icite-fixture", default=None,
                        help='JSON {"pmid": rcr} replacing live iCite (tests)')
    parser.add_argument("--json", action="store_true", help="Emit the structured feed as JSON")
    args = parser.parse_args()

    seeds = collect_used_pmids(args.kg_folder)
    api_key = os.environ.get("NCBI_API_KEY")
    link_fn = _build_link_fn(args, api_key)

    try:
        refs_by_seed = fetch_references(seeds, link_fn)
    except Exception as e:
        print(f"Error: citation elink failed: {e}", file=sys.stderr)
        sys.exit(1)

    known = load_known_pmids(args.kg_folder)
    candidates = build_candidates(refs_by_seed, known)

    feed = sorted(
        ({"pmid": p, "cocitation_count": c["cocitation_count"],
          "referenced_by": sorted(c["referenced_by"])}
         for p, c in candidates.items()),
        key=lambda c: (-c["cocitation_count"], c["pmid"]))

    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "seed_count": len(seeds), "candidate_count": len(feed),
               "candidates": feed}
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Citation chase: {len(feed)} candidates from {len(seeds)} cited PMIDs.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_chase_citations.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

```bash
git add scripts/chase_citations.py tests/unit/test_chase_citations.py
git commit -m "feat: citation discovery core (elink backward refs + co-citation)"
```

---

## Task 3: Ranking, bounding, iCite enrichment, output

Extend `chase_citations.py` to rank candidates (co-citation primary, iCite RCR tiebreak), bound the list (`--min-cocitation`, `--top-n`), enrich with iCite (best-effort), log a `citation` op, and emit `rcr`/`icite_status` in the feed.

**Files:**
- Modify: `scripts/chase_citations.py` (add `rcr_lookup`, `rank_candidates`; extend `main`)
- Modify: `scripts/append_log.py:22` (add `"citation"` to `VALID_OPS`) and `:7` (docstring)
- Test: `tests/unit/test_chase_citations.py` (add ranking + CLI tests)

**Interfaces:**
- Consumes: `build_candidates` (Task 2); `append_log.append_entry`.
- Produces:
  - `rcr_lookup(pmids: list[str], icite_fixture: str | None) -> tuple[dict[str, float | None], str]` — returns `(rcr_map, status)` where `status` ∈ `{"ok", "unavailable"}`; on any live-iCite failure returns `({p: None for p in pmids}, "unavailable")`.
  - `rank_candidates(candidates: dict[str, dict], rcr_map: dict[str, float | None], min_cocitation: int, top_n: int) -> list[dict]` — filters to `cocitation_count >= min_cocitation`, sorts by `(-cocitation_count, -rcr_or_0, pmid)`, caps at `top_n`, returns `[{pmid, cocitation_count, rcr, referenced_by}]`.
  - The CLI feed gains `icite_status` and per-candidate `rcr`; a `citation` log op is appended.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_chase_citations.py`:

```python
def test_rank_candidates_orders_by_cocitation_then_rcr():
    candidates = {
        "aaa": {"cocitation_count": 3, "referenced_by": ["111", "222", "333"]},
        "bbb": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
        "ccc": {"cocitation_count": 2, "referenced_by": ["111", "333"]},
        "ddd": {"cocitation_count": 1, "referenced_by": ["111"]},
    }
    rcr = {"aaa": 1.0, "bbb": 0.5, "ccc": 9.9, "ddd": 50.0}
    ranked = chase_citations.rank_candidates(candidates, rcr, min_cocitation=2, top_n=10)
    # ddd dropped (below min); aaa first (count 3); ccc before bbb (count tie, higher RCR)
    assert [c["pmid"] for c in ranked] == ["aaa", "ccc", "bbb"]
    assert ranked[0]["rcr"] == 1.0


def test_rank_candidates_caps_top_n():
    candidates = {p: {"cocitation_count": 2, "referenced_by": ["111", "222"]}
                  for p in ["a", "b", "c", "d"]}
    ranked = chase_citations.rank_candidates(candidates, {}, min_cocitation=2, top_n=2)
    assert len(ranked) == 2


def test_rank_candidates_null_rcr_sorts_last_and_stable_by_pmid():
    candidates = {
        "200": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
        "100": {"cocitation_count": 2, "referenced_by": ["111", "222"]},
    }
    ranked = chase_citations.rank_candidates(candidates, {"200": None, "100": None},
                                             min_cocitation=2, top_n=10)
    # equal count, both rcr None -> tiebreak by pmid ascending
    assert [c["pmid"] for c in ranked] == ["100", "200"]
    assert ranked[0]["rcr"] is None


def test_rcr_lookup_fixture(tmp_path):
    fixture = tmp_path / "icite.json"
    fixture.write_text(json.dumps({"111": 4.2}), encoding="utf-8")
    rcr_map, status = chase_citations.rcr_lookup(["111", "222"], str(fixture))
    assert status == "ok"
    assert rcr_map["111"] == 4.2 and rcr_map["222"] is None
```

And the full-CLI ranking test:

```python
def test_cli_feed_has_rcr_and_icite_status(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    _write_ledger(kg, {
        "111": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_001"]},
        "222": {"disposition": "used", "first_seen": "x", "last_checked": "x", "assigned_nodes": ["node_002"]},
    })
    (kg / "_log.md").write_text("", encoding="utf-8")
    elink = tmp_path / "elink.json"
    elink.write_text(json.dumps({"111": ["aaa", "bbb"], "222": ["aaa"]}), encoding="utf-8")
    icite = tmp_path / "icite.json"
    icite.write_text(json.dumps({"aaa": 7.0}), encoding="utf-8")
    res = subprocess.run([sys.executable, SCRIPT, str(kg), "--min-cocitation", "1", "--top-n", "5",
                          "--elink-fixture", str(elink), "--icite-fixture", str(icite), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["icite_status"] == "ok"
    top = out["candidates"][0]
    assert top["pmid"] == "aaa" and top["cocitation_count"] == 2 and top["rcr"] == 7.0
    # log entry written
    assert "citation |" in (kg / "_log.md").read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_chase_citations.py -v`
Expected: FAIL — `rank_candidates`/`rcr_lookup` not defined; feed lacks `icite_status`/`rcr`.

- [ ] **Step 3: Add `"citation"` to append_log**

In `scripts/append_log.py` line 22:

```python
VALID_OPS = {"build", "update", "evaluate", "link", "query", "lint", "schedule", "preflight", "digest", "retraction", "citation"}
```

And line 7 docstring:

```python
Operations: build, update, evaluate, link, query, lint, schedule, preflight, digest, retraction, citation
```

- [ ] **Step 4: Add iCite lookup + ranking to chase_citations.py**

In `scripts/chase_citations.py`, add the iCite endpoint constant next to `EUTILS_ELINK`:

```python
ICITE_API = "https://icite.od.nih.gov/api/pubs"
```

Add the import for the log near the top (after the `from preflight import load_known_pmids` line):

```python
from append_log import append_entry
```

Add these functions above `main`:

```python
def rcr_lookup(pmids: list[str], icite_fixture: str | None) -> tuple[dict, str]:
    """Return ({pmid: rcr|None}, status). Best-effort: any live failure -> all None, 'unavailable'."""
    if not pmids:
        return {}, "ok"
    if icite_fixture is not None:
        with open(icite_fixture, "r", encoding="utf-8") as fh:
            fx = json.load(fh)
        return ({p: fx.get(p) for p in pmids}, "ok")
    try:
        params = {"pmids": ",".join(pmids), "legacy": "false"}
        url = ICITE_API + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        out = {p: None for p in pmids}
        for rec in data.get("data", []):
            out[str(rec.get("pmid"))] = rec.get("relative_citation_ratio")
        return out, "ok"
    except Exception:
        return ({p: None for p in pmids}, "unavailable")


def rank_candidates(candidates: dict, rcr_map: dict, min_cocitation: int, top_n: int) -> list[dict]:
    """Filter by min co-citation, sort by (-count, -rcr, pmid), cap at top_n."""
    rows = [{"pmid": p, "cocitation_count": c["cocitation_count"],
             "rcr": rcr_map.get(p), "referenced_by": sorted(c["referenced_by"])}
            for p, c in candidates.items() if c["cocitation_count"] >= min_cocitation]
    rows.sort(key=lambda c: (-c["cocitation_count"], -(c["rcr"] or 0.0), c["pmid"]))
    return rows[:top_n]
```

Then replace the bottom of `main` (everything from `candidates = build_candidates(...)` to the end) with:

```python
    known = load_known_pmids(args.kg_folder)
    candidates = build_candidates(refs_by_seed, known)

    survivors = sorted(p for p, c in candidates.items()
                       if c["cocitation_count"] >= args.min_cocitation)
    rcr_map, icite_status = rcr_lookup(survivors, args.icite_fixture)
    feed = rank_candidates(candidates, rcr_map, args.min_cocitation, args.top_n)

    summary = {"kg": os.path.basename(os.path.abspath(args.kg_folder)),
               "seed_count": len(seeds), "candidate_count": len(feed),
               "icite_status": icite_status, "candidates": feed}

    try:
        append_entry(args.kg_folder, "citation",
                     f"Citation chase: {len(feed)} candidates from {len(seeds)} cited PMIDs "
                     f"(min co-citation {args.min_cocitation}, top {args.top_n}); iCite {icite_status}.")
    except Exception:
        pass  # never fail the sweep over logging

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(f"Citation chase: {len(feed)} candidates from {len(seeds)} cited PMIDs; "
              f"iCite {icite_status}.", file=sys.stderr)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_chase_citations.py tests/unit/test_append_log.py -v`
Expected: PASS (Task 2 tests + new ranking/CLI tests + existing append_log tests).

- [ ] **Step 6: Run full suite + commit**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (all).

```bash
git add scripts/chase_citations.py scripts/append_log.py tests/unit/test_chase_citations.py
git commit -m "feat: citation ranking, bounding, iCite enrichment, citation log op"
```

---

## Task 4: Wire the opt-in sweep into the scheduled run

Prose edits: add an opt-in `--with-citation-chase` scheduler flag that runs the sweep and carries its candidates into the run-record. No unit tests (LLM-instruction prose); verified by diff.

**Files:**
- Modify: `.claude/commands/schedule-kg.md` (flag docs + conditional scheduled-prompt step + skip-record carry)
- Modify: `.claude/commands/build-kg.md` (Phase 4 step 1e run-record: include `citation_candidates`)

**Interfaces:**
- Consumes: `scripts/chase_citations.py --json`; the run-record schema's `citation_candidates` field (Task 1).
- Produces: scheduled runs with the flag set invoke the sweep and record `citation_candidates`; without it, the step and field are omitted.

- [ ] **Step 1: Document the flag**

In `.claude/commands/schedule-kg.md`, near the existing `--threshold` flag bullet (around line 16), add:

```markdown
- **--with-citation-chase** (optional): If set, the scheduled run also runs the deterministic citation-chasing discovery sweep (`scripts/chase_citations.py`) and records its ranked candidate feed in the run-record's `citation_candidates` field (surfaced in the digest). Recorded as `schedule.citation_chase: true` in the manifest and substituted into the scheduled prompt. Off by default — citation chasing is a growth feature, opt-in per KG.
```

- [ ] **Step 2: Add the conditional step to the scheduled prompt**

In `.claude/commands/schedule-kg.md`, in the scheduled-agent prompt block, insert a new lettered step immediately AFTER the Step 0 retraction sweep and BEFORE step 1 (preflight):

````markdown
0b. ONLY IF this KG has `schedule.citation_chase: true` in its manifest: run the deterministic citation-chasing discovery sweep (no MCP, no LLM, read-only):
   ```
   python3 scripts/chase_citations.py <KG_FolderName> --json
   ```
   Keep the JSON `candidates` array from its output. It must be carried into the
   `citation_candidates` field of whichever run-record you write later this session
   (the build/update record, or the skip record on a quiet week). The sweep is
   read-only; you only carry its candidate list. If the sweep exits non-zero, or the
   KG does not have `schedule.citation_chase: true`, proceed with no `citation_candidates`.
````

- [ ] **Step 3: Populate `citation_candidates` in the skip run-record**

In `.claude/commands/schedule-kg.md`, in the skip-path step that writes the `mode: "skip"` run-record, extend the field list (right after the `retractions` clause added in item 3): "...and `citation_candidates` (the `candidates` array from the Step 0b sweep, or omit if citation chasing is off or the sweep produced none), per `schemas/run_record_schema.json`."

- [ ] **Step 4: Populate `citation_candidates` in the build/update run-record**

In `.claude/commands/build-kg.md`, Phase 4 step `1e` (the run-record field list), add one bullet right after the `retractions` bullet:

```markdown
   - `citation_candidates`: the `candidates` array from the scheduled run's citation-chasing sweep (Step 0b of the scheduled prompt, only when `schedule.citation_chase` is set), or omit for a manual run or when citation chasing is off.
```

- [ ] **Step 5: Verify the edits**

Run: `git diff .claude/commands/schedule-kg.md .claude/commands/build-kg.md`
Confirm: a documented `--with-citation-chase` flag; a conditional Step 0b running `chase_citations.py --json` (gated on `schedule.citation_chase`); both the skip record and the build/update run-record can include `citation_candidates`; manual/off runs omit it.

- [ ] **Step 6: Commit**

```bash
git add .claude/commands/schedule-kg.md .claude/commands/build-kg.md
git commit -m "feat: opt-in citation-chase scheduler flag, record in run-record"
```

---

## Task 5: End-to-end smoke gate

Verify the whole path with no production code change: full suite green, and a real `chase_citations.py` sweep against a temp KG produces a correctly ranked, bounded, ledger-deduped feed and logs — without mutating the KG.

**Files:** none modified.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS (run-record/digest + discovery + ranking + existing tests).

- [ ] **Step 2: Real CLI sweep against a temp KG (read-only + ranking + dedup)**

```bash
TMP=$(mktemp -d); KG="$TMP/KG_Demo"; mkdir -p "$KG"
cat > "$KG/_pmid_ledger.json" <<'JSON'
{"kg_name":"KG_Demo","created":"2026-01-01","updated":"2026-01-01","version":1,
 "entries":{
   "111":{"disposition":"used","first_seen":"x","last_checked":"x","assigned_nodes":["node_001"]},
   "222":{"disposition":"used","first_seen":"x","last_checked":"x","assigned_nodes":["node_002"]},
   "known99":{"disposition":"irrelevant","first_seen":"x","last_checked":"x","assigned_nodes":[]}},
 "statistics":{"total":3,"used":2,"irrelevant":1,"failed":0,"superseded":0}}
JSON
touch "$KG/_log.md"
BEFORE=$(cat "$KG/_pmid_ledger.json")
echo '{"111":["aaa","bbb","known99"],"222":["aaa","ccc"]}' > "$TMP/elink.json"
echo '{"aaa":3.3,"bbb":1.1,"ccc":0.2}' > "$TMP/icite.json"
python3 scripts/chase_citations.py "$KG" --min-cocitation 2 --top-n 5 \
   --elink-fixture "$TMP/elink.json" --icite-fixture "$TMP/icite.json" --json
echo "--- ledger unchanged? ---"; [ "$BEFORE" = "$(cat "$KG/_pmid_ledger.json")" ] && echo "LEDGER UNTOUCHED"
echo "--- log ---"; grep -q "citation |" "$KG/_log.md" && echo "LOG OK"
rm -rf "$TMP"
```

Expected: the JSON feed has exactly one candidate `aaa` (co-citation 2, RCR 3.3); `bbb`/`ccc` are dropped (co-citation 1 < min 2) and `known99` is excluded (in ledger); `icite_status` is `ok`; "LEDGER UNTOUCHED" prints; `_log.md` has a `citation |` entry ("LOG OK").

- [ ] **Step 3: Confirm iCite degradation is a safe success**

```bash
TMP=$(mktemp -d); KG="$TMP/KG_Demo2"; mkdir -p "$KG"
cat > "$KG/_pmid_ledger.json" <<'JSON'
{"kg_name":"KG_Demo2","created":"2026-01-01","updated":"2026-01-01","version":1,
 "entries":{
   "111":{"disposition":"used","first_seen":"x","last_checked":"x","assigned_nodes":["node_001"]},
   "222":{"disposition":"used","first_seen":"x","last_checked":"x","assigned_nodes":["node_002"]}},
 "statistics":{"total":2,"used":2,"irrelevant":0,"failed":0,"superseded":0}}
JSON
touch "$KG/_log.md"
echo '{"111":["aaa"],"222":["aaa"]}' > "$TMP/elink.json"
# No --icite-fixture: with network access this calls live iCite; without it, degrades.
python3 scripts/chase_citations.py "$KG" --min-cocitation 2 \
   --elink-fixture "$TMP/elink.json" --icite-fixture /nonexistent-on-purpose.json --json; echo "exit=$?"
rm -rf "$TMP"
```

Expected: a non-existent iCite fixture path raises inside `rcr_lookup`'s fixture branch — to test *live* degradation instead, omit `--icite-fixture`. Re-run without it; if iCite is unreachable the feed shows `icite_status: "unavailable"` and `rcr: null` for `aaa`, exit 0. (If iCite is reachable, `icite_status: "ok"` — both are valid passes.)

> Note: the fixture branch of `rcr_lookup` deliberately does NOT swallow a missing-file error (a bad fixture path is a test bug, not a network event); only the *live* branch degrades. Use the no-`--icite-fixture` form to exercise degradation.

- [ ] **Step 4: Final commit (only if verification produced artifacts)**

If Steps 1-3 produced no file changes, no commit is needed. Otherwise:
```bash
git add -A
git commit -m "test: smoke-verify citation-chasing path"
```

---

## Self-Review Notes

- **Spec coverage:** backward-reference discovery via elink → Task 2 (`elink_references`); seeds = `used` PMIDs → Task 2 (`collect_used_pmids` reuse); ledger dedup any-disposition → Task 2 (`load_known_pmids` reuse) + `build_candidates`; co-citation frequency → Task 2; iCite RCR tiebreak best-effort → Task 3 (`rcr_lookup`, `rank_candidates`); `--min-cocitation`/`--top-n` bounding → Task 3; read-only guarantee → Task 2/3 (no KG writes) + Task 5 LEDGER-UNTOUCHED check; no-false-empty-feed on elink error → Task 2 (`sys.exit(1)`); `citation` log op → Task 3; run-record `citation_candidates` → Task 1; digest section → Task 1; opt-in `--with-citation-chase` + run-record carry → Task 4; manual/off omit → Task 4; `icite_status` field → Task 3.
- **Type consistency:** `collect_used_pmids`/`load_known_pmids` (existing signatures); `build_candidates` returns `{pmid: {cocitation_count, referenced_by}}` consumed by `rank_candidates`; `rcr_lookup` returns `(rcr_map, status)`; feed item shape `{pmid, cocitation_count, rcr, referenced_by}` matches the run-record schema's `citation_candidates` items (Task 1) and the digest renderer (Task 1).
- **Out of scope (per spec):** candidate ingestion; forward (`citedin`) chasing; per-tier seed selection; recursive multi-hop chasing.
