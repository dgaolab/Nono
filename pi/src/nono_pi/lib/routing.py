"""Load and filter the curated grant/paper skill routing tables."""
import json

from nono_pi.paths import routing_dir


def load_table(doc_type):
    if doc_type not in ("grant", "paper"):
        raise ValueError(f"unknown doc_type: {doc_type!r}")
    with open(routing_dir() / f"{doc_type}.json", encoding="utf-8") as fh:
        return json.load(fh)


def all_sections(table):
    return list(table["order"])


def select(table, sections, mode):
    if mode not in ("create", "revise"):
        raise ValueError(f"unknown mode: {mode!r}")
    chosen = set(sections)
    plan = []
    for key in table["order"]:
        if key in chosen:
            entry = table["sections"][key]
            plan.append({"section": key, "title": entry["title"], "skills": entry[mode]})
    return plan
