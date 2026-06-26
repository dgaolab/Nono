from nono_librarian.cli import librarian_evaluate as le


def _fake_meta(pmids):
    return {p: {"title": f"Article {p}", "abstract":
                "BRCA1 loss impairs homologous recombination repair.", "pmcid": None}
            for p in pmids}


def _fake_full_text(pmcid):
    return ""


def test_judge_node_passes_with_verbatim_quote():
    fm = {"title": "BRCA1 and HR",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR"}]}
    judg = {"111": {"verdict": "supported", "reasoning": "ok",
                    "quotes": [{"text": "impairs homologous recombination", "source": "abstract"}]}}
    entry = le.judge_node("node_001", fm, judg,
                          fetch_metadata=_fake_meta, fetch_full_text=_fake_full_text)
    assert entry["overall_status"] == "passed"
    assert entry["pmid_checks"][0]["verdict"] == "supported"


def test_judge_node_fails_when_quote_absent():
    fm = {"title": "BRCA1 and HR",
          "pubmed_ids": [{"pmid": "111", "supports": "BRCA1 loss impairs HR"}]}
    judg = {"111": {"verdict": "supported", "reasoning": "claimed",
                    "quotes": [{"text": "fabricated", "source": "abstract"}]}}
    entry = le.judge_node("node_001", fm, judg,
                          fetch_metadata=_fake_meta, fetch_full_text=_fake_full_text)
    assert entry["overall_status"] == "failed"


def test_judge_node_marks_missing_pmid_unrelated():
    fm = {"title": "x", "pubmed_ids": [{"pmid": "999", "supports": "y"}]}
    entry = le.judge_node("node_001", fm, {"999": {"verdict": "supported", "quotes": []}},
                          fetch_metadata=lambda p: {}, fetch_full_text=_fake_full_text)
    assert entry["pmid_checks"][0]["verdict"] == "unrelated"
    assert entry["overall_status"] == "failed"


# --------------------------------------------------------------------------
# build_source_text — kept from original
# --------------------------------------------------------------------------

_ABSTRACT = "Patients on drug X showed a 40.2% response rate at 12 weeks."
_FULLTEXT = "In the full cohort, the hazard ratio was 0.61 (95% CI 0.44-0.85)."


def test_build_source_text_joins_abstract_and_full_text():
    meta = {"abstract": _ABSTRACT}
    out = le.build_source_text(meta, _FULLTEXT)
    assert _ABSTRACT in out and _FULLTEXT in out


def test_build_source_text_abstract_only_when_no_full_text():
    assert le.build_source_text({"abstract": _ABSTRACT}, "") == _ABSTRACT


# --------------------------------------------------------------------------
# frontmatter_updates — kept from original
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
