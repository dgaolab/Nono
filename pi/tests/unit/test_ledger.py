import json
import os

import pytest

from nono_pi.lib import ledger as L


def test_new_ledger_validates():
    led = L.new_ledger("/some/out")
    L.validate_ledger(led)
    assert led["schema_version"] == 1
    assert led["gap_gate"] == {"status": "pending", "decision": None}


def test_read_missing_returns_new(tmp_path):
    led = L.read_ledger(str(tmp_path))
    assert led["si_status"] == "pending"


def test_write_then_read_roundtrip(tmp_path):
    led = L.new_ledger(str(tmp_path))
    led["goal"] = "cure X"
    L.write_ledger(str(tmp_path), led)
    assert os.path.exists(tmp_path / "pi_run.json")
    assert L.read_ledger(str(tmp_path))["goal"] == "cure X"


def test_reconcile_marks_built_kg_and_si(tmp_path):
    led = L.new_ledger(str(tmp_path))
    led["kg_status"] = {"sub-a": "pending", "_overall": "pending"}
    (tmp_path / "kgs" / "sub-a").mkdir(parents=True)
    (tmp_path / "kgs" / "sub-a" / "manifest.json").write_text("{}")
    (tmp_path / "Significance_and_Innovation.md").write_text("# x")
    L.reconcile(str(tmp_path), led)
    assert led["kg_status"]["sub-a"] == "built"
    assert led["kg_status"]["_overall"] == "pending"
    assert led["si_status"] == "done"


def test_reconcile_create_sections_and_revise_version(tmp_path):
    (tmp_path / "draft").mkdir()
    led = L.new_ledger(str(tmp_path))
    led["mode"] = "create"
    led["requested_sections"] = ["specific_aims", "approach"]
    (tmp_path / "draft" / "specific_aims.md").write_text("aims")
    L.reconcile(str(tmp_path), led)
    assert led["sections"]["specific_aims"] == "written"
    assert led["sections"]["approach"] == "requested"

    led2 = L.new_ledger(str(tmp_path))
    led2["mode"] = "revise"
    (tmp_path / "draft" / "v000.docx").write_text("orig")
    (tmp_path / "draft" / "v002.md").write_text("rev2")
    L.reconcile(str(tmp_path), led2)
    assert led2["draft_version"] == 2


def test_validate_ledger_rejects_bad_enum():
    led = L.new_ledger("/out")
    led["doc_type"] = "thesis"  # not in enum grant|paper|null
    with pytest.raises(Exception):
        L.validate_ledger(led)


def test_validate_ledger_rejects_missing_required():
    led = L.new_ledger("/out")
    del led["gap_gate"]  # a top-level required key
    with pytest.raises(Exception):
        L.validate_ledger(led)
