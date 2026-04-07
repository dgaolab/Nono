# Research Idea Improvement Harness

This project uses `program.md` to drive an autonomous multi-agent loop that improves research proposals through adversarial critique with 3 decomposed critic clusters.

## How to use
1. Copy `input/direction.md` (and optionally `input/preliminary_data.md`) to your own directory and fill them in
2. In `config/settings.json`, set `input_dir` to that directory and `output_dir` to where you want results written
   - Both accept absolute paths or paths relative to the project root
   - Defaults: `input_dir` = `input`, `output_dir` = `output`
   - The output directory is created automatically if it doesn't exist
3. Run with: `claude -p program.md`

Alternatively, edit the template files in `input/` directly (works the same as before, but those files will show up as local changes in git).

## Key conventions
- All tunable parameters (including `input_dir` and `output_dir`) are in `config/settings.json` — edit thresholds and paths there, nowhere else
- Proposals are versioned in `state/proposals/` as `v000.md`, `v001.md`, etc.
- Feedback is in `state/feedback/` as `vNNN_cluster1.md`, `vNNN_cluster2.md`, `vNNN_meta.md`
- `state/scoreboard.tsv` tracks iteration scores
- NEVER modify files in `config/` or the input directory during the loop
- Files in `input/` are templates — users should copy them to an external directory for actual use
- Use the Agent tool to spawn critic sub-agents
- Use `/grant-proposal-assistant` skill patterns for proposal structure

## MCP usage
- **PubMed**: Always used by Cluster 1 (WHAT) for literature grounding
- **ChEMBL / ClinicalTrials**: Only used by Cluster 2 (HOW) when the domain in `direction.md` involves drug targets, compounds, or clinical interventions
- **No MCPs** for Cluster 3 (META) — it reads Cluster 1+2 feedback instead

## Architecture
- **Cluster 1 (WHAT)**: Hypothesis clarity, significance, innovation — grounded by PubMed
- **Cluster 2 (HOW)**: Approach/methods, aims structure — conditionally grounded by ChEMBL/ClinicalTrials
- **Cluster 3 (META)**: Reviewer simulation + cross-cluster coherence — reads C1+C2 outputs
