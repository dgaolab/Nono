# Knowledge Graph Query Agent

You answer questions against one or more knowledge graphs by searching for relevant nodes, reading their content, optionally augmenting with external sources, and synthesizing a cited answer. Good answers can be filed back as synthesis nodes so explorations compound in the knowledge base.

## Input

Parse `$ARGUMENTS` for:
- **query** (required): The question to answer. First positional argument, quoted.
- **--kg <folder>** (optional): Specific KG folder to search. If omitted, scan the current directory for all `KG_*` folders and search all of them.
- **--augment** (optional flag): Enable external source augmentation (PubMed, bioRxiv, ClinicalTrials.gov, ChEMBL). Default: off.
- **--file** (optional flag): After answering, file the answer as a new synthesis node in the KG.
- **--top <N>** (optional): Max number of nodes to read in full. Default: 10.
- **--evidence-min <tier>** (optional): Minimum evidence tier to include (e.g., `--evidence-min cohort` excludes case_report, review, opinion). Default: no filter.
- **--include-quarantined** (optional flag): Include quarantined (failed evaluation) nodes in search results. Default: off — quarantined nodes are excluded.

Example invocations:
```
/query-kg "What is the role of SCN1A mutations in Dravet syndrome?"
/query-kg "How do mTOR inhibitors compare to gene therapy for TSC?" --kg KG_TSC
/query-kg "Latest findings on CRISPR delivery vectors" --augment --file
/query-kg "Mechanisms of drug resistance in AML" --kg KG_AML --top 15 --evidence-min cohort
/query-kg "What happened to the retracted claims about X?" --include-quarantined
```

If no arguments are provided, ask the user for a question.

---

## Phase 1: KG Resolution and Index Loading

### If `--kg` is specified:
1. Verify the folder exists and contains a `manifest.json`. If not, error: "KG folder not found or has no manifest.json."
2. Read `manifest.json`.

### If `--kg` is NOT specified:
1. Scan the current directory for all folders matching `KG_*` using Glob.
2. For each, verify `manifest.json` exists. Collect valid KG folders.
3. If zero KGs found, error: "No knowledge graphs found in the current directory. Run /build-kg first to create one."

Report: "Searching N knowledge graph(s): KG_X, KG_Y (M total nodes)."

Collect the list of manifest paths for Phase 2.

---

## Phase 2: Node Relevance Search

Run the search script to rank all nodes by relevance to the query:

```bash
python3 scripts/search_nodes.py "{query}" {manifest_path_1} [{manifest_path_2} ...] --top {top_value} --compact [--include-quarantined]
```

Only add `--include-quarantined` if the user passed that flag. By default, quarantined nodes are excluded from results.

If `--evidence-min` was specified, add: `--evidence-min {tier}`

Parse the JSON output.

### Score cliff heuristic
After receiving ranked results, apply a score-cliff cutoff:
- Walk the results in order. If score drops by more than 50% between consecutive results (e.g., 0.82 → 0.31), cut off at the cliff.
- The hard cap is `--top` regardless.
- Always read at least 1 node if any matched.

If zero results matched, proceed to Phase 4 (augmentation) if `--augment` is set. Otherwise report: "No relevant nodes found for this query. Consider running with --augment to search external sources, or run /build-kg to expand the knowledge graph."

---

## Phase 3: Deep Reading

For each node in the post-cliff result set, read the full content:

```bash
python3 scripts/parse_node.py {kg_folder}/nodes/{node_file}
```

Read the returned JSON — the `frontmatter` dict and `body` string contain the full detail, evidence, and related concepts.

### Context hops
After reading the top nodes, check their `relationships` and `related_nodes` fields:
- If a high-scoring node has a `depends_on` or `mechanism_of` relationship pointing to a node NOT already in the read set, read that node too.
- Limit: up to 3 additional context-hop nodes total.
- These hops provide prerequisite knowledge that makes the answer more complete.

### Cross-KG awareness
If any read node has `cross_kg_links` in its frontmatter, note these for the report. Do NOT traverse into other KGs (that would explode scope). Instead, mention them in the "Related" section of the answer.

---

## Phase 4: External Source Augmentation (conditional)

**Trigger**: Only if `--augment` flag is present, OR if Phase 2 returned zero nodes (automatic fallback).

### Source routing
Classify the query using the same signals as `/build-kg` Phase 1a-bis:

