import json
import os

import jsonschema

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schemas", "graph_schema.json")


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def minimal_manifest():
    return {
        "kg_name": "KG_Test", "topic": "t",
        "created": "2026-01-01", "updated": "2026-01-01", "version": 1,
        "nodes": [], "edges": [],
        "statistics": {"total_nodes": 0, "total_edges": 0, "total_unique_pmids": 0,
                       "evaluation_passed": 0, "evaluation_failed": 0},
    }


def test_search_profile_is_a_declared_property():
    schema = load_schema()
    assert "search_profile" in schema["properties"]
    manifest = minimal_manifest()
    manifest["search_profile"] = {
        "breadth": "narrow",
        "sub_queries": ["melatonin circadian rhythm molecular mechanism"],
        "updated": "2026-06-09",
    }
    jsonschema.validate(manifest, schema)  # must not raise


def test_search_profile_rejects_bad_breadth():
    schema = load_schema()
    manifest = minimal_manifest()
    manifest["search_profile"] = {"breadth": "huge", "sub_queries": ["x"]}
    try:
        jsonschema.validate(manifest, schema)
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass


def test_schedule_threshold_is_declared():
    schema = load_schema()
    assert "threshold" in schema["properties"]["schedule"]["properties"]
    manifest = minimal_manifest()
    manifest["schedule"] = {"cron": "0 8 * * 1", "trigger_name": "kg-update-t",
                            "last_run": None, "threshold": 3}
    jsonschema.validate(manifest, schema)
