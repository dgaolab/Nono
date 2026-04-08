#!/usr/bin/env python3
"""Persistent PMID ledger for KG build/update cycles.

Tracks every PMID encountered — used, irrelevant, failed, or superseded —
so that future runs can skip already-seen literature and avoid redundant
MCP calls.

Usage:
    python3 scripts/pmid_ledger.py init <kg_folder> [--kg-name <NAME>] [--force]
    python3 scripts/pmid_ledger.py add <kg_folder> --pmid <PMID> --disposition <DISP> [...]
    python3 scripts/pmid_ledger.py batch-add <kg_folder> --input <file.json>
    python3 scripts/pmid_ledger.py update <kg_folder> --pmid <PMID> --disposition <DISP> [...]
    python3 scripts/pmid_ledger.py query <kg_folder> [--pmid <PMID>] [--disposition <DISP>] [--node <ID>] [--pmids-only]
    python3 scripts/pmid_ledger.py stats <kg_folder>
    python3 scripts/pmid_ledger.py sync <kg_folder> [--from-eval-log]
    python3 scripts/pmid_ledger.py validate <kg_folder> [--schema <path>]
"""

import argparse
import datetime
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.frontmatter import parse as parse_node


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEDGER_FILENAME = "_pmid_ledger.json"
DISPOSITIONS = {"used", "irrelevant", "failed", "superseded"}

# Valid disposition transitions.  Key = current, value = set of allowed targets.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "used": {"failed", "superseded"},
    "irrelevant": {"used"},
    "failed": {"used"},        # re-found and re-verified in a later cycle
    "superseded": {"used"},    # re-assigned in a later cycle
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _find_project_root() -> str:
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(d, "schemas")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(__file__))


def _ledger_path(kg_folder: str) -> str:
    return os.path.join(kg_folder, LEDGER_FILENAME)


