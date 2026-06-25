#!/usr/bin/env python3
"""Claude-free build reasoning — the model-driven steps of build-kg, made local.

Every function that needs the model takes an injected ``chat`` callable
(`scripts/lib/llm.py`); retrieval is done by the orchestrator via
`scripts/lib/pubmed.py`. The orchestrator owns IDs, dedup, PMID filtering, and
file writes — these functions only reason and return validated data.
"""

from lib import llm

TIERS = {
    "narrow": {"sub_queries": 3, "max_results": 10, "metadata": 15,
               "full_text": 5, "nodes_min": 8, "nodes_max": 15},
    "medium": {"sub_queries": 4, "max_results": 20, "metadata": 25,
               "full_text": 8, "nodes_min": 15, "nodes_max": 30},
    "broad":  {"sub_queries": 6, "max_results": 30, "metadata": 40,
               "full_text": 12, "nodes_min": 25, "nodes_max": 45},
}


class BuildError(RuntimeError):
    """Raised when a model reply for a build step cannot be used."""


def _ask_json(chat, messages, *, temperature=0.2):
    """Call the model and parse a JSON object from the reply, or raise BuildError."""
    reply = chat(messages, temperature=temperature)
    try:
        return llm.extract_json_object(reply)
    except ValueError as e:
        raise BuildError(f"model reply was not valid JSON: {e}") from e


_PLAN_SYS = (
    "You plan a PubMed literature search for a biomedical topic. Classify the "
    "topic breadth and propose focused sub-queries. Reply with ONE JSON object "
    "and nothing else: "
    '{"breadth": "narrow|medium|broad", "sub_queries": ["...", "..."]}. '
    "narrow = single mechanism/intervention (3 sub-queries), medium = a topic "
    "with several facets (4), broad = multi-disciplinary survey (6). Each "
    "sub-query is a specific PubMed search string."
)


def plan_search(topic, *, chat, breadth_override=None):
    """Classify breadth and generate sub-queries in one model call."""
    user = f"TOPIC:\n{topic}"
    if breadth_override:
        n = TIERS[breadth_override]["sub_queries"]
        user += f"\n\nUse breadth='{breadth_override}' and produce exactly {n} sub-queries."
    obj = _ask_json(chat, [{"role": "system", "content": _PLAN_SYS},
                           {"role": "user", "content": user}])
    breadth = breadth_override or str(obj.get("breadth", "")).strip().lower()
    if breadth not in TIERS:
        breadth = "medium"
    subs = [str(s).strip() for s in (obj.get("sub_queries") or []) if str(s).strip()]
    if not subs:
        raise BuildError("no sub-queries produced")
    return {"breadth": breadth, "sub_queries": subs}
