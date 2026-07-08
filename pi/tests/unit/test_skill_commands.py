import pathlib
import re

from nono_pi.cli.__main__ import COMMANDS

SKILL = pathlib.Path(__file__).resolve().parents[2] / ".claude" / "skills" / "nono-pi" / "SKILL.md"


def _code_spans(text):
    # inline `...` and fenced ``` ... ``` blocks only (ignore prose)
    return "\n".join(re.findall(r"`{1,3}.*?`{1,3}", text, re.S))


def test_skill_exists():
    assert SKILL.exists(), f"missing {SKILL}"


def test_skill_references_only_real_commands():
    code = _code_spans(SKILL.read_text())
    referenced = set(re.findall(r"nono-pi ([a-z][a-z-]+)", code))
    unknown = referenced - set(COMMANDS)
    assert not unknown, f"SKILL.md references unknown nono-pi commands: {sorted(unknown)}"


def test_skill_documents_every_command():
    code = _code_spans(SKILL.read_text())
    referenced = set(re.findall(r"nono-pi ([a-z][a-z-]+)", code))
    missing = set(COMMANDS) - referenced
    assert not missing, f"SKILL.md never references these commands: {sorted(missing)}"
