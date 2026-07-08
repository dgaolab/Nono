import json

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.assemble_si import render_si, assemble_si


def test_render_si_includes_sections_and_pmids():
    doc = {"title": "T",
           "significance": ["matters a lot"],
           "innovation": ["novel method"],
           "evidence": [{"claim": "X drives Y", "pmids": ["123", "456"]}],
           "caveats": ["small n"]}
    md = render_si(doc)
    assert "matters a lot" in md
    assert "novel method" in md
    assert "X drives Y" in md
    assert "PMID:123, PMID:456" in md
    assert "small n" in md


def test_render_si_handles_empty_lists():
    md = render_si({"title": "T"})
    assert "_None provided._" in md


def test_assemble_si_writes_file_and_sets_ledger(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    path = assemble_si(str(out), {"title": "T", "significance": ["s"]})
    assert (out / "Significance_and_Innovation.md").exists()
    led = json.loads((out / "pi_run.json").read_text())
    assert led["si_status"] == "done"


def test_assemble_si_rejects_missing_title(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(Exception):
        assemble_si(str(out), {"significance": ["s"]})
