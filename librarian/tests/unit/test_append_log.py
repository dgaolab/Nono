import os
import sys

import pytest

from nono_librarian.cli.append_log import VALID_OPS, append_entry


def test_append_entry_creates_log_and_prepends(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()

    ts1 = append_entry(str(kg), "build", "first entry")
    ts2 = append_entry(str(kg), "preflight", "second entry", details="2 novel PMIDs")

    content = (kg / "_log.md").read_text()
    assert content.index("second entry") < content.index("first entry")  # newest on top
    assert f"## [{ts1}] build | first entry" in content
    assert f"## [{ts2}] preflight | second entry" in content
    assert "2 novel PMIDs" in content


def test_preflight_is_a_valid_op():
    assert "preflight" in VALID_OPS


def test_append_entry_rejects_bad_op_and_missing_dir(tmp_path):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    with pytest.raises(ValueError):
        append_entry(str(kg), "bogus", "x")
    with pytest.raises(FileNotFoundError):
        append_entry(str(tmp_path / "nope"), "build", "x")
