"""`nono-pi assemble-si <out> --input si.json` — render the Significance & Innovation doc."""
import argparse
import json
import os

from nono_pi.lib import ledger as L
from nono_pi.paths import data_file

_SCHEMA = data_file("schemas", "si_input_schema.json")
_TEMPLATE = data_file("templates", "significance_innovation.md")
OUT_NAME = "Significance_and_Innovation.md"


def _validate(doc):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(doc)


def _bullets(items):
    return "\n".join(f"- {x}" for x in items) if items else "_None provided._"


def _evidence(items):
    lines = []
    for e in items:
        pmids = ", ".join(f"PMID:{p}" for p in e.get("pmids", []))
        lines.append(f"- **{e['claim']}** ({pmids})" if pmids else f"- **{e['claim']}**")
    return "\n".join(lines) if lines else "_None provided._"


def render_si(doc):
    tpl = open(_TEMPLATE, encoding="utf-8").read()
    return tpl.format(
        significance=_bullets(doc.get("significance", [])),
        innovation=_bullets(doc.get("innovation", [])),
        evidence=_evidence(doc.get("evidence", [])),
        caveats=_bullets(doc.get("caveats", [])),
    )


def assemble_si(out_dir, doc):
    _validate(doc)
    md = f"# Significance and Innovation — {doc['title']}\n\n" + render_si(doc).split("\n", 2)[2]
    path = os.path.join(out_dir, OUT_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    led = L.read_ledger(out_dir)
    led["si_status"] = "done"
    L.write_ledger(out_dir, led)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi assemble-si")
    ap.add_argument("out_dir")
    ap.add_argument("--input", required=True)
    args = ap.parse_args(argv)
    with open(args.input, encoding="utf-8") as fh:
        doc = json.load(fh)
    path = assemble_si(args.out_dir, doc)
    print(f"Wrote Significance & Innovation → {path}")
    return 0
