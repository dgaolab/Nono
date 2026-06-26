import json
from nono_librarian.cli import gather


def _esearch(q, **kw):
    return {"brca1 repair": ["111", "222"], "brca1 cancer": ["222", "333"]}[q]


def _fetch_metadata(pmids):
    return {p: {"title": f"T{p}", "abstract": f"abstract {p}", "pmcid": None}
            for p in pmids}


def _fetch_full_text(pmcid):
    return ""


def test_gather_articles_dedups_and_caps():
    arts = gather.gather_articles(
        ["brca1 repair", "brca1 cancer"], esearch=_esearch,
        fetch_metadata=_fetch_metadata, fetch_full_text=_fetch_full_text,
        known_pmids=set(), tier={"max_results": 10, "metadata": 10, "full_text": 0})
    pmids = [a["pmid"] for a in arts]
    assert pmids == ["111", "222", "333"]


def test_gather_main_writes_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(gather.pubmed, "esearch", _esearch)
    monkeypatch.setattr(gather.pubmed, "fetch_metadata", _fetch_metadata)
    monkeypatch.setattr(gather.pubmed, "fetch_full_text", _fetch_full_text)
    out = tmp_path / "_candidates.json"
    rc = gather.main(["brca1", "--query", "brca1 repair", "--query", "brca1 cancer",
                      "--breadth", "narrow", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert {a["pmid"] for a in data["articles"]} == {"111", "222", "333"}
    assert data["breadth"] == "narrow"
    assert data["sub_queries"] == ["brca1 repair", "brca1 cancer"]
