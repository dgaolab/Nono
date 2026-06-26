import os
import sys

from nono_librarian.lib.frontmatter import parse
from nono_librarian.paths import data_file

TEMPLATE = str(data_file("templates", "node_template.md"))


def test_template_parses_and_documents_quotes():
    fm, body = parse(TEMPLATE)
    # Template still parses as valid frontmatter with a pubmed_ids list.
    assert isinstance(fm.get("pubmed_ids"), list)
    # The quotes shape is documented somewhere in the template text.
    raw = open(TEMPLATE, encoding="utf-8").read()
    assert "quotes:" in raw
    assert "source:" in raw and "abstract" in raw
