"""`nono-pi mark <out> ...` — record agent-driven progress into the ledger."""
import argparse
import sys

from nono_pi.lib import ledger as L


def mark(out_dir, *, kg=None, kg_status=None, section=None, section_status=None,
         gate=None, bump_draft=False):
    led = L.read_ledger(out_dir)
    if kg is not None:
        led.setdefault("kg_status", {})[kg] = kg_status
    if section is not None:
        led.setdefault("sections", {})[section] = section_status
    if gate is not None:
        gg = led.setdefault("gap_gate", {"status": "pending", "decision": None})
        if gate == "confirmed":
            gg["status"] = "confirmed"
            gg["decision"] = "confirmed"
        else:
            gg["status"] = gate
    if bump_draft:
        led["draft_version"] = int(led.get("draft_version", 0)) + 1
    L.write_ledger(out_dir, led)
    return led


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi mark")
    ap.add_argument("out_dir")
    ap.add_argument("--kg")
    ap.add_argument("--kg-status", choices=["pending", "built", "failed"], dest="kg_status")
    ap.add_argument("--section")
    ap.add_argument("--section-status", choices=["requested", "written"], dest="section_status")
    ap.add_argument("--gate", choices=["gaps", "clear", "confirmed"])
    ap.add_argument("--bump-draft", action="store_true", dest="bump_draft")
    args = ap.parse_args(argv)

    if args.kg and not args.kg_status:
        print("nono-pi mark: --kg requires --kg-status", file=sys.stderr)
        return 2
    if args.section and not args.section_status:
        print("nono-pi mark: --section requires --section-status", file=sys.stderr)
        return 2

    mark(args.out_dir, kg=args.kg, kg_status=args.kg_status, section=args.section,
         section_status=args.section_status, gate=args.gate, bump_draft=args.bump_draft)
    print(f"Updated ledger → {args.out_dir}/pi_run.json")
    return 0
