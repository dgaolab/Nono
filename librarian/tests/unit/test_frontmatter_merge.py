import os
import sys

from nono_librarian.lib.frontmatter import deep_merge


def _base_node():
    return {
        "id": "node_001",
        "pubmed_ids": [
            {"pmid": "111", "verified": True,
             "quotes": [{"text": "old quote", "source": "abstract"}]},
            {"pmid": "222", "verified": True,
             "quotes": [{"text": "keep me", "source": "abstract"}]},
        ],
    }


def test_quotes_replaced_wholesale_on_matching_pmid():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "111", "verified": True,
         "quotes": [{"text": "new quote", "source": "full_text"}]},
    ]}
    merged = deep_merge(base, updates)
    pmid_111 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "111")
    assert pmid_111["quotes"] == [{"text": "new quote", "source": "full_text"}]


def test_other_pmid_quotes_untouched():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "111", "quotes": [{"text": "new quote", "source": "full_text"}]},
    ]}
    merged = deep_merge(base, updates)
    pmid_222 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "222")
    assert pmid_222["quotes"] == [{"text": "keep me", "source": "abstract"}]


def test_new_pmid_with_quotes_is_appended():
    base = _base_node()
    updates = {"pubmed_ids": [
        {"pmid": "333", "verified": True,
         "quotes": [{"text": "brand new", "source": "abstract"}]},
    ]}
    merged = deep_merge(base, updates)
    pmids = [p["pmid"] for p in merged["pubmed_ids"]]
    assert pmids == ["111", "222", "333"]
    pmid_333 = next(p for p in merged["pubmed_ids"] if p["pmid"] == "333")
    assert pmid_333["quotes"] == [{"text": "brand new", "source": "abstract"}]
