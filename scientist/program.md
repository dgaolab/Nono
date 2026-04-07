# Research Idea Improvement Harness

An autonomous multi-agent loop that improves research proposals through adversarial critique with 3 decomposed evaluation clusters.

## Background

This harness takes a research direction (and optional preliminary data) and iteratively generates and improves a grant proposal through adversarial evaluation. Three independent critic clusters evaluate different dimensions, and a generator revises the proposal targeting the weakest dimension each round.

**Cluster 1 (WHAT)** — Conceptual Foundation: hypothesis clarity, significance, innovation. Grounded by PubMed.
**Cluster 2 (HOW)** — Technical Feasibility: approach/methods, aims structure. Conditionally grounded by ChEMBL/ClinicalTrials (only for drug/clinical domains).
**Cluster 3 (META)** — Holistic Review: reviewer simulation (3-pass) + cross-cluster coherence. Reads C1+C2 feedback.

## Setup

Before entering the loop, perform these setup steps:

1. **Verify input exists**: Read `input/direction.md`. If it still contains only the template placeholders (e.g., the Overall Idea section starts with `[1-2 paragraphs`), stop and tell the user to fill it in.
2. **Read settings**: Read `config/settings.json` and store the values:
   - `passing_threshold` (default 3.5)
   - `max_iterations` (default 8)
   - `convergence_delta` (default 0.2)
   - `convergence_patience` (default 2)
   - `adversarial_calibration` (calibration instruction for critics)
3. **Initialize state**: If `state/scoreboard.tsv` does not exist, create it with the header:
   ```
   iteration	cluster1_score	cluster2_score	meta_score	status	description
   ```
4. **Determine domain**: Read the `## Domain` section of `input/direction.md`. This determines which MCPs Cluster 2 will use:
   - If domain mentions drug targets, compounds, therapeutics, clinical interventions, pharmacology, or similar → Cluster 2 uses ChEMBL + ClinicalTrials
   - Otherwise → Cluster 2 skips ChEMBL/ClinicalTrials and evaluates feasibility from methods quality and literature alone
5. **Read preliminary data**: If `input/preliminary_data.md` exists and is not just the template, read it for use in initial generation.

Once setup is complete, enter the loop.

---

## The Improvement Loop

LOOP from iteration 0 to `max_iterations - 1`:

### Step 1: Read State

- Read `state/scoreboard.tsv` to understand iteration history.
- Determine the current iteration number N (count of existing data rows).
- If N == 0: the proposal does not exist yet — proceed to Step 1a (Initial Generation).
- If N > 0: read `state/proposals/v{N-1:03d}.md` as the current proposal — proceed to Step 2.

### Step 1a: Initial Generation (iteration 0 only)

Generate the first proposal draft from `input/direction.md` (and `input/preliminary_data.md` if available).

Structure the proposal using this template:

```
# Research Proposal: [Title]

## Specific Aims (1 page)

[OPENING PARAGRAPH - 5-7 sentences]
- Sentence 1: Hook — significance statement with numbers
- Sentence 2-3: Current state and limitation
- Sentence 4: Long-term goal
- Sentence 5: Central hypothesis (specific, testable, falsifiable)
- Sentence 6: Rationale (based on preliminary data or literature)
- Sentence 7: Overview of aims

**Aim 1: [Verb phrase]**
[2-3 sentences: what, why, expected outcome]

**Aim 2: [Verb phrase]**
[2-3 sentences: what, why, expected outcome]

**Aim 3 (optional): [Verb phrase]**
[2-3 sentences]

[CLOSING PARAGRAPH]
- Expected outcomes
- Impact statement
- Future directions

## Significance
[Why does this matter? What gap does it fill? What changes if successful?]

## Innovation
[What is new? Specify type: conceptual, methodological, or application-level]

## Approach
[For each aim: rationale, preliminary data, strategy, expected outcomes, potential pitfalls and alternatives, timeline]
```

Save the initial proposal as `state/proposals/v000.md`. Then proceed to Step 2.

### Step 2: Parallel Cluster Evaluation

Launch **two Agent sub-agents in parallel** using the Agent tool:

#### Agent 1: Cluster 1 (WHAT) Critic

Use this prompt for the Agent tool (adapt the iteration number and file paths):

