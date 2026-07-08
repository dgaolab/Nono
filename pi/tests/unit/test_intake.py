import json
import os

import pytest

from nono_pi.cli.init import scaffold
from nono_pi.cli.intake import record_intake, main


def test_record_intake_create_copies_files_and_updates_ledger(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    prelim = tmp_path / "prelim.md"
    prelim.write_text("preliminary data")

    payload = record_intake(str(out), goal="study Y", doc_type="paper",
                            mode="create", files=[str(prelim)])

    assert payload["mode"] == "create"
    assert payload["draft_file"] is None
    assert (out / "input" / "prelim.md").read_text() == "preliminary data"
    assert (out / "intake.json").exists()
    led = json.loads((out / "pi_run.json").read_text())
    assert led["goal"] == "study Y" and led["doc_type"] == "paper" and led["mode"] == "create"


def test_record_intake_revise_seeds_immutable_baseline(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    draft = tmp_path / "old_grant.docx"
    draft.write_text("existing draft")

    payload = record_intake(str(out), goal="renew grant", doc_type="grant",
                            mode="revise", draft=str(draft))

    assert payload["draft_file"] == os.path.join("draft", "v000.docx")
    assert (out / "draft" / "v000.docx").read_text() == "existing draft"


def test_record_intake_revise_without_draft_raises(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    with pytest.raises(ValueError):
        record_intake(str(out), goal="g", doc_type="grant", mode="revise")


def test_main_rejects_missing_output_folder(tmp_path):
    assert main([str(tmp_path / "nope"), "--goal", "g", "--doc-type", "grant",
                 "--mode", "create"]) == 2
