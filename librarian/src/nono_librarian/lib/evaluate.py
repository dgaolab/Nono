#!/usr/bin/env python3
"""Deterministic evidence evaluation — the guardrail core for evaluate-kg.

This module provides the *judgment* layer of the evaluator: given an
agent-supplied verdict dict and the text of a cited article, enforce the
verbatim-quote guardrail and return a validated result.

There are no model calls here. The agent (Claude or a local open-weight model
orchestrated externally) produces the verdict dict; this module validates it
and applies the deterministic guardrail.

Two deterministic guards bracket every verdict:

  1. **Schema validation** (`parse_judgment`) — the dict must carry a known
     verdict; anything else raises `EvaluationError`.
  2. **Verbatim-quote guardrail** (`apply_guardrail`) — a `supported` /
     `partially_supported` verdict must carry at least one quote that appears
     verbatim in the article text. Quotes that don't are dropped; if none
     survive, the verdict is forced down to `not_supported`. An agent cannot
     claim support it can't quote.

PMID existence (old Step E1), node-file writes (E6), and the node-level
pass/fail rule (E3, `node_verdict`) are deterministic and live here or in
existing scripts.
"""

# Per-reference verdicts, mirroring evaluate-kg-worker Step E2.
VERDICTS = {"supported", "partially_supported", "not_supported", "unrelated"}
# Verdicts that count as support for a node and may carry quotes.
SUPPORTING = {"supported", "partially_supported"}
# Valid quote source tags (node frontmatter pubmed_ids[].quotes[].source).
SOURCES = {"abstract", "full_text"}


class EvaluationError(RuntimeError):
    """Raised when an agent verdict dict cannot be parsed into a valid verdict."""


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


def parse_judgment(obj):
    """Validate an agent-supplied verdict dict into {verdict, reasoning, quotes}."""
    if not isinstance(obj, dict):
        raise EvaluationError("judgment was not an object")
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise EvaluationError(f"unknown verdict: {obj.get('verdict')!r}")
    quotes = obj.get("quotes") or []
    if not isinstance(quotes, list):
        quotes = []
    return {"verdict": verdict,
            "reasoning": str(obj.get("reasoning", "")).strip(),
            "quotes": quotes}


def judge_pmid(judgment, *, source_text):
    """Guardrail an agent judgment against the article text. No model call."""
    return apply_guardrail(parse_judgment(judgment), source_text)


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
