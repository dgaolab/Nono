"""`nono-pi route <out>` — record requested sections/depth, print the skill plan."""
import argparse
import json
import sys

from nono_pi.lib import ledger as L
from nono_pi.lib import routing as R


def resolve(out_dir, *, sections=None, full=False):
    led = L.read_ledger(out_dir)
    doc_type, mode = led.get("doc_type"), led.get("mode")
    if not doc_type or not mode:
        raise ValueError("run 'nono-pi intake' before 'route' (doc_type/mode unset)")
    table = R.load_table(doc_type)
    chosen = R.all_sections(table) if full else list(sections or [])
    unknown = [s for s in chosen if s not in table["sections"]]
    if unknown:
        raise ValueError(f"unknown sections for {doc_type}: {unknown}")
    plan = R.select(table, chosen, mode)
    led["depth"] = "full" if full else "sections"
    new_sections = [p["section"] for p in plan]
    if full:
        led["requested_sections"] = new_sections
    else:
        led["requested_sections"] = list(dict.fromkeys(
            led.get("requested_sections", []) + new_sections))
    led.setdefault("sections", {})
    for p in plan:
        led["sections"].setdefault(p["section"], "requested")
    L.write_ledger(out_dir, led)
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi route")
    ap.add_argument("out_dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true")
    g.add_argument("--sections", help="comma-separated section keys")
    args = ap.parse_args(argv)
    sections = args.sections.split(",") if args.sections else None
    try:
        plan = resolve(args.out_dir, sections=sections, full=args.full)
    except ValueError as e:
        print(f"nono-pi route: {e}", file=sys.stderr)
        return 2
    json.dump({"plan": plan}, sys.stdout, indent=2)
    print()
    return 0
