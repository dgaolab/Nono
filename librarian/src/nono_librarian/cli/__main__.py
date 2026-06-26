"""`nono-librarian` — entry point dispatching to nono_librarian.cli.* subcommands."""
import importlib
import sys

# subcommand -> module under nono_librarian.cli
COMMANDS = {
    "assemble": "assemble",
    "gather": "gather",
    "search": "search_nodes",
    "lint": "linter_kg",
    "retractions": "check_retractions",
    "chase": "chase_citations",
    "ledger": "pmid_ledger",
    "digest": "render_digest",
    "embeddings": "build_embeddings",
    "index": "generate_index",
    "cross-index": "build_cross_indices",
    "preflight": "preflight",
    "cost-report": "cost_report",
    "verify": "verify",
    "finalize": "finalize",
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: nono-librarian <command> [args]\n\ncommands:")
        for name in sorted(COMMANDS):
            print(f"  {name}")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"nono-librarian: unknown command {cmd!r}. Try 'nono-librarian --help'.", file=sys.stderr)
        return 2
    mod = importlib.import_module(f"nono_librarian.cli.{COMMANDS[cmd]}")
    return mod.main(rest)


if __name__ == "__main__":
    sys.exit(main())
