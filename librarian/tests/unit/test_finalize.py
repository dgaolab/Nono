import json
import os
from nono_librarian.cli import assemble, finalize
from nono_librarian.lib.frontmatter import parse as parse_node

RAW = {"sub_queries": ["brca1"], "nodes": [
    {"title": "BRCA1 in HR", "summary": "BRCA1 enables HR.", "detail": "p",
     "tags": ["mechanism"], "keywords": ["brca1"], "pmids": ["111"],
     "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 enables HR",
                     "verdict": "supported",
                     "quotes": [{"text": "BRCA1 enables HR", "source": "abstract"}]}]}]}

RAW2 = {"sub_queries": ["tp53"], "nodes": [
    {"title": "TP53 mutation in cancer", "summary": "TP53 is mutated.", "detail": "p",
     "tags": ["mechanism"], "keywords": ["tp53"], "pmids": ["222"],
     "pubmed_ids": [{"pmid": "222", "supports": "TP53 is mutated",
                     "verdict": "supported",
                     "quotes": [{"text": "TP53 is mutated", "source": "abstract"}]}]}]}


def _meta(pmids):
    return {p: {"title": f"T{p}", "abstract": "BRCA1 enables HR repair.",
                "pmcid": None, "authors": [{"last_name": "Author", "fore_name": "A"}],
                "journal": "J", "year": "2024",
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
    assert (kg / "_log.md").exists()         # append_log writes _log.md


def test_finalize_update_appends(tmp_path, monkeypatch):
    """UPDATE mode: second finalize must not clobber node_001 and must scope run-record to new nodes."""
    monkeypatch.chdir(tmp_path)
    kg = tmp_path / "KG_BRCA1"

    def _meta_both(pmids):
        data = {
            "111": {"title": "T111", "abstract": "BRCA1 enables HR repair.",
                    "pmcid": None, "authors": [{"last_name": "Author", "fore_name": "A"}],
                    "journal": "J", "year": "2024", "publication_types": ["Journal Article"]},
            "222": {"title": "T222", "abstract": "TP53 is mutated in cancer.",
                    "pmcid": None, "authors": [{"last_name": "Author2", "fore_name": "B"}],
                    "journal": "J2", "year": "2024", "publication_types": ["Journal Article"]},
        }
        return {p: data[p] for p in pmids if p in data}

    monkeypatch.setattr(finalize.pubmed, "fetch_metadata", _meta_both)
    monkeypatch.setattr(finalize.pubmed, "fetch_full_text", _ft)

    # --- BUILD phase (node_001) ---
    npath1 = tmp_path / "_nodes1.json"
    npath1.write_text(json.dumps(RAW))
    assemble.main([str(kg), "--nodes", str(npath1), "--topic", "BRCA1", "--breadth", "narrow"])
    (kg / "_candidates.json").write_text(json.dumps({"articles": [
        {"pmid": "111", "metadata": _meta_both(["111"])["111"]}]}))
    summary1 = finalize.finalize_kg(str(kg), mode="build", version=1)
    assert summary1["passed"] == 1

    # --- UPDATE phase (node_002) ---
    npath2 = tmp_path / "_nodes2.json"
    npath2.write_text(json.dumps(RAW2))
    assemble.main([str(kg), "--nodes", str(npath2), "--topic", "BRCA1",
                   "--breadth", "narrow", "--start-id", "2"])
    (kg / "_candidates.json").write_text(json.dumps({"articles": [
        {"pmid": "222", "metadata": _meta_both(["222"])["222"]}]}))
    summary2 = finalize.finalize_kg(str(kg), mode="update", version=2)

    # (a) Manifest contains both node_001 and node_002
    manifest = json.loads((kg / "manifest.json").read_text())
    ids = [n["id"] for n in manifest["nodes"]]
    assert "node_001" in ids, f"node_001 missing from manifest: {ids}"
    assert "node_002" in ids, f"node_002 missing from manifest: {ids}"

    # (b) node_001 was NOT quarantined by the update run
    node_001_entry = next(n for n in manifest["nodes"] if n["id"] == "node_001")
    node_001_file = os.path.join(str(kg), node_001_entry["file"])
    fm, _ = parse_node(node_001_file)
    assert fm.get("status", "active") != "quarantined", \
        f"node_001 was quarantined by update run: status={fm.get('status')}"

    # (c) statistics reflect both nodes (total_nodes >= 2)
    assert manifest["statistics"].get("total_nodes", 0) >= 2, \
        f"statistics.total_nodes should be >=2: {manifest['statistics']}"

    # (d) v2 run-record's nodes_created contains only node_002
    runs_dir = kg / "runs"
    run_files = sorted(runs_dir.iterdir())
    # find v2 run-record (the one with -v2 in the run_id)
    v2_runs = [f for f in run_files if "-v2" in f.name]
    assert v2_runs, f"No v2 run-record found in {list(runs_dir.iterdir())}"
    v2_record = json.loads(v2_runs[-1].read_text())
    assert v2_record["nodes_created"] == ["node_002"], \
        f"v2 run-record nodes_created should be ['node_002'], got {v2_record['nodes_created']}"
