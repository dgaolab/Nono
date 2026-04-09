# Knowledge Graph Lint — Health Checker

You run structural and semantic health checks on a knowledge graph to identify data integrity issues, quality gaps, and improvement opportunities. Structural checks are automated via a Python script; semantic checks use LLM reasoning on pre-computed candidates.

## Input

Parse `$ARGUMENTS` for:
- **kg_folder** (optional): Path to the KG folder to lint. If omitted, scan the current directory for `KG_*` folders and prompt the user to select one or lint all.
- **--fix** (optional flag): Auto-fix simple structural issues (stats drift, ledger drift, file-manifest drift).
- **--deep** (optional flag): Enable LLM-based semantic checks (potential contradictions, entity gaps, missing cross-references, stale claims). Without this, only structural checks run.
- **--stale-check** (optional flag): Search PubMed for newer evidence on nodes flagged as potentially stale. Requires `--deep`.

Example invocations:
```
/lint-kg KG_SCN1A_Epilepsy
/lint-kg KG_mRNA_Vaccines --fix
/lint-kg --deep --stale-check
/lint-kg KG_CRISPR --fix --deep
```

If `--stale-check` is given without `--deep`, warn the user and enable `--deep` automatically.

---

## Phase 1: KG Resolution

### If a KG folder is specified:
1. Verify the folder exists and contains `manifest.json`. If not, error.
2. Read `manifest.json` to confirm. Report: "Linting KG_TopicName (N nodes, M edges)."

### If no KG folder is specified:
1. Scan for all `KG_*` folders in the current directory.
2. If exactly one is found, use it.
3. If multiple are found, list them and ask the user which to lint (or offer to lint all sequentially).
4. If none found, error: "No knowledge graphs found."

---

## Phase 2: Structural Lint

Run the automated checks via the Python script. Use `--summary-only` to reduce output (omits the per-finding details array):

```bash
python3 scripts/lint_kg.py {kg_folder} --summary-only
```

If `--fix` was specified, add the `--fix` flag:
```bash
python3 scripts/lint_kg.py {kg_folder} --fix --summary-only
```

Parse the JSON output. With `--summary-only`, the script returns:
- `summary` — counts of errors, warnings, info, fixable, fixed
- `semantic_check_candidates` — pre-computed data for Phase 3

(The `findings[]` array is omitted to save tokens. If you need per-finding details — e.g., to list specific issues for the user — re-run without `--summary-only`.)

### Report structural results immediately

Report the counts from the `summary` object:

```
Structural: {errors} errors, {warnings} warnings, {info} info.
```

If `--fix` was used, report what was auto-fixed: "Auto-fixed: N issues."

If there are errors and the user needs to see specifics, re-run the script without `--summary-only` and group findings by severity:

**Errors** (must fix):
- List each error with its check_id and message.

**Warnings** (should investigate):
- List each warning with its check_id and message.

**Info** (suggestions):
- List each info finding briefly.

If there are structural errors and `--deep` was NOT specified, recommend: "Found N structural errors. Consider fixing them with --fix before running --deep semantic checks."

---

## Phase 3: Semantic Checks (requires `--deep`)

If `--deep` was not specified, skip this phase entirely and proceed to Phase 4.

Use the `semantic_check_candidates` from the script output to guide focused LLM analysis. Only read node files that appear in the candidates — do not read the entire KG.

### Check 11: Potential Contradictions

For each pair in `high_similarity_pairs`:
1. Read both node files via:
   ```bash
   python3 scripts/parse_node.py {kg_folder}/nodes/{file_a}
   python3 scripts/parse_node.py {kg_folder}/nodes/{file_b}
   ```
2. Compare the claims in each node's Summary and Detail sections.
3. Assess: Do these nodes make conflicting claims about the same phenomenon?
4. If yes, and they are not already linked with a `contradicts` edge, flag as a potential contradiction.

Report each finding:
```
Potential contradiction: node_005 ("SCN1A gain-of-function in GEFS+") vs 
node_012 ("SCN1A loss-of-function in Dravet"). These discuss related but 
opposing mechanisms. Recommend adding a 'contradicts' edge and [!debate] callouts.
```

### Check 12: Entity Gaps

For each entity in `frequent_entities_without_nodes`:
1. Verify that no existing node is specifically dedicated to this entity (check node titles and summaries from the manifest).
2. If a frequently referenced entity truly lacks its own node, suggest creating one.

Report:
```
Entity gap: SCN1A (HGNC:10585) is referenced by 5 nodes but has no dedicated 
concept node. Consider creating a node covering SCN1A gene function, expression, 
and clinical significance.
```

### Check 13: Missing Cross-References