```
You are the WHAT Critic — an adversarial evaluator of the conceptual foundation of a research proposal.

## Your task
1. Read the proposal: state/proposals/v{NNN}.md
2. Read the rubric: config/rubric_cluster1_what.json
3. Read the settings: config/settings.json — use the adversarial_calibration instruction
4. Use PubMed to ground your evaluation:
   - Search for recent publications that overlap with or contradict the hypothesis
   - Verify the stated knowledge gap is genuine
   - Check if innovation claims have already been published
   - Use the pubmed-search or pubmed-synthesis skills as appropriate
5. Score each criterion on the 1-5 scale defined in the rubric
6. Write your evaluation to: state/feedback/v{NNN}_cluster1.md

## Required output format (write to the feedback file)

## Cluster 1 Evaluation: WHAT (Conceptual Foundation)
### Scores
- Hypothesis Clarity: [score]/5 — [one-line rationale]
- Significance and Gap Assessment: [score]/5 — [one-line rationale]
- Innovation Evaluation: [score]/5 — [one-line rationale]
- Weighted Average: [compute as (HC*1.2 + SGA*1.0 + IE*1.0) / 3.2]

### Critical Weaknesses (prioritized)
1. [Weakness] — [specific location in proposal] — [suggested fix]
2. ...

### Strengths to Preserve
1. [Strength] — [why it works]

### PubMed-Grounded Evidence
- [PMID or search result that supports or contradicts proposal claims]

[adversarial_calibration from settings.json goes here as your scoring instruction]
```

#### Agent 2: Cluster 2 (HOW) Critic

Use this prompt for the Agent tool:

```
You are the HOW Critic — an adversarial evaluator of the technical feasibility and aims structure of a research proposal.

## Your task
1. Read the proposal: state/proposals/v{NNN}.md
2. Read the rubric: config/rubric_cluster2_how.json
3. Read the settings: config/settings.json — use the adversarial_calibration instruction
4. Read the domain from input/direction.md:
   - If the domain involves drug targets, compounds, or clinical interventions:
     Use ChEMBL to check target druggability and known bioactivity.
     Use ClinicalTrials to check existing trial landscape and competitive studies.
   - Otherwise: skip ChEMBL/ClinicalTrials. Evaluate feasibility from methods quality, preliminary data, and published literature alone.
5. Evaluate whether aims are independent (project survives if one fails)
6. Check that methods are established or supported by preliminary data
7. Score each criterion on the 1-5 scale defined in the rubric
8. Write your evaluation to: state/feedback/v{NNN}_cluster2.md

## Required output format (write to the feedback file)

## Cluster 2 Evaluation: HOW (Technical Feasibility)
### Scores
- Approach/Methods Review: [score]/5 — [one-line rationale]
- Specific Aims Structure: [score]/5 — [one-line rationale]
- Weighted Average: [compute as (AMR*1.2 + SAS*1.0) / 2.2]

### Critical Weaknesses (prioritized)
1. [Weakness] — [specific location in proposal] — [suggested fix]
2. ...

### Strengths to Preserve
1. [Strength] — [why it works]

### MCP-Grounded Evidence (if domain-appropriate)
- [ChEMBL/ClinicalTrials finding, or "Domain does not require ChEMBL/ClinicalTrials grounding"]

[adversarial_calibration from settings.json goes here as your scoring instruction]
```

**Important**: Launch both agents in a single message (two Agent tool calls) so they run in parallel.

### Step 3: Parse Scores and Decide

After both agents complete:

1. Read `state/feedback/v{NNN}_cluster1.md` — extract the Weighted Average score.
2. Read `state/feedback/v{NNN}_cluster2.md` — extract the Weighted Average score.
3. Append a row to `state/scoreboard.tsv`:
   ```
   {N}	{c1_score}	{c2_score}	-	evaluated	[brief description of what changed in this iteration]
   ```
4. **Decision logic** (read `passing_threshold`, `convergence_delta`, `convergence_patience` from settings):

   - **If BOTH c1_score >= passing_threshold AND c2_score >= passing_threshold**: Proceed to Step 4 (META evaluation).
   - **If N >= max_iterations**: Proceed to Step 6 (Finalize) with the best version so far.
   - **Check convergence**: Look at the last `convergence_patience` rows in scoreboard.tsv. If the total score improvement (c1 + c2 combined) has been < `convergence_delta` for `convergence_patience` consecutive rounds, proceed to Step 6 (Finalize).
   - **Otherwise**: Proceed to Step 5 (Revise), targeting the cluster with the lower score.

### Step 4: META Evaluation

Launch **one Agent sub-agent**:

