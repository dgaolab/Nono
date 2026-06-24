#!/usr/bin/env python3
"""Render a deterministic, audit-readable digest for a KG run.

Rendering is a pure function of its inputs (no LLM, no clock, no randomness):
the same inputs always produce a byte-identical digest body.

CLI and file IO are added below the rendering functions.
"""


def _outcome_line(rr: dict) -> str:
    novel = (rr.get("preflight") or {}).get("novel_count")
    ev = rr.get("eval_summary", {})
    parts = []
    if novel is not None:
        parts.append(f"{novel} novel PMIDs")
    parts.append(f"{len(rr.get('nodes_created', []))} added, "
                 f"{len(rr.get('nodes_revised', []))} revised")
    parts.append(f"{ev.get('passed', 0)}/{ev.get('evaluated', 0)} passed evaluation")
    return " → ".join([parts[0], "; ".join(parts[1:])]) if novel is not None \
        else "; ".join(parts)


def _header(rr: dict) -> list[str]:
    run_date = rr.get("timestamp", "")[:10]
    return [
        f"# Digest — {rr.get('kg_name', '')} — {run_date}",
        "",
        f"**Run:** {rr.get('run_id', '')} · **Mode:** {rr.get('mode', '')} "
        f"· **Version:** v{rr.get('version', '')}",
        f"**Outcome:** {_outcome_line(rr)}",
        "",
    ]


def _node_block(node_id: str, title: str, refs_for_node: list, eval_index: dict) -> list[str]:
    entry = eval_index.get(node_id)
    if entry is None:
        return [f"### {node_id} — {title}  [evaluation pending]", ""]
    lines = [f"### {node_id} — {title}  [{entry.get('overall_status', '?')}]"]
    checks = {c.get("pmid"): c for c in entry.get("pmid_checks", [])}
    for pmid in refs_for_node:
        c = checks.get(pmid)
        if c is None:
            lines.append(f"- **PMID {pmid}** [no evaluation record]")
            continue
        cite = c.get("article_title") or ""
        lines.append(f"- **PMID {pmid}** [{c.get('verdict', '?')}]: {cite}".rstrip())
        for q in c.get("quotes", []) or []:
            lines.append(f"  > {q.get('text', '')}  _({q.get('source', '')})_")
    lines.append("")
    return lines


def _refs_added_for(node_id: str, rr: dict) -> list[str]:
    return [r["pmid"] for r in rr.get("refs_added", []) if node_id in r.get("nodes", [])]


def _all_refs_for(node_id: str, eval_index: dict) -> list[str]:
    entry = eval_index.get(node_id)
    if not entry:
        return []
    return [c.get("pmid") for c in entry.get("pmid_checks", []) if c.get("pmid")]


def _cost_line(cost: dict) -> str:
    status = cost.get("status")
    if status == "ok":
        m = cost.get("models", {})
        tin = sum(v.get("input", 0) for v in m.values())
        tout = sum(v.get("output", 0) for v in m.values())
        return f"- Cost: ${cost.get('est_cost_usd', 0):.4f} ({tin} in / {tout} out tokens)"
    if status == "pending":
        sid = cost.get("session_id") or "unknown"
        return f"- Cost: pending — session {sid}, see _cost_log.jsonl"
    return "- Cost: unavailable"


def _totals(stats: dict, cost: dict) -> list[str]:
    lines = ["## Run totals"]
    if stats:
        lines.append(f"- Nodes: {stats.get('total_nodes', '?')} total, "
                     f"{stats.get('active_nodes', '?')} active, "
                     f"{stats.get('quarantined_nodes', '?')} quarantined")
        tiers = stats.get("evidence_tier_distribution") or {}
        if tiers:
            lines.append("- Evidence tiers: "
                         + ", ".join(f"{k} {v}" for k, v in sorted(tiers.items())))
    lines.append(_cost_line(cost))
    lines.append("")
    return lines


def _failures(rr: dict, eval_index: dict, node_titles: dict) -> list[str]:
    failed_nodes = [nid for nid in rr.get("nodes_created", []) + rr.get("nodes_revised", [])
                    if (eval_index.get(nid) or {}).get("overall_status") == "failed"]
    refs_failed = rr.get("refs_failed", [])
    if not failed_nodes and not refs_failed:
        return []
    lines = ["## Failures & quarantines"]
    for nid in failed_nodes:
        lines.append(f"- {nid} — {node_titles.get(nid, nid)}: FAILED evaluation")
    for r in refs_failed:
        lines.append(f"- PMID {r.get('pmid')} ({r.get('node')}) — {r.get('reason')}")
    lines.append("")
    return lines


