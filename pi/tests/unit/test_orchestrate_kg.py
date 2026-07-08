import json

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.orchestrate_kg import plan_kgs, _slugify


def test_slugify():
    assert _slugify("Tumor Micro-environment & T cells") == "tumor-micro-environment-t-cells"
    assert _slugify("!!!") == "topic"


def test_plan_kgs_adds_overall_and_records_ledger(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    doc = {"topic": "immunotherapy resistance",
           "subtopics": [{"title": "T cell exhaustion"},
                         {"title": "Tumor antigens", "slug": "antigens"}]}
    kgs = plan_kgs(str(out), doc)

    slugs = [k["slug"] for k in kgs]
    assert slugs == ["t-cell-exhaustion", "antigens", "_overall"]
    assert kgs[-1]["kind"] == "overall"
    assert kgs[0]["kg_dir"] == "kgs/t-cell-exhaustion"

    led = json.loads((out / "pi_run.json").read_text())
    assert led["kg_status"] == {"t-cell-exhaustion": "pending",
                                "antigens": "pending", "_overall": "pending"}
    assert [s["slug"] for s in led["subtopics"]] == ["t-cell-exhaustion", "antigens"]


def test_plan_kgs_rejects_empty_subtopics(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(Exception):
        plan_kgs(str(out), {"topic": "x", "subtopics": []})
