"""`nono-pi orchestrate-kg plan <out>` — resolve the KG build plan (subtopics + overall).

This is a deterministic driver/bookkeeper only: it computes each KG's target
folder, records them as pending in the ledger, and prints the ordered plan. The
agent performs the actual librarian-driven KG build for each entry.
"""
import argparse
import json
import os
import re
import sys

from nono_pi.lib import ledger as L
from nono_pi.lib import schema
from nono_pi.paths import data_file

_SCHEMA = data_file("schemas", "subtopics_schema.json")


def _slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "topic"


def plan_kgs(out_dir, subtopics_doc):
    schema.validate(_SCHEMA, subtopics_doc)
    kgs = []
    for st in subtopics_doc["subtopics"]:
        slug = st.get("slug") or _slugify(st["title"])
        kgs.append({"slug": slug, "title": st["title"], "kind": "subtopic",
                    "kg_dir": os.path.join("kgs", slug)})
    kgs.append({"slug": "_overall", "title": subtopics_doc["topic"],
                "kind": "overall", "kg_dir": os.path.join("kgs", "_overall")})

    led = L.read_ledger(out_dir)
    led["subtopics"] = [{"slug": k["slug"], "title": k["title"]}
                        for k in kgs if k["kind"] == "subtopic"]
    for k in kgs:
        led.setdefault("kg_status", {}).setdefault(k["slug"], "pending")
    L.write_ledger(out_dir, led)
    return kgs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi orchestrate-kg")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("plan", help="record + print the KG build plan")
    p.add_argument("out_dir")
    p.add_argument("--subtopics", required=True, help="path to a subtopics.json file")
    args = ap.parse_args(argv)

    with open(args.subtopics, encoding="utf-8") as fh:
        doc = json.load(fh)
    kgs = plan_kgs(args.out_dir, doc)
    json.dump({"kgs": kgs}, sys.stdout, indent=2)
    print()
    return 0
