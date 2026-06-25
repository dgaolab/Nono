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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import build, llm, pubmed
from lib.frontmatter import write as write_node


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
                    known_pmids, tier):
    per_query = [esearch(q, retmax=tier["max_results"]) for q in sub_queries]
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


def _run(scripts_dir, *args):
    subprocess.run([sys.executable, *args], check=True)


def _evaluate_and_writeback(kg_folder, nodes, today, *, fetch_metadata, fetch_full_text, chat):
    """Evaluate each node with librarian_evaluate and write results back to disk.

    Shared by run_build and (later) run_update so the ~20-line loop is not
    duplicated. Returns (passed, failed) counts.
    """
    import librarian_evaluate as le
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
    # apply evaluation results to node files
    for n in nodes:
        path = os.path.join(kg_folder, "nodes", n["file"])
        fm, body = build.render_node_markdown(n, today)
        upd = fm_by_node[n["id"]]
        fm["evaluation_status"] = upd["evaluation_status"]
        fm["quarantined"] = upd["quarantined"]
        verified = {r["pmid"]: r for r in upd["pubmed_ids"]}
        for ref in fm["pubmed_ids"]:
            r = verified.get(ref["pmid"])
            if r:
                ref["verified"] = r["verified"]
                if r.get("quotes"):
                    ref["quotes"] = r["quotes"]
        write_node(path, fm, body)
    return passed, failed


def run_build(topic, kg_folder, kg_name, *, esearch, fetch_metadata, fetch_full_text,
              chat, breadth_override, today, run_subprocess=True):
    os.makedirs(os.path.join(kg_folder, "nodes"), exist_ok=True)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    plan = build.plan_search(topic, chat=chat, breadth_override=breadth_override)
    breadth, sub_queries = plan["breadth"], plan["sub_queries"]
    tier = build.TIERS[breadth]

    if run_subprocess:
        _run(scripts_dir, os.path.join(scripts_dir, "pmid_ledger.py"), "init",
             kg_folder, "--kg-name", kg_name)
        known = set(subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "pmid_ledger.py"), "query",
             kg_folder, "--pmids-only"], capture_output=True, text=True, check=True
        ).stdout.split())
    else:
        known = set()

    articles = gather_articles(
        sub_queries, esearch=esearch, fetch_metadata=fetch_metadata,
        fetch_full_text=fetch_full_text, known_pmids=known, tier=tier)
    if not articles:
        raise build.BuildError("no articles retrieved for topic")

    nodes, manifest = construct_graph(
        topic, kg_name, articles, chat=chat, breadth=breadth,
        sub_queries=sub_queries, today=today)
    write_nodes(kg_folder, nodes, today)
    with open(os.path.join(kg_folder, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    if run_subprocess:
        batch_path = os.path.join(kg_folder, "_build_ledger_batch.json")
        with open(batch_path, "w", encoding="utf-8") as fh:
            json.dump(ledger_batch_for_used(articles), fh)
        _run(scripts_dir, os.path.join(scripts_dir, "pmid_ledger.py"), "batch-add",
             kg_folder, "--input", batch_path)
        os.remove(batch_path)
        _run(scripts_dir, os.path.join(scripts_dir, "classify_evidence_tier.py"),
             kg_folder, "--update-ledger")
        _run(scripts_dir, os.path.join(scripts_dir, "stamp_literature.py"), kg_folder)

    # Phase 3 evaluation — reuse the Phase-2 evaluator in-process.
    passed, failed = _evaluate_and_writeback(
        kg_folder, nodes, today,
        fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text, chat=chat)

    if run_subprocess:
        _run(scripts_dir, os.path.join(scripts_dir, "enforce_quarantine.py"), kg_folder)
        _run(scripts_dir, os.path.join(scripts_dir, "generate_index.py"), kg_folder,
             "--overview-text", f"Knowledge graph on {topic}.")
        _run(scripts_dir, os.path.join(scripts_dir, "update_manifest_stats.py"), kg_folder)
        _run(scripts_dir, os.path.join(scripts_dir, "validate_manifest.py"),
             os.path.join(kg_folder, "manifest.json"))
        subprocess.run([sys.executable, os.path.join(scripts_dir, "build_embeddings.py"),
                        kg_folder], check=False)  # non-fatal
        _run(scripts_dir, os.path.join(scripts_dir, "append_log.py"), kg_folder,
             "--op", "build",
             "--summary", f"Local BUILD: {len(nodes)} nodes, {passed} passed, {failed} failed.")

    return {"nodes": len(nodes), "passed": passed, "failed": failed, "kg_folder": kg_folder}


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
    try:
        summary = run_build(
            args.topic, kg_folder, kg_name, esearch=pubmed.esearch,
            fetch_metadata=pubmed.fetch_metadata, fetch_full_text=pubmed.fetch_full_text,
            chat=llm.chat, breadth_override=args.breadth, today=_now_date())
    except llm.LLMUnavailable as e:
        print(f"Error: local model unavailable — nothing written: {e}", file=sys.stderr)
        return 2
    except build.BuildError as e:
        print(f"Error: build failed — {e}", file=sys.stderr)
        return 1
    print(f"BUILD complete: {summary['nodes']} nodes "
          f"({summary['passed']} passed, {summary['failed']} failed) → {summary['kg_folder']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
