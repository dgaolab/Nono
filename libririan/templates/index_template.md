---
kg_name: KG_TopicName
topic: "Full topic description"
total_nodes: 0
last_updated: "YYYY-MM-DD"
---

# Knowledge Graph: Topic Name

## Overview

Brief 2-3 sentence summary of what this knowledge graph covers and its scope.

## Nodes

### Category 1
- [[node_001_concept_a]] - Short description
- [[node_002_concept_b]] - Short description

### Category 2
- [[node_003_concept_c]] - Short description

## Statistics

- Total nodes: 0
- Total unique references: 0
- Evaluation: 0 passed, 0 failed

## Graph Structure

<!-- For small graphs (<30 nodes), use a flat diagram: -->
```mermaid
graph TD
    node_001[Concept A] -->|is_part_of| node_003[Concept C]
    node_002[Concept B] -->|supports| node_001[Concept A]
```

<!-- For medium graphs (30-50 nodes), use subgraph clusters grouped by primary tag: -->
<!--
```mermaid
graph TD
    subgraph Category1["Category 1"]
        node_001[Concept A]
        node_002[Concept B]
    end
    subgraph Category2["Category 2"]
        node_003[Concept C]
        node_004[Concept D]
    end
    node_001 -->|is_part_of| node_003
    node_002 -->|supports| node_001
```
-->

<!-- For large graphs (50+ nodes), use a category-level overview diagram plus per-category detail diagrams in collapsible sections: -->
<!--
```mermaid
graph TD
    Cat1["Category 1 (5 nodes)"] -->|cross-category| Cat2["Category 2 (8 nodes)"]
```

<details><summary>Category 1 detail</summary>

```mermaid
graph TD
    node_001[Concept A] -->|supports| node_002[Concept B]
```

</details>
-->