```
You are the META Reviewer — a holistic reviewer who simulates a real NIH study section review and checks cross-cluster coherence.

## Your task
1. Read the proposal: state/proposals/v{NNN}.md
2. Read the rubric: config/rubric_cluster3_meta.json
3. Read the settings: config/settings.json — use the adversarial_calibration instruction
4. Read Cluster 1 feedback: state/feedback/v{NNN}_cluster1.md
5. Read Cluster 2 feedback: state/feedback/v{NNN}_cluster2.md
6. Perform the 3-pass reviewer simulation:
   - 2-minute skim: Read the Specific Aims section only. Can you identify the hypothesis, significance, and innovation within 2 minutes? Is it compelling enough to keep reading?
   - 15-minute read: Skim all sections. Note weaknesses in logic, feasibility concerns, missing controls, unclear methods. What would a critical reviewer flag?
   - Scoring pass: Assign NIH-style criterion scores for Significance, Innovation, Approach. What overall impact score (1-9) would this receive?
7. Check cross-cluster coherence: Do innovation claims (WHAT) match the methods (HOW)? Do the aims actually test the stated hypothesis? Have the weaknesses identified by C1 and C2 been reconciled?
8. Score each criterion on the 1-5 scale defined in the rubric
9. Write your evaluation to: state/feedback/v{NNN}_meta.md

## Required output format (write to the feedback file)

## Cluster 3 Evaluation: META (Holistic Reviewer Simulation)

### 3-Pass Simulation
**2-minute skim**: [What a reviewer sees/thinks in 2 minutes]
**15-minute read**: [Key weaknesses and concerns noted during deeper read]
**Scoring pass**: [NIH-style overall impact score 1-9, with criterion scores]

### Scores
- Reviewer Perspective: [score]/5 — [one-line rationale]
- Cross-Cluster Coherence: [score]/5 — [one-line rationale]
- Weighted Average: [compute as (RP*1.0 + CCC*1.0) / 2.0]

### Critical Weaknesses (prioritized)
1. [Weakness] — [specific location in proposal] — [suggested fix]
2. ...

### Coherence Issues
- [Any disconnect between WHAT claims and HOW methods]

### Strengths to Preserve
1. [Strength] — [why it works]

[adversarial_calibration from settings.json goes here as your scoring instruction]
```

After the META agent completes:

1. Read `state/feedback/v{NNN}_meta.md` — extract the Weighted Average score.
2. Update the current row in `state/scoreboard.tsv` to fill in the `meta_score` column.
3. **Decision logic**:
   - **If meta_score >= passing_threshold**: Proceed to Step 6 (Finalize). The proposal has passed all 3 clusters.
   - **Otherwise**: Proceed to Step 5 (Revise) with META feedback included.

### Step 5: Generator Revision

This is where you (the main orchestrator agent) revise the proposal.

1. Read ALL feedback files for the current iteration:
   - `state/feedback/v{NNN}_cluster1.md`
   - `state/feedback/v{NNN}_cluster2.md`
   - `state/feedback/v{NNN}_meta.md` (if it exists for this iteration)
2. Identify the **lowest-scoring cluster** from scoreboard.tsv.
3. Read the current proposal `state/proposals/v{NNN}.md`.
4. Revise the proposal with these priorities:
   - **First**: Address all Critical Weaknesses from the lowest-scoring cluster
   - **Second**: Address Critical Weaknesses from other clusters
   - **Third**: Preserve all identified Strengths
   - **Fourth**: Incorporate MCP-Grounded Evidence where it strengthens the proposal
5. For each critical weakness addressed, ensure the fix is specific and traceable — don't just acknowledge the weakness, change the proposal text.
6. Save the revised proposal as `state/proposals/v{N+1:03d}.md`.
7. Go back to Step 2 with the new iteration number.

### Step 6: Finalize

1. Identify the best proposal version: the one with the highest combined score (c1 + c2 + meta) in scoreboard.tsv. If meta_score is "-" for some iterations, use c1 + c2 only for comparison.
2. Copy the best proposal to `output/final_proposal.md`.
3. Generate `output/evolution_report.md` with:
   ```
   # Evolution Report

   ## Summary
   - Total iterations: [N]
   - Final scores: Cluster 1 = [X.XX], Cluster 2 = [X.XX], META = [X.XX]
   - Outcome: [passed all clusters / converged / max iterations reached]

   ## Iteration History
   [scoreboard.tsv formatted as a markdown table]

   ## Key Improvements by Iteration
   [For each iteration: what changed, what improved, what regressed]

   ## Remaining Weaknesses
   [Any unresolved issues from the final feedback files]

   ## Recommendations for Human Review
   [Areas that need human expertise: domain-specific judgments, collaborator decisions, budget details]
   ```
4. Print the final scoreboard to the console.
5. Stop the loop.

---

## Crash Recovery

If a critic sub-agent fails (returns an error or produces malformed output):
1. Check the error message.
2. If it's an MCP failure (PubMed/ChEMBL/ClinicalTrials unavailable): re-run the critic agent with a note to skip MCP grounding and evaluate on intrinsic quality alone.
3. If it's a parsing error: re-run the agent with a reminder about the exact output format.
4. If it fails twice: write a placeholder feedback file noting the failure and proceed with the other cluster's feedback only.

## Important Notes

- **Do NOT modify** any files in `config/` or `input/` during the loop.
- **Do NOT skip iterations** — every iteration must produce feedback files and update the scoreboard.
- **Do NOT inflate scores** — read and apply the `adversarial_calibration` instruction from settings.json in every critic prompt.
- All file paths are relative to the `research-improver/` project root.
