# Cross-KG Linker

You scan multiple knowledge graphs and create bidirectional links between them based on shared references, shared entities, and overlapping concepts. This enables cross-domain discovery — the primary value of maintaining multiple related KGs.

## Input

Parse `$ARGUMENTS` for:
- **--kgs <folder1> <folder2> [...]** (optional): Specific KG folders to link. If omitted, scan the current directory for all `KG_*` folders.
- **--dry-run** (optional): Report potential links without writing any changes.

Example invocations:
```
/link-kg
/link-kg --kgs KG_SCN1A KG_Dravet_Syndrome
/link-kg --dry-run
```

If fewer than 2 KG folders are found, report: "Need at least 2 knowledge graphs to link. Found: [list]."

---

## Phase 1: Discovery

1. For each KG folder, read `manifest.json` and load the node index.
2. Build three cross-reference indices:

### Index A: Shared References (strongest signal)
Map each reference ID (PMID, NCT ID, ChEMBL ID) to the list of `(KG_name, node_id)` pairs that cite it.
- Extract PMIDs from each node's `pubmed_ids` array.
- Extract NCT/ChEMBL IDs from each node's `external_ids` array (if present).
- Any reference cited by nodes in 2+ different KGs is a cross-link candidate.

### Index B: Shared Entities (strong signal)
Map each `normalized_id` from node `entities` arrays to `(KG_name, node_id)` pairs.
- Only use entities with a `normalized_id` that does NOT start with `?` (uncertain normalizations are excluded).
- Any entity referenced by nodes in 2+ different KGs is a cross-link candidate.

### Index C: Topic Similarity (weak signal)
For each pair of KGs, compare node summaries and keywords for semantic overlap.
- This is a fallback for cases where neither shared references nor shared entities exist but the concepts are clearly related.
- Only flag high-confidence matches — err on the side of missing a link over creating a spurious one.

Report the index sizes: "Built cross-reference indices: X shared references, Y shared entities, Z topic similarity candidates."

---

## Phase 2: Link Identification

For each pair of KGs, identify cross-links using the indices. Process signals in priority order:

### Signal 1: Shared References (`cross_kg_shared_evidence`)
- For each reference ID cited by nodes in both KG_X and KG_Y, create a link between every (KG_X node, KG_Y node) pair that shares that reference.
- Record the shared reference IDs.

### Signal 2: Shared Entities (`cross_kg_shared_entity`)
- For each normalized entity referenced by nodes in both KG_X and KG_Y, create a link between every (KG_X node, KG_Y node) pair that shares that entity.
- Record the shared entity names and IDs.
- **Dedup**: If a node pair already has a Signal 1 link, do not create a duplicate Signal 2 link. The stronger signal takes precedence.

### Signal 3: Semantic Overlap (`cross_kg_related`)
- For remaining unlinked node pairs with high semantic similarity (matching keywords, closely related summaries), create a candidate link.
- Only create these if you are confident the connection is real and useful. When in doubt, list as "suggested" in the report but do not write.
- **Dedup**: Skip if the node pair already has a Signal 1 or 2 link.

### Controversy Detection
If a cross-KG link connects two nodes where one `supports` a claim and the other `contradicts` a related claim (or they directly contradict each other), flag this as a **cross-KG controversy**:
- Add `> [!debate]` callouts to both nodes' Detail sections summarizing the cross-KG conflict.
- Note the controversy in the cross-KG report.

---

## Phase 3: Write Links

If `--dry-run` was specified, skip this phase and go directly to Phase 4.

For each confirmed cross-KG link:

### 3a. Update node files (both sides)

Add to the node's YAML frontmatter:
```yaml
cross_kg_links:
  - kg: "KG_OtherTopic"
    node: "node_005"
    relationship: "cross_kg_shared_evidence"
    shared: ["PMID:35486828"]
```

Add to the node's "Cross-KG Links" section (create if it doesn't exist):
```markdown
## Cross-KG Links
- [[KG_OtherTopic/nodes/node_005_name]] (shared evidence: PMID 35486828)
```

If the node file has no "Cross-KG Links" section, add it before the final section of the file.

### 3b. Update manifests (both sides)

Add or update the `cross_kg_edges` array in each KG's `manifest.json`:
```json
{
  "local_node": "node_003",
  "remote_kg": "KG_Dravet",
  "remote_node": "node_012",
  "relationship": "cross_kg_shared_evidence",
  "shared_refs": ["35486828"]
}
```

Avoid duplicate entries — if a cross_kg_edge already exists for the same (local_node, remote_kg, remote_node) triple, update it rather than adding a duplicate.

### 3c. Generate cross-KG report

Write `_cross_kg_report.md` in each linked KG folder:

```markdown
---
generated: "YYYY-MM-DD"
linked_kgs: ["KG_SCN1A", "KG_Dravet"]
---

# Cross-KG Links: KG_TopicName

## Summary
- Total cross-KG links: N
- Linked to: KG_X (M links), KG_Y (K links)

## Links by Signal Type

### Shared Evidence (strongest)
| Local Node | Remote KG | Remote Node | Shared References |
|------------|-----------|-------------|-------------------|
| [[node_003]] | KG_Dravet | [[node_012]] | PMID 35486828 |

### Shared Entities
| Local Node | Remote KG | Remote Node | Shared Entities |
|------------|-----------|-------------|-----------------|
| [[node_005]] | KG_Dravet | [[node_008]] | SCN1A (HGNC:10585) |

### Semantic Overlap
| Local Node | Remote KG | Remote Node | Confidence |
|------------|-----------|-------------|------------|

## Controversies
<!-- List any cross-KG contradictions detected -->

## Suggested Links (not written)
<!-- High-potential but uncertain links for manual review -->
```

---

## Phase 4: Output

Print a terminal summary:

```
=== Cross-KG Linking Complete ===
KGs scanned: 3 (KG_SCN1A, KG_Dravet, KG_Sodium_Channels)
Links found: 12
  Shared evidence: 5
  Shared entities: 6
  Semantic overlap: 1
Controversies: 1
Nodes updated: 18
```

If `--dry-run` was used, append: "(dry run — no files modified)"

---

## Important Rules

1. **Links are always bidirectional.** If node A in KG_X links to node B in KG_Y, then node B must also link back to node A.
2. **Never delete existing cross-KG links.** Only add new ones or update shared_refs on existing ones.
3. **Signal priority**: shared evidence > shared entities > semantic overlap. Do not create a weaker link when a stronger one already exists for the same node pair.
4. **Uncertain entity normalizations** (prefixed with `?`) are excluded from entity-based linking.
5. **Wikilinks across KGs** use the format `[[KG_Name/nodes/node_XXX_slug]]` to enable Obsidian vault navigation.
6. **Be conservative with semantic overlap links.** False positives are worse than missed connections — users can always run `/link-kg` again after updating their KGs.
