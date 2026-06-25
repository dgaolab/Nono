import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib import build


def _chat_returning(reply):
    def chat(messages, **kw):
        return reply
    return chat


def test_tiers_have_expected_keys():
    for tier in ("narrow", "medium", "broad"):
        t = build.TIERS[tier]
        assert {"sub_queries", "max_results", "metadata", "full_text",
                "nodes_min", "nodes_max"} <= set(t)


def test_plan_search_returns_breadth_and_subqueries():
    chat = _chat_returning(
        '{"breadth": "medium", "sub_queries": ["a immune response", "b delivery"]}')
    out = build.plan_search("mRNA vaccines", chat=chat)
    assert out["breadth"] == "medium"
    assert out["sub_queries"] == ["a immune response", "b delivery"]


def test_plan_search_honors_breadth_override():
    chat = _chat_returning('{"breadth": "broad", "sub_queries": ["x", "y", "z"]}')
    out = build.plan_search("t", chat=chat, breadth_override="narrow")
    assert out["breadth"] == "narrow"


def test_plan_search_raises_on_unparseable():
    chat = _chat_returning("I cannot help")
    with pytest.raises(build.BuildError):
        build.plan_search("t", chat=chat)


def test_select_candidates_dedups_excludes_and_caps():
    out = build.select_candidates(
        [["1", "2", "3"], ["2", "4", "5"]], known_pmids={"3"}, cap=3)
    assert out == ["1", "2", "4"]
