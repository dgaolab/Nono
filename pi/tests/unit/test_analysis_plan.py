import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.analysis_plan import render_plan, write_analysis_plan


def _doc():
    return {"goal": "close mechanism gap",
            "analyses": [{"name": "scRNA reanalysis", "gap": "cell type unknown",
                          "rationale": "resolve heterogeneity", "method": "scanpy clustering",
                          "expected_output": "annotated clusters", "inputs": ["GSE12345"]}]}


def test_render_plan_numbers_analyses_and_lists_fields():
    md = render_plan(_doc())
    assert "**Goal:** close mechanism gap" in md
    assert "### Analysis 1: scRNA reanalysis" in md
    assert "**Method:** scanpy clustering" in md
    assert "**Inputs:** GSE12345" in md


def test_render_plan_inputs_default_na():
    d = _doc()
    del d["analyses"][0]["inputs"]
    assert "**Inputs:** n/a" in render_plan(d)


def test_write_analysis_plan_creates_file(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    path = write_analysis_plan(str(out), _doc())
    assert (out / "analysis_plan.md").exists()
    assert path.endswith("analysis_plan.md")


def test_write_rejects_missing_fields(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(Exception):
        write_analysis_plan(str(out), {"goal": "g", "analyses": [{"name": "x"}]})
