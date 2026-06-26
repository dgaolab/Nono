#!/usr/bin/env python3
"""`nono-librarian verify` — apply agent verdicts to a KG behind the verbatim-quote guardrail."""
import argparse
import datetime
import json
import os
import subprocess
import sys

from nono_librarian.cli import librarian_evaluate as le
from nono_librarian.lib import pubmed
from nono_librarian.lib.frontmatter import parse as parse_node


def load_judgments(kg_folder, path):
    p = path or os.path.join(kg_folder, "_judgments.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def verify_kg(kg_folder, judgments, *, only_ids=None,
              fetch_metadata=pubmed.fetch_metadata,
              fetch_full_text=pubmed.fetch_full_text):
    node_files = le._node_files(kg_folder, only_ids)
    entries = []
    for node_id, path in node_files.items():
        fm, _body = parse_node(path)
        entry = le.judge_node(node_id, fm, judgments.get(node_id, {}),
                              fetch_metadata=fetch_metadata, fetch_full_text=fetch_full_text)
        entry["timestamp"] = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append(entry)

    with open(os.path.join(kg_folder, "_evaluation_log.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)
    for entry in entries:
        updates = le.frontmatter_updates(entry)
        upd_path = os.path.join(kg_folder, f"_eval_upd_{entry['node_id']}.json")
        with open(upd_path, "w", encoding="utf-8") as fh:
            json.dump(updates, fh)
        subprocess.run([sys.executable, "-m", "nono_librarian.cli.update_frontmatter",
                        node_files[entry["node_id"]], "--updates-file", upd_path], check=True)
        os.remove(upd_path)
    subprocess.run([sys.executable, "-m", "nono_librarian.cli.update_manifest_stats",
                    kg_folder], check=True)
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nono-librarian verify",
                                     description="Guardrailed evaluation writeback from agent verdicts")
    parser.add_argument("kg_folder")
    parser.add_argument("--verdicts", default=None,
                        help="agent verdicts JSON (default: <KG>/_judgments.json)")
    parser.add_argument("--nodes", default=None, help="comma-separated node IDs")
    args = parser.parse_args(argv)
    only_ids = set(args.nodes.split(",")) if args.nodes else None
    judgments = load_judgments(args.kg_folder, args.verdicts)
    entries = verify_kg(args.kg_folder, judgments, only_ids=only_ids)
    passed = sum(1 for e in entries if e["overall_status"] == "passed")
    print(f"Verified {len(entries)} nodes: {passed} passed, {len(entries) - passed} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
