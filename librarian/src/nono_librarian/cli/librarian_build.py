#!/usr/bin/env python3
"""Claude-free KG build orchestrator — deterministic equivalent of build-kg.md.

State machine over the build pipeline. Calls lib/llm.py for narrow reasoning
steps and lib/pubmed.py for retrieval; reuses every existing deterministic
script for ledger, evidence tiers, literature stamping, index, validation,
embeddings, digest, and the Phase-2 evaluator. Aborts without writing on model
unavailability (never-mutate-on-failure).

Usage:
    python3 scripts/librarian_build.py "<topic>" [--output NAME] [--since YYYY-MM-DD]
            [--breadth narrow|medium|broad] [--interactive]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

from nono_librarian.lib import build, llm, pubmed
from nono_librarian.lib.frontmatter import parse as parse_node, write as write_node


def source_report(topic, mode, breadth, sub_queries, articles):
    lines = [
        "=== Source Gathering Complete — Awaiting Review ===",
        f"Topic: {topic}", f"Mode: {mode}", f"Breadth tier: {breadth}",
        f"Sub-queries: {', '.join(sub_queries)}",
        f"PMIDs retrieved: {len(articles)}",
        "",
        "Top articles:",
    ]
    for a in articles[:5]:
        lines.append(f"  PMID {a['pmid']} — {a['title']}")
    lines.append("")
    lines.append("Steer: <enter>=proceed | narrow:<term>=drop matching articles")
    return "\n".join(lines)


def apply_steer(steer, articles, sub_queries):
    s = (steer or "").strip()
    if s.lower().startswith("narrow:"):
        term = s.split(":", 1)[1].strip().lower()
        kept = [a for a in articles
                if term not in a["title"].lower() and term not in a.get("abstract", "").lower()]
        return kept, sub_queries, True
    return articles, sub_queries, True


def construct_graph(topic, kg_name, articles, *, chat, breadth, sub_queries,
                    today, start_id=1):
    """Skeleton → per-node synthesis → ids → relationships → manifest."""
    tier = build.TIERS[breadth]
    skeleton = build.propose_skeleton(
        topic, articles, chat=chat,
        nodes_min=tier["nodes_min"], nodes_max=tier["nodes_max"])
    by_pmid = {a["pmid"]: a for a in articles}
    synthesized = [build.synthesize_node(s, by_pmid, chat=chat) for s in skeleton]
    # carry each skeleton's pmids onto the synthesized node for relationship fallback
    for syn, skel in zip(synthesized, skeleton):
        syn.setdefault("pmids", skel["pmids"])
        syn.setdefault("related_nodes", [])
        syn.setdefault("relationships", {})
    nodes = build.assign_ids(synthesized, start=start_id)
    edges = build.propose_relationships(nodes, chat=chat)
    build.apply_relationships(nodes, edges)
    manifest = build.assemble_manifest(
        kg_name, topic, breadth, sub_queries, nodes, edges, today)
    return nodes, manifest


def gather_articles(sub_queries, *, esearch, fetch_metadata, fetch_full_text,
                    known_pmids, tier, mindate=None):
    per_query = [
        esearch(q, retmax=tier["max_results"], **({"mindate": mindate} if mindate else {}))
        for q in sub_queries
    ]
    pmids = build.select_candidates(per_query, known_pmids, cap=tier["metadata"])
    meta_map = fetch_metadata(pmids) if pmids else {}
    articles = []
    for p in pmids:
        meta = meta_map.get(p)
        if not meta:
            continue
        articles.append({"pmid": p, "title": meta.get("title", ""),
                         "abstract": meta.get("abstract", ""), "metadata": meta})
    for a in articles[:tier["full_text"]]:
        pmcid = a["metadata"].get("pmcid")
        if not pmcid:
            continue
        try:
            ft = fetch_full_text(pmcid)
        except pubmed.PubMedUnavailable:
            ft = ""
        if ft:
            a["abstract"] = (a["abstract"] + "\n\n" + ft).strip()
    return articles


def write_nodes(kg_folder, nodes, today):
    nodes_dir = os.path.join(kg_folder, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)
    for n in nodes:
        fm, body = build.render_node_markdown(n, today)
        write_node(os.path.join(nodes_dir, n["file"]), fm, body)


def ledger_batch_for_used(articles):
    batch = []
    for a in articles:
        m = a["metadata"]
        batch.append({
            "pmid": a["pmid"], "disposition": "used", "title": m.get("title"),
            "authors": m.get("authors", []), "journal": m.get("journal"),
            "year": m.get("year"), "publication_types": m.get("publication_types", []),
        })
    return batch


def _now_date():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_run_record(*, kg_name, mode, version, timestamp, nodes, passed, failed,
                     since_date=None):
    """Build a run-record dict (conforms to schemas/run_record_schema.json).

    Pure (clock injected via ``timestamp``). ``run_id`` is the timestamp with
    colons stripped plus ``-v<version>`` (e.g. ``2026-06-25T080012Z-v1``),
    matching the build-kg convention. ``refs_added`` groups the node IDs that
    cite each PMID this run; ``nodes`` are the run's newly created nodes.
    """
    run_id = timestamp.replace(":", "") + f"-v{version}"
    refs = {}
    for n in nodes:
        for pmid in n.get("supports", {}):
            refs.setdefault(pmid, set()).add(n["id"])
    # Sort PMIDs numerically (not lexicographically, so "10" follows "9").
    refs_added = [{"pmid": p, "nodes": sorted(ns)}
                  for p, ns in sorted(refs.items(), key=lambda kv: int(kv[0]))]
    return {
        "run_id": run_id, "kg_name": kg_name, "mode": mode, "timestamp": timestamp,
        "version": version, "since_date": since_date,
        "nodes_created": [n["id"] for n in nodes], "nodes_revised": [],
        "refs_added": refs_added, "refs_failed": [],
        "eval_summary": {"evaluated": len(nodes), "passed": passed, "failed": failed},
    }


def _run(*args):
    subprocess.run([sys.executable, "-m", *args], check=True)


def _evaluate_and_writeback(kg_folder, nodes, today, *, fetch_metadata, fetch_full_text, chat):
    """Evaluate each node with librarian_evaluate and write results back to disk.

    Shared by run_build and (later) run_update so the ~20-line loop is not
    duplicated. Returns (passed, failed) counts.
    """
    from nono_librarian.cli import librarian_evaluate as le
    passed = failed = 0
    fm_by_node = {}
    for n in nodes:
        node_fm = {"title": n["title"],
                   "pubmed_ids": [{"pmid": p, "supports": c} for p, c in n["supports"].items()]}
        entry = le.evaluate_node(
            n["id"], node_fm, fetch_metadata=fetch_metadata,
            fetch_full_text=fetch_full_text, chat=chat)
        fm_by_node[n["id"]] = le.frontmatter_updates(entry)
        passed += entry["overall_status"] == "passed"
        failed += entry["overall_status"] == "failed"
    # apply evaluation results to node files — read existing files to preserve
    # any changes made by classify_evidence_tier.py and stamp_literature.py
    for n in nodes:
        path = os.path.join(kg_folder, "nodes", n["file"])
        fm, body = parse_node(path)
        upd = fm_by_node[n["id"]]
        fm["evaluation_status"] = upd["evaluation_status"]
        fm["quarantined"] = upd["quarantined"]
        verified = {r["pmid"]: r for r in upd["pubmed_ids"]}
        for ref in fm.get("pubmed_ids", []):
            r = verified.get(ref["pmid"])
            if r:
                ref["verified"] = r["verified"]
                if r.get("quotes"):
                    ref["quotes"] = r["quotes"]
        write_node(path, fm, body)
    return passed, failed


def _persist_and_classify(kg_folder, articles):
    """Ledger-persist this run's used PMIDs, then classify evidence tiers + stamp literature."""
    if articles:
        batch_path = os.path.join(kg_folder, "_build_ledger_batch.json")
        with open(batch_path, "w", encoding="utf-8") as fh:
            json.dump(ledger_batch_for_used(articles), fh)
        _run("nono_librarian.cli.pmid_ledger", "batch-add",
             kg_folder, "--input", batch_path)
        os.remove(batch_path)
    _run("nono_librarian.cli.classify_evidence_tier",
         kg_folder, "--update-ledger")
    _run("nono_librarian.cli.stamp_literature", kg_folder)


