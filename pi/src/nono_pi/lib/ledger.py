"""Read/write/reconcile the pi_run.json progress ledger (disk = source of truth)."""
import json
import os
import re

from nono_pi.paths import data_file

LEDGER_NAME = "pi_run.json"
_SCHEMA = data_file("schemas", "pi_run_schema.json")
_VER_RE = re.compile(r"^v(\d+)\.")


def new_ledger(out_dir=""):
    return {
        "schema_version": 1,
        "out_dir": out_dir,
        "goal": "",
        "doc_type": None,
        "mode": None,
        "depth": None,
        "draft_version": 0,
        "requested_sections": [],
        "subtopics": [],
        "kg_status": {},
        "gap_gate": {"status": "pending", "decision": None},
        "si_status": "pending",
        "sections": {},
    }


def ledger_path(out_dir):
    return os.path.join(out_dir, LEDGER_NAME)


def read_ledger(out_dir):
    p = ledger_path(out_dir)
    if not os.path.exists(p):
        return new_ledger(out_dir)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def write_ledger(out_dir, ledger):
    with open(ledger_path(out_dir), "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2)


def _max_draft_version(draft_dir):
    best = 0
    if not os.path.isdir(draft_dir):
        return 0
    for name in os.listdir(draft_dir):
        m = _VER_RE.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def reconcile(out_dir, ledger):
    for slug in list(ledger.get("kg_status", {})):
        if os.path.exists(os.path.join(out_dir, "kgs", slug, "manifest.json")):
            ledger["kg_status"][slug] = "built"
    si = os.path.join(out_dir, "Significance_and_Innovation.md")
    if os.path.exists(si):
        ledger["si_status"] = "done"
    draft_dir = os.path.join(out_dir, "draft")
    if ledger.get("mode") == "create":
        for key in ledger.get("requested_sections", []):
            written = os.path.exists(os.path.join(draft_dir, f"{key}.md"))
            ledger.setdefault("sections", {})[key] = "written" if written else "requested"
    elif ledger.get("mode") == "revise":
        ledger["draft_version"] = _max_draft_version(draft_dir)
    return ledger


def validate_ledger(ledger):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(ledger)
