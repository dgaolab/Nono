import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(PROJECT_ROOT, "tests", "fixtures", "mock_esearch.json")

SUB_QUERIES = [
    "melatonin circadian rhythm molecular mechanism",
    "melatonin sleep disorder treatment",
]


def make_kg(tmp_path, *, with_profile=True, known_pmids=(), schedule=None):
    kg = tmp_path / "KG_Test"
    kg.mkdir()
    manifest = {
        "kg_name": "KG_Test", "topic": "melatonin",
        "created": "2026-01-01", "updated": "2026-05-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }
    if with_profile:
        manifest["search_profile"] = {"breadth": "narrow", "sub_queries": SUB_QUERIES,
                                      "updated": "2026-05-01"}
    if schedule is not None:
        manifest["schedule"] = schedule
    (kg / "manifest.json").write_text(json.dumps(manifest))
    if known_pmids:
        ledger = {"kg_name": "KG_Test", "created": "2026-01-01", "updated": "2026-05-01",
                  "version": 1, "statistics": {},
                  "entries": {p: {"disposition": "used"} for p in known_pmids}}
        (kg / "_pmid_ledger.json").write_text(json.dumps(ledger))
    return kg


def run_preflight(kg, *extra):
    return subprocess.run(
        [sys.executable, "-m", "nono_librarian.cli.preflight", str(kg),
         "--esearch-fixture", FIXTURE, *extra],
        cwd=PROJECT_ROOT, capture_output=True, text=True)


def test_counts_novel_pmids_and_dedups_ledger(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"])
    result = run_preflight(kg)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    # union of fixture idlists = 5 unique; 2 known in ledger -> 3 novel
    assert report["novel_count"] == 3
    assert sorted(report["novel_pmids"]) == ["99000101", "99000102", "99000103"]
    assert report["proceed"] is True       # default threshold 3
    assert len(report["per_query"]) == 2


def test_threshold_blocks_proceed(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"])
    result = run_preflight(kg, "--threshold", "4")
    report = json.loads(result.stdout)
    assert report["proceed"] is False


def test_threshold_from_manifest_schedule(tmp_path):
    kg = make_kg(tmp_path, known_pmids=["99000001", "99000002"],
                 schedule={"cron": "0 8 * * 1", "trigger_name": "t",
                           "last_run": "2026-06-01T08:00:00Z", "threshold": 4})
    result = run_preflight(kg)
    report = json.loads(result.stdout)
    assert report["threshold"] == 4
    assert report["proceed"] is False
    assert report["since_date"] == "2026/06/01"  # from schedule.last_run


def test_since_falls_back_to_manifest_updated(tmp_path):
    kg = make_kg(tmp_path)
    report = json.loads(run_preflight(kg).stdout)
    assert report["since_date"] == "2026/05/01"


def test_missing_search_profile_exits_2(tmp_path):
    kg = make_kg(tmp_path, with_profile=False)
    result = run_preflight(kg)
    assert result.returncode == 2
    assert "search_profile" in result.stderr


def test_log_flag_appends_preflight_entry(tmp_path):
    kg = make_kg(tmp_path)
    result = run_preflight(kg, "--log")
    assert result.returncode == 0, result.stderr
    log = (kg / "_log.md").read_text()
    assert "preflight |" in log