def _finalize_kg(kg_folder, *, op, summary, overview_text=None,
                 run_record=None):
    """Quarantine enforcement, index, stats, validation, embeddings (non-fatal), log.

    When ``run_record`` is provided, also writes ``runs/<run_id>.json`` and
    renders the audit digest (``digests/<run_id>.md`` + ``_digest.md``). Digest
    rendering is non-fatal — it never fails the run.
    """
    _run("nono_librarian.cli.enforce_quarantine", kg_folder)
    if overview_text is not None:
        _run("nono_librarian.cli.generate_index", kg_folder,
             "--overview-text", overview_text)
    else:
        _run("nono_librarian.cli.generate_index", kg_folder)
    _run("nono_librarian.cli.update_manifest_stats", kg_folder)
    _run("nono_librarian.cli.validate_manifest",
         os.path.join(kg_folder, "manifest.json"))
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.build_embeddings",
                    kg_folder], check=False)  # non-fatal
    # Persist the run-record + digest BEFORE append_log: the run-record is the
    # durable baseline for future updates, so it must survive a log-append
    # failure rather than be lost behind a fatal step.
    if run_record is not None:
        runs_dir = os.path.join(kg_folder, "runs")
        os.makedirs(runs_dir, exist_ok=True)
        rr_path = os.path.join(runs_dir, run_record["run_id"] + ".json")
        with open(rr_path, "w", encoding="utf-8") as fh:
            json.dump(run_record, fh, indent=2)
        # Digest is read-after-stats; never fails the run (matches build-kg 1e).
        subprocess.run([sys.executable, "-m", "nono_librarian.cli.render_digest",
                        kg_folder, "--run-record", rr_path], check=False)
    _run("nono_librarian.cli.append_log", kg_folder,
         "--op", op, "--summary", summary)


