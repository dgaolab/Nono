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