For `high_similarity_pairs` that were NOT flagged as contradictions in Check 11:
1. Assess whether an edge should exist between them.
2. Determine the appropriate relationship type: `supports`, `related_to`, `mechanism_of`, `derived_from`, etc.
3. Only recommend edges where the connection is clear and valuable.

Report:
```
Missing edge: node_003 → node_015 (relationship: mechanism_of). 
Node_003 describes the mTOR pathway; node_015 describes rapamycin's 
mechanism of action via mTOR inhibition.
```

### Check 14: Stale Claims

For each node in `old_pmid_nodes`:
1. Assess the topic pace: is this a fast-moving field (e.g., immunotherapy, CRISPR) or a stable one (e.g., anatomy, classical biochemistry)?
2. If the topic is fast-moving and the newest PMID is >3 years old, flag as potentially stale.

If `--stale-check` is enabled, actively search for newer evidence:
1. For each stale candidate, call `mcp__plugin_pubmed_PubMed__search_articles` with the node's keywords, `date_from` set to 2 years ago, `max_results: 3`.
2. If newer relevant articles are found, flag the node with the specific PMIDs.

Report:
```
Stale evidence: node_008 ("CAR-T Manufacturing Advances") — newest PMID is 
from 2022 in a rapidly evolving field. Found 2 newer articles:
  - PMID 39876543 (2025): "Next-generation CAR-T production platforms"
  - PMID 39654321 (2026): "Automated CAR-T cell manufacturing"
Recommend running /build-kg in UPDATE mode to incorporate newer evidence.
```

**Important**: Cap PubMed calls for stale checks at 5 total nodes to avoid excessive API usage. Prioritize the stalest nodes (oldest newest-PMID year) if there are more candidates.

---

## Phase 4: Report and Log

### Write lint report

Write `_lint_report.json` to the KG folder. This file is overwritten on each run (it's a point-in-time snapshot). The `findings` array requires per-finding detail. If the summary shows errors or warnings, run:
```bash
python3 scripts/lint_kg.py {kg_folder}
```
(without `--summary-only`) and use its `findings` array for the report. If the summary shows zero errors and zero warnings, set `findings` to `[]`.

```json
{
  "kg_name": "KG_TopicName",
  "timestamp": "2026-04-08T...",
  "structural": {
    "checks_run": 10,
    "errors": 2,
    "warnings": 3,
    "info": 4,
    "fixed": 1,
    "findings": [...]
  },
  "semantic": {
    "checks_run": 4,
    "contradictions": [...],
    "entity_gaps": [...],
    "missing_edges": [...],
    "stale_claims": [...]
  }
}
```

### Validate (if --fix was used)

If auto-fixes were applied, verify the manifest is still valid:
```bash
python3 scripts/validate_manifest.py {kg_folder}/manifest.json
```

### Log the operation

```bash
python3 scripts/append_log.py {kg_folder} --op lint --summary "Structural: {E} errors, {W} warnings, {I} info. Semantic: {deep or skipped}. Fixed: {N}."
```

### Terminal summary

```
=== KG Lint Complete ===
KG: KG_TopicName/
Structural checks: 10 run
  Errors: 2 (dangling_edges: 1, file_manifest_drift: 1)
  Warnings: 3 (orphan_nodes: 2, under_referenced: 1)
  Info: 4 (tag_coverage_gaps: 2, evidence_tier_imbalance: 1, duplicate_entities: 1)
  Auto-fixed: 1 (stats_drift)
Semantic checks: 4 run (--deep)
  Potential contradictions: 1
  Entity gaps: 2
  Missing cross-references: 3
  Stale claims: 1 (--stale-check: 2 newer PMIDs found)
Report: _lint_report.json
```

If `--deep` was not used:
```
=== KG Lint Complete ===
KG: KG_TopicName/
Structural checks: 10 run
  Errors: 0
  Warnings: 2 (orphan_nodes: 1, under_referenced: 1)
  Info: 3
Semantic checks: skipped (use --deep to enable)
Report: _lint_report.json
```

---

## Important Rules

1. **Structural checks are always run.** They are fast and automated. Never skip them.
2. **Semantic checks require `--deep`** and are more expensive (node file reads + optional PubMed calls). They are opt-in.
3. **`--fix` only auto-fixes deterministic issues**: stats recomputation, ledger sync, adding orphan files to manifest. It never deletes nodes, removes edges, or modifies node content.
4. **Semantic findings are suggestions**, not commands. Present them as recommendations with reasoning, not as automated fixes.
5. **Cap stale-check PubMed calls at 5** to avoid excessive API usage.
6. **`_lint_report.json` is overwritten each run.** For historical tracking, users have `_log.md` and git history.
7. **Do not read all node files in Phase 3.** Only read the specific nodes identified by `semantic_check_candidates` to keep the operation efficient.