def run_build(topic, kg_folder, kg_name, *, esearch, fetch_metadata, fetch_full_text,
              chat, breadth_override, today, run_subprocess=True, prompt_fn=None):
    os.makedirs(os.path.join(kg_folder, "nodes"), exist_ok=True)

    plan = build.plan_search(topic, chat=chat, breadth_override=breadth_override)
    breadth, sub_queries = plan["breadth"], plan["sub_queries"]
    tier = build.TIERS[breadth]

    if run_subprocess:
        _run("nono_librarian.cli.pmid_ledger", "init",
             kg_folder, "--kg-name", kg_name)
        known = set(json.loads(subprocess.run(
            [sys.executable, "-m", "nono_librarian.cli.pmid_ledger", "query",
             kg_folder, "--pmids-only"], capture_output=True, text=True, check=True
        ).stdout))
    else:
        known = set()

    articles = gather_articles(
        sub_queries, esearch=esearch, fetch_metadata=fetch_metadata,
        fetch_full_text=fetch_full_text, known_pmids=known, tier=tier)
    if prompt_fn is not None:
        print(source_report(topic, "build", breadth, sub_queries, articles))
        articles, sub_queries, _ = apply_steer(prompt_fn(), articles, sub_queries)
    if not articles:
        raise build.BuildError("no articles retrieved for topic")

    nodes, manifest = construct_graph(
        topic, kg_name, articles, chat=chat, breadth=breadth,
        sub_queries=sub_queries, today=today)
    write_nodes(kg_folder, nodes, today)
    with open(os.path.join(kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    if run_subprocess:
        _persist_and_classify(kg_folder, articles)

    # Phase 3 evaluation — reuse the Phase-2 evaluator in-process.
    passed, failed = _evaluate_and_writeback(
        kg_folder, nodes, today,
        fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text, chat=chat)

    if run_subprocess:
        run_record = build_run_record(
            kg_name=kg_name, mode="build", version=1, timestamp=_now_iso(),
            nodes=nodes, passed=passed, failed=failed)
        _finalize_kg(kg_folder, op="build",
                     summary=f"Local BUILD: {len(nodes)} nodes, {passed} passed, {failed} failed.",
                     overview_text=f"Knowledge graph on {topic}.", run_record=run_record)

    return {"nodes": len(nodes), "passed": passed, "failed": failed, "kg_folder": kg_folder}


def next_node_number(manifest):
    """Return highest existing node_NNN number + 1, or 1 if there are none."""
    nums = [int(n["id"].split("_")[1]) for n in manifest.get("nodes", [])
            if n.get("id", "").startswith("node_")]
    return (max(nums) + 1) if nums else 1


def run_update(topic, kg_folder, *, esearch, fetch_metadata, fetch_full_text, chat,
               since_date, today, run_subprocess=True, prompt_fn=None):
    """Load an existing KG manifest, gather new articles, append new nodes/edges.

    Never deletes or rewrites existing nodes. Returns a summary dict with
    nodes_created, passed, failed, changelog, kg_folder.
    """
    with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    breadth = manifest.get("search_profile", {}).get("breadth", "medium")
    tier = build.TIERS[breadth]
    recent_qs = manifest.get("search_profile", {}).get("sub_queries", [])

    # split sub-query budget ~60/40 recent/gap-fill
    n_gap = max(1, tier["sub_queries"] - len(recent_qs)) if recent_qs else 1
    weak_ids = set(build.weak_spots(manifest["nodes"]))
    weak_summaries = [n["summary"] for n in manifest["nodes"] if n["id"] in weak_ids]
    try:
        gap_qs = build.gap_fill_queries(topic, weak_summaries or [topic], chat=chat, count=n_gap)
    except build.BuildError:
        gap_qs = []

    if run_subprocess:
        known = set(json.loads(subprocess.run(
            [sys.executable, "-m", "nono_librarian.cli.pmid_ledger", "query",
             kg_folder, "--pmids-only"], capture_output=True, text=True, check=True
        ).stdout))
    else:
        known = {p for n in manifest["nodes"] for p in n.get("pubmed_ids", [])}

    sub_queries = recent_qs + gap_qs
    articles = gather_articles(sub_queries, esearch=esearch,
                               fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text,
                               known_pmids=known, tier=tier, mindate=since_date)
    if prompt_fn is not None:
        print(source_report(topic, "update", breadth, sub_queries, articles))
        articles, sub_queries, _ = apply_steer(prompt_fn(), articles, sub_queries)
    if not articles:
        return {"nodes_created": [], "passed": 0, "failed": 0, "changelog": [], "kg_folder": kg_folder}

    start = next_node_number(manifest)
    new_nodes, sub_manifest = construct_graph(
        topic, manifest["kg_name"], articles, chat=chat, breadth=breadth,
        sub_queries=recent_qs, today=today, start_id=start)
    write_nodes(kg_folder, new_nodes, today)

    manifest["nodes"].extend(sub_manifest["nodes"])
    manifest["edges"].extend(sub_manifest["edges"])
    manifest["version"] = manifest.get("version", 1) + 1
    manifest["updated"] = today
    manifest.setdefault("search_profile", {})["updated"] = today
    with open(os.path.join(kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    if run_subprocess:
        _persist_and_classify(kg_folder, articles)

    # Evaluate new nodes via the shared helper (reads node files written above).
    passed, failed = _evaluate_and_writeback(
        kg_folder, new_nodes, today,
        fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text, chat=chat)

    new_nodes_count = len(new_nodes)
    if run_subprocess:
        run_record = build_run_record(
            kg_name=manifest["kg_name"], mode="update", version=manifest["version"],
            timestamp=_now_iso(), nodes=new_nodes, passed=passed, failed=failed,
            since_date=since_date)
        _finalize_kg(kg_folder, op="update",
                     summary=f"Local UPDATE: {new_nodes_count} new nodes, {passed} passed, {failed} failed.",
                     run_record=run_record)

    changelog = [{"id": n["id"], "title": n["title"]} for n in new_nodes]
    return {"nodes_created": [n["id"] for n in new_nodes], "passed": passed,
            "failed": failed, "changelog": changelog, "kg_folder": kg_folder}


def resolve_mode(kg_folder, topic):
    return "update" if os.path.exists(os.path.join(kg_folder, "manifest.json")) else "build"


def derive_since(manifest, override):
    if override:
        return override.replace("-", "/")
    last = (manifest.get("schedule") or {}).get("last_run")
    if last:
        return last[:10].replace("-", "/")
    return (manifest.get("updated") or "").replace("-", "/")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude-free KG build orchestrator")
    parser.add_argument("topic")
    parser.add_argument("--output", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--breadth", choices=["narrow", "medium", "broad"], default=None)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)

    kg_name = args.output or "KG_" + build.slugify(args.topic)
    kg_folder = kg_name
    mode = resolve_mode(kg_folder, args.topic)
    prompt_fn = (lambda: input("> ")) if args.interactive else None
    try:
        if mode == "build":
            summary = run_build(
                args.topic, kg_folder, kg_name, esearch=pubmed.esearch,
                fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
                chat=llm.chat, breadth_override=args.breadth, today=_now_date(),
                prompt_fn=prompt_fn)
            print(f"BUILD complete: {summary['nodes']} nodes "
                  f"({summary['passed']} passed, {summary['failed']} failed) → {kg_folder}")
        else:
            with open(os.path.join(kg_folder, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            since = derive_since(manifest, args.since)
            summary = run_update(
                args.topic, kg_folder, esearch=pubmed.esearch,
                fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
                chat=llm.chat, since_date=since, today=_now_date(),
                prompt_fn=prompt_fn)
            print(f"UPDATE complete: {len(summary['nodes_created'])} new nodes "
                  f"({summary['passed']} passed, {summary['failed']} failed) → {kg_folder}")
    except llm.LLMUnavailable as e:
        print(f"Error: local model unavailable — aborted; any partial output can be completed by re-running: {e}", file=sys.stderr)
        return 2
    except build.BuildError as e:
        print(f"Error: build failed — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
