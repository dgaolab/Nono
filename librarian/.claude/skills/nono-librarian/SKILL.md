---
name: nono-librarian
description: >-
  Front door for the libririan PubMed knowledge-graph toolkit. Whichever agent
  is running (Claude or a local Hermes agent) drives the end-to-end KG curation
  itself — there is NO separate local model to set up, serve, or find. Use this
  whenever the user wants to build, update, query, evaluate, lint, or maintain a
  libririan KG (folders with manifest.json + nodes/) on their own machine, or
  mentions running the librarian "locally", "offline", "in the nono env", or
  "without the Claude PubMed MCP". The toolkit installs into a shared ~/.nono uv
  venv and is invoked as `$NONO_HOME/.venv/bin/nono-librarian <command>`.
---

# nono-librarian

The harness-agnostic front door to the libririan toolkit. Two jobs: **(1)
guarantee the shared `~/.nono` environment**, and **(2) dispatch** a request to
the right deterministic `nono-librarian` subcommand — while **you, the running
agent, do all the reasoning**. No Claude PubMed MCP, no served model.

## Step 1 — Guarantee the environment (always first)

`~/.nono` is the research-assistant home (a normal directory). It holds ONE
shared uv venv at `~/.nono/.venv`; nono-librarian is the module at
`~/.nono/librarian`, installed editable into that venv. Everything runs through
`$NONO_HOME/.venv/bin/nono-librarian`.

```bash
NONO_HOME="${NONO_HOME:-$HOME/.nono}"
mkdir -p "$NONO_HOME"
test -d "$NONO_HOME/.venv" || uv venv "$NONO_HOME/.venv" --python 3.14
test -d "$NONO_HOME/librarian" || git clone git@github.com:dgaolab/Nono.git "$NONO_HOME/librarian"
uv pip install --python "$NONO_HOME/.venv" -e "$NONO_HOME/librarian"
mkdir -p "$HOME/.claude/skills"
ln -sfn "$NONO_HOME/librarian/.claude/skills/nono-librarian" "$HOME/.claude/skills/nono-librarian"
```

Invoke the toolkit as `"$NONO_HOME/.venv/bin/nono-librarian" <command> …`.
Python 3.14 is the newest the embedding runtime (onnxruntime) ships wheels for.
`requirements` live in `pyproject.toml`; there is no `requirements.txt` and no conda.

## Step 2 — You are the reasoner (no model to find)

There is **no model discovery, no `LLM_*` env, no OpenAI/Anthropic endpoint**.
The agent reading this skill performs every reasoning step itself: search
planning, node design, per-node synthesis, relationship calls, and the
claim↔evidence judgment. The `nono-librarian` subcommands do only deterministic work —
retrieval, structural writes, the finish pipeline, and the verbatim-quote
guardrail. You hand them structured JSON.

The guardrail is enforced in Python regardless of what you decide: a claim you
mark `supported`/`partially_supported` is kept only if at least one of your
quotes appears **verbatim** in the article text; otherwise it is forced to
`not_supported`. Quote exactly.

## Step 3 — Dispatch by intent

Let `N="$NONO_HOME/.venv/bin/nono-librarian"`.

### Query / search a KG
`$N search "<query>" <KG>/manifest.json --top 10` ranks nodes (semantic +
lexical, no model). To answer in prose, read the top nodes and write the answer
yourself, citing PMIDs — do not fabricate beyond the returned summaries/quotes.
Refresh the index after node changes: `$N embeddings <KG>`.

### Maintain a KG
Deterministic, no reasoning: `$N lint <KG>`, `$N retractions <KG>`,
`$N chase <KG>`, `$N ledger <subcommand> <KG>`, `$N digest <KG>`,
`$N index <KG>`, `$N cross-index …`. Run any with `--help` first.

### Build or update a KG
1. **Plan** sub-queries for the topic yourself (breadth: narrow=3, medium=4,
   broad=6 sub-queries).
2. **Gather:** `$N gather "<topic>" --query "<q1>" --query "<q2>" … --breadth <b> [--since YYYY-MM-DD] --out _candidates.json`. For an UPDATE, pass `--since` and copy `_candidates.json` into the KG folder.
3. **Reason:** read `_candidates.json` and write `_nodes.json` — design nodes
   (each one citable claim), synthesize each from its articles, set relationships,
   and for every cited PMID give a `verdict` plus verbatim `quotes`. Conform to
   `~/.nono/librarian/src/nono_librarian/data/schemas/nodes_input_schema.json`.
4. **Assemble:** `$N assemble <KG> --nodes _nodes.json --topic "<topic>" --breadth <b>` (UPDATE: add `--start-id <next node number>`).
5. **Finalize:** `$N finalize <KG> --mode build` (UPDATE: `--mode update --version <v> --since <date>`). This runs ledger → tiers → stamping → guardrailed verdict writeback → quarantine → index → stats → validation → embeddings → run-record → digest → log.

### Evaluate / fact-check an existing KG
Read each node's claims and cited sources, judge support, and write a verdicts
file `{node_id: {pmid: {verdict, reasoning, quotes:[{text,source}]}}}`. Then
`$N verify <KG> --verdicts <file>`. The guardrail + writeback are identical to
finalize's evaluation step; a claim never passes without a verbatim quote.

## Scope (be honest)

- **Local, agent-driven, no model server:** build, update, query, prose answers,
  evaluation/fact-check, and all maintenance.
- **Not implemented:** ClinicalTrials.gov and ChEMBL as sources, entity-ID
  normalization across nodes, user-provided `--source` materials. PubMed is the
  only source.

Quality now tracks **whichever agent runs this** rather than a fixed local
model. The Python guardrail bounds the failure mode (no quote → no pass) but
cannot make a weak agent's synthesis strong.
