import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(PROJECT_ROOT, "tests", "fixtures", "mock_transcript.jsonl")

# opus 4.8: (1000*5 + 200*25 + 2000*0.5 + 500*6.25) / 1e6 = 0.014125
# haiku 4.5 (dated id, prefix-matched): (500*1 + 100*5) / 1e6 = 0.001
EXPECTED_COST = 0.014125 + 0.001


def test_direct_mode_sums_dedups_and_prices():
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", FIXTURE, "--session-id", "sess-1"],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)
    opus = entry["models"]["claude-opus-4-8"]
    assert opus["output"] == 200          # dedup by message id, last wins
    assert opus["input"] == 1000
    assert opus["cache_read"] == 2000 and opus["cache_write"] == 500
    haiku = entry["models"]["claude-haiku-4-5-20251001"]
    assert haiku["input"] == 500 and haiku["output"] == 100
    assert entry["est_cost_usd"] == pytest.approx(EXPECTED_COST, abs=1e-4)


def test_hook_mode_appends_to_log_and_never_fails(tmp_path):
    log_file = tmp_path / "_cost_log.jsonl"
    payload = json.dumps({"session_id": "sess-1", "transcript_path": FIXTURE,
                          "hook_event_name": "Stop"})
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--hook",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, input=payload, capture_output=True, text=True)
    assert result.returncode == 0
    entry = json.loads(log_file.read_text().strip())
    assert entry["session_id"] == "sess-1"
    assert entry["est_cost_usd"] == pytest.approx(EXPECTED_COST, abs=1e-4)

    # garbage stdin must not break the hook
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--hook",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, input="not json", capture_output=True, text=True)
    assert result.returncode == 0


def test_summary_mode_takes_last_entry_per_session(tmp_path):
    log_file = tmp_path / "_cost_log.jsonl"
    log_file.write_text(
        json.dumps({"session_id": "s1", "ts": "2026-06-09T10:00:00Z",
                    "models": {}, "est_cost_usd": 1.0}) + "\n" +
        json.dumps({"session_id": "s1", "ts": "2026-06-09T11:00:00Z",
                    "models": {}, "est_cost_usd": 2.5}) + "\n")
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", "--summary",
         "--log-file", str(log_file)],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "2.5" in result.stdout
    assert result.stdout.count("s1") == 1   # one row per session


def test_unknown_model_is_flagged_not_priced(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(
        {"type": "assistant", "sessionId": "x",
         "message": {"id": "m1", "model": "future-model-9",
                     "usage": {"input_tokens": 10, "output_tokens": 10}}}) + "\n")
    result = subprocess.run(
        [sys.executable, "scripts/cost_report.py", str(transcript)],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    entry = json.loads(result.stdout)
    assert entry["est_cost_usd"] == 0
    assert entry["unpriced_models"] == ["future-model-9"]
