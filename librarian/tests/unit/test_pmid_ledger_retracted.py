import json
import os
import sys

import jsonschema

from nono_librarian.cli import pmid_ledger
from nono_librarian.paths import data_file

SCHEMA_PATH = str(data_file("schemas", "pmid_ledger_schema.json"))


def test_retracted_in_dispositions():
    assert "retracted" in pmid_ledger.DISPOSITIONS


def test_used_to_retracted_transition_allowed():
    assert "retracted" in pmid_ledger._VALID_TRANSITIONS["used"]


def test_retracted_to_used_transition_allowed():
    # a retracted PMID can be re-validated later (recovery path)
    assert "used" in pmid_ledger._VALID_TRANSITIONS.get("retracted", set())


def test_schema_accepts_retracted_disposition():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    entry = {
        "disposition": "retracted",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_checked": "2026-06-24T00:00:00+00:00",
        "assigned_nodes": ["node_001"],
    }
    # Validate a single entry against the entries' additionalProperties subschema.
    entry_schema = schema["properties"]["entries"]["additionalProperties"]
    jsonschema.validate(entry, entry_schema)


def test_schema_rejects_unknown_disposition():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    entry_schema = schema["properties"]["entries"]["additionalProperties"]
    bad = {"disposition": "bogus", "first_seen": "2026-01-01T00:00:00+00:00",
           "last_checked": "2026-01-01T00:00:00+00:00", "assigned_nodes": []}
    try:
        jsonschema.validate(bad, entry_schema)
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
