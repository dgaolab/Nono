import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib import evaluate


# --------------------------------------------------------------------------
# parse_response — robust JSON extraction from a small model's drift
# --------------------------------------------------------------------------

def test_parse_response_plain_json():
    out = evaluate.parse_response(
        '{"verdict": "supported", "reasoning": "ok", '
        '"quotes": [{"text": "A says B.", "source": "abstract"}]}')
    assert out["verdict"] == "supported"
    assert out["reasoning"] == "ok"
    assert out["quotes"] == [{"text": "A says B.", "source": "abstract"}]


def test_parse_response_strips_code_fence_and_prose():
    text = (
        "Sure, here is my assessment:\n"
        "```json\n"
        '{"verdict": "not_supported", "reasoning": "no match"}\n'
        "```\n"
        "Hope that helps!"
    )
    out = evaluate.parse_response(text)
    assert out["verdict"] == "not_supported"
    assert out["quotes"] == []  # defaulted when absent


def test_parse_response_normalizes_verdict_case_and_space():
    out = evaluate.parse_response('{"verdict": "  Partially_Supported "}')
    assert out["verdict"] == "partially_supported"


def test_parse_response_raises_on_no_json():
    with pytest.raises(evaluate.EvaluationError):
        evaluate.parse_response("I cannot help with that.")


def test_parse_response_raises_on_unknown_verdict():
    with pytest.raises(evaluate.EvaluationError):
        evaluate.parse_response('{"verdict": "maybe"}')


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
# build_prompt
# --------------------------------------------------------------------------

def test_build_prompt_includes_claim_title_and_text_and_asks_for_json():
    msgs = evaluate.build_prompt("SCN1A causes Dravet", "Some Title", "body text here")
    assert isinstance(msgs, list) and all("role" in m and "content" in m for m in msgs)
    blob = " ".join(m["content"] for m in msgs)
    assert "SCN1A causes Dravet" in blob
    assert "Some Title" in blob
    assert "body text here" in blob
    assert "json" in blob.lower()


# --------------------------------------------------------------------------
# verify_pmid — model call + retry + guardrail, with an injected chat
# --------------------------------------------------------------------------

def _chat_returning(*replies):
    seq = list(replies)
    calls = {"n": 0}

    def chat(messages, **kw):
        calls["n"] += 1
        return seq.pop(0)

    chat.calls = calls
    return chat


def test_verify_pmid_supported_with_valid_quote():
    chat = _chat_returning(
        '{"verdict": "supported", "reasoning": "ok", '
        '"quotes": [{"text": "40.2% of patients achieved a response", "source": "abstract"}]}')
    out = evaluate.verify_pmid("claim", article_title="t", source_text=_SOURCE, chat=chat)
    assert out["verdict"] == "supported"
    assert len(out["quotes"]) == 1


def test_verify_pmid_retries_on_bad_json_then_succeeds():
    chat = _chat_returning(
        "I refuse to output JSON",
        '{"verdict": "unrelated", "reasoning": "different topic"}')
    out = evaluate.verify_pmid("claim", article_title="t", source_text=_SOURCE,
                               chat=chat, attempts=2)
    assert out["verdict"] == "unrelated"
    assert chat.calls["n"] == 2


def test_verify_pmid_raises_after_exhausting_attempts():
    chat = _chat_returning("nope", "still nope")
    with pytest.raises(evaluate.EvaluationError):
        evaluate.verify_pmid("claim", article_title="t", source_text=_SOURCE,
                             chat=chat, attempts=2)


def test_verify_pmid_propagates_model_unavailable():
    def chat(messages, **kw):
        raise RuntimeError("endpoint down")

    with pytest.raises(RuntimeError):
        evaluate.verify_pmid("claim", article_title="t", source_text=_SOURCE, chat=chat)


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
