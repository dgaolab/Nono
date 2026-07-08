import json

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.intake import record_intake
from nono_pi.lib import routing as R
from nono_pi.cli.route import resolve, main


def test_load_table_and_all_sections():
    grant = R.load_table("grant")
    assert R.all_sections(grant) == ["specific_aims", "significance", "innovation", "approach"]
    with pytest.raises(ValueError):
        R.load_table("thesis")


def test_select_respects_order_and_mode():
    paper = R.load_table("paper")
    plan = R.select(paper, ["discussion", "abstract"], "create")
    # returned in table order, not request order
    assert [p["section"] for p in plan] == ["abstract", "discussion"]
    assert plan[0]["skills"] == ["abstract-summarizer"]
    revise = R.select(paper, ["methods"], "revise")
    assert revise[0]["skills"] == ["sci-paper-reviewer", "methods-section-writer"]


def test_resolve_full_records_ledger(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="grant", mode="create")
    plan = resolve(str(out), full=True)
    assert [p["section"] for p in plan] == ["specific_aims", "significance", "innovation", "approach"]
    led = json.loads((out / "pi_run.json").read_text())
    assert led["depth"] == "full"
    assert led["sections"]["specific_aims"] == "requested"


def test_resolve_rejects_unknown_section(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    record_intake(str(out), goal="g", doc_type="paper", mode="create")
    with pytest.raises(ValueError):
        resolve(str(out), sections=["nonsense"])


def test_main_requires_intake_first(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    assert main([str(out), "--full"]) == 2
