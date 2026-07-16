"""`nono-pi eval` — record hypothesis/draft evaluation rounds and user decisions."""
import argparse
import json
import os

from nono_pi.lib import ledger as L
from nono_pi.paths import data_file

_SCHEMA = data_file("schemas", "eval_round_schema.json")
_TEMPLATE = data_file("templates", "evaluation_report.md")
LOOPS = ("aims", "draft")


def _loop_key(loop):
    return f"{loop}_loop"


def _validate_round(doc):
    import jsonschema
    with open(_SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(doc)


def _render_report(out_dir, loop, lp):
    tpl = open(_TEMPLATE, encoding="utf-8").read()
    blocks = []
    for rnd in lp["rounds"]:
        vlines = []
        for dim, v in rnd.get("verdicts", {}).items():
            cites = ", ".join(v.get("citations", [])) or "—"
            vlines.append(f"- **{dim}:** {v['verdict']} — {v['rationale']} ({cites})")
        wlines = [f"- {w['issue']} → {w['fix']}" for w in rnd.get("weaknesses", [])]
        blocks.append(
            f"## Round {rnd['round']} — decision: {rnd.get('decision') or 'pending'}\n\n"
            f"### Verdicts\n" + ("\n".join(vlines) or "_none_") + "\n\n"
            f"### Weaknesses\n" + ("\n".join(wlines) or "_none_") + "\n\n"
            f"### Proposed revision\n{rnd['proposed_revision']}\n"
        )
    md = tpl.format(loop=loop, status=lp["status"], rounds="\n".join(blocks))
    with open(os.path.join(out_dir, f"{loop}_evaluation.md"), "w", encoding="utf-8") as fh:
        fh.write(md)


def record_round(out_dir, loop, round_input):
    _validate_round(round_input)
    led = L.read_ledger(out_dir)
    lp = led.setdefault(_loop_key(loop), {"status": "pending", "rounds": []})
    rnd = {
        "round": len(lp["rounds"]),
        "verdicts": round_input["verdicts"],
        "weaknesses": round_input["weaknesses"],
        "proposed_revision": round_input["proposed_revision"],
        "decision": None,
        "note": None,
    }
    lp["rounds"].append(rnd)
    lp["status"] = "in_progress"
    L.write_ledger(out_dir, led)
    _render_report(out_dir, loop, lp)
    return rnd


def decide_round(out_dir, loop, decision, note=None):
    led = L.read_ledger(out_dir)
    lp = led.get(_loop_key(loop))
    if not lp or not lp.get("rounds"):
        raise ValueError(f"no {loop} rounds to decide on; run 'nono-pi eval record' first")
    rnd = lp["rounds"][-1]
    rnd["decision"] = decision
    if note is not None:
        rnd["note"] = note
    lp["status"] = decision if decision in ("accepted", "stopped") else "in_progress"
    L.write_ledger(out_dir, led)
    _render_report(out_dir, loop, lp)
    return rnd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi eval")
    sub = ap.add_subparsers(dest="action", required=True)
    r = sub.add_parser("record", help="append an evaluation round")
    r.add_argument("out_dir")
    r.add_argument("--loop", choices=LOOPS, required=True)
    r.add_argument("--input", required=True, help="path to a round JSON")
    d = sub.add_parser("decide", help="record the user's decision on the latest round")
    d.add_argument("out_dir")
    d.add_argument("--loop", choices=LOOPS, required=True)
    d.add_argument("--decision", choices=["approved", "accepted", "stopped"], required=True)
    d.add_argument("--note", default=None)
    args = ap.parse_args(argv)

    if args.action == "record":
        with open(args.input, encoding="utf-8") as fh:
            doc = json.load(fh)
        rnd = record_round(args.out_dir, args.loop, doc)
        print(f"Recorded {args.loop} round {rnd['round']} "
              f"→ {os.path.join(args.out_dir, f'{args.loop}_evaluation.md')}")
    else:
        rnd = decide_round(args.out_dir, args.loop, args.decision, note=args.note)
        print(f"{args.loop} round {rnd['round']} decision: {rnd['decision']}")
    return 0
