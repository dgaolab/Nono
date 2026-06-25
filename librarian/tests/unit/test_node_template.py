import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib.frontmatter import parse

TEMPLATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                        "templates", "node_template.md"))


def test_template_parses_and_documents_quotes():
    fm, body = parse(TEMPLATE)
    # Template still parses as valid frontmatter with a pubmed_ids list.
    assert isinstance(fm.get("pubmed_ids"), list)
    # The quotes shape is documented somewhere in the template text.
    raw = open(TEMPLATE, encoding="utf-8").read()
    assert "quotes:" in raw
    assert "source:" in raw and "abstract" in raw
