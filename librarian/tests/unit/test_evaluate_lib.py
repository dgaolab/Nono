from nono_librarian.lib import evaluate

import pytest


# --------------------------------------------------------------------------
# parse_judgment — validate an agent-supplied verdict dict
# --------------------------------------------------------------------------

def test_parse_judgment_accepts_agent_dict():
    j = evaluate.parse_judgment(
        {"verdict": "supported", "reasoning": "ok",
         "quotes": [{"text": "BRCA1 loss impairs repair", "source": "abstract"}]})
    assert j["verdict"] == "supported"
    assert j["quotes"][0]["source"] == "abstract"


def test_parse_judgment_rejects_unknown_verdict():
    with pytest.raises(evaluate.EvaluationError):
        evaluate.parse_judgment({"verdict": "definitely", "quotes": []})


def test_judge_pmid_forces_not_supported_without_verbatim_quote():
    src = "Cells with BRCA1 loss show impaired homologous recombination."
    out = evaluate.judge_pmid(
        {"verdict": "supported", "reasoning": "claimed",
         "quotes": [{"text": "totally fabricated sentence", "source": "abstract"}]},
        source_text=src)
    assert out["verdict"] == "not_supported"
    assert out["quotes"] == []
    assert out["guardrail_triggered"] is True


def test_judge_pmid_keeps_verbatim_quote():
    src = "Cells with BRCA1 loss show impaired homologous recombination."
    out = evaluate.judge_pmid(
        {"verdict": "supported", "reasoning": "ok",
         "quotes": [{"text": "impaired homologous recombination", "source": "abstract"}]},
        source_text=src)
    assert out["verdict"] == "supported"
    assert out["quotes"] and out["guardrail_triggered"] is False


# --------------------------------------------------------------------------
# verbatim-quote guardrail
# --------------------------------------------------------------------------

_SOURCE = (
    "At 12 weeks, 40.2% of patients achieved a response versus 11.1% with "
    "placebo. The drug was generally well tolerated."
)


def test_quote_present_matches_despite_whitespace_and_case():
    assert evaluate.quote_present(
        "40.2%   of   patients\nachieved a RESPONSE", _SOURCE)


def test_quote_present_rejects_fabrication():
    assert not evaluate.quote_present("cured 100% of patients", _SOURCE)


def test_guardrail_keeps_only_verbatim_quotes():
    result = {
        "verdict": "supported",
        "reasoning": "r",
        "quotes": [
            {"text": "40.2% of patients achieved a response", "source": "abstract"},
            {"text": "this sentence is invented", "source": "abstract"},
        ],
    }
    out = evaluate.apply_guardrail(result, _SOURCE)
    assert out["verdict"] == "supported"
    assert len(out["quotes"]) == 1
    assert "40.2%" in out["quotes"][0]["text"]


def test_guardrail_downgrades_when_no_quote_survives():
    result = {
        "verdict": "supported",
        "reasoning": "r",
        "quotes": [{"text": "totally made up", "source": "abstract"}],
    }
    out = evaluate.apply_guardrail(result, _SOURCE)
    assert out["verdict"] == "not_supported"
    assert out["quotes"] == []
    assert out["guardrail_triggered"] is True


def test_guardrail_downgrades_supporting_verdict_with_zero_quotes():
    result = {"verdict": "partially_supported", "reasoning": "r", "quotes": []}
    out = evaluate.apply_guardrail(result, _SOURCE)
    assert out["verdict"] == "not_supported"
    assert out["guardrail_triggered"] is True


def test_guardrail_drops_quotes_on_nonsupporting_verdict():
    result = {
        "verdict": "unrelated",
        "reasoning": "r",
        "quotes": [{"text": "40.2% of patients achieved a response", "source": "abstract"}],
    }
    out = evaluate.apply_guardrail(result, _SOURCE)
    assert out["verdict"] == "unrelated"
    assert out["quotes"] == []


# --------------------------------------------------------------------------
# node_verdict — deterministic pass/fail rule (Step E3)
# --------------------------------------------------------------------------

def test_node_passes_when_any_reference_supports():
    status, _ = evaluate.node_verdict(
        [{"verdict": "unrelated"}, {"verdict": "supported"}])
    assert status == "passed"


def test_node_fails_when_no_reference_supports():
    status, _ = evaluate.node_verdict(
        [{"verdict": "unrelated"}, {"verdict": "not_supported"}])
    assert status == "failed"


def test_node_passes_with_partial_note():
    status, note = evaluate.node_verdict([{"verdict": "partially_supported"}])
    assert status == "passed"
    assert note  # non-empty narrowing suggestion


def test_node_fails_on_empty():
    status, _ = evaluate.node_verdict([])
    assert status == "failed"
