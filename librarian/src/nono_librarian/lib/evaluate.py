#!/usr/bin/env python3
"""Claude-free evidence evaluation — the reasoning behind evaluate-kg, made local.

This module re-expresses the *judgment* part of the evaluator (Step E2 of
`evaluate-kg-worker`): given a node's claim and the text of a cited article,
decide whether the article supports the claim. It drives the local model via an
injected ``chat`` callable (`scripts/lib/llm.py`) and retrieves nothing itself —
article text comes from `scripts/lib/pubmed.py`.

Small open-weight models drift and confabulate, so the model's word is never
taken on faith. Two deterministic guards bracket every verdict:

  1. **Schema validation** (`parse_response`) — the reply must be a JSON object
     with a known verdict; anything else is an `EvaluationError` (→ retry).
  2. **Verbatim-quote guardrail** (`apply_guardrail`) — a `supported` /
     `partially_supported` verdict must carry at least one quote that appears
     verbatim in the article text. Quotes that don't are dropped; if none
     survive, the verdict is forced down to `not_supported`. A model cannot
     claim support it can't quote.

PMID existence (old Step E1), node-file writes (E6), and the node-level pass/fail
rule (E3, `node_verdict`) are deterministic and live here or in existing scripts;
only the claim↔evidence call needs the model.
"""

import json

# Per-reference verdicts, mirroring evaluate-kg-worker Step E2.
VERDICTS = {"supported", "partially_supported", "not_supported", "unrelated"}
# Verdicts that count as support for a node and may carry quotes.
SUPPORTING = {"supported", "partially_supported"}
# Valid quote source tags (node frontmatter pubmed_ids[].quotes[].source).
SOURCES = {"abstract", "full_text"}


class EvaluationError(RuntimeError):
    """Raised when a model reply cannot be parsed into a valid verdict."""


def parse_response(text):
    """Parse a model reply into ``{verdict, reasoning, quotes}`` or raise."""
    from nono_librarian.lib import llm
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


def _normalize(s):
    """Collapse whitespace and lowercase for forgiving verbatim matching."""
    return " ".join(str(s).split()).lower()


def quote_present(quote_text, source_text):
    """True if ``quote_text`` appears in ``source_text`` (whitespace/case-insensitive).

    Tolerates reflowed whitespace and capitalization the model may introduce,
    but a genuinely fabricated sentence will not be found.
    """
    q = _normalize(quote_text)
    return bool(q) and q in _normalize(source_text)


def apply_guardrail(result, source_text):
    """Enforce the verbatim-quote rule on a parsed verdict; return a new dict.

    For a supporting verdict, only quotes actually present in ``source_text``
    are kept; if none survive (or none were given), the verdict is forced to
    ``not_supported`` and ``guardrail_triggered`` is set. Non-supporting
    verdicts never carry quotes (worker exclusion rule).
    """
    out = dict(result)
    if out["verdict"] in SUPPORTING:
        kept = [q for q in out.get("quotes", [])
                if quote_present(q.get("text", ""), source_text)]
        if kept:
            out["quotes"] = kept
            out["guardrail_triggered"] = False
        else:
            out["verdict"] = "not_supported"
            out["quotes"] = []
            out["guardrail_triggered"] = True
    else:
        out["quotes"] = []
        out["guardrail_triggered"] = False
    return out


_SYSTEM = (
    "You are an independent biomedical fact-checker. You have NO prior knowledge "
    "of how a knowledge graph was built; you skeptically verify one claim against "
    "one article's text. You are verifying, not defending. An article that "
    "discusses the same topic but a different mechanism is 'unrelated', not "
    "'supported'.\n\n"
    "Reply with a SINGLE JSON object and nothing else:\n"
    '{"verdict": "<supported|partially_supported|not_supported|unrelated>", '
    '"reasoning": "<one or two sentences>", '
    '"quotes": [{"text": "<verbatim excerpt, copied exactly>", "source": "abstract"}]}\n'
    "Rules: capture 1-3 quotes ONLY for supported/partially_supported, each a "
    "verbatim excerpt (no paraphrase) of <=2 sentences copied exactly from the "
    "article text below. A claim you cannot quote is not supported."
)


def build_prompt(claim, article_title, article_text):
    """Build the chat messages for verifying one claim against one article."""
    user = (
        f"CLAIM TO VERIFY:\n{claim}\n\n"
        f"ARTICLE TITLE:\n{article_title}\n\n"
        f"ARTICLE TEXT:\n{article_text}"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def verify_pmid(claim, *, article_title, source_text, chat, attempts=2,
                temperature=0.0):
    """Verify ``claim`` against one article via the local model, guardrailed.

    Calls ``chat(messages, ...)`` (the llm.py seam), parsing+validating its
    reply and retrying up to ``attempts`` times on malformed output. The
    surviving verdict passes through `apply_guardrail`, so a supported verdict
    always rests on a verbatim quote. Errors from ``chat`` (e.g. the model being
    unavailable) propagate unchanged — the caller decides whether to abort
    without mutating anything. Raises `EvaluationError` if no attempt parses.
    """
    messages = build_prompt(claim, article_title, source_text)
    last = None
    for _ in range(max(1, attempts)):
        reply = chat(messages, temperature=temperature)
        try:
            parsed = parse_response(reply)
        except EvaluationError as e:
            last = e
            continue
        return apply_guardrail(parsed, source_text)
    raise EvaluationError(f"model never returned a valid verdict: {last}")


def node_verdict(pmid_results):
    """Node-level pass/fail from per-PMID verdicts (Step E3) → (status, note).

    Passes if any reference is supported/partially_supported; fails otherwise.
    A pass resting only on partial support carries a narrowing suggestion.
    """
    verdicts = [r.get("verdict") for r in pmid_results]
    if any(v == "supported" for v in verdicts):
        return "passed", ""
    if any(v == "partially_supported" for v in verdicts):
        return "passed", "Only partial support found — consider narrowing the claim."
    return "failed", ""
