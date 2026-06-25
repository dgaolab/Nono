import json
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import librarian_build as lb

_ARTS = [
    {"pmid": "1", "title": "Melatonin and clock genes", "abstract": "Melatonin entrains the SCN."},
    {"pmid": "2", "title": "Melatonin for sleep", "abstract": "Melatonin reduces sleep latency."},
]


# --------------------------------------------------------------------------
# build_run_record — pure run-record builder (GAP-A: digest/run-record parity)
# --------------------------------------------------------------------------

def test_build_run_record_shape_and_run_id():
    nodes = [
        {"id": "node_001", "supports": {"1": "a", "2": "b"}},
        {"id": "node_002", "supports": {"2": "c"}},
    ]
    rr = lb.build_run_record(
        kg_name="KG_Mel", mode="build", version=1,
        timestamp="2026-06-25T08:00:12Z", nodes=nodes, passed=2, failed=0)
    assert rr["run_id"] == "2026-06-25T080012Z-v1"   # colons stripped + -v<version>
    assert rr["nodes_created"] == ["node_001", "node_002"]
    assert rr["nodes_revised"] == []
    assert rr["eval_summary"] == {"evaluated": 2, "passed": 2, "failed": 0}
    # refs_added groups node ids per pmid (sorted)
    by_pmid = {r["pmid"]: r["nodes"] for r in rr["refs_added"]}
    assert by_pmid["1"] == ["node_001"]
    assert by_pmid["2"] == ["node_001", "node_002"]
    assert rr["refs_failed"] == []


def test_build_run_record_orders_pmids_numerically():
    nodes = [{"id": "node_001", "supports": {"2": "a", "10": "b", "9": "c"}}]
    rr = lb.build_run_record(
        kg_name="K", mode="build", version=1, timestamp="2026-06-25T08:00:12Z",
        nodes=nodes, passed=1, failed=0)
    assert [r["pmid"] for r in rr["refs_added"]] == ["2", "9", "10"]  # not "10","2","9"


def test_build_run_record_validates_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "schemas",
                               "run_record_schema.json")
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    rr = lb.build_run_record(
        kg_name="KG_Mel", mode="update", version=3,
        timestamp="2026-06-25T08:00:12Z",
        nodes=[{"id": "node_003", "supports": {"9": "x"}}],
        passed=1, failed=0, since_date="2021/01/01")
    jsonschema.validate(rr, schema)   # raises if non-conforming
    assert rr["since_date"] == "2021/01/01"


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


def test_gather_articles_forwards_mindate_to_esearch():
    """gather_articles should pass mindate kwarg through to esearch when provided."""
    received_kwargs = {}

    def esearch(q, retmax=10, **kw):
        received_kwargs[q] = kw
        return ["1"]

    def fetch_metadata(pmids):
        return {p: {"title": f"T{p}", "abstract": f"abs{p}", "pmcid": None,
                    "authors": [], "journal": "J", "year": "2021", "publication_types": []}
                for p in pmids}

    tier = lb.build.TIERS["narrow"]
    lb.gather_articles(["q1"], esearch=esearch, fetch_metadata=fetch_metadata,
                       fetch_full_text=lambda p: "", known_pmids=set(), tier=tier,
                       mindate="2022/01/01")
    assert received_kwargs.get("q1", {}).get("mindate") == "2022/01/01"


def _make_integration_chat_build():
    """Scripted chat replies for a 2-node build: plan→skeleton→2 synths→rels→2 evals."""
    replies = iter([
        # plan_search
        '{"breadth": "narrow", "sub_queries": ["melatonin circadian", "melatonin sleep"]}',
        # skeleton (2 nodes)
        '{"nodes": ['
        '{"title": "Circadian Entrainment", "summary": "Melatonin entrains SCN.", "pmids": ["1"]},'
        '{"title": "Sleep Latency", "summary": "Melatonin finding 2.", "pmids": ["2"]}'
        ']}',
        # node 1 synthesis
        '{"title": "Circadian Entrainment", "summary": "Melatonin entrains SCN.", "detail": "d1",'
        '"tags": ["circadian"], "keywords": ["scn"], "entities": [], "supports": {"1": "Melatonin finding 1."}}',
        # node 2 synthesis
        '{"title": "Sleep Latency", "summary": "Melatonin finding 2.", "detail": "d2",'
        '"tags": ["circadian"], "keywords": ["sleep"], "entities": [], "supports": {"2": "Melatonin finding 2."}}',
        # relationships
        '{"edges": []}',
        # evaluator verdicts — supported with verbatim quote matching abstract text
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "Melatonin finding 1.", "source": "abstract"}]}',
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "Melatonin finding 2.", "source": "abstract"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    return chat


