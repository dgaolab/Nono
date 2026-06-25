# Claude-Free Build Orchestrator (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Python orchestrator (`scripts/librarian_build.py`) that constructs and updates a PubMed knowledge graph using a local open-weight model + NCBI E-utilities, with no Claude and no MCP.

**Architecture:** A state machine owns all control flow, file writes, and validation. It calls the local model (`lib/llm.py`) only for narrow, schema-validated reasoning steps — search planning, node skeletons, per-node synthesis, relationships — and `lib/pubmed.py` for retrieval. All other steps reuse existing deterministic scripts (ledger, evidence-tier classification, literature stamping, index generation, validation, embeddings, digest, the Phase-2 evaluator). Graph construction is *staged*: one call proposes node skeletons (title + one-line summary + supporting PMIDs), then one narrow call per node fleshes out detail. The orchestrator — not the model — assigns node IDs, filters hallucinated PMIDs, dedups, and assembles the manifest.

**Tech Stack:** Python 3.14 (conda env `nono`), stdlib only for new code (`urllib`, `xml.etree`, `json`, `argparse`, `subprocess`), `pytest` for tests, `PyYAML` (already a dep, via `lib/frontmatter`). Reuses `lib/llm.py`, `lib/pubmed.py`, `lib/evaluate.py`, `librarian_evaluate.py`, and the existing deterministic `scripts/*.py`.

## Global Constraints

