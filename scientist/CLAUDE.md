# Research Idea Improvement Harness

This project uses `program.md` to drive an autonomous multi-agent loop that improves research proposals through adversarial critique with 3 decomposed critic clusters.

## How to use
1. Fill in `input/direction.md` with your research idea
2. Optionally fill in `input/preliminary_data.md`
3. Run with: `claude -p program.md`

## Key conventions
- All tunable parameters are in `config/settings.json` — edit thresholds there, nowhere else
- Proposals are versioned in `state/proposals/` as `v000.md`, `v001.md`, etc.
- Feedback is in `state/feedback/` as `vNNN_cluster1.md`, `vNNN_cluster2.md`, `vNNN_meta.md`
- `state/scoreboard.tsv` tracks iteration scores
- NEVER modify files in `config/` or `input/` during the loop
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
