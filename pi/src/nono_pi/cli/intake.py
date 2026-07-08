"""`nono-pi intake <out>` — record goal/doc-type/mode and ingest input files."""
import argparse
import json
import os
import shutil
import sys

from nono_pi.lib import ledger as L
from nono_pi.paths import data_file

INTAKE_NAME = "intake.json"
_SCHEMA = data_file("schemas", "intake_schema.json")


def _validate(payload):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(payload)


def record_intake(out_dir, *, goal, doc_type, mode, files=(), draft=None):
    input_dir = os.path.join(out_dir, "input")
    draft_dir = os.path.join(out_dir, "draft")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(draft_dir, exist_ok=True)

    copied = []
    for f in files:
        shutil.copy2(f, os.path.join(input_dir, os.path.basename(f)))
        copied.append(os.path.join("input", os.path.basename(f)))

    draft_rel = None
    if mode == "revise":
        if not draft:
            raise ValueError("revise mode requires a draft file")
        # Preserve the immutable baseline: never overwrite an existing v000.
        existing = ([n for n in os.listdir(draft_dir) if n.startswith("v000.")]
                    if os.path.isdir(draft_dir) else [])
        if existing:
            draft_rel = os.path.join("draft", sorted(existing)[0])
        else:
            ext = os.path.splitext(draft)[1] or ".md"
            shutil.copy2(draft, os.path.join(draft_dir, f"v000{ext}"))
            draft_rel = os.path.join("draft", f"v000{ext}")

    payload = {"goal": goal, "doc_type": doc_type, "mode": mode,
               "input_files": copied, "draft_file": draft_rel}
    _validate(payload)
    with open(os.path.join(out_dir, INTAKE_NAME), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    led = L.read_ledger(out_dir)
    led.update({"goal": goal, "doc_type": doc_type, "mode": mode})
    L.write_ledger(out_dir, led)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi intake")
    ap.add_argument("out_dir")
    ap.add_argument("--goal", required=True)
    ap.add_argument("--doc-type", choices=["grant", "paper"], required=True, dest="doc_type")
    ap.add_argument("--mode", choices=["create", "revise"], required=True)
    ap.add_argument("--file", action="append", default=[], dest="files")
    ap.add_argument("--draft", default=None)
    args = ap.parse_args(argv)

    if not os.path.isdir(args.out_dir):
        print(f"nono-pi intake: output folder not found: {args.out_dir} "
              f"(run 'nono-pi init' first)", file=sys.stderr)
        return 2
    for f in args.files:
        if not os.path.exists(f):
            print(f"nono-pi intake: input file not found: {f}", file=sys.stderr)
            return 2
    if args.mode == "revise" and (not args.draft or not os.path.exists(args.draft)):
        print("nono-pi intake: revise mode requires an existing --draft file", file=sys.stderr)
        return 2

    payload = record_intake(args.out_dir, goal=args.goal, doc_type=args.doc_type,
                            mode=args.mode, files=args.files, draft=args.draft)
    print(f"Recorded intake ({payload['mode']} / {payload['doc_type']}) "
          f"→ {os.path.join(args.out_dir, INTAKE_NAME)}")
    return 0
