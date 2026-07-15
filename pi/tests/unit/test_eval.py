import json

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.eval import record_round, decide_round


def _round():
    return {
        "verdicts": {"soundness": {"verdict": "weak", "rationale": "thin support",
                                   "citations": ["node_1", "12345"]}},
        "weaknesses": [{"issue": "aim 2 unsupported", "fix": "add mechanism",
                        "closable_by_analysis": True}],
        "proposed_revision": "Tighten aim 2 around the mechanism.",
    }


def test_record_round_appends_numbers_and_renders(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    r0 = record_round(str(out), "aims", _round())
    r1 = record_round(str(out), "aims", _round())
    assert r0["round"] == 0 and r1["round"] == 1
    assert r0["decision"] is None
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["status"] == "in_progress"
    assert len(led["aims_loop"]["rounds"]) == 2
    report = (out / "aims_evaluation.md").read_text()
    assert "# aims evaluation" in report
    assert "Round 0" in report and "Tighten aim 2" in report


def test_decide_transitions_status(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "approved")
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["rounds"][-1]["decision"] == "approved"
    assert led["aims_loop"]["status"] == "in_progress"
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "accepted", note="premise sound")
    led = json.loads((out / "pi_run.json").read_text())
    assert led["aims_loop"]["status"] == "accepted"
    assert led["aims_loop"]["rounds"][-1]["note"] == "premise sound"


def test_decide_without_rounds_raises(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(ValueError):
        decide_round(str(out), "draft", "accepted")


def test_record_rejects_bad_round(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(Exception):
        record_round(str(out), "aims", {"weaknesses": [], "proposed_revision": "x"})  # no verdicts
