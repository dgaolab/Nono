"""`nono-pi` — entry point dispatching to nono_pi.cli.* subcommands."""
import importlib
import sys

# subcommand -> module basename under nono_pi.cli
COMMANDS = {
    "init": "init",
    "intake": "intake",
    "route": "route",
    "orchestrate-kg": "orchestrate_kg",
    "assemble-si": "assemble_si",
    "analysis-plan": "analysis_plan",
    "status": "status",
    "mark": "mark",
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: nono-pi <command> [args]\n\ncommands:")
        for name in sorted(COMMANDS):
            print(f"  {name}")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"nono-pi: unknown command {cmd!r}. Try 'nono-pi --help'.", file=sys.stderr)
        return 2
    mod = importlib.import_module(f"nono_pi.cli.{COMMANDS[cmd]}")
    return mod.main(rest)


if __name__ == "__main__":
    sys.exit(main())
