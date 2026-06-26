---
id: "node_XXX"
title: "Descriptive title of this knowledge unit"
tags: ["category", "subcategory"]
evidence_tier: "unclassified"
pubmed_ids:
  - pmid: "XXXXXXXX"
    supports: "Specific claim this PMID backs"
    verified: true
    evidence_tier: "unclassified"
    # quotes added by the evaluator for verified refs — 1-3 verbatim excerpts:
    # quotes:
    #   - text: "Verbatim sentence from the source that backs the claim."
    #     source: "abstract"   # abstract | full_text
external_ids:
  # - source: "clinicaltrials"
  #   id: "NCT04000000"
  #   supports: "Phase III trial showing efficacy"
  # - source: "chembl"
  #   id: "CHEMBL25"
  #   supports: "IC50 = 3.2 nM against target X"
entities:
  # - name: "SCN1A"
  #   type: "gene"
  #   normalized_id: "HGNC:10585"
  # - name: "Dravet syndrome"
  #   type: "disease"
  #   normalized_id: "OMIM:607208"
related_nodes: ["node_YYY"]
relationships:
  node_YYY: "is_part_of"
created: "YYYY-MM-DD"
updated: "YYYY-MM-DD"
evaluation_status: "pending"
---

# Descriptive Title

## Summary

One paragraph distillation of this knowledge unit.

## Detail

Longer explanation with nuance. Can include multiple paragraphs covering the mechanism, context, and significance of this knowledge.

## Evidence

### Literature
- **PMID XXXXXXXX** (Author et al., Year, *Journal Name*) [evidence_tier]: Article title. *(Stamped by stamp_literature.py)*

### Clinical Trials
<!-- Include this section only if the node has ClinicalTrials.gov references -->
<!-- - **NCT04000000** (Phase III, Recruiting): Primary endpoint description. -->

### Compound Data
<!-- Include this section only if the node has ChEMBL references -->
<!-- - **CHEMBL25** (Aspirin): IC50 = 3.2 nM against COX-2. -->

## Entities
<!-- List normalized biomedical entities mentioned in this node -->
<!-- - **Gene**: SCN1A (HGNC:10585) -->
<!-- - **Disease**: Dravet syndrome (OMIM:607208) -->
<!-- - **Phenotype**: Epileptic encephalopathy (HP:0200134) -->

## Related Concepts

- [[node_YYY_parent_concept]] (is part of)

## Cross-KG Links
<!-- Populated by /link-kg command -->
<!-- - [[KG_OtherTopic/nodes/node_005_name]] (cross-KG: shared evidence via PMID 35486828) -->
