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
