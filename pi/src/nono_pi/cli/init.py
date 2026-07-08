"""`nono-pi init <out>` — scaffold an output folder and its progress ledger."""
import argparse
import os

from nono_pi.lib import ledger as L

SUBDIRS = ("input", "kgs", "draft")


def scaffold(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for d in SUBDIRS:
        os.makedirs(os.path.join(out_dir, d), exist_ok=True)
    if not os.path.exists(L.ledger_path(out_dir)):
        L.write_ledger(out_dir, L.new_ledger(os.path.abspath(out_dir)))
    return out_dir


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi init")
    ap.add_argument("out_dir")
    args = ap.parse_args(argv)
    scaffold(args.out_dir)
    print(f"Initialized nono-pi output folder → {args.out_dir}")
    return 0
