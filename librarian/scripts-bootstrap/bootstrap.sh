#!/usr/bin/env bash
set -euo pipefail
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
test -d "$NONO_HOME/librarian" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
mkdir -p "$HOME/.claude/skills"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$HOME/.claude/skills/nono-librarian"
echo "nono-librarian ready: $NONO_HOME/.venv/bin/nono-librarian --help"
