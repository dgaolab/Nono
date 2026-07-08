"""`nono-pi status <out>` — reconcile the ledger against disk and print progress."""
import argparse

from nono_pi.lib import ledger as L


def _fmt_kgs(led):
    lines = [f"  - {slug}: {st}" for slug, st in led.get("kg_status", {}).items()]
    return "\n".join(lines) if lines else "  (none planned)"


def status_report(out_dir):
    led = L.reconcile(out_dir, L.read_ledger(out_dir))
    L.write_ledger(out_dir, led)
    gate = led.get("gap_gate", {})
    lines = [
        f"nono-pi status: {out_dir}",
        f"  doc_type: {led.get('doc_type')}   mode: {led.get('mode')}   depth: {led.get('depth')}",
        f"  gap_gate: {gate.get('status')} (decision: {gate.get('decision')})",
        f"  significance & innovation: {led.get('si_status')}",
        "  KGs:",
        _fmt_kgs(led),
    ]
    if led.get("mode") == "revise":
        lines.append(f"  draft version: v{led.get('draft_version', 0):03d}")
    else:
        for key in led.get("requested_sections", []):
            lines.append(f"  section {key}: {led.get('sections', {}).get(key, 'requested')}")
    return "\n".join(lines), led


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nono-pi status")
    ap.add_argument("out_dir")
    args = ap.parse_args(argv)
    report, _ = status_report(args.out_dir)
    print(report)
    return 0
