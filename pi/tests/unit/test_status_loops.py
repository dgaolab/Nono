from nono_pi.cli.init import scaffold
from nono_pi.cli.intake import record_intake
from nono_pi.cli.eval import record_round, decide_round
from nono_pi.cli.status import status_report


def _round():
    return {"verdicts": {"soundness": {"verdict": "sound", "rationale": "ok", "citations": ["node_1"]}},
            "weaknesses": [], "proposed_revision": "none"}


def test_status_shows_loop_rounds(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="grant", mode="create")
    record_round(str(out), "aims", _round())
    decide_round(str(out), "aims", "accepted")
    report, led = status_report(str(out))
    assert "aims loop: accepted" in report
    assert "round 0" in report
    assert "soundness=sound" in report


def test_status_hides_untouched_loops_on_fresh_project(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="grant", mode="create")
    report, _ = status_report(str(out))
    # a fresh project has seeded-but-untouched loops → no loop lines
    assert "aims loop:" not in report
    assert "draft loop:" not in report
    # ...but once a round is recorded, the section appears
    record_round(str(out), "aims", _round())
    report2, _ = status_report(str(out))
    assert "aims loop: in_progress" in report2


def test_status_ok_when_loops_absent(tmp_path):
    # An old-style ledger without loop keys must not break status.
    out = tmp_path / "proj"
    scaffold(str(out))
    import json
    p = out / "pi_run.json"
    led = json.loads(p.read_text())
    led.pop("aims_loop"); led.pop("draft_loop")
    p.write_text(json.dumps(led))
    report, _ = status_report(str(out))
    assert "nono-pi status" in report  # no crash
