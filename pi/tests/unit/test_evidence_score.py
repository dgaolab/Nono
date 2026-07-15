import json

from nono_pi.cli.evidence_score import score_node, write_scores, _kg_slugs


def test_score_node_strong_vs_weak():
    strong, _ = score_node({"id": "node_1", "evidence_tier": "meta_analysis",
                            "pubmed_ids": ["1", "2", "3"], "evaluation_status": "passed"})
    weak, _ = score_node({"id": "node_2", "evidence_tier": "opinion",
                          "pubmed_ids": ["9"], "evaluation_status": "failed"})
    assert strong > weak
    assert 0.0 <= weak <= strong <= 1.0
    assert strong == 1.0  # meta_analysis * passed * not-quarantined * (0.5+0.5*1)


def test_score_node_quarantine_penalty():
    base, _ = score_node({"id": "n", "evidence_tier": "rct", "pubmed_ids": ["1", "2", "3"],
                          "evaluation_status": "passed"})
    quar, factors = score_node({"id": "n", "evidence_tier": "rct", "pubmed_ids": ["1", "2", "3"],
                                "evaluation_status": "passed", "quarantined": True})
    assert quar < base
    assert factors["quarantined"] is True


def test_score_node_missing_fields_use_defaults():
    s, factors = score_node({"id": "node_x"})
    assert factors["evidence_tier"] == "unclassified"
    assert factors["evaluation_status"] == "pending"
    assert factors["n_pmids"] == 0
    assert 0.0 <= s <= 1.0


def test_write_scores_and_slug_discovery(tmp_path):
    out = tmp_path / "proj"
    kg = out / "kgs" / "sub-a"
    kg.mkdir(parents=True)
    manifest = {"nodes": [
        {"id": "node_1", "evidence_tier": "rct", "pubmed_ids": ["1", "2"], "evaluation_status": "passed"},
        {"id": "node_2", "evidence_tier": "opinion", "pubmed_ids": [], "evaluation_status": "pending"},
    ]}
    (kg / "manifest.json").write_text(json.dumps(manifest))
    scores, mean = write_scores(str(out), "sub-a")
    assert set(scores) == {"node_1", "node_2"}
    assert (kg / "_evidence_score.json").exists()
    assert 0.0 <= mean <= 1.0
    assert _kg_slugs(str(out)) == ["sub-a"]  # only dirs with manifest.json
