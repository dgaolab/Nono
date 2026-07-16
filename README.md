# Nono

Nono is a monorepo of **harness-agnostic, agent-driven research-assistant
modules** that share one local home (`~/.nono`) and one Python virtual
environment. Each module follows the same pattern:

- a **`SKILL.md` program** — the front door that the *running agent* (Claude
  Code, another frontier model, or a locally-hosted agent) reads and executes,
  doing all the reasoning itself; and
- a **thin, deterministic Python CLI** — installed into the shared venv, doing
  only mechanical work (file I/O, structural writes, bookkeeping, validation).
  **No module calls an LLM or a served model** — that keeps Nono model-agnostic.

## Modules

| Module | Package / CLI | What it does |
|--------|---------------|--------------|
| **nono-librarian** (`librarian/`) | `nono_librarian` / `nono-librarian` | Build, update, query, and maintain PubMed knowledge graphs (KGs) locally. |
| **nono-pi** (`pi/`) | `nono_pi` / `nono-pi` | PI orchestrator: intake a goal → decompose subtopics → drive `nono-librarian` to build KGs → hypothesis-evaluation loops → Significance & Innovation → draft/revise a grant or paper. |
| `scientist/` | — | Legacy standalone research-improvement harness (not part of the `~/.nono` install flow; kept for reference). |

Future modules (e.g. `nono-analyst`) install as siblings into the same shared
venv with no extra setup.

## How a local install is laid out

`~/.nono` is a normal directory — the shared "research-assistant home". You
clone this repo **once** and editable-install each module's subdirectory into
the **one** shared venv:

```
~/.nono/                      # NONO_HOME — the assistant home (a normal dir)
├── .venv/                    # ONE shared uv venv (Python 3.14); all modules install here
├── Nono/                     # this repo, cloned once (contains every module)
│   ├── librarian/
│   ├── pi/
│   └── ...
├── librarian -> Nono/librarian   # symlink → editable-install target
└── pi        -> Nono/pi          # symlink → editable-install target
```

Each module also ships its front-door skill at
`<module>/.claude/skills/<name>/SKILL.md`, which the install symlinks into
`~/.claude/skills/` so Claude Code discovers it.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — manages the venv and fetches Python 3.14:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **git**, with access to `dgaolab/Nono` (SSH `git@github.com:dgaolab/Nono.git`,
  or swap in the HTTPS URL below).
- **An agent harness that can read skills and run a shell** — Claude Code is the
  reference (it auto-discovers skills under `~/.claude/skills/`). Any other
  frontier or local agent works too: point its skill/instructions loader at the
  module's `SKILL.md` path.
- Python itself is **not** a prerequisite — `uv` provisions Python 3.14 for the venv.

## Install (deploy Nono locally)

Run this once. It is **idempotent** (safe to re-run) and installs **both**
current modules plus registers their skills. It's the same bootstrap embedded in
each module's `SKILL.md` Step 0, expanded to cover every module:

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"

# 1. One shared venv (Python 3.14) for all modules.
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14

# 2. Clone the monorepo once (contains every module).
#    HTTPS alternative: https://github.com/dgaolab/Nono.git
test -d "$NONO_HOME/Nono" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/Nono"

# 3. Point per-module symlinks at the repo subdirs, then editable-install
#    each into the shared venv. Source stays in the clone; edits/`git pull`
#    reflect immediately.
ln -sfn "$NONO_HOME/Nono/librarian" "$NONO_HOME/librarian"
ln -sfn "$NONO_HOME/Nono/pi"        "$NONO_HOME/pi"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian" -e "$NONO_HOME/pi"

# 4. Register each module's front-door skill for Claude Code.
mkdir -p "$HOME/.claude/skills"
for m in librarian pi; do
  name="nono-$m"
  link="$HOME/.claude/skills/$name"
  [ -L "$link" ] || rm -rf "$link"   # replace a stale real directory
  ln -sfn "$NONO_HOME/$m/.claude/skills/$name" "$link"
done

echo "Nono ready:"
echo "  $NONO_HOME/.venv/bin/nono-librarian --help"
echo "  $NONO_HOME/.venv/bin/nono-pi --help"
```

> A librarian-only bootstrap script also lives at
> `librarian/scripts-bootstrap/bootstrap.sh`.

### Verify

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
"$NONO_HOME/.venv/bin/nono-librarian" --help
"$NONO_HOME/.venv/bin/nono-pi" --help
```

In Claude Code, the skills appear as `nono-librarian` and `nono-pi` (restart
Claude Code once after the first install so it picks up the new skill symlinks).

## Usage

You don't call the CLIs by hand for normal work — you ask your agent, and it
follows the module's `SKILL.md`:

- **Claude Code:** invoke the skill (`nono-pi` or `nono-librarian`) or just
  describe the task; the agent reads the SKILL.md and drives the CLI.
- **Another agent/harness:** have it read the relevant
  `~/.nono/<module>/.claude/skills/<name>/SKILL.md` and follow it; the CLI is
  invoked as `$NONO_HOME/.venv/bin/<cli> <command>`.

The agent does the reasoning; the CLI does the deterministic work.

## Updating

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
git -C "$NONO_HOME/Nono" pull
# Editable installs reflect source changes automatically. Re-run only if a
# module's dependencies or entry points changed:
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian" -e "$NONO_HOME/pi"
```

New modules added to the repo: after `git pull`, add their `-e` install and skill
symlink (repeat steps 3–4 above for the new module).

## Notes

- `NONO_HOME` defaults to `~/.nono`; override it by exporting `NONO_HOME` before
  installing.
- The install never touches system Python and adds nothing to `PATH` — the venv
  binaries are invoked by absolute path (`$NONO_HOME/.venv/bin/...`).
- Model-agnostic by design: no module contains LLM/provider calls, so the same
  install works whether the driving agent is Claude, another frontier model, or
  a local one.