def test_run_build_subprocess_true_finishes_kg(tmp_path):
    """run_build with run_subprocess=True should produce a fully-finished KG:
    ledger with used PMIDs, classified evidence tiers, index, and valid manifest stats.
    """
    kg = tmp_path / "KG_Mel"

    def esearch(q, retmax=10, **kw):
        return ["1", "2"]

    def fetch_metadata(pmids):
        return {p: {
            "title": f"T{p}",
            "abstract": f"Melatonin finding {p}.",
            "pmcid": None,
            "authors": [],
            "journal": "J",
            "year": "2021",
            # "Randomized Controlled Trial" → evidence_tier "rct" per PUBTYPE_MAP
            "publication_types": ["Randomized Controlled Trial"],
        } for p in pmids}

    chat = _make_integration_chat_build()

    result = lb.run_build(
        "melatonin", str(kg), "KG_Mel",
        esearch=esearch,
        fetch_metadata=fetch_metadata,
        fetch_full_text=lambda p: "",
        chat=chat,
        breadth_override="narrow",
        today="2026-06-24",
        run_subprocess=True,
    )

    # Ledger exists and contains both PMIDs as "used"
    ledger_path = kg / "_pmid_ledger.json"
    assert ledger_path.exists(), "_pmid_ledger.json not created"
    ledger = json.loads(ledger_path.read_text())
    entries = ledger.get("entries", {})
    assert "1" in entries, "PMID 1 not in ledger"
    assert "2" in entries, "PMID 2 not in ledger"
    assert entries["1"]["disposition"] == "used"
    assert entries["2"]["disposition"] == "used"

    # Index was generated
    assert (kg / "_index.md").exists(), "_index.md not generated"

    # Evidence tier classified — "Randomized Controlled Trial" → "rct"
    manifest = json.loads((kg / "manifest.json").read_text())
    node_file = kg / manifest["nodes"][0]["file"]   # file is kg-root-relative (nodes/...)
    from lib.frontmatter import parse as parse_fm
    sys.path.insert(0, str(kg.parent.parent.parent / "scripts"))
    fm, _ = parse_fm(str(node_file))
    assert fm.get("evidence_tier") == "rct", (
        f"Expected evidence_tier='rct', got '{fm.get('evidence_tier')}'"
    )

    # Log was appended
    assert (kg / "_log.md").exists(), "_log.md not created"

    # Manifest statistics updated
    assert manifest["statistics"].get("total_nodes") == 2, (
        f"Expected total_nodes=2, got {manifest['statistics']}"
    )

    # GAP-A: run-record + digest produced
    runs = list((kg / "runs").glob("*.json"))
    assert len(runs) == 1, f"expected one run-record, got {runs}"
    rr = json.loads(runs[0].read_text())
    assert rr["mode"] == "build" and rr["eval_summary"]["evaluated"] == 2
    assert (kg / "_digest.md").exists(), "_digest.md not rendered"