| Signal in query | Additional source | MCP tools |
|-----------------|-------------------|-----------|
| Therapy, treatment, clinical, trial, phase I/II/III | **ClinicalTrials.gov** | `search_trials`, `get_trial_details` |
| Drug name, compound, inhibitor, IC50, binding | **ChEMBL** | `compound_search`, `target_search`, `get_bioactivity`, `get_mechanism` |
| Basic science, mechanism, pathway, genetics only | None beyond PubMed | |

Always include PubMed and bioRxiv.

### PubMed (always)
1. Call `mcp__plugin_pubmed_PubMed__search_articles` with the query (max_results: 5, sorted by relevance).
2. For the top 3 results, call `mcp__plugin_pubmed_PubMed__get_article_metadata` to get title, abstract, authors, journal, year.
3. Note: Do NOT get full text — this is a query, not a build. Abstracts suffice.

### bioRxiv (always)
1. Call `mcp__plugin_biorxiv_bioRxiv__search_preprints` with the query, limited to the last 6 months.
2. Note up to 3 relevant preprints. **Flag them explicitly as not peer-reviewed.**

### ClinicalTrials.gov (if routed)
1. Call `mcp__plugin_clinical-trials_ClinicalTrials__search_trials` with condition/intervention from the query.
2. For top 3, call `mcp__plugin_clinical-trials_ClinicalTrials__get_trial_details`.

### ChEMBL (if routed)
1. Call `mcp__plugin_chembl_ChEMBL__compound_search` or `mcp__plugin_chembl_ChEMBL__target_search` as appropriate.
2. For top hits, call `mcp__plugin_chembl_ChEMBL__get_mechanism` or `mcp__plugin_chembl_ChEMBL__get_bioactivity`.

**Important**: Cap total augmentation MCP calls at 10 to keep response time reasonable. Batch calls efficiently — no more than 5 in parallel.

---

## Phase 5: Answer Synthesis

Synthesize the answer from all gathered information. Every factual claim must have an inline citation `[N]`.

### Answer structure:

```markdown
=== Query Result ===
Question: {the user's question}
KG(s) searched: KG_X, KG_Y (M total nodes)
Nodes consulted: N (P direct, Q context hops)

## Answer

{Synthesized narrative answer, 2-5 paragraphs. Written in scientific style.
Every factual claim has an inline citation [1], [2], etc. Present both
sides of any contradictions found.}

## Evidence Summary

| # | Source | Title | Evidence Tier | Status |
|---|--------|-------|---------------|--------|
| 1 | KG_X / node_005 | SCN1A Loss-of-Function | cohort | passed |
| 2 | KG_X / node_012 | Sodium Channel Pharmacogenomics | rct | passed |
| 3 | PubMed (augmented) | PMID 39876543 | — | not in KG |

## Citations

### From Knowledge Graph
- [1] [[node_005_scn1a_lof_dravet]] (KG_SCN1A_Epilepsy) — PMID 35486828, PMID 36123456
- [2] [[node_012_sodium_pharmacogenomics]] (KG_SCN1A_Epilepsy) — PMID 37654321

### From External Sources (supplementary)
- [3] PMID 39876543: "New findings on SCN1A variant spectrum" (Author et al., 2026, *Epilepsia*)
- [4] bioRxiv 10.1101/2026.03.15.123456: "Title" (Author et al., 2026) ⚠️ NOT PEER-REVIEWED

## Gaps and Limitations

{Note any aspects of the question NOT covered by the KG, nodes that
failed evaluation, weak evidence areas, and potential contradictions.}

## Related

- Cross-KG: [[KG_Dravet/nodes/node_008_clinical_presentation]] shares entity SCN1A
- Suggested follow-up: "What are the functional consequences of SCN1A truncating mutations?"
```

### Citation rules:
- KG nodes use wikilinks `[[node_file_slug]]` for Obsidian navigation.
- External PMIDs use standard format: Author et al., Year, *Journal*.
- bioRxiv preprints always carry a ⚠️ NOT PEER-REVIEWED warning.
- If all consulted nodes have `evaluation_status: "failed"`, add a prominent warning at the top of the answer.
- If any consulted node has `quarantined: true` (only possible when `--include-quarantined` was used), add a `> [!caution] Quarantined Evidence` callout before the answer explaining that some consulted nodes failed independent verification and their claims should be treated with extra skepticism.

### Contradiction handling:
If consulted nodes have `contradicts` relationships or `> [!debate]` callouts, present both sides fairly. Do not pick one side.

---

## Phase 6: File Back as Synthesis Node (conditional)

**Trigger**: Only if `--file` flag is present.

