import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
import librarian_evaluate as le
from lib import pubmed


_ABSTRACT = "Patients on drug X showed a 40.2% response rate at 12 weeks."
_FULLTEXT = "In the full cohort, the hazard ratio was 0.61 (95% CI 0.44-0.85)."


def _meta(pmid="111", pmcid="PMC9", title="A trial of X"):
    return {pmid: {"title": title, "abstract": _ABSTRACT, "pmcid": pmcid,
                   "journal": "J", "year": "2021", "authors": [],
                   "publication_types": []}}


def _chat_supported(messages, **kw):
    return ('{"verdict": "supported", "reasoning": "matches", '
            '"quotes": [{"text": "40.2% response rate at 12 weeks", "source": "abstract"}]}')


# --------------------------------------------------------------------------
# build_source_text
# --------------------------------------------------------------------------

def test_build_source_text_joins_abstract_and_full_text():
    meta = {"abstract": _ABSTRACT}
    out = le.build_source_text(meta, _FULLTEXT)
    assert _ABSTRACT in out and _FULLTEXT in out


def test_build_source_text_abstract_only_when_no_full_text():
    assert le.build_source_text({"abstract": _ABSTRACT}, "") == _ABSTRACT


# --------------------------------------------------------------------------
# evaluate_node
# --------------------------------------------------------------------------

def test_evaluate_node_supported_passes():
    fm = {"title": "X works", "pubmed_ids": [{"pmid": "111", "supports": "X works at 12 weeks"}]}
    entry = le.evaluate_node(
        "node_001", fm,
        fetch_metadata=lambda pmids: _meta(),
        fetch_full_text=lambda pmcid: _FULLTEXT,
        chat=_chat_supported)
    assert entry["node_id"] == "node_001"
    assert entry["overall_status"] == "passed"
    check = entry["pmid_checks"][0]
    assert check["exists"] is True
    assert check["verdict"] == "supported"
    assert check["quotes"]


def test_evaluate_node_uses_per_pmid_supports_claim_as_query():
    seen = {}

    def chat(messages, **kw):
        seen["blob"] = " ".join(m["content"] for m in messages)
        return _chat_supported(messages)

    fm = {"title": "node title", "pubmed_ids": [{"pmid": "111", "supports": "SPECIFIC per-pmid claim"}]}
    le.evaluate_node("n", fm, fetch_metadata=lambda p: _meta(),
                     fetch_full_text=lambda p: "", chat=chat)
    assert "SPECIFIC per-pmid claim" in seen["blob"]


def test_evaluate_node_flags_invalid_pmid_without_model_call():
    called = {"n": 0}

    def chat(messages, **kw):
        called["n"] += 1
        return _chat_supported(messages)

    fm = {"title": "t", "pubmed_ids": [{"pmid": "999", "supports": "c"}]}
    entry = le.evaluate_node("n", fm, fetch_metadata=lambda p: {},  # 999 not found
                             fetch_full_text=lambda p: "", chat=chat)
    assert entry["pmid_checks"][0]["exists"] is False
    assert entry["overall_status"] == "failed"
    assert called["n"] == 0  # no model call for a non-existent PMID


def test_evaluate_node_degrades_to_abstract_when_full_text_unavailable():
    def boom(pmcid):
        raise pubmed.PubMedUnavailable("PMC down")

    fm = {"title": "t", "pubmed_ids": [{"pmid": "111", "supports": "X works at 12 weeks"}]}
    entry = le.evaluate_node("n", fm, fetch_metadata=lambda p: _meta(),
                             fetch_full_text=boom, chat=_chat_supported)
    # abstract alone still supports → still passes, no crash
    assert entry["overall_status"] == "passed"


def test_evaluate_node_propagates_model_unavailable():
    def chat(messages, **kw):
        raise RuntimeError("model down")

    fm = {"title": "t", "pubmed_ids": [{"pmid": "111", "supports": "c"}]}
    with pytest.raises(RuntimeError):
        le.evaluate_node("n", fm, fetch_metadata=lambda p: _meta(),
                         fetch_full_text=lambda p: "", chat=chat)


# --------------------------------------------------------------------------
# frontmatter_updates — what gets written back to the node
# --------------------------------------------------------------------------

def test_frontmatter_updates_passed_node_sets_verified_and_quotes():
    entry = {
        "overall_status": "passed",
        "pmid_checks": [
            {"pmid": "111", "verdict": "supported",
             "quotes": [{"text": "q", "source": "abstract"}]},
            {"pmid": "222", "verdict": "unrelated", "quotes": []},
        ],
    }
    upd = le.frontmatter_updates(entry)
    assert upd["evaluation_status"] == "passed"
    assert upd["quarantined"] is False
    by_pmid = {p["pmid"]: p for p in upd["pubmed_ids"]}
    assert by_pmid["111"]["verified"] is True
    assert by_pmid["111"]["quotes"] == [{"text": "q", "source": "abstract"}]
    assert by_pmid["222"]["verified"] is False


def test_frontmatter_updates_failed_node_quarantines():
    entry = {"overall_status": "failed",
             "pmid_checks": [{"pmid": "111", "verdict": "not_supported", "quotes": []}]}
    upd = le.frontmatter_updates(entry)
    assert upd["evaluation_status"] == "failed"
    assert upd["quarantined"] is True