def render(run_record: dict, eval_index: dict, node_titles: dict,
           stats: dict, cost: dict) -> str:
    """Render the digest markdown. Pure function of its inputs."""
    mode = run_record.get("mode")
    lines = _header(run_record)

    if mode == "skip":
        pf = run_record.get("preflight") or {}
        lines.append(
            f"Quiet week: {pf.get('novel_count', 0)} novel PMIDs since "
            f"{run_record.get('since_date', '?')}, below threshold "
            f"{pf.get('threshold', '?')} — no update.")
        lines.append("")
        return "\n".join(lines)

    if mode == "build":
        lines.append("## Summary")
        ev = run_record.get("eval_summary", {})
        lines.append(f"- {len(run_record.get('nodes_created', []))} nodes created; "
                     f"{ev.get('passed', 0)}/{ev.get('evaluated', 0)} passed evaluation")
        lines.append("- Nodes: " + ", ".join(
            f"{nid} ({node_titles.get(nid, nid)})" for nid in run_record.get("nodes_created", [])))
        lines.append("")
        lines.extend(_failures(run_record, eval_index, node_titles))
        lines.extend(_totals(stats, cost))
        return "\n".join(lines)

    # update mode — full audit body
    lines.append("## What changed")
    lines.append("")
    for nid in run_record.get("nodes_created", []):
        lines.append(f"**New:**")
        lines.extend(_node_block(nid, node_titles.get(nid, nid),
                                 _all_refs_for(nid, eval_index), eval_index))
    for nid in run_record.get("nodes_revised", []):
        lines.append(f"**Revised:**")
        lines.extend(_node_block(nid, node_titles.get(nid, nid),
                                 _refs_added_for(nid, run_record), eval_index))
    lines.extend(_failures(run_record, eval_index, node_titles))
    lines.extend(_totals(stats, cost))
    return "\n".join(lines)


import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from append_log import append_entry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COST_LOG = os.path.join(REPO_ROOT, "_cost_log.jsonl")


def load_cost(log_path: str, session_id) -> dict:
    """Return a cost dict: ok (with totals) / pending / unavailable."""
    if not os.path.exists(log_path):
        return {"status": "unavailable"}
    if not session_id:
        return {"status": "pending", "session_id": session_id}
    found = None
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("session_id") == session_id:
                found = entry          # last match wins
    if found is None:
        return {"status": "pending", "session_id": session_id}
    return {"status": "ok", "est_cost_usd": found.get("est_cost_usd", 0.0),
            "models": found.get("models", {})}


def generate(kg_folder: str, run_record_path: str,
             cost_log_path: str = DEFAULT_COST_LOG, do_log: bool = True) -> str:
    """Render the digest for a run-record and write digests/<run_id>.md + _digest.md.
    Returns the path to the per-run digest file."""
    with open(run_record_path, "r", encoding="utf-8") as fh:
        run_record = json.load(fh)

    eval_log = []
    eval_path = os.path.join(kg_folder, "_evaluation_log.json")
    if os.path.exists(eval_path):
        try:
            with open(eval_path, "r", encoding="utf-8") as fh:
                eval_log = json.load(fh)
        except (json.JSONDecodeError, OSError):
            eval_log = []
    eval_index = {e["node_id"]: e for e in eval_log if isinstance(e, dict) and "node_id" in e}

    node_titles, stats = {}, {}
    manifest_path = os.path.join(kg_folder, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            node_titles = {n["id"]: n.get("title", n["id"])
                           for n in manifest.get("nodes", []) if "id" in n}
            stats = manifest.get("statistics", {})
        except (json.JSONDecodeError, OSError):
            pass

    cost = load_cost(cost_log_path, run_record.get("cost_session_id"))

    digest_md = render(run_record, eval_index, node_titles, stats, cost)

    digests_dir = os.path.join(kg_folder, "digests")
    os.makedirs(digests_dir, exist_ok=True)
    digest_path = os.path.join(digests_dir, run_record["run_id"] + ".md")
    with open(digest_path, "w", encoding="utf-8") as fh:
        fh.write(digest_md)
    with open(os.path.join(kg_folder, "_digest.md"), "w", encoding="utf-8") as fh:
        fh.write(digest_md)

    if do_log:
        try:
            append_entry(kg_folder, "digest",
                         f"Digest written for {run_record['run_id']} (mode {run_record.get('mode')}).")
        except Exception:
            pass        # never fail the run over logging

    return digest_path


def main():
    parser = argparse.ArgumentParser(description="Render a KG run digest.")
    parser.add_argument("kg_folder", help="Path to the KG folder")
    parser.add_argument("--run-record", required=True, help="Path to runs/<run_id>.json")
    parser.add_argument("--cost-log", default=DEFAULT_COST_LOG, help="Path to _cost_log.jsonl")
    parser.add_argument("--no-log", action="store_true", help="Do not append a digest entry to _log.md")
    args = parser.parse_args()
    try:
        path = generate(args.kg_folder, args.run_record, args.cost_log, do_log=not args.no_log)
    except Exception as e:          # digest must never fail the run
        print(f"Warning: digest generation failed: {e}", file=sys.stderr)
        sys.exit(0)
    print(f"Digest written: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
