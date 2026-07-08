"""`nono-pi analysis-plan <out> --input plan.json` — emit a nono-analyst-consumable plan."""
import argparse
import json
import os

from nono_pi.paths import data_file

_SCHEMA = data_file("schemas", "analysis_input_schema.json")
_TEMPLATE = data_file("templates", "analysis_plan.md")
OUT_NAME = "analysis_plan.md"


def _validate(doc):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(doc)


def render_plan(doc):
    tpl = open(_TEMPLATE, encoding="utf-8").read()
    blocks = []
    for i, a in enumerate(doc["analyses"], 1):
        inputs = ", ".join(a.get("inputs", [])) or "n/a"
        blocks.append(
            f"### Analysis {i}: {a['name']}\n\n"
            f"- **Gap addressed:** {a['gap']}\n"
            f"- **Rationale:** {a['rationale']}\n"
            f"- **Method:** {a['method']}\n"
            f"- **Inputs:** {inputs}\n"
            f"- **Expected output:** {a['expected_output']}\n"
        )
    return tpl.format(goal=doc["goal"], analyses="\n".join(blocks))


def write_analysis_plan(out_dir, doc):
    _validate(doc)
    md = render_plan(doc)
    path = os.path.join(out_dir, OUT_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi analysis-plan")
    ap.add_argument("out_dir")
    ap.add_argument("--input", required=True)
    args = ap.parse_args(argv)
    with open(args.input, encoding="utf-8") as fh:
        doc = json.load(fh)
    path = write_analysis_plan(args.out_dir, doc)
    print(f"Wrote further-analysis plan → {path}")
    return 0
