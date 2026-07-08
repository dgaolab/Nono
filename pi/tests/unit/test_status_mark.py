import json

from nono_pi.cli.init import scaffold
from nono_pi.cli.intake import record_intake
from nono_pi.cli.orchestrate_kg import plan_kgs
from nono_pi.cli.status import status_report
from nono_pi.cli.mark import mark


def test_status_reconciles_disk(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="grant", mode="create")
    plan_kgs(str(out), {"topic": "t", "subtopics": [{"title": "A"}]})
    (out / "kgs" / "a" / ".keep").parent.mkdir(parents=True, exist_ok=True)
    (out / "kgs" / "a" / "manifest.json").write_text("{}")

    report, led = status_report(str(out))
    assert "nono-pi status" in report
    assert led["kg_status"]["a"] == "built"
    assert led["kg_status"]["_overall"] == "pending"
    # reconciled ledger is persisted
    assert json.loads((out / "pi_run.json").read_text())["kg_status"]["a"] == "built"


def test_mark_updates_gate_section_and_draft(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    mark(str(out), gate="confirmed")
    mark(str(out), section="specific_aims", section_status="written")
    mark(str(out), bump_draft=True)
    led = json.loads((out / "pi_run.json").read_text())
    assert led["gap_gate"] == {"status": "confirmed", "decision": "confirmed"}
    assert led["sections"]["specific_aims"] == "written"
    assert led["draft_version"] == 1


def test_mark_kg_status(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    mark(str(out), kg="sub-a", kg_status="failed")
    led = json.loads((out / "pi_run.json").read_text())
    assert led["kg_status"]["sub-a"] == "failed"
