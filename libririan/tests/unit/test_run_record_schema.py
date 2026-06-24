import json
import os

import jsonschema

SCHEMA_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "schemas", "run_record_schema.json"))


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def valid_update_record():
    return {
        "run_id": "2026-06-24T080012Z-v7",
        "kg_name": "KG_Topic",
        "mode": "update",
        "timestamp": "2026-06-24T08:00:12Z",
        "version": 7,
        "since_date": "2026-06-17",
        "preflight": {"novel_count": 9, "threshold": 3},
        "nodes_created": ["node_016"],
        "nodes_revised": ["node_003"],
        "refs_added": [{"pmid": "39876543", "nodes": ["node_003", "node_016"]}],
        "refs_failed": [{"pmid": "00000001", "node": "node_005", "reason": "verification failed"}],
        "eval_summary": {"evaluated": 3, "passed": 2, "failed": 1},
        "cost_session_id": "abc-123",
    }


def test_valid_update_record_passes():
    jsonschema.validate(valid_update_record(), load_schema())


def test_skip_record_passes():
    rec = valid_update_record()
    rec["mode"] = "skip"
    rec["since_date"] = "2026-06-17"
    rec["nodes_created"] = []
    rec["nodes_revised"] = []
    rec["refs_added"] = []
    rec["refs_failed"] = []
    rec["eval_summary"] = {"evaluated": 0, "passed": 0, "failed": 0}
    jsonschema.validate(rec, load_schema())


def test_build_record_allows_null_since_and_cost():
    rec = valid_update_record()
    rec["mode"] = "build"
    rec["since_date"] = None
    rec["preflight"] = None
    rec["cost_session_id"] = None
    jsonschema.validate(rec, load_schema())


def test_bad_mode_rejected():
    rec = valid_update_record()
    rec["mode"] = "rebuild"
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass


def test_missing_required_field_rejected():
    rec = valid_update_record()
    del rec["run_id"]
    try:
        jsonschema.validate(rec, load_schema())
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
