import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def make_kg(tmp_path, schedule=None):
    kg = tmp_path / "KG_Test"
    (kg / "nodes").mkdir(parents=True)
    manifest = {
        "kg_name": "KG_Test", "topic": "test topic",
        "created": "2026-01-01", "updated": "2026-01-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }
    if schedule is not None:
        manifest["schedule"] = schedule
    (kg / "manifest.json").write_text(json.dumps(manifest))
    return kg


def run_stats(kg, *extra):
    return subprocess.run(
        [sys.executable, "-m", "nono_librarian.cli.update_manifest_stats", str(kg), *extra],
        cwd=PROJECT_ROOT, capture_output=True, text=True)


def test_stamps_last_run_when_schedule_exists(tmp_path):
    kg = make_kg(tmp_path, schedule={"cron": "0 8 * * 1", "last_run": None,
                                     "trigger_name": "kg-update-test"})
    result = run_stats(kg, "--stamp-last-run")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["schedule"]["last_run"] is not None
    assert manifest["schedule"]["last_run"].endswith("Z")


def test_no_op_without_schedule_block(tmp_path):
    kg = make_kg(tmp_path)
    result = run_stats(kg, "--stamp-last-run")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert "schedule" not in manifest


def test_default_run_does_not_stamp(tmp_path):
    kg = make_kg(tmp_path, schedule={"cron": "0 8 * * 1", "last_run": None,
                                     "trigger_name": "kg-update-test"})
    result = run_stats(kg)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((kg / "manifest.json").read_text())
    assert manifest["schedule"]["last_run"] is None
