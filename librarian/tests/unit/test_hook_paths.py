"""Guard: every hook command in project settings points at a file that exists.

A directory restructure (e.g. moving ``scripts/`` -> ``src/nono_librarian/cli/``)
can silently break a hook whose command names an in-repo script path, because
hooks live in ``.claude/settings.json`` and never appear in a code diff. This
test fails fast if any hook command references a missing ``.py`` file — the
exact regression that left the Stop hook pointing at a deleted
``scripts/cost_report.py``.
"""
import json
import shlex
from pathlib import Path

# tests/unit/test_hook_paths.py -> librarian/ (the project root where hooks run)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILES = [".claude/settings.json", ".claude/settings.local.json"]
# hook commands may reference Claude Code's project-dir env var as a prefix
_ENV_PREFIXES = ("$CLAUDE_PROJECT_DIR/", "${CLAUDE_PROJECT_DIR}/")


def _hook_commands(settings):
    """Yield (event, command) for every command-type hook in a settings dict."""
    for event, matchers in (settings.get("hooks") or {}).items():
        for matcher in matchers or []:
            for hook in matcher.get("hooks", []) or []:
                cmd = hook.get("command")
                if hook.get("type") == "command" and cmd:
                    yield event, cmd


def _script_paths(command):
    """Yield each ``.py`` path token referenced by a shell command string."""
    for tok in shlex.split(command):
        for prefix in _ENV_PREFIXES:
            if tok.startswith(prefix):
                tok = tok[len(prefix):]
        if tok.endswith(".py"):
            yield tok


def test_settings_hook_commands_reference_existing_scripts():
    missing = []
    for rel_settings in SETTINGS_FILES:
        path = PROJECT_ROOT / rel_settings
        if not path.exists():
            continue
        settings = json.loads(path.read_text())
        for event, command in _hook_commands(settings):
            for rel in _script_paths(command):
                if not (PROJECT_ROOT / rel).is_file():
                    missing.append(
                        f"{rel_settings} [{event}]: {rel} (command: {command!r})")
    assert not missing, (
        "Hook command(s) reference missing script file(s):\n" + "\n".join(missing))