### Step 1: Determine target KG
- If only one KG was searched, use that.
- If multiple KGs were searched, file into the one that contributed the most source nodes. If tied, ask the user.

### Step 2: Create synthesis node

1. Read `manifest.json` to find the highest existing `node_XXX` ID. The new node gets the next sequential ID.
2. Derive a filename slug from the query (2-4 words, snake_case): `node_XXX_synthesis_slug.md`
3. Create the node file in `{kg_folder}/nodes/` with this structure:

```yaml
---
id: "node_XXX"
title: "Synthesis: {short title derived from query}"
tags: ["synthesis", "{primary_topic_tag}"]
evidence_tier: "{inherited from highest-tier source node}"
pubmed_ids:
  # Inherit PMIDs from all source nodes
  - pmid: "XXXXXXXX"
    supports: "Inherited from node_005"
    verified: false
    evidence_tier: "cohort"
entities:
  # Inherit entities from source nodes
related_nodes: ["node_005", "node_012"]
relationships:
  node_005: "derived_from"
  node_012: "derived_from"
synthesis_meta:
  query: "{the original question}"
  source_nodes: ["node_005", "node_012"]
  source_kgs: ["KG_X"]
  augmented: true
  augmented_sources: ["pubmed", "biorxiv"]
  filed_date: "{today's date}"
created: "{today's date}"
updated: "{today's date}"
evaluation_status: "pending"
---

# Synthesis: {Short Title}

## Summary

{One paragraph distillation of the synthesized answer.}

## Detail

{The full synthesized answer from Phase 5, adapted for node format.}

## Evidence

### Literature
{PMIDs inherited from source nodes, with inline evidence tier tags.}

## Synthesis Provenance

- **Query**: "{original question}"
- **Source nodes**: [[node_005_slug]], [[node_012_slug]]
- **Augmented**: Yes (PubMed, bioRxiv)
- **Filed**: {today's date}

## Related Concepts

- [[node_005_slug]] (derived from)
- [[node_012_slug]] (derived from)
```

### Step 3: Update manifest.json
1. Add the new node entry to `manifest.nodes[]` with standard fields (id, title, file, tags, summary, keywords, pubmed_ids, evaluation_status, evidence_tier, entities).
2. Add `derived_from` edges from the synthesis node to each source node in `manifest.edges[]`.
3. Increment `manifest.version`.
4. Update `manifest.updated` to today's date.

### Step 4: Update _index.md
Add the synthesis node under a "Synthesis" category. If no "Synthesis" section exists in `_index.md`, create one at the end (before the Graph Structure section):

```markdown
### Synthesis
- [[node_XXX_synthesis_slug]] - Query: "{short question}" (derived from node_005, node_012)
```

### Step 5: Validate
```bash
python3 scripts/validate_manifest.py {kg_folder}/manifest.json
```

Report: "Filed synthesis as node_XXX in KG_TopicName."

---

## Phase 7: Log and Report

Log the query operation:
```bash
python3 scripts/append_log.py {kg_folder} --op query --summary "{short query summary}" --details "Nodes consulted: N. Filed as: {node_id or 'not filed'}. Augmented: {yes/no}."
```

If multiple KGs were searched, log to each KG that had at least one consulted node.

Print a terminal summary:

```
=== Query Complete ===
KG(s): KG_X, KG_Y
Nodes consulted: 5 (3 direct, 2 context hops)
Augmented: yes (PubMed: 3, bioRxiv: 1)
Filed as: node_025 in KG_X (or: not filed)
```

---

## Important Rules

1. **Every citation must be traceable.** Inline `[N]` references must map to a specific node or external source in the Citations section. Never fabricate citation numbers.
2. **Do not hallucinate reference IDs.** Only use PMIDs from node files or MCP search results. Never invent IDs.
3. **Distinguish KG content from augmented content.** The answer must clearly separate what came from the knowledge graph vs external searches.
4. **Respect evaluation and quarantine status.** If a cited node has `evaluation_status: "failed"` or `quarantined: true`, note this in the Evidence Summary table. Quarantined nodes are excluded from search results by default — they only appear when `--include-quarantined` is used.
5. **bioRxiv preprints must be flagged** as not peer-reviewed every time they are cited.
6. **Synthesis nodes are regular nodes** with a `synthesis` tag. They participate in future searches, evaluations, and cross-KG linking normally.
7. **Cap augmentation at 10 MCP calls** to keep response time reasonable.
8. **Present contradictions fairly.** Never suppress one side of a `contradicts` relationship.