def _load_ledger(kg_folder: str) -> dict:
    path = _ledger_path(kg_folder)
    if not os.path.exists(path):
        print(f"Error: ledger not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_ledger(kg_folder: str, ledger: dict) -> None:
    """Atomic write of ledger to disk."""
    ledger["updated"] = _today_iso()
    ledger["version"] = ledger.get("version", 0) + 1
    path = _ledger_path(kg_folder)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _recompute_stats(ledger: dict) -> dict:
    counts: dict[str, int] = {"used": 0, "irrelevant": 0, "failed": 0, "superseded": 0}
    for entry in ledger.get("entries", {}).values():
        disp = entry.get("disposition", "")
        if disp in counts:
            counts[disp] += 1
    counts["total"] = sum(counts.values())
    return counts


def _apply_entry(ledger: dict, pmid: str, disposition: str, *,
                 title: str | None = None, journal: str | None = None,
                 year: int | None = None, node: str | None = None,
                 tier: str | None = None, notes: str | None = None) -> dict | None:
    """Add or merge a single PMID entry. Returns the entry, or None on invalid transition."""
    entries = ledger.setdefault("entries", {})
    now = _now_iso()

    if pmid in entries:
        existing = entries[pmid]
        old_disp = existing["disposition"]
        if disposition != old_disp:
            if disposition not in _VALID_TRANSITIONS.get(old_disp, set()):
                print(f"Warning: invalid transition {old_disp}→{disposition} for PMID {pmid}, skipping",
                      file=sys.stderr)
                return None
            existing["disposition"] = disposition
        existing["last_checked"] = now
        if title is not None:
            existing["title"] = title
        if journal is not None:
            existing["journal"] = journal
        if year is not None:
            existing["year"] = year
        if tier is not None:
            existing["evidence_tier"] = tier
        if notes is not None:
            existing["notes"] = notes
        if node and node not in existing.get("assigned_nodes", []):
            existing.setdefault("assigned_nodes", []).append(node)
        return existing
    else:
        entry: dict = {
            "title": title,
            "disposition": disposition,
            "first_seen": now,
            "last_checked": now,
            "assigned_nodes": [node] if node else [],
            "evidence_tier": tier,
            "journal": journal,
            "year": year,
            "notes": notes or "",
        }
        entries[pmid] = entry
        return entry


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init(args):
    kg_folder = args.kg_folder
    path = _ledger_path(kg_folder)

    if os.path.exists(path) and not args.force:
        print(f"Error: ledger already exists at {path}. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    manifest_path = os.path.join(kg_folder, "manifest.json")
    folder_basename = os.path.basename(os.path.abspath(kg_folder))

    # Resolve kg_name: CLI flag > manifest > folder basename (with KG_ prefix enforced)
    if args.kg_name:
        kg_name = args.kg_name
    else:
        kg_name = folder_basename
    if not kg_name.startswith("KG_"):
        kg_name = "KG_" + kg_name

    ledger: dict = {
        "kg_name": kg_name,
        "created": _today_iso(),
        "updated": _today_iso(),
        "version": 0,
        "entries": {},
        "statistics": {"total": 0, "used": 0, "irrelevant": 0, "failed": 0, "superseded": 0},
    }

    bootstrapped = 0

    # Bootstrap from manifest if it exists
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        kg_name = manifest.get("kg_name", kg_name)
        ledger["kg_name"] = kg_name
        created_date = manifest.get("created", _today_iso())
        now = _now_iso()

        # Build a reverse map: pmid -> list of node_ids
        pmid_to_nodes: dict[str, list[str]] = {}
        for node_entry in manifest.get("nodes", []):
            node_id = node_entry.get("id", "")
            for pmid in node_entry.get("pubmed_ids", []):
                pmid_str = str(pmid)
                pmid_to_nodes.setdefault(pmid_str, []).append(node_id)

        for pmid_str, node_ids in pmid_to_nodes.items():
            ledger["entries"][pmid_str] = {
                "title": None,
                "disposition": "used",
                "first_seen": f"{created_date}T00:00:00Z",
                "last_checked": now,
                "assigned_nodes": node_ids,
                "evidence_tier": None,
                "journal": None,
                "year": None,
                "notes": "bootstrapped from manifest.json",
            }
            bootstrapped += 1

        # Also check evaluation log for failed PMIDs
        eval_log_path = os.path.join(kg_folder, "_evaluation_log.json")
        if os.path.exists(eval_log_path):
            try:
                with open(eval_log_path, "r", encoding="utf-8") as fh:
                    eval_log = json.load(fh)
                if isinstance(eval_log, list):
                    for entry in eval_log:
                        for check in entry.get("pmid_checks", []):
                            pmid = str(check.get("pmid", ""))
                            if not pmid:
                                continue
                            if not check.get("exists", True):
                                if pmid in ledger["entries"]:
                                    ledger["entries"][pmid]["disposition"] = "failed"
                                    ledger["entries"][pmid]["notes"] = "PMID does not exist (from eval log)"
                            elif check.get("verdict") in ("not_supported", "unrelated"):
                                if pmid in ledger["entries"]:
                                    ledger["entries"][pmid]["disposition"] = "failed"
                                    ledger["entries"][pmid]["notes"] = f"verdict: {check['verdict']} (from eval log)"
            except (json.JSONDecodeError, OSError):
                pass

    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(kg_folder, ledger)

    result = {"initialized": True, "bootstrapped_pmids": bootstrapped}
    json.dump(result, sys.stdout, indent=2)
    print()


def cmd_add(args):
    ledger = _load_ledger(args.kg_folder)
    entry = _apply_entry(
        ledger, args.pmid, args.disposition,
        title=args.title, journal=args.journal,
        year=args.year, node=args.node,
        tier=args.tier, notes=args.notes,
    )
    if entry is None:
        sys.exit(1)
    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(args.kg_folder, ledger)
    json.dump(entry, sys.stdout, indent=2)
    print()


def cmd_batch_add(args):
    ledger = _load_ledger(args.kg_folder)

    with open(args.input, "r", encoding="utf-8") as fh:
        batch = json.load(fh)

    if not isinstance(batch, list):
        print("Error: batch input must be a JSON array", file=sys.stderr)
        sys.exit(1)

    added = 0
    updated = 0
    for item in batch:
        pmid = str(item.get("pmid", ""))
        disposition = item.get("disposition", "")
        if not pmid or disposition not in DISPOSITIONS:
            print(f"Warning: skipping invalid entry: pmid={pmid!r} disposition={disposition!r}",
                  file=sys.stderr)
            continue

        was_new = pmid not in ledger.get("entries", {})
        result = _apply_entry(
            ledger, pmid, disposition,
            title=item.get("title"),
            journal=item.get("journal"),
            year=item.get("year"),
            node=item.get("node"),
            tier=item.get("tier"),
            notes=item.get("notes"),
        )
        if result is not None:
            if was_new:
                added += 1
            else:
                updated += 1

    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(args.kg_folder, ledger)

    result = {"added": added, "updated": updated}
    json.dump(result, sys.stdout, indent=2)
    print()


def cmd_update(args):
    ledger = _load_ledger(args.kg_folder)
    entries = ledger.get("entries", {})

    if args.pmid not in entries:
        print(f"Error: PMID {args.pmid} not found in ledger", file=sys.stderr)
        sys.exit(1)

    entry = _apply_entry(
        ledger, args.pmid, args.disposition,
        node=args.node, notes=args.notes,
    )
    if entry is None:
        sys.exit(1)

    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(args.kg_folder, ledger)
    json.dump(entry, sys.stdout, indent=2)
    print()


def cmd_query(args):
    ledger = _load_ledger(args.kg_folder)
    entries = ledger.get("entries", {})

    # Single PMID lookup
    if args.pmid:
        if args.pmid in entries:
            if args.pmids_only:
                json.dump([args.pmid], sys.stdout)
            else:
                json.dump({args.pmid: entries[args.pmid]}, sys.stdout, indent=2)
        else:
            if args.pmids_only:
                json.dump([], sys.stdout)
            else:
                json.dump({}, sys.stdout, indent=2)
        print()
        return

    # Filter
    matched: dict[str, dict] = {}
    for pmid, entry in entries.items():
        if args.disposition and entry.get("disposition") != args.disposition:
            continue
        if args.node and args.node not in entry.get("assigned_nodes", []):
            continue
        matched[pmid] = entry

    if args.pmids_only:
        json.dump(sorted(matched.keys()), sys.stdout)
    else:
        json.dump(matched, sys.stdout, indent=2)
    print()


def cmd_stats(args):
    ledger = _load_ledger(args.kg_folder)
    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(args.kg_folder, ledger)
    json.dump(ledger["statistics"], sys.stdout, indent=2)
    print()


def cmd_sync(args):
    kg_folder = args.kg_folder
    ledger = _load_ledger(kg_folder)
    changes = {"from_eval_log": 0, "from_nodes": 0, "from_manifest": 0}

    # Sync from evaluation log
    if args.from_eval_log:
        eval_log_path = os.path.join(kg_folder, "_evaluation_log.json")
        if os.path.exists(eval_log_path):
            with open(eval_log_path, "r", encoding="utf-8") as fh:
                eval_log = json.load(fh)

            if isinstance(eval_log, list):
                for eval_entry in eval_log:
                    timestamp = eval_entry.get("timestamp", _now_iso())

                    for check in eval_entry.get("pmid_checks", []):
                        pmid = str(check.get("pmid", ""))
                        if not pmid:
                            continue

                        if not check.get("exists", True):
                            # PMID doesn't exist
                            result = _apply_entry(
                                ledger, pmid, "failed",
                                notes=f"PMID does not exist (eval {timestamp})",
                            )
                            if result is not None:
                                changes["from_eval_log"] += 1

                        elif check.get("verdict") in ("not_supported", "unrelated"):
                            result = _apply_entry(
                                ledger, pmid, "failed",
                                title=check.get("article_title"),
                                notes=f"verdict: {check['verdict']} (eval {timestamp})",
                            )
                            if result is not None:
                                changes["from_eval_log"] += 1

                        elif check.get("verdict") in ("supported", "partially_supported"):
                            # Update last_checked for verified PMIDs
                            if pmid in ledger.get("entries", {}):
                                ledger["entries"][pmid]["last_checked"] = timestamp

    # Sync PMIDs from node files that aren't in the ledger (remediation additions)
    node_files = sorted(glob.glob(os.path.join(kg_folder, "nodes", "*.md")))
    for node_file in node_files:
        try:
            fm, _ = parse_node(node_file)
        except Exception:
            continue
        node_id = fm.get("id", "")
        for pmid_entry in fm.get("pubmed_ids", []):
            pmid = pmid_entry.get("pmid") if isinstance(pmid_entry, dict) else str(pmid_entry)
            if not pmid:
                continue
            if pmid not in ledger.get("entries", {}):
                tier = pmid_entry.get("evidence_tier") if isinstance(pmid_entry, dict) else None
                _apply_entry(
                    ledger, pmid, "used",
                    node=node_id, tier=tier,
                    notes="discovered during sync (likely remediation addition)",
                )
                changes["from_nodes"] += 1
            else:
                # Ensure node assignment is tracked
                entry = ledger["entries"][pmid]
                if node_id and node_id not in entry.get("assigned_nodes", []):
                    entry.setdefault("assigned_nodes", []).append(node_id)

    # Sync from manifest (PMIDs in manifest but not in ledger)
    manifest_path = os.path.join(kg_folder, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        for node_entry in manifest.get("nodes", []):
            node_id = node_entry.get("id", "")
            for pmid in node_entry.get("pubmed_ids", []):
                pmid_str = str(pmid)
                if pmid_str not in ledger.get("entries", {}):
                    _apply_entry(
                        ledger, pmid_str, "used",
                        node=node_id,
                        notes="discovered during sync (in manifest but not ledger)",
                    )
                    changes["from_manifest"] += 1

    ledger["statistics"] = _recompute_stats(ledger)
    _write_ledger(kg_folder, ledger)
    json.dump(changes, sys.stdout, indent=2)
    print()


def cmd_validate(args):
    kg_folder = args.kg_folder
    ledger_path_val = _ledger_path(kg_folder)

    if not os.path.exists(ledger_path_val):
        print(f"Error: ledger not found at {ledger_path_val}", file=sys.stderr)
        sys.exit(1)

    try:
        import jsonschema
    except ImportError:
        print("Error: 'jsonschema' package required. Install with: pip install jsonschema",
              file=sys.stderr)
        sys.exit(1)

    with open(ledger_path_val, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)

    # Schema validation
    schema_path = args.schema
    if not schema_path:
        project_root = _find_project_root()
        schema_path = os.path.join(project_root, "schemas", "pmid_ledger_schema.json")

    if not os.path.exists(schema_path):
        print(f"Error: schema not found at {schema_path}", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(ledger), key=lambda e: list(e.path)):
        path = "$.{}".format(".".join(str(p) for p in error.absolute_path)) if error.absolute_path else "$"
        errors.append({"path": path, "message": error.message})

    # Soft checks: cross-reference with manifest
    warnings = []
    manifest_path = os.path.join(kg_folder, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        manifest_pmids: set[str] = set()
        for node in manifest.get("nodes", []):
            manifest_pmids.update(str(p) for p in node.get("pubmed_ids", []))

        ledger_used = {pmid for pmid, e in ledger.get("entries", {}).items()
                       if e.get("disposition") == "used"}

        orphaned = manifest_pmids - ledger_used
        if orphaned:
            warnings.append(f"PMIDs in manifest but not marked 'used' in ledger: {sorted(orphaned)}")

        missing = ledger_used - manifest_pmids
        if missing:
            warnings.append(f"PMIDs marked 'used' in ledger but absent from manifest: {sorted(missing)}")

    # Check statistics consistency
    expected_stats = _recompute_stats(ledger)
    actual_stats = ledger.get("statistics", {})
    for key in ("total", "used", "irrelevant", "failed", "superseded"):
        if actual_stats.get(key) != expected_stats.get(key):
            warnings.append(
                f"statistics.{key}: stored={actual_stats.get(key)}, computed={expected_stats.get(key)}"
            )

    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)

    if errors:
        result = {"valid": False, "errors": errors, "warnings": len(warnings)}
        json.dump(result, sys.stdout, indent=2)
        print()
        sys.exit(1)
    else:
        result = {"valid": True, "warnings": len(warnings)}
        json.dump(result, sys.stdout, indent=2)
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Persistent PMID ledger for KG build/update cycles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Initialize a new PMID ledger")
    p_init.add_argument("kg_folder", help="Path to the KG folder")
    p_init.add_argument("--kg-name", help="Canonical KG name (overrides folder basename)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing ledger")

    # add
    p_add = sub.add_parser("add", help="Add a single PMID to the ledger")
    p_add.add_argument("kg_folder")
    p_add.add_argument("--pmid", required=True)
    p_add.add_argument("--disposition", required=True, choices=sorted(DISPOSITIONS))
    p_add.add_argument("--title")
    p_add.add_argument("--journal")
    p_add.add_argument("--year", type=int)
    p_add.add_argument("--node")
    p_add.add_argument("--tier")
    p_add.add_argument("--notes")

    # batch-add
    p_batch = sub.add_parser("batch-add", help="Batch add/update PMIDs from a JSON file")
    p_batch.add_argument("kg_folder")
    p_batch.add_argument("--input", required=True, help="Path to JSON array of entries")

    # update
    p_update = sub.add_parser("update", help="Update disposition of an existing PMID")
    p_update.add_argument("kg_folder")
    p_update.add_argument("--pmid", required=True)
    p_update.add_argument("--disposition", required=True, choices=sorted(DISPOSITIONS))
    p_update.add_argument("--node")
    p_update.add_argument("--notes")

    # query
    p_query = sub.add_parser("query", help="Query ledger entries")
    p_query.add_argument("kg_folder")
    p_query.add_argument("--pmid", help="Look up a single PMID")
    p_query.add_argument("--disposition", choices=sorted(DISPOSITIONS))
    p_query.add_argument("--node", help="Filter by assigned node ID")
    p_query.add_argument("--pmids-only", action="store_true",
                         help="Output only the PMID strings as a JSON array")

    # stats
    p_stats = sub.add_parser("stats", help="Recompute and display ledger statistics")
    p_stats.add_argument("kg_folder")

    # sync
    p_sync = sub.add_parser("sync", help="Reconcile ledger with manifest, node files, and eval log")
    p_sync.add_argument("kg_folder")
    p_sync.add_argument("--from-eval-log", action="store_true",
                        help="Also sync from _evaluation_log.json")

    # validate
    p_validate = sub.add_parser("validate", help="Validate ledger against schema")
    p_validate.add_argument("kg_folder")
    p_validate.add_argument("--schema", help="Path to ledger JSON Schema file")

    args = parser.parse_args()
    commands = {
        "init": cmd_init,
        "add": cmd_add,
        "batch-add": cmd_batch_add,
        "update": cmd_update,
        "query": cmd_query,
        "stats": cmd_stats,
        "sync": cmd_sync,
        "validate": cmd_validate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
