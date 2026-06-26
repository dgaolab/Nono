#!/usr/bin/env bash
set -euo pipefail
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
# Shared uv venv for all ~/.nono modules.
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
# The toolkit is the librarian/ SUBDIRECTORY of the Nono repo, so clone the repo
# and point $NONO_HOME/librarian at that subdir (the editable-install target).
test -d "$NONO_HOME/Nono" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/Nono"
ln -sfn "$NONO_HOME/Nono/librarian" "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
# Make the skill globally available via a symlink to the repo (single source of
# truth). Replace a stale real directory; -sfn handles an existing symlink.
mkdir -p "$HOME/.claude/skills"
SKILL_LINK="$HOME/.claude/skills/nono-librarian"
[ -L "$SKILL_LINK" ] || rm -rf "$SKILL_LINK"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$SKILL_LINK"
echo "nono-librarian ready: $NONO_HOME/.venv/bin/nono-librarian --help"