def _make_integration_chat_update():
    """Scripted chat replies for a 1-node update (PMID 3): gap_fill→skeleton→synth→rels→eval."""
    replies = iter([
        # gap_fill_queries
        '{"queries": ["melatonin review"]}',
        # skeleton (1 new node citing pmid 3)
        '{"nodes": [{"title": "Review Finding", "summary": "Melatonin finding 3.", "pmids": ["3"]}]}',
        # node synthesis
        '{"title": "Review Finding", "summary": "Melatonin finding 3.", "detail": "d3",'
        '"tags": ["circadian"], "keywords": ["review"], "entities": [], "supports": {"3": "Melatonin finding 3."}}',
        # relationships among new nodes
        '{"edges": []}',
        # evaluator verdict for node 3 / pmid 3
        '{"verdict": "supported", "reasoning": "ok", "quotes": [{"text": "Melatonin finding 3.", "source": "abstract"}]}',
    ])
    def chat(messages, **kw):
        return next(replies)
    return chat


def test_run_update_subprocess_true_persists_novel_pmids(tmp_path):
    """run_update with run_subprocess=True must persist new PMIDs to ledger and
    update manifest statistics — the C1 bug (stdout.split vs json.loads) would
    cause PMID 3 to never be recognised as novel and the ledger would stay stale.
    """
    kg = tmp_path / "KG_Mel"

    # ---- First: run a real build so we have a properly-finished KG ----
    def esearch_build(q, retmax=10, **kw):
        return ["1", "2"]

    def fetch_metadata_build(pmids):
        return {p: {
            "title": f"T{p}",
            "abstract": f"Melatonin finding {p}.",
            "pmcid": None, "authors": [], "journal": "J", "year": "2021",
            "publication_types": ["Randomized Controlled Trial"],
        } for p in pmids}

    lb.run_build(
        "melatonin", str(kg), "KG_Mel",
        esearch=esearch_build,
        fetch_metadata=fetch_metadata_build,
        fetch_full_text=lambda p: "",
        chat=_make_integration_chat_build(),
        breadth_override="narrow",
        today="2026-06-24",
        run_subprocess=True,
    )

    # ---- Now run UPDATE with PMID 3 as novel ----
    def esearch_update(q, retmax=10, **kw):
        return ["3"]

    def fetch_metadata_update(pmids):
        return {p: {
            "title": f"T{p}",
            "abstract": f"Melatonin finding {p}.",
            "pmcid": None, "authors": [], "journal": "J", "year": "2022",
            "publication_types": ["Review"],
        } for p in pmids}

    result = lb.run_update(
        "melatonin", str(kg),
        esearch=esearch_update,
        fetch_metadata=fetch_metadata_update,
        fetch_full_text=lambda p: "",
        chat=_make_integration_chat_update(),
        since_date="2021/01/01",
        today="2026-06-25",
        run_subprocess=True,
    )

    # PMID "3" must be in ledger as "used" (proves C1 fix + _persist_and_classify)
    ledger = json.loads((kg / "_pmid_ledger.json").read_text())
    entries = ledger.get("entries", {})
    assert "3" in entries, (
        f"PMID 3 not found in ledger entries; ledger keys: {list(entries.keys())}"
    )
    assert entries["3"]["disposition"] == "used"

    # Manifest must have 3 nodes total
    manifest = json.loads((kg / "manifest.json").read_text())
    assert len(manifest["nodes"]) == 3, (
        f"Expected 3 nodes in manifest, got {len(manifest['nodes'])}"
    )

    # Manifest statistics must reflect all 3 nodes
    assert manifest["statistics"].get("total_nodes") == 3, (
        f"Expected total_nodes=3, got {manifest['statistics']}"
    )

    # Index still present
    assert (kg / "_index.md").exists(), "_index.md was removed or never regenerated"

    # GAP-A: UPDATE also writes a run-record (mode update) + refreshes the digest
    update_runs = [p for p in (kg / "runs").glob("*.json")
                   if json.loads(p.read_text())["mode"] == "update"]
    assert len(update_runs) == 1, f"expected one update run-record, got {update_runs}"
    assert (kg / "_digest.md").exists()


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
    assert summary["passed"] == 2
    assert summary["failed"] == 0
    manifest = json.loads((kg / "manifest.json").read_text())
    assert len(manifest["nodes"]) == 2
    assert (kg / manifest["nodes"][0]["file"]).exists()   # file is kg-root-relative (nodes/...)