- **No new pip dependency.** New code uses stdlib only. (`urllib`, `xml.etree`, `json`.)
- **Conda env `nono`.** All commands run via `conda run -n nono python ...`.
- **Graceful degradation / never-mutate-on-failure.** If `lib/llm.LLMUnavailable` is raised at any reasoning step, abort the run and write nothing partial; mirror `check_retractions.py`. PubMed full-text failure degrades to abstract-only (full text is an enhancement at build time, not a hard gate).
- **No live model or network in CI.** Every reasoning/retrieval call takes an injection seam (`chat=`, `fetch_metadata=`, `fetch_full_text=`, `esearch=`); the unit suite uses fakes only. A single opt-in integration test may run end-to-end behind an env flag, skipped by default.
- **Concurrency cap 3.** Any concurrent model/E-utilities fan-out is bounded at 3 (the user's local model ceiling). v1 may run serially; never exceed 3.
- **Accept run-to-run variance.** No required temperature/seed pinning; pass `temperature=0.2` for synthesis, `0.0` for classification, but do not assert determinism of model output.
- **Never hallucinate reference IDs.** The orchestrator filters every model-proposed PMID against the set actually retrieved from PubMed; unknown IDs are dropped, never written.
- **Entities are names + types only.** Do not ask the model for `normalized_id`; omit it (avoids fabricated HGNC/OMIM IDs).
- **manifest.json is the source of truth**, consistent with node files; `_index.md` is derived. Node IDs are sequential `node_NNN`; file names `node_NNN_short_slug.md`.
- **Scope:** BUILD + UPDATE modes, PubMed-only sources, plus the `--interactive` checkpoint. ClinicalTrials.gov / ChEMBL sources and entity ID normalization are explicitly out of scope for this plan.

---

## File Structure

- **Create `scripts/lib/build.py`** — testable reasoning core: tier config, search planning, skeleton proposal, per-node synthesis, PMID filtering, relationship proposal, node-markdown rendering, manifest assembly. All model calls injected via a `chat` parameter. Pure functions wherever possible.
- **Create `scripts/librarian_build.py`** — the orchestrator CLI + state machine (`main`), BUILD and UPDATE wiring, `--interactive` checkpoint, and all subprocess calls to deterministic scripts. Thin glue over `lib/build.py`.
- **Modify `scripts/lib/llm.py`** — add `extract_json_object(text) -> dict` (shared robust JSON extraction) raising `ValueError` on failure.
- **Modify `scripts/lib/evaluate.py`** — refactor `_extract_json` to delegate to `llm.extract_json_object` (wrap `ValueError` → `EvaluationError`); behavior unchanged.
- **Create `tests/unit/test_build_lib.py`** — unit tests for `lib/build.py` (fakes only).
- **Create `tests/unit/test_librarian_build.py`** — orchestrator core + one fixture-driven end-to-end BUILD/UPDATE test with injected seams.
- **Modify `.claude/skills/nono-librarian/SKILL.md`** — flip the "Build — NOT yet Claude-free" section to a runnable local build entry.

---

## Task 1: Shared JSON extraction in the LLM seam

**Files:**
- Modify: `scripts/lib/llm.py`
- Modify: `scripts/lib/evaluate.py:_extract_json`
- Test: `tests/unit/test_llm_lib.py`

**Interfaces:**
- Produces: `llm.extract_json_object(text: str) -> dict` — returns the first balanced `{...}` object parsed from `text`; raises `ValueError` if none parses.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_llm_lib.py`)

```python
def test_extract_json_object_from_fenced_prose():
    text = 'Here:\n```json\n{"a": 1, "b": {"c": 2}}\n```\nthanks'
    assert llm.extract_json_object(text) == {"a": 1, "b": {"c": 2}}


def test_extract_json_object_raises_when_absent():
    import pytest
    with pytest.raises(ValueError):
        llm.extract_json_object("no json here")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n nono python -m pytest tests/unit/test_llm_lib.py -k extract_json -v`
Expected: FAIL with `AttributeError: module 'lib.llm' has no attribute 'extract_json_object'`

- [ ] **Step 3: Implement in `scripts/lib/llm.py`** (add at end of module)

```python
def extract_json_object(text):
    """Parse the first balanced ``{...}`` object out of a model reply.

    Tolerates code fences and surrounding prose. Raises ``ValueError`` if no
    parseable JSON object is present.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model reply JSON was not an object")
    return obj
```

- [ ] **Step 4: Refactor `scripts/lib/evaluate.py`** so `parse_response` uses it (keep `EvaluationError` behavior)

Replace the body of `_extract_json` and the `json.loads` in `parse_response`:

```python
def parse_response(text):
    """Parse a model reply into ``{verdict, reasoning, quotes}`` or raise."""
    from lib import llm
    try:
        obj = llm.extract_json_object(text)
    except ValueError as e:
        raise EvaluationError(f"reply was not valid JSON: {e}") from e
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise EvaluationError(f"unknown verdict: {obj.get('verdict')!r}")
    quotes = obj.get("quotes") or []
    if not isinstance(quotes, list):
        quotes = []
    return {
        "verdict": verdict,
        "reasoning": str(obj.get("reasoning", "")).strip(),
        "quotes": quotes,
    }
```

Delete the now-unused `_extract_json` function.

- [ ] **Step 5: Run tests to verify all pass**

Run: `conda run -n nono python -m pytest tests/unit/test_llm_lib.py tests/unit/test_evaluate_lib.py -v`
Expected: PASS (all existing evaluate tests still green + 2 new llm tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/llm.py scripts/lib/evaluate.py tests/unit/test_llm_lib.py
git commit -m "refactor: shared llm.extract_json_object used by evaluate"
```

---

## Task 2: Breadth tiers + search planning

**Files:**
- Create: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces:
  - `build.TIERS: dict[str, dict]` — keyed `"narrow"|"medium"|"broad"`, each `{"sub_queries": int, "max_results": int, "metadata": int, "full_text": int, "nodes_min": int, "nodes_max": int}`.
  - `build.plan_search(topic: str, *, chat, breadth_override: str | None = None) -> dict` — returns `{"breadth": str, "sub_queries": list[str]}`. One model call returning both; `breadth_override` forces the tier.

- [ ] **Step 1: Write the failing test** (`tests/unit/test_build_lib.py`)

```python
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib import build


def _chat_returning(reply):
    def chat(messages, **kw):
        return reply
    return chat


def test_tiers_have_expected_keys():
    for tier in ("narrow", "medium", "broad"):
        t = build.TIERS[tier]
        assert {"sub_queries", "max_results", "metadata", "full_text",
                "nodes_min", "nodes_max"} <= set(t)


def test_plan_search_returns_breadth_and_subqueries():
    chat = _chat_returning(
        '{"breadth": "medium", "sub_queries": ["a immune response", "b delivery"]}')
    out = build.plan_search("mRNA vaccines", chat=chat)
    assert out["breadth"] == "medium"
    assert out["sub_queries"] == ["a immune response", "b delivery"]


def test_plan_search_honors_breadth_override():
    chat = _chat_returning('{"breadth": "broad", "sub_queries": ["x", "y", "z"]}')
    out = build.plan_search("t", chat=chat, breadth_override="narrow")
    assert out["breadth"] == "narrow"


def test_plan_search_raises_on_unparseable():
    chat = _chat_returning("I cannot help")
    with pytest.raises(build.BuildError):
        build.plan_search("t", chat=chat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -v`
Expected: FAIL with `ImportError: cannot import name 'build'`.

- [ ] **Step 3: Implement `scripts/lib/build.py`**

```python
#!/usr/bin/env python3
"""Claude-free build reasoning — the model-driven steps of build-kg, made local.

Every function that needs the model takes an injected ``chat`` callable
(`scripts/lib/llm.py`); retrieval is done by the orchestrator via
`scripts/lib/pubmed.py`. The orchestrator owns IDs, dedup, PMID filtering, and
file writes — these functions only reason and return validated data.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/scripts")
from lib import llm

TIERS = {
    "narrow": {"sub_queries": 3, "max_results": 10, "metadata": 15,
               "full_text": 5, "nodes_min": 8, "nodes_max": 15},
    "medium": {"sub_queries": 4, "max_results": 20, "metadata": 25,
               "full_text": 8, "nodes_min": 15, "nodes_max": 30},
    "broad":  {"sub_queries": 6, "max_results": 30, "metadata": 40,
               "full_text": 12, "nodes_min": 25, "nodes_max": 45},
}


class BuildError(RuntimeError):
    """Raised when a model reply for a build step cannot be used."""


def _ask_json(chat, messages, *, temperature=0.2):
    """Call the model and parse a JSON object from the reply, or raise BuildError."""
    reply = chat(messages, temperature=temperature)
    try:
        return llm.extract_json_object(reply)
    except ValueError as e:
        raise BuildError(f"model reply was not valid JSON: {e}") from e


_PLAN_SYS = (
    "You plan a PubMed literature search for a biomedical topic. Classify the "
    "topic breadth and propose focused sub-queries. Reply with ONE JSON object "
    "and nothing else: "
    '{"breadth": "narrow|medium|broad", "sub_queries": ["...", "..."]}. '
    "narrow = single mechanism/intervention (3 sub-queries), medium = a topic "
    "with several facets (4), broad = multi-disciplinary survey (6). Each "
    "sub-query is a specific PubMed search string."
)


def plan_search(topic, *, chat, breadth_override=None):
    """Classify breadth and generate sub-queries in one model call."""
    user = f"TOPIC:\n{topic}"
    if breadth_override:
        n = TIERS[breadth_override]["sub_queries"]
        user += f"\n\nUse breadth='{breadth_override}' and produce exactly {n} sub-queries."
    obj = _ask_json(chat, [{"role": "system", "content": _PLAN_SYS},
                           {"role": "user", "content": user}])
    breadth = breadth_override or str(obj.get("breadth", "")).strip().lower()
    if breadth not in TIERS:
        breadth = "medium"
    subs = [str(s).strip() for s in (obj.get("sub_queries") or []) if str(s).strip()]
    if not subs:
        raise BuildError("no sub-queries produced")
    return {"breadth": breadth, "sub_queries": subs}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build search planning (breadth + sub-queries)"
```

---

## Task 3: PMID candidate selection (dedup + ledger exclusion)

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces: `build.select_candidates(per_query_pmids: list[list[str]], known_pmids: set[str], cap: int) -> list[str]` — flattens, dedups preserving first-seen order, drops anything in `known_pmids`, truncates to `cap`.

- [ ] **Step 1: Write the failing test**

```python
def test_select_candidates_dedups_excludes_and_caps():
    out = build.select_candidates(
        [["1", "2", "3"], ["2", "4", "5"]], known_pmids={"3"}, cap=3)
    assert out == ["1", "2", "4"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k select_candidates -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
def select_candidates(per_query_pmids, known_pmids, cap):
    """Flatten per-query PMID lists → deduped, ledger-excluded, capped order."""
    seen = set()
    out = []
    for pmids in per_query_pmids:
        for p in pmids:
            if p in seen or p in known_pmids:
                continue
            seen.add(p)
            out.append(p)
            if len(out) >= cap:
                return out
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k select_candidates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build PMID candidate selection"
```

---

## Task 4: Node skeleton proposal

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces: `build.propose_skeleton(topic: str, articles: list[dict], *, chat, nodes_min: int, nodes_max: int) -> list[dict]`. Each `articles` item is `{"pmid": str, "title": str, "abstract": str}`. Returns a list of `{"title": str, "summary": str, "pmids": list[str]}`; every returned `pmids` is filtered to PMIDs present in `articles` (hallucinations dropped); skeleton nodes with no surviving PMID are removed.

- [ ] **Step 1: Write the failing test**

```python
_ARTS = [
    {"pmid": "1", "title": "Melatonin and clock genes", "abstract": "Melatonin entrains the SCN."},
    {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."},
]


def test_propose_skeleton_filters_hallucinated_pmids_and_empty_nodes():
    reply = (
        '{"nodes": ['
        '{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "pmids": ["1", "999"]},'
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]},'
        '{"title": "Ghost", "summary": "Nothing real.", "pmids": ["999"]}'
        ']}'
    )
    def chat(messages, **kw):
        return reply
    out = build.propose_skeleton("melatonin", _ARTS, chat=chat, nodes_min=1, nodes_max=10)
    assert len(out) == 2
    assert out[0]["pmids"] == ["1"]          # 999 dropped
    assert out[1]["pmids"] == ["2"]
    assert all(n["title"] and n["summary"] for n in out)
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k skeleton -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
def _articles_blob(articles):
    return "\n\n".join(
        f"PMID {a['pmid']}: {a['title']}\n{a.get('abstract', '')}" for a in articles)


_SKELETON_SYS = (
    "You design a biomedical knowledge graph from article abstracts. Propose "
    "coherent knowledge nodes, each ONE citable claim/concept. Reply with ONE "
    "JSON object: {\"nodes\": [{\"title\": \"...\", \"summary\": \"one sentence\", "
    "\"pmids\": [\"<pmid>\", ...]}]}. Use ONLY PMIDs from the provided articles. "
    "Each node cites the PMIDs whose abstracts support it."
)


def propose_skeleton(topic, articles, *, chat, nodes_min, nodes_max):
    """Propose node skeletons, keeping only real PMIDs and non-empty nodes."""
    allowed = {a["pmid"] for a in articles}
    user = (f"TOPIC: {topic}\nPropose {nodes_min}-{nodes_max} nodes.\n\n"
            f"ARTICLES:\n{_articles_blob(articles)}")
    obj = _ask_json(chat, [{"role": "system", "content": _SKELETON_SYS},
                           {"role": "user", "content": user}])
    out = []
    for n in obj.get("nodes", []) or []:
        title = str(n.get("title", "")).strip()
        summary = str(n.get("summary", "")).strip()
        pmids = [p for p in (n.get("pmids") or []) if p in allowed]
        if title and summary and pmids:
            out.append({"title": title, "summary": summary, "pmids": pmids})
    if not out:
        raise BuildError("skeleton produced no usable nodes")
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k skeleton -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build node skeleton proposal with PMID filtering"
```

---

## Task 5: Per-node synthesis

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces: `build.synthesize_node(skeleton_node: dict, articles_by_pmid: dict, *, chat) -> dict`. Returns `{"title", "summary", "detail", "tags": list[str], "keywords": list[str], "entities": list[{"name","type"}], "supports": dict[pmid -> claim]}`. `supports` keys are filtered to the skeleton's PMIDs; `entities` carry name + type only (no IDs); a `category` (first tag) is always present.

- [ ] **Step 1: Write the failing test**

```python
def test_synthesize_node_shapes_fields_and_filters_supports():
    skel = {"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]}
    arts = {"2": {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."}}
    reply = (
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.",'
        '"detail": "Across trials melatonin reduced latency.",'
        '"tags": ["sleep", "melatonin"], "keywords": ["melatonin", "sleep latency"],'
        '"entities": [{"name": "melatonin", "type": "drug", "normalized_id": "FAKE:1"}],'
        '"supports": {"2": "Reports reduced sleep latency.", "999": "should be dropped"}}'
    )
    def chat(messages, **kw):
        return reply
    out = build.synthesize_node(skel, arts, chat=chat)
    assert out["category"] == "sleep"
    assert set(out["supports"]) == {"2"}                 # 999 dropped
    assert out["entities"][0] == {"name": "melatonin", "type": "drug"}  # no id
    assert out["keywords"] and out["detail"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k synthesize -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
_NODE_SYS = (
    "You write one biomedical knowledge-graph node from its supporting articles. "
    "Reply with ONE JSON object: {\"title\": \"...\", \"summary\": \"one sentence\", "
    "\"detail\": \"a paragraph\", \"tags\": [\"category\", \"...\"], "
    "\"keywords\": [\"3-8 search terms\"], "
    "\"entities\": [{\"name\": \"...\", \"type\": \"gene|variant|phenotype|drug|pathway|protein|disease\"}], "
    "\"supports\": {\"<pmid>\": \"what this article contributes\"}}. "
    "tags[0] is a broad category. Use ONLY the provided PMIDs. Do NOT invent "
    "identifiers; entities carry name and type only."
)


def synthesize_node(skeleton_node, articles_by_pmid, *, chat):
    """Flesh out one node; filter supports to real PMIDs, strip entity IDs."""
    pmids = skeleton_node["pmids"]
    arts = [articles_by_pmid[p] for p in pmids if p in articles_by_pmid]
    user = (f"NODE TITLE: {skeleton_node['title']}\n"
            f"WORKING SUMMARY: {skeleton_node['summary']}\n"
            f"SUPPORTING PMIDS: {', '.join(pmids)}\n\n"
            f"ARTICLES:\n{_articles_blob(arts)}")
    obj = _ask_json(chat, [{"role": "system", "content": _NODE_SYS},
                           {"role": "user", "content": user}])
    allowed = set(pmids)
    supports = {k: str(v).strip() for k, v in (obj.get("supports") or {}).items()
                if k in allowed}
    if not supports:                       # never leave a node unreferenced
        supports = {p: skeleton_node["summary"] for p in pmids}
    entities = [{"name": str(e.get("name", "")).strip(), "type": str(e.get("type", "")).strip()}
                for e in (obj.get("entities") or []) if str(e.get("name", "")).strip()]
    tags = [str(t).strip() for t in (obj.get("tags") or []) if str(t).strip()] or ["general"]
    keywords = [str(k).strip() for k in (obj.get("keywords") or []) if str(k).strip()]
    return {
        "title": str(obj.get("title") or skeleton_node["title"]).strip(),
        "summary": str(obj.get("summary") or skeleton_node["summary"]).strip(),
        "detail": str(obj.get("detail", "")).strip(),
        "tags": tags,
        "category": tags[0],
        "keywords": keywords,
        "entities": entities,
        "supports": supports,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k synthesize -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build per-node synthesis"
```

---

## Task 6: Node ID assignment + slug + markdown rendering

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces:
  - `build.slugify(title: str) -> str` — 2-4 word snake_case slug, alnum only.
  - `build.assign_ids(nodes: list[dict], start: int = 1) -> list[dict]` — adds `id` (`node_NNN`) and `file` (`node_NNN_<slug>.md`) to each node, sequential from `start`.
  - `build.render_node_markdown(node: dict, today: str) -> tuple[dict, str]` — returns `(frontmatter_dict, body)` for `lib/frontmatter.write`. Frontmatter has `id, title, tags, evidence_tier:"unclassified", pubmed_ids:[{pmid, supports, verified:false, evidence_tier:"unclassified"}], entities, related_nodes, relationships, created, updated, evaluation_status:"pending"`. Body has Summary/Detail/Evidence(Literature placeholder)/Related Concepts sections.

- [ ] **Step 1: Write the failing test**

```python
def test_slugify_is_snake_case_and_short():
    assert build.slugify("Melatonin & the SCN clock, revisited!") in (
        "melatonin_the_scn", "melatonin_scn_clock")
    assert " " not in build.slugify("A B C D E F")


def test_assign_ids_sequential_with_files():
    nodes = [{"title": "Sleep latency"}, {"title": "Clock genes"}]
    out = build.assign_ids(nodes, start=1)
    assert out[0]["id"] == "node_001"
    assert out[0]["file"].startswith("node_001_")
    assert out[1]["id"] == "node_002"


def test_render_node_markdown_frontmatter_and_body():
    node = {"id": "node_001", "title": "Sleep latency", "tags": ["sleep"],
            "summary": "Melatonin shortens sleep latency.", "detail": "Detail text.",
            "keywords": ["melatonin"], "entities": [{"name": "melatonin", "type": "drug"}],
            "supports": {"2": "Reports reduced latency."},
            "related_nodes": [], "relationships": {}}
    fm, body = build.render_node_markdown(node, today="2026-06-24")
    assert fm["id"] == "node_001"
    assert fm["evaluation_status"] == "pending"
    assert fm["pubmed_ids"][0] == {"pmid": "2", "supports": "Reports reduced latency.",
                                   "verified": False, "evidence_tier": "unclassified"}
    assert "## Summary" in body and "## Detail" in body and "### Literature" in body
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "slugify or assign_ids or render_node" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
def slugify(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "_".join(words[:4]) if words else "node"


def assign_ids(nodes, start=1):
    out = []
    for i, n in enumerate(nodes):
        node = dict(n)
        num = start + i
        node["id"] = f"node_{num:03d}"
        node["file"] = f"{node['id']}_{slugify(node.get('title', 'node'))}.md"
        out.append(node)
    return out


def render_node_markdown(node, today):
    fm = {
        "id": node["id"],
        "title": node["title"],
        "tags": node.get("tags") or ["general"],
        "evidence_tier": "unclassified",
        "pubmed_ids": [
            {"pmid": p, "supports": claim, "verified": False, "evidence_tier": "unclassified"}
            for p, claim in node.get("supports", {}).items()
        ],
        "entities": node.get("entities", []),
        "related_nodes": node.get("related_nodes", []),
        "relationships": node.get("relationships", {}),
        "created": today,
        "updated": today,
        "evaluation_status": "pending",
    }
    related = "\n".join(
        f"- [[{rid}]] ({node['relationships'].get(rid, 'related_to')})"
        for rid in node.get("related_nodes", [])) or "- (none yet)"
    body = (
        f"# {node['title']}\n\n"
        f"## Summary\n{node['summary']}\n\n"
        f"## Detail\n{node.get('detail', '')}\n\n"
        f"## Evidence\n\n### Literature\n"
        f"- (stamped by stamp_literature.py)\n\n"
        f"## Related Concepts\n{related}\n"
    )
    return fm, body
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "slugify or assign_ids or render_node" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build node id/slug/markdown rendering"
```

---

## Task 7: Relationship proposal (with deterministic fallback)

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces: `build.propose_relationships(nodes: list[dict], *, chat) -> list[dict]`. Input nodes have `id`, `title`, `summary`. Returns `[{"source", "target", "relationship"}]`; relationship ∈ `{is_part_of, depends_on, supports, contradicts, related_to, derived_from, mechanism_of}`; both endpoints must be existing node IDs; invalid edges dropped. On `BuildError`/empty, falls back to deterministic `related_to` edges between nodes sharing ≥1 PMID.

- [ ] **Step 1: Write the failing test**

```python
_REL_NODES = [
    {"id": "node_001", "title": "A", "summary": "sa", "pmids": ["1", "2"]},
    {"id": "node_002", "title": "B", "summary": "sb", "pmids": ["2"]},
    {"id": "node_003", "title": "C", "summary": "sc", "pmids": ["9"]},
]


def test_propose_relationships_validates_edges():
    reply = ('{"edges": [{"source": "node_001", "target": "node_002", "relationship": "supports"},'
             '{"source": "node_001", "target": "node_999", "relationship": "supports"},'
             '{"source": "node_002", "target": "node_001", "relationship": "bogus"}]}')
    def chat(messages, **kw):
        return reply
    out = build.propose_relationships(_REL_NODES, chat=chat)
    assert {"source": "node_001", "target": "node_002", "relationship": "supports"} in out
    assert all(e["target"] != "node_999" for e in out)
    assert all(e["relationship"] != "bogus" for e in out)


def test_propose_relationships_falls_back_to_shared_pmids():
    def chat(messages, **kw):
        return "garbage"
    out = build.propose_relationships(_REL_NODES, chat=chat)
    # node_001 & node_002 share PMID 2 → a related_to edge; node_003 shares none
    assert any({e["source"], e["target"]} == {"node_001", "node_002"} for e in out)
    assert all("node_003" not in (e["source"], e["target"]) for e in out)
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k relationships -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
RELATIONSHIPS = {"is_part_of", "depends_on", "supports", "contradicts",
                 "related_to", "derived_from", "mechanism_of"}

_REL_SYS = (
    "You connect biomedical knowledge nodes. Reply with ONE JSON object: "
    "{\"edges\": [{\"source\": \"<node_id>\", \"target\": \"<node_id>\", "
    "\"relationship\": \"is_part_of|depends_on|supports|contradicts|related_to|"
    "derived_from|mechanism_of\"}]}. Use only the listed node IDs."
)


def _shared_pmid_edges(nodes):
    edges = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if set(a.get("pmids", [])) & set(b.get("pmids", [])):
                edges.append({"source": a["id"], "target": b["id"],
                              "relationship": "related_to"})
    return edges


def propose_relationships(nodes, *, chat):
    ids = {n["id"] for n in nodes}
    listing = "\n".join(f"{n['id']}: {n['title']} — {n['summary']}" for n in nodes)
    try:
        obj = _ask_json(chat, [{"role": "system", "content": _REL_SYS},
                               {"role": "user", "content": listing}])
        edges = []
        for e in obj.get("edges", []) or []:
            s, t, r = e.get("source"), e.get("target"), e.get("relationship")
            if s in ids and t in ids and s != t and r in RELATIONSHIPS:
                edges.append({"source": s, "target": t, "relationship": r})
        if edges:
            return edges
    except BuildError:
        pass
    return _shared_pmid_edges(nodes)
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k relationships -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build relationship proposal with shared-PMID fallback"
```

---

## Task 8: Manifest assembly + relationship back-population

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces:
  - `build.apply_relationships(nodes: list[dict], edges: list[dict]) -> None` — mutates each node's `related_nodes` (sorted unique targets/sources it touches) and `relationships` map.
  - `build.assemble_manifest(kg_name: str, topic: str, breadth: str, sub_queries: list[str], nodes: list[dict], edges: list[dict], today: str) -> dict` — builds a manifest dict per `schemas/graph_schema.json` with `nodes[]` (id, title, file, tags, summary, keywords, pubmed_ids as string list, evaluation_status, evidence_tier, entities), `edges`, `data_sources:["pubmed"]`, `search_profile`, `version:1`, empty `statistics` (filled later by `update_manifest_stats.py`).

- [ ] **Step 1: Write the failing test**

```python
def test_apply_relationships_populates_node_links():
    nodes = [{"id": "node_001", "related_nodes": [], "relationships": {}},
             {"id": "node_002", "related_nodes": [], "relationships": {}}]
    edges = [{"source": "node_001", "target": "node_002", "relationship": "supports"}]
    build.apply_relationships(nodes, edges)
    assert "node_002" in nodes[0]["related_nodes"]
    assert nodes[0]["relationships"]["node_002"] == "supports"


def test_assemble_manifest_minimal_shape():
    nodes = [{"id": "node_001", "title": "T", "file": "node_001_t.md", "tags": ["c"],
              "summary": "s", "keywords": ["k"], "supports": {"2": "x"},
              "entities": [], "evidence_tier": "unclassified"}]
    m = build.assemble_manifest("KG_X", "topic", "narrow", ["q1"], nodes, [], "2026-06-24")
    assert m["kg_name"] == "KG_X"
    assert m["data_sources"] == ["pubmed"]
    assert m["search_profile"]["sub_queries"] == ["q1"]
    n = m["nodes"][0]
    assert n["pubmed_ids"] == ["2"]            # manifest stores PMIDs as strings
    assert n["evaluation_status"] == "pending"
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "apply_relationships or assemble_manifest" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
def apply_relationships(nodes, edges):
    by_id = {n["id"]: n for n in nodes}
    for e in edges:
        s, t, r = e["source"], e["target"], e["relationship"]
        for a, b in ((s, t), (t, s)):
            node = by_id.get(a)
            if node is None:
                continue
            if b not in node["related_nodes"]:
                node["related_nodes"].append(b)
        by_id[s]["relationships"][t] = r
    for n in nodes:
        n["related_nodes"] = sorted(set(n["related_nodes"]))


def assemble_manifest(kg_name, topic, breadth, sub_queries, nodes, edges, today):
    return {
        "kg_name": kg_name,
        "topic": topic,
        "version": 1,
        "created": today,
        "updated": today,
        "data_sources": ["pubmed"],
        "search_profile": {"breadth": breadth, "sub_queries": sub_queries, "updated": today},
        "nodes": [
            {
                "id": n["id"], "title": n["title"], "file": n["file"],
                "tags": n.get("tags") or ["general"], "summary": n["summary"],
                "keywords": n.get("keywords", []),
                "pubmed_ids": list(n.get("supports", {}).keys()),
                "evaluation_status": "pending",
                "evidence_tier": n.get("evidence_tier", "unclassified"),
                "entities": n.get("entities", []),
            }
            for n in nodes
        ],
        "edges": edges,
        "statistics": {},
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "apply_relationships or assemble_manifest" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: build manifest assembly + relationship back-population"
```

---

## Task 9: Orchestrator BUILD — construct phase (pure core)

**Files:**
- Create: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces: `librarian_build.construct_graph(topic, kg_name, articles, *, chat, breadth, sub_queries, today, start_id=1) -> tuple[list[dict], dict]` — runs skeleton → per-node synthesis (serial; concurrency ≤3 when parallelized later) → id assignment → relationships → manifest assembly. Returns `(nodes, manifest)`. `articles` is `list[{pmid,title,abstract}]`.

- [ ] **Step 1: Write the failing test** (`tests/unit/test_librarian_build.py`)

```python
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import librarian_build as lb

_ARTS = [
    {"pmid": "1", "title": "Melatonin and clock genes", "abstract": "Melatonin entrains the SCN."},
    {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."},
]


def _scripted_chat():
    """Return a chat that answers skeleton, then node, then relationships in order."""
    replies = iter([
        # skeleton
        '{"nodes": [{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "pmids": ["1"]},'
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "pmids": ["2"]}]}',
        # node 1 synthesis
        '{"title": "SCN entrainment", "summary": "Melatonin entrains the clock.", "detail": "d1",'
        '"tags": ["circadian"], "keywords": ["scn"], "entities": [], "supports": {"1": "entrains"}}',
        # node 2 synthesis
        '{"title": "Sleep latency", "summary": "Melatonin shortens sleep latency.", "detail": "d2",'
        '"tags": ["sleep"], "keywords": ["latency"], "entities": [], "supports": {"2": "reduces"}}',
        # relationships
        '{"edges": [{"source": "node_001", "target": "node_002", "relationship": "related_to"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    return chat


def test_construct_graph_produces_nodes_and_manifest():
    nodes, manifest = lb.construct_graph(
        "melatonin", "KG_Mel", _ARTS, chat=_scripted_chat(),
        breadth="narrow", sub_queries=["q1"], today="2026-06-24")
    assert len(nodes) == 2
    assert manifest["nodes"][0]["id"] == "node_001"
    assert manifest["edges"][0]["relationship"] == "related_to"
    assert nodes[0]["related_nodes"] == ["node_002"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k construct_graph -v`
Expected: FAIL `ModuleNotFoundError: No module named 'librarian_build'`.

- [ ] **Step 3: Implement the module skeleton + `construct_graph`** (`scripts/librarian_build.py`)

```python
#!/usr/bin/env python3
"""Claude-free KG build orchestrator — deterministic equivalent of build-kg.md.

State machine over the build pipeline. Calls lib/llm.py for narrow reasoning
steps and lib/pubmed.py for retrieval; reuses every existing deterministic
script for ledger, evidence tiers, literature stamping, index, validation,
embeddings, digest, and the Phase-2 evaluator. Aborts without writing on model
unavailability (never-mutate-on-failure).

Usage:
    python3 scripts/librarian_build.py "<topic>" [--output NAME] [--since YYYY-MM-DD]
            [--breadth narrow|medium|broad] [--interactive]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import build, evaluate, llm, pubmed
from lib.frontmatter import write as write_node


def construct_graph(topic, kg_name, articles, *, chat, breadth, sub_queries,
                    today, start_id=1):
    """Skeleton → per-node synthesis → ids → relationships → manifest."""
    tier = build.TIERS[breadth]
    skeleton = build.propose_skeleton(
        topic, articles, chat=chat,
        nodes_min=tier["nodes_min"], nodes_max=tier["nodes_max"])
    by_pmid = {a["pmid"]: a for a in articles}
    synthesized = [build.synthesize_node(s, by_pmid, chat=chat) for s in skeleton]
    # carry each skeleton's pmids onto the synthesized node for relationship fallback
    for syn, skel in zip(synthesized, skeleton):
        syn.setdefault("pmids", skel["pmids"])
        syn.setdefault("related_nodes", [])
        syn.setdefault("relationships", {})
    nodes = build.assign_ids(synthesized, start=start_id)
    edges = build.propose_relationships(nodes, chat=chat)
    build.apply_relationships(nodes, edges)
    manifest = build.assemble_manifest(
        kg_name, topic, breadth, sub_queries, nodes, edges, today)
    return nodes, manifest
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k construct_graph -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: build orchestrator construct_graph core"
```

---

## Task 10: Orchestrator BUILD — retrieval + persistence helpers

**Files:**
- Modify: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces:
  - `librarian_build.gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text, known_pmids, tier) -> list[dict]` — runs each sub-query through `esearch` (≤ tier max_results), selects candidates via `build.select_candidates` (cap = tier metadata), fetches metadata, attaches abstract; for the top `tier["full_text"]` with a `pmcid`, appends full text to the abstract (degrading to abstract-only on `pubmed.PubMedUnavailable`). Returns `list[{pmid,title,abstract,metadata}]`.
  - `librarian_build.write_nodes(kg_folder, nodes, today)` — writes each node `.md` via `write_node` into `<kg>/nodes/`.
  - `librarian_build.ledger_batch_for_used(articles) -> list[dict]` — builds ledger batch-add entries (`disposition:"used"`, title/authors/journal/year/publication_types) from article metadata.

- [ ] **Step 1: Write the failing tests**

```python
def test_gather_articles_dedups_and_attaches_full_text():
    def esearch(q, retmax=10, **kw):
        return {"melatonin clock": ["1", "2"], "melatonin sleep": ["2", "3"]}[q]
    def fetch_metadata(pmids):
        return {p: {"title": f"T{p}", "abstract": f"abs{p}", "pmcid": ("PMC9" if p == "1" else None),
                    "authors": [], "journal": "J", "year": "2021", "publication_types": []}
                for p in pmids}
    def fetch_full_text(pmcid):
        return "FULLTEXT BODY"
    tier = lb.build.TIERS["narrow"]
    arts = lb.gather_articles(["melatonin clock", "melatonin sleep"],
                              esearch=esearch, fetch_metadata=fetch_metadata,
                              fetch_full_text=fetch_full_text, known_pmids=set(), tier=tier)
    pmids = {a["pmid"] for a in arts}
    assert pmids == {"1", "2", "3"}
    a1 = next(a for a in arts if a["pmid"] == "1")
    assert "FULLTEXT BODY" in a1["abstract"]      # full text appended for PMC article


def test_ledger_batch_for_used_shape():
    arts = [{"pmid": "1", "metadata": {"title": "T1", "authors": [], "journal": "J",
                                        "year": "2021", "publication_types": ["Journal Article"]}}]
    batch = lb.ledger_batch_for_used(arts)
    assert batch[0]["disposition"] == "used"
    assert batch[0]["pmid"] == "1"
    assert batch[0]["publication_types"] == ["Journal Article"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "gather_articles or ledger_batch" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `librarian_build.py`)

```python
def gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text,
                    known_pmids, tier):
    per_query = [esearch(q, retmax=tier["max_results"]) for q in sub_queries]
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


def write_nodes(kg_folder, nodes, today):
    nodes_dir = os.path.join(kg_folder, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)
    for n in nodes:
        fm, body = build.render_node_markdown(n, today)
        write_node(os.path.join(nodes_dir, n["file"]), fm, body)


def ledger_batch_for_used(articles):
    batch = []
    for a in articles:
        m = a["metadata"]
        batch.append({
            "pmid": a["pmid"], "disposition": "used", "title": m.get("title"),
            "authors": m.get("authors", []), "journal": m.get("journal"),
            "year": m.get("year"), "publication_types": m.get("publication_types", []),
        })
    return batch
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "gather_articles or ledger_batch" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: build orchestrator retrieval + persistence helpers"
```

---

## Task 11: Orchestrator BUILD — `main` wiring + end-to-end fixture test

**Files:**
- Modify: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces: `librarian_build.run_build(topic, kg_folder, kg_name, *, esearch, fetch_metadata, fetch_full_text, chat, breadth_override, today, run_subprocess=True) -> dict` — the full BUILD path with all seams injected, returning a summary dict `{nodes, passed, failed, kg_folder}`. When `run_subprocess=False`, skips the deterministic-script subprocesses (for unit testing the orchestration logic); writes manifest + node files either way. `main(argv)` parses args and calls `run_build` with real seams + `run_subprocess=True`.

- [ ] **Step 1: Write the failing end-to-end test** (uses scratchpad dir, fakes, `run_subprocess=False`)

```python
def test_run_build_end_to_end_writes_manifest_and_nodes(tmp_path):
    kg = tmp_path / "KG_Mel"
    def esearch(q, retmax=10, **kw):
        return ["1", "2"]
    def fetch_metadata(pmids):
        return {p: {"title": f"T{p}", "abstract": f"Melatonin fact {p}.", "pmcid": None,
                    "authors": [], "journal": "J", "year": "2021", "publication_types": []}
                for p in pmids}
    def fetch_full_text(pmcid):
        return ""
    # plan_search, skeleton, 2x node synth, relationships, then per-PMID eval verdicts
    replies = iter([
        '{"breadth": "narrow", "sub_queries": ["melatonin clock", "melatonin sleep"]}',
        '{"nodes": [{"title": "Entrainment", "summary": "Melatonin fact 1.", "pmids": ["1"]},'
        '{"title": "Latency", "summary": "Melatonin fact 2.", "pmids": ["2"]}]}',
        '{"title": "Entrainment", "summary": "Melatonin fact 1.", "detail": "d", "tags": ["c"],'
        '"keywords": ["k"], "entities": [], "supports": {"1": "Melatonin fact 1."}}',
        '{"title": "Latency", "summary": "Melatonin fact 2.", "detail": "d", "tags": ["c"],'
        '"keywords": ["k"], "entities": [], "supports": {"2": "Melatonin fact 2."}}',
        '{"edges": []}',
        # evaluator verdicts (one per node/pmid) — supported with verbatim quote
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "Melatonin fact 1.", "source": "abstract"}]}',
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "Melatonin fact 2.", "source": "abstract"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    summary = lb.run_build(
        "melatonin", str(kg), "KG_Mel", esearch=esearch, fetch_metadata=fetch_metadata,
        fetch_full_text=fetch_full_text, chat=chat, breadth_override="narrow",
        today="2026-06-24", run_subprocess=False)
    assert summary["nodes"] == 2
    manifest = json.loads((kg / "manifest.json").read_text())
    assert len(manifest["nodes"]) == 2
    assert (kg / "nodes" / manifest["nodes"][0]["file"]).exists()
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k run_build -v`
Expected: FAIL `AttributeError: module 'librarian_build' has no attribute 'run_build'`.

- [ ] **Step 3: Implement `run_build` + `main`** (append to `librarian_build.py`)

```python
def _now_date():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _run(scripts_dir, *args):
    subprocess.run([sys.executable, *args], check=True)


def run_build(topic, kg_folder, kg_name, *, esearch, fetch_metadata, fetch_full_text,
              chat, breadth_override, today, run_subprocess=True):
    os.makedirs(os.path.join(kg_folder, "nodes"), exist_ok=True)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    plan = build.plan_search(topic, chat=chat, breadth_override=breadth_override)
    breadth, sub_queries = plan["breadth"], plan["sub_queries"]
    tier = build.TIERS[breadth]

    if run_subprocess:
        _run(scripts_dir, os.path.join(scripts_dir, "pmid_ledger.py"), "init",
             kg_folder, "--kg-name", kg_name)
        known = set(subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "pmid_ledger.py"), "query",
             kg_folder, "--pmids-only"], capture_output=True, text=True, check=True
        ).stdout.split())
    else:
        known = set()

    articles = gather_articles(
        sub_queries, esearch=esearch, fetch_metadata=fetch_metadata,
        fetch_full_text=fetch_full_text, known_pmids=known, tier=tier)
    if not articles:
        raise build.BuildError("no articles retrieved for topic")

    nodes, manifest = construct_graph(
        topic, kg_name, articles, chat=chat, breadth=breadth,
        sub_queries=sub_queries, today=today)
    write_nodes(kg_folder, nodes, today)
    with open(os.path.join(kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    if run_subprocess:
        batch_path = os.path.join(kg_folder, "_build_ledger_batch.json")
        with open(batch_path, "w", encoding="utf-8") as fh:
            json.dump(ledger_batch_for_used(articles), fh)
        _run(scripts_dir, os.path.join(scripts_dir, "pmid_ledger.py"), "batch-add",
             kg_folder, "--input", batch_path)
        os.remove(batch_path)
        _run(scripts_dir, os.path.join(scripts_dir, "classify_evidence_tier.py"),
             kg_folder, "--update-ledger")
        _run(scripts_dir, os.path.join(scripts_dir, "stamp_literature.py"), kg_folder)

    # Phase 3 evaluation — reuse the Phase-2 evaluator in-process.
    import librarian_evaluate as le
    passed = failed = 0
    fm_by_node = {}
    for n in nodes:
        node_fm = {"title": n["title"],
                   "pubmed_ids": [{"pmid": p, "supports": c} for p, c in n["supports"].items()]}
        entry = le.evaluate_node(
            n["id"], node_fm, fetch_metadata=fetch_metadata,
            fetch_full_text=fetch_full_text, chat=chat)
        fm_by_node[n["id"]] = le.frontmatter_updates(entry)
        passed += entry["overall_status"] == "passed"
        failed += entry["overall_status"] == "failed"
    # apply evaluation results to node files
    for n in nodes:
        path = os.path.join(kg_folder, "nodes", n["file"])
        fm, body = build.render_node_markdown(n, today)
        upd = fm_by_node[n["id"]]
        fm["evaluation_status"] = upd["evaluation_status"]
        fm["quarantined"] = upd["quarantined"]
        verified = {r["pmid"]: r for r in upd["pubmed_ids"]}
        for ref in fm["pubmed_ids"]:
            r = verified.get(ref["pmid"])
            if r:
                ref["verified"] = r["verified"]
                if r.get("quotes"):
                    ref["quotes"] = r["quotes"]
        write_node(path, fm, body)

    if run_subprocess:
        _run(scripts_dir, os.path.join(scripts_dir, "enforce_quarantine.py"), kg_folder)
        _run(scripts_dir, os.path.join(scripts_dir, "generate_index.py"), kg_folder,
             "--overview-text", f"Knowledge graph on {topic}.")
        _run(scripts_dir, os.path.join(scripts_dir, "update_manifest_stats.py"), kg_folder)
        _run(scripts_dir, os.path.join(scripts_dir, "validate_manifest.py"),
             os.path.join(kg_folder, "manifest.json"))
        subprocess.run([sys.executable, os.path.join(scripts_dir, "build_embeddings.py"),
                        kg_folder], check=False)  # non-fatal
        _run(scripts_dir, os.path.join(scripts_dir, "append_log.py"), kg_folder,
             "--op", "build",
             "--summary", f"Local BUILD: {len(nodes)} nodes, {passed} passed, {failed} failed.")

    return {"nodes": len(nodes), "passed": passed, "failed": failed, "kg_folder": kg_folder}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude-free KG build orchestrator")
    parser.add_argument("topic")
    parser.add_argument("--output", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default=None)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)

    kg_name = args.output or "KG_" + build.slugify(args.topic)
    kg_folder = kg_name
    try:
        summary = run_build(
            args.topic, kg_folder, kg_name, esearch=pubmed.esearch,
            fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
            chat=llm.chat, breadth_override=args.breadth, today=_now_date())
    except llm.LLMUnavailable as e:
        print(f"Error: local model unavailable — nothing written: {e}", file=sys.stderr)
        return 2
    except build.BuildError as e:
        print(f"Error: build failed — {e}", file=sys.stderr)
        return 1
    print(f"BUILD complete: {summary['nodes']} nodes "
          f"({summary['passed']} passed, {summary['failed']} failed) → {summary['kg_folder']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Note: `--interactive` and UPDATE-mode dispatch are added in Tasks 12–14; for now `main` always runs BUILD. The `--since` arg is parsed but only used by UPDATE (Task 13).

- [ ] **Step 4: Run to verify pass + full suite**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -v && conda run -n nono python -m pytest tests/unit -q`
Expected: PASS (all green).

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: build orchestrator BUILD run_build + main"
```

---

## Task 12: UPDATE mode — two-track query construction

**Files:**
- Modify: `scripts/lib/build.py`
- Test: `tests/unit/test_build_lib.py`

**Interfaces:**
- Produces:
  - `build.weak_spots(manifest_nodes: list[dict]) -> list[str]` — node IDs that are under-referenced (≤1 PMID) or failed/quarantined.
  - `build.gap_fill_queries(topic, weak_node_summaries, *, chat, count) -> list[str]` — model proposes `count` gap-fill queries (synonyms/MeSH targeting weak spots); raises `BuildError` if none.

- [ ] **Step 1: Write the failing test**

```python
def test_weak_spots_finds_under_referenced_and_failed():
    nodes = [
        {"id": "node_001", "pubmed_ids": ["1"], "evaluation_status": "passed"},
        {"id": "node_002", "pubmed_ids": ["1", "2"], "evaluation_status": "passed"},
        {"id": "node_003", "pubmed_ids": ["3", "4"], "evaluation_status": "failed"},
    ]
    assert set(build.weak_spots(nodes)) == {"node_001", "node_003"}


def test_gap_fill_queries_returns_strings():
    def chat(messages, **kw):
        return '{"queries": ["alt term A", "MeSH B"]}'
    out = build.gap_fill_queries("topic", ["summary one"], chat=chat, count=2)
    assert out == ["alt term A", "MeSH B"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "weak_spots or gap_fill" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `lib/build.py`)

```python
def weak_spots(manifest_nodes):
    out = []
    for n in manifest_nodes:
        if len(n.get("pubmed_ids", [])) <= 1 or \
           n.get("evaluation_status") == "failed" or n.get("quarantined"):
            out.append(n["id"])
    return out


_GAP_SYS = (
    "Propose PubMed gap-fill queries that find DIFFERENT articles than an "
    "original search — use synonyms, MeSH headings, alternate phrasings — to "
    "strengthen weakly-supported knowledge nodes. Reply with ONE JSON object: "
    "{\"queries\": [\"...\"]}."
)


def gap_fill_queries(topic, weak_node_summaries, *, chat, count):
    user = (f"TOPIC: {topic}\nProduce {count} gap-fill queries for these weak nodes:\n"
            + "\n".join(f"- {s}" for s in weak_node_summaries))
    obj = _ask_json(chat, [{"role": "system", "content": _GAP_SYS},
                           {"role": "user", "content": user}])
    qs = [str(q).strip() for q in (obj.get("queries") or []) if str(q).strip()]
    if not qs:
        raise BuildError("no gap-fill queries produced")
    return qs[:count]
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_build_lib.py -k "weak_spots or gap_fill" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/build.py tests/unit/test_build_lib.py
git commit -m "feat: UPDATE two-track query construction"
```

---

## Task 13: UPDATE mode — merge into existing graph + changelog

**Files:**
- Modify: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces:
  - `librarian_build.next_node_number(manifest: dict) -> int` — highest existing `node_NNN` + 1 (1 if none).
  - `librarian_build.run_update(topic, kg_folder, *, esearch, fetch_metadata, fetch_full_text, chat, since_date, today, run_subprocess=True) -> dict` — loads manifest, derives recent (manifest `search_profile.sub_queries`) + gap-fill tracks, gathers novel articles (excluding ledger-known), constructs NEW nodes only (IDs continue from `next_node_number`), appends them to the manifest `nodes[]`/`edges`, writes node files, evaluates new nodes, and records a changelog buffer returned as `summary["changelog"]`. Never deletes/rewrites existing nodes.

- [ ] **Step 1: Write the failing test**

```python
def test_next_node_number():
    assert lb.next_node_number({"nodes": [{"id": "node_001"}, {"id": "node_004"}]}) == 5
    assert lb.next_node_number({"nodes": []}) == 1


def test_run_update_appends_new_nodes(tmp_path):
    kg = tmp_path / "KG_Mel"
    (kg / "nodes").mkdir(parents=True)
    manifest = {"kg_name": "KG_Mel", "topic": "melatonin", "version": 1,
                "data_sources": ["pubmed"],
                "search_profile": {"breadth": "narrow", "sub_queries": ["melatonin clock"]},
                "nodes": [{"id": "node_001", "title": "Existing", "file": "node_001_existing.md",
                           "tags": ["c"], "summary": "old", "keywords": [], "pubmed_ids": ["1"],
                           "evaluation_status": "passed", "evidence_tier": "review", "entities": []}],
                "edges": [], "statistics": {}}
    (kg / "manifest.json").write_text(json.dumps(manifest))
    def esearch(q, retmax=10, **kw):
        return ["2"]                       # one novel PMID
    def fetch_metadata(pmids):
        return {p: {"title": f"T{p}", "abstract": f"New melatonin fact {p}.", "pmcid": None,
                    "authors": [], "journal": "J", "year": "2022", "publication_types": []}
                for p in pmids}
    replies = iter([
        # gap-fill queries
        '{"queries": ["melatonin pineal"]}',
        # skeleton (new nodes)
        '{"nodes": [{"title": "New finding", "summary": "New melatonin fact 2.", "pmids": ["2"]}]}',
        # node synthesis
        '{"title": "New finding", "summary": "New melatonin fact 2.", "detail": "d", "tags": ["c"],'
        '"keywords": ["k"], "entities": [], "supports": {"2": "New melatonin fact 2."}}',
        # relationships among new nodes
        '{"edges": []}',
        # eval verdict
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "New melatonin fact 2.", "source": "abstract"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    summary = lb.run_update("melatonin", str(kg), esearch=esearch, fetch_metadata=fetch_metadata,
                            fetch_full_text=lambda p: "", chat=chat, since_date="2021/01/01",
                            today="2026-06-24", run_subprocess=False)
    m = json.loads((kg / "manifest.json").read_text())
    ids = [n["id"] for n in m["nodes"]]
    assert "node_001" in ids and "node_002" in ids   # old kept, new appended
    assert summary["nodes_created"] == ["node_002"]
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "next_node_number or run_update" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `librarian_build.py`)

```python
def next_node_number(manifest):
    nums = [int(n["id"].split("_")[1]) for n in manifest.get("nodes", [])
            if n.get("id", "").startswith("node_")]
    return (max(nums) + 1) if nums else 1


def run_update(topic, kg_folder, *, esearch, fetch_metadata, fetch_full_text, chat,
               since_date, today, run_subprocess=True):
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    breadth = manifest.get("search_profile", {}).get("breadth", "medium")
    tier = build.TIERS[breadth]
    recent_qs = manifest.get("search_profile", {}).get("sub_queries", [])

    # split sub-query budget ~60/40 recent/gap-fill
    n_gap = max(1, tier["sub_queries"] - len(recent_qs)) if recent_qs else 1
    weak_ids = set(build.weak_spots(manifest["nodes"]))
    weak_summaries = [n["summary"] for n in manifest["nodes"] if n["id"] in weak_ids]
    try:
        gap_qs = build.gap_fill_queries(topic, weak_summaries or [topic], chat=chat, count=n_gap)
    except build.BuildError:
        gap_qs = []

    known = set()
    if run_subprocess:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        known = set(subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "pmid_ledger.py"), "query",
             kg_folder, "--pmids-only"], capture_output=True, text=True, check=True
        ).stdout.split())
    else:
        known = {p for n in manifest["nodes"] for p in n.get("pubmed_ids", [])}

    articles = gather_articles(recent_qs + gap_qs, esearch=esearch,
                               fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text,
                               known_pmids=known, tier=tier)
    if not articles:
        return {"nodes_created": [], "passed": 0, "failed": 0, "changelog": [], "kg_folder": kg_folder}

    start = next_node_number(manifest)
    new_nodes, sub_manifest = construct_graph(
        topic, manifest["kg_name"], articles, chat=chat, breadth=breadth,
        sub_queries=recent_qs, today=today, start_id=start)
    write_nodes(kg_folder, new_nodes, today)

    manifest["nodes"].extend(sub_manifest["nodes"])
    manifest["edges"].extend(sub_manifest["edges"])
    manifest["version"] = manifest.get("version", 1) + 1
    manifest["updated"] = today
    manifest.setdefault("search_profile", {})["updated"] = today
    with open(os.path.join(kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    import librarian_evaluate as le
    passed = failed = 0
    for n in new_nodes:
        node_fm = {"title": n["title"],
                   "pubmed_ids": [{"pmid": p, "supports": c} for p, c in n["supports"].items()]}
        entry = le.evaluate_node(n["id"], node_fm, fetch_metadata=fetch_metadata,
                                 fetch_full_text=fetch_full_text, chat=chat)
        upd = le.frontmatter_updates(entry)
        path = os.path.join(kg_folder, "nodes", n["file"])
        fm, body = build.render_node_markdown(n, today)
        fm["evaluation_status"] = upd["evaluation_status"]
        fm["quarantined"] = upd["quarantined"]
        verified = {r["pmid"]: r for r in upd["pubmed_ids"]}
        for ref in fm["pubmed_ids"]:
            r = verified.get(ref["pmid"])
            if r:
                ref["verified"] = r["verified"]
                if r.get("quotes"):
                    ref["quotes"] = r["quotes"]
        write_node(path, fm, body)
        passed += entry["overall_status"] == "passed"
        failed += entry["overall_status"] == "failed"

    changelog = [{"id": n["id"], "title": n["title"]} for n in new_nodes]
    return {"nodes_created": [n["id"] for n in new_nodes], "passed": passed,
            "failed": failed, "changelog": changelog, "kg_folder": kg_folder}
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "next_node_number or run_update" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: UPDATE mode merge + changelog"
```

---

## Task 14: Mode dispatch in `main` (BUILD vs UPDATE)

**Files:**
- Modify: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces: `librarian_build.resolve_mode(kg_folder, topic) -> str` — `"update"` if `<kg_folder>/manifest.json` exists, else `"build"`. `librarian_build.derive_since(manifest, override) -> str` — `YYYY/MM/DD` from `--since`, else `schedule.last_run` date, else manifest `updated`.

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_mode(tmp_path):
    kg = tmp_path / "KG_X"
    assert lb.resolve_mode(str(kg), "t") == "build"
    (kg).mkdir(); (kg / "manifest.json").write_text("{}")
    assert lb.resolve_mode(str(kg), "t") == "update"


def test_derive_since_prefers_override():
    assert lb.derive_since({"updated": "2026-01-01"}, "2026-03-01") == "2026/03/01"
    assert lb.derive_since({"updated": "2026-01-01"}, None) == "2026/01/01"
    assert lb.derive_since({"schedule": {"last_run": "2026-05-05T00:00:00Z"},
                            "updated": "2026-01-01"}, None) == "2026/05/05"
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "resolve_mode or derive_since" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement + wire `main`** (append helpers; update `main` to dispatch)

```python
def resolve_mode(kg_folder, topic):
    return "update" if os.path.exists(os.path.join(kg_folder, "manifest.json")) else "build"


def derive_since(manifest, override):
    if override:
        return override.replace("-", "/")
    last = (manifest.get("schedule") or {}).get("last_run")
    if last:
        return last[:10].replace("-", "/")
    return (manifest.get("updated") or "").replace("-", "/")
```

In `main`, replace the BUILD-only body with mode dispatch:

```python
    kg_name = args.output or "KG_" + build.slugify(args.topic)
    kg_folder = kg_name
    mode = resolve_mode(kg_folder, args.topic)
    try:
        if mode == "build":
            summary = run_build(
                args.topic, kg_folder, kg_name, esearch=pubmed.esearch,
                fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
                chat=llm.chat, breadth_override=args.breadth, today=_now_date())
            print(f"BUILD complete: {summary['nodes']} nodes "
                  f"({summary['passed']} passed, {summary['failed']} failed) → {kg_folder}")
        else:
            with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            since = derive_since(manifest, args.since)
            summary = run_update(
                args.topic, kg_folder, esearch=pubmed.esearch,
                fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
                chat=llm.chat, since_date=since, today=_now_date())
            print(f"UPDATE complete: {len(summary['nodes_created'])} new nodes "
                  f"({summary['passed']} passed, {summary['failed']} failed) → {kg_folder}")
    except llm.LLMUnavailable as e:
        print(f"Error: local model unavailable — nothing written: {e}", file=sys.stderr)
        return 2
    except build.BuildError as e:
        print(f"Error: build failed — {e}", file=sys.stderr)
        return 1
    return 0
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -v && conda run -n nono python -m pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: build orchestrator mode dispatch (BUILD/UPDATE)"
```

---

## Task 15: `--interactive` source-gathering checkpoint

**Files:**
- Modify: `scripts/librarian_build.py`
- Test: `tests/unit/test_librarian_build.py`

**Interfaces:**
- Produces: `librarian_build.source_report(topic, mode, breadth, sub_queries, articles) -> str` — the formatted source-gathering report text. `librarian_build.apply_steer(steer: str, articles, sub_queries) -> tuple[list, list, bool]` — interprets a steer command (`""`/`"proceed"` → unchanged + proceed=True; `"narrow:<term>"` → drop matching articles; otherwise proceed=True ignoring unknown). `run_build`/`run_update` gain an optional `prompt_fn=None` param; when provided (and `--interactive`), after `gather_articles` they print `source_report`, call `prompt_fn()` for a steer string, and apply `apply_steer` before construction. Default `prompt_fn=None` skips the pause (keeps tests non-interactive).

- [ ] **Step 1: Write the failing test**

```python
def test_source_report_lists_counts():
    arts = [{"pmid": "1", "title": "T1", "abstract": "a"}]
    rep = lb.source_report("melatonin", "build", "narrow", ["q1"], arts)
    assert "melatonin" in rep and "narrow" in rep and "PMIDs" in rep


def test_apply_steer_narrow_drops_matching():
    arts = [{"pmid": "1", "title": "melatonin sleep", "abstract": "a"},
            {"pmid": "2", "title": "cancer trial", "abstract": "b"}]
    kept, subs, proceed = lb.apply_steer("narrow:cancer", arts, ["q1"])
    assert [a["pmid"] for a in kept] == ["1"]
    assert proceed is True


def test_apply_steer_empty_proceeds_unchanged():
    arts = [{"pmid": "1", "title": "t", "abstract": "a"}]
    kept, subs, proceed = lb.apply_steer("", arts, ["q1"])
    assert kept == arts and proceed is True
```

- [ ] **Step 2: Run to verify fail**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -k "source_report or apply_steer" -v`
Expected: FAIL `AttributeError`.

- [ ] **Step 3: Implement** (append to `librarian_build.py`; thread `prompt_fn` through `run_build`/`run_update` right after `gather_articles`)

```python
def source_report(topic, mode, breadth, sub_queries, articles):
    lines = [
        "=== Source Gathering Complete — Awaiting Review ===",
        f"Topic: {topic}", f"Mode: {mode}", f"Breadth tier: {breadth}",
        f"Sub-queries: {', '.join(sub_queries)}",
        f"PMIDs retrieved: {len(articles)}",
        "",
        "Top articles:",
    ]
    for a in articles[:5]:
        lines.append(f"  PMID {a['pmid']} — {a['title']}")
    lines.append("")
    lines.append("Steer: <enter>=proceed | narrow:<term>=drop matching articles")
    return "\n".join(lines)


def apply_steer(steer, articles, sub_queries):
    s = (steer or "").strip()
    if s.lower().startswith("narrow:"):
        term = s.split(":", 1)[1].strip().lower()
        kept = [a for a in articles
                if term not in a["title"].lower() and term not in a.get("abstract", "").lower()]
        return kept, sub_queries, True
    return articles, sub_queries, True
```

In `run_build` and `run_update`, immediately after the `articles = gather_articles(...)` line, insert:

```python
    if prompt_fn is not None:
        print(source_report(topic, "build", breadth, sub_queries, articles))
        articles, sub_queries, _ = apply_steer(prompt_fn(), articles, sub_queries)
```

Add `prompt_fn=None` to both signatures. In `main`, pass `prompt_fn=(lambda: input("> ")) if args.interactive else None`.

- [ ] **Step 4: Run to verify pass + full suite**

Run: `conda run -n nono python -m pytest tests/unit/test_librarian_build.py -v && conda run -n nono python -m pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/librarian_build.py tests/unit/test_librarian_build.py
git commit -m "feat: build --interactive source checkpoint"
```

---

## Task 16: Wire the build path into the skill + close the loop

**Files:**
- Modify: `.claude/skills/nono-librarian/SKILL.md`
- Test: manual CLI smoke (`--help`) + full suite

**Interfaces:** none (docs + dispatch).

- [ ] **Step 1: Replace the "Build / ingest — NOT yet Claude-free" section** with:

```markdown
### Build / ingest a KG — Claude-free now (uses the local model)

`scripts/librarian_build.py` constructs (BUILD) or extends (UPDATE) a KG using
the local model (`lib/llm.py`) + E-utilities (`lib/pubmed.py`), then runs the
full deterministic finish (ledger, evidence tiers, literature stamping,
evaluation, quarantine, index, validation, embeddings, log). It auto-detects
UPDATE when the target folder already has a manifest.

```bash
conda run -n nono python scripts/librarian_build.py "<topic>" \
    [--output KG_Name] [--since YYYY-MM-DD] [--breadth narrow|medium|broad] [--interactive]
```

The orchestrator owns control flow; the model only does narrow, schema-validated
steps (search planning, node skeletons, per-node synthesis, relationships).
Hallucinated PMIDs are filtered against what PubMed actually returned, and every
node is verified by the same guardrailed evaluator as `librarian_evaluate.py`.
If the model endpoint is down the run aborts and writes nothing. Quality tracks
the local model; the Claude `/build-kg` command remains the higher-quality
default for important graphs.
```

Also update the "Scope" bullets: move build from "Not yet local" to "Works locally".

- [ ] **Step 2: Smoke-test the CLI**

Run: `conda run -n nono python scripts/librarian_build.py --help`
Expected: usage text with `--output/--since/--breadth/--interactive`.

- [ ] **Step 3: Run the full suite**

Run: `conda run -n nono python -m pytest tests/unit -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/nono-librarian/SKILL.md
git commit -m "docs: route build intent to local orchestrator in skill"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-06-24-claude-free-librarian-design.md` Phase 3 + resolved decisions):
- Deterministic state machine over build-kg phases → Tasks 9–15. ✓
- Each LLM step constrained, schema-checked, retried → `_ask_json`/`BuildError` (Task 2), `verify_pmid` retry reused from Phase 2. ✓ (Note: build-step retry is single-shot via `_ask_json`; per-step retry loop is folded into the abort-on-`BuildError` contract — acceptable since the whole run aborts cleanly. If multi-attempt is wanted later, wrap `_ask_json` in a retry like `verify_pmid`.)
- Reuses pmid_ledger, classify_evidence_tier, build_embeddings, render_digest, validate_manifest, stamp_literature, generate_index, enforce_quarantine, update_manifest_stats, evaluator → Tasks 11/13. ✓
- Decision 1 (no model bar) → no gating anywhere. ✓
- Decision 2 (full text) → `gather_articles` fetches PMC full text for top-N, degrades to abstract-only on failure. ✓ (build-time degradation is explicit in Global Constraints.)
- Decision 3 (deterministic evidence tiers) → `classify_evidence_tier.py` subprocess, zero model calls. ✓
- Decision 4 (accept variance) → no determinism assertions. ✓
- Decision 5 (concurrency 3) → serial v1, documented ceiling. ✓
- Scope BUILD+UPDATE+interactive, PubMed-only → Tasks 9–15; CT.gov/ChEMBL & entity-ID normalization explicitly out of scope. ✓
- Digest/run-record (build-kg Phase 4 1e): **GAP** — `run_build` does not yet write `runs/<run_id>.json` + `render_digest.py`. Add as a follow-up step in Task 11 if digest parity is required for manual builds; the scheduled path already renders digests independently. Flagged, not silently dropped.

**Placeholder scan:** no TBD/TODO; every code step has complete code. ✓

**Type consistency:** `chat(messages, **kw)` signature uniform; `articles` item shape `{pmid,title,abstract,metadata}` consistent between `gather_articles`, `propose_skeleton`, `synthesize_node`; `supports` is `dict[pmid->claim]` throughout; manifest `pubmed_ids` are strings (manifest) vs node-frontmatter objects (node files) — intentional, matches existing schema split. ✓

**Known follow-ups (out of scope, listed for honesty):** run-record/digest in manual build (gap above); multi-attempt retry on build reasoning steps; ClinicalTrials/ChEMBL seams; entity ID normalization; `--source` user-provided materials; controversy `[!debate]` callouts.
