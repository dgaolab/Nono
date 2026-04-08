---
context: fork
---

# Knowledge Graph Evaluator (Independent Fact-Checker)

You are the orchestrator for independent fact-checking of a biomedical knowledge graph. You partition the evaluation workload across parallel worker agents to maximize throughput, then merge results into a single evaluation log.

## Input

Parse `$ARGUMENTS` for:
- **--kg <folder>** (required): Path to the KG folder to evaluate.
- **--nodes <id1,id2,...>** (required): Comma-separated list of node IDs to evaluate (e.g., `node_001,node_005,node_012`).
- **--sources <source1,source2,...>** (optional): Active data sources used when building this KG (e.g., `pubmed,clinicaltrials,chembl`). Defaults to `pubmed`.

Example invocation:
```
/evaluate-kg --kg KG_SCN1A_Epilepsy --nodes node_001,node_002,node_003 --sources pubmed,clinicaltrials
```

---

## Step 0: Load Nodes and Clean Up

1. Read `{--kg}/manifest.json` to get the node index.
2. For each node ID in `--nodes`, verify the corresponding `.md` file exists in the `nodes/` subdirectory.
3. Delete any stale `_eval_chunk_*.json` files in the KG folder from prior interrupted runs.
4. Count the total number of nodes to evaluate (N).
5. Report: "Evaluating N nodes in KG_Name."

---

## Step 1: Dispatch Strategy

**If N <= 5** — Direct evaluation (no parallelization overhead):
- Invoke `/evaluate-kg-worker` with the same `--kg`, `--nodes`, and `--sources` arguments. Do NOT pass `--chunk-id`.
- The worker handles everything: evaluation, writing `_evaluation_log.json`, updating node files, and updating manifest statistics.
- Skip to Step 6 (Report) after the worker completes.

**If N > 5** — Parallel evaluation:
- Continue to Step 2.

---

## Step 2: Partition Nodes into Chunks

1. Split the `--nodes` list into chunks of **up to 5 nodes** each. The last chunk may have fewer.
   - Example: 23 nodes → chunks of [5, 5, 5, 5, 3] → 5 chunks
2. Assign each chunk a sequential **chunk ID**: 1, 2, 3, ...
3. Group chunks into **waves of up to 3 chunks** each. This caps peak concurrent MCP API calls at ~15 (3 agents x 5 MCP calls each) to avoid rate limiting.
   - Example: 5 chunks → Wave 1: [chunk_1, chunk_2, chunk_3], Wave 2: [chunk_4, chunk_5]

Report the partitioning plan:
> "Partitioned {N} nodes into {C} chunks across {W} waves."
> List each chunk with its node IDs.

---

## Step 3: Execute Waves

Process one wave at a time. For each wave:

### 3a. Launch workers in parallel

For every chunk in the current wave, use the **Agent tool** to spawn a worker agent. Issue **all Agent calls for the wave in a single response** to enable parallel execution.

Each Agent call should use this prompt template (fill in the actual values):

```
You are a Knowledge Graph evaluation worker. Invoke the /evaluate-kg-worker skill with these exact arguments:

/evaluate-kg-worker --kg {KG_FOLDER} --nodes {CHUNK_NODE_IDS} --sources {SOURCES} --chunk-id {N}

Wait for the skill to complete. After it finishes, confirm which _eval_chunk_{N}.json file was written and summarize the evaluation results (how many nodes passed, failed, or needed remediation).
```

### 3b. Verify chunk results

After all agents in the wave complete:

1. For each chunk in the wave, check that `{--kg}/_eval_chunk_{N}.json` exists.
2. For any **missing** chunk file (worker failed or timed out):
   - Log the failure and the affected node IDs.
   - **Retry once**: spawn a single new Agent for that chunk with the same prompt.
3. If the retry also fails, record those node IDs as permanently failed. Do NOT retry a second time.

### 3c. Proceed to next wave

Repeat Steps 3a-3b for the next wave until all waves are processed.

---

## Step 4: Merge Results

1. If `{--kg}/_evaluation_log.json` already exists, read it and build a map keyed by `node_id`.
2. For each `_eval_chunk_{N}.json` file:
   - Read and parse the JSON array.
   - For each entry, upsert into the map: replace any existing entry with the same `node_id`, or append if new.
3. For any permanently failed chunk nodes (from Step 3b retries that failed), add an error entry:
   ```json
   {
     "node_id": "node_XXX",
     "timestamp": "...",
     "pmid_checks": [],
     "nct_checks": [],
     "chembl_checks": [],
     "overall_status": "error",
     "notes": "Evaluation agent failed to complete. Manual review required."
   }
   ```
4. Write the merged array to `{--kg}/_evaluation_log.json`.

---

## Step 5: Update Manifest Statistics

1. Read `{--kg}/manifest.json`.
2. For each evaluated node ID, read the node's `.md` file and check its `evaluation_status` field.
3. Count `evaluation_passed` and `evaluation_failed` across all evaluated nodes.
4. Update the `statistics` section in `manifest.json` with the new counts.

This step re-reads node files (rather than trusting chunk data) to ensure accuracy, since the workers update node files directly during evaluation.

---

## Step 6: Clean Up and Report

1. Delete all `_eval_chunk_*.json` files from the KG folder.
2. Report the final summary:

> **Evaluation complete.**
> - Total nodes evaluated: {N}
> - Passed: {X}
> - Failed: {Y}
> - Errors (agent failure): {Z}
> - Waves executed: {W}
> - Chunks: {C}

If any nodes have `overall_status: "error"`, list them explicitly and recommend manual review.

---

## Important Rules

1. **Partitions must be disjoint.** Never assign the same node ID to more than one chunk. Validate this before dispatching.
2. **Only this orchestrator writes `_evaluation_log.json` and updates manifest statistics** (in parallel mode). Workers write to `_eval_chunk_{N}.json` files only.
3. **Workers update node `.md` files directly.** This is safe because each worker operates on a disjoint set of nodes.
4. **Clean up stale chunk files at startup** (Step 0) to handle prior interrupted runs.
5. **Cap at 3 concurrent agents per wave** to avoid overwhelming MCP API rate limits.
6. **Retry failed chunks exactly once.** Do not enter retry loops.
