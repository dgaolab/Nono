import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from render_digest import render


def update_record():
    return {
        "run_id": "2026-06-24T080012Z-v7", "kg_name": "KG_Topic", "mode": "update",
        "timestamp": "2026-06-24T08:00:12Z", "version": 7, "since_date": "2026-06-17",
        "preflight": {"novel_count": 9, "threshold": 3},
        "nodes_created": ["node_016"], "nodes_revised": ["node_003"],
        "refs_added": [{"pmid": "39876543", "nodes": ["node_003"]}],
        "refs_failed": [{"pmid": "00000001", "node": "node_005", "reason": "verification failed"}],
        "eval_summary": {"evaluated": 3, "passed": 2, "failed": 1},
        "cost_session_id": "abc-123",
    }


def eval_index():
    return {
        "node_016": {"node_id": "node_016", "overall_status": "passed", "pmid_checks": [
            {"pmid": "39876543", "article_title": "A study", "verdict": "supported",
             "quotes": [{"text": "Effect size was 0.4 (p<0.01).", "source": "abstract"}]}]},
        "node_003": {"node_id": "node_003", "overall_status": "passed", "pmid_checks": [
            {"pmid": "39876543", "article_title": "A study", "verdict": "partially_supported",
             "quotes": [{"text": "Benefit seen in a subgroup.", "source": "full_text"}]}]},
        "node_005": {"node_id": "node_005", "overall_status": "failed", "pmid_checks": []},
    }


def titles():
    return {"node_016": "New concept", "node_003": "Existing concept", "node_005": "Bad node"}


def stats():
    return {"total_nodes": 17, "active_nodes": 16, "quarantined_nodes": 1,
            "evidence_tier_distribution": {"rct": 3, "cohort": 2}}


def test_update_shows_verbatim_quotes_and_verdicts():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "pending", "session_id": "abc-123"})
    assert "Effect size was 0.4 (p<0.01)." in out          # verbatim quote, created node
    assert "Benefit seen in a subgroup." in out             # verbatim quote, revised node
    assert "partially_supported" in out                     # per-ref verdict preserved
    assert "New concept" in out and "Existing concept" in out


def test_update_has_failures_section():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "Failures" in out
    assert "00000001" in out and "verification failed" in out   # failed ref
    assert "node_005" in out                                    # failed-eval node


def test_outcome_line_counts():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "9 novel" in out
    assert "2/3 passed" in out


def test_cost_ok_renders_dollar_amount():
    cost = {"status": "ok", "est_cost_usd": 0.1234, "models": {"claude-opus-4-8": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0}}}
    out = render(update_record(), eval_index(), titles(), stats(), cost)
    assert "0.1234" in out


def test_cost_pending_and_unavailable_text():
    pend = render(update_record(), eval_index(), titles(), stats(), {"status": "pending", "session_id": "abc-123"})
    assert "pending" in pend and "abc-123" in pend
    unav = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "unavailable" in unav


def test_skip_mode_is_one_liner():
    rec = update_record()
    rec["mode"] = "skip"
    out = render(rec, {}, {}, stats(), {"status": "unavailable"})
    assert "below threshold 3" in out
    assert "Effect size" not in out          # no audit body
    assert "What changed" not in out


def test_build_mode_is_summary_no_quotes():
    rec = update_record()
    rec["mode"] = "build"
    rec["since_date"] = None
    rec["preflight"] = None
    out = render(rec, eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "node_016" in out                 # node listed
    assert "Effect size was 0.4 (p<0.01)." not in out   # no per-quote dump in build mode


def test_missing_eval_entry_does_not_crash():
    out = render(update_record(), {}, titles(), stats(), {"status": "unavailable"})
    assert "evaluation pending" in out.lower()


def test_render_is_deterministic():
    a = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    b = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert a == b


import json


def _make_kg(tmp_path, run_record, eval_log, manifest_stats, manifest_nodes):
    kg = tmp_path / "KG_Topic"
    kg.mkdir()
    (kg / "runs").mkdir()
    rr_path = kg / "runs" / (run_record["run_id"] + ".json")
    rr_path.write_text(json.dumps(run_record), encoding="utf-8")
    (kg / "_evaluation_log.json").write_text(json.dumps(eval_log), encoding="utf-8")
    (kg / "manifest.json").write_text(json.dumps(
        {"kg_name": "KG_Topic", "nodes": manifest_nodes, "statistics": manifest_stats}),
        encoding="utf-8")
    return str(kg), str(rr_path)


def test_generate_writes_digest_and_pointer(tmp_path):
    from render_digest import generate
    nodes = [{"id": "node_016", "title": "New concept"}, {"id": "node_003", "title": "Existing concept"}]
    kg, rr = _make_kg(tmp_path, update_record(),
                      list(eval_index().values()), stats(), nodes)
    cost_log = tmp_path / "_cost_log.jsonl"   # absent on purpose
    out_path = generate(kg, rr, str(cost_log), do_log=False)
    assert out_path.endswith("digests/2026-06-24T080012Z-v7.md")
    digest_text = open(out_path, encoding="utf-8").read()
    pointer_text = open(os.path.join(kg, "_digest.md"), encoding="utf-8").read()
    assert digest_text == pointer_text                  # latest pointer is a copy
    assert "Effect size was 0.4 (p<0.01)." in digest_text
    assert "Cost: unavailable" in digest_text           # no cost log file


def test_load_cost_statuses(tmp_path):
    from render_digest import load_cost
    missing = tmp_path / "nope.jsonl"
    assert load_cost(str(missing), "abc")["status"] == "unavailable"
    log = tmp_path / "_cost_log.jsonl"
    log.write_text(json.dumps({"session_id": "abc", "est_cost_usd": 0.5,
                               "models": {"m": {"input": 1, "output": 2}}}) + "\n", encoding="utf-8")
    assert load_cost(str(log), "abc")["status"] == "ok"
    assert load_cost(str(log), "other")["status"] == "pending"     # file present, no match
    assert load_cost(str(log), None)["status"] == "pending"        # no session id


def test_generate_survives_logging_failure(tmp_path, monkeypatch):
    import render_digest
    from render_digest import generate
    nodes = [{"id": "node_016", "title": "New concept"}, {"id": "node_003", "title": "Existing concept"}]
    kg, rr = _make_kg(tmp_path, update_record(), list(eval_index().values()), stats(), nodes)
    def boom(*a, **k):
        raise PermissionError("read-only fs")
    monkeypatch.setattr(render_digest, "append_entry", boom)
    out_path = generate(kg, rr, str(tmp_path / "_cost_log.jsonl"), do_log=True)  # must not raise
    assert os.path.exists(out_path)
    assert os.path.exists(os.path.join(kg, "_digest.md"))


def test_retractions_section_renders_when_present():
    rec = update_record()
    rec["retractions"] = [{"pmid": "99999", "nodes": ["node_003"], "action": "quarantined"}]
    out = render(rec, eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "Retractions" in out
    assert "99999" in out and "node_003" in out and "quarantined" in out


def test_no_retractions_section_when_absent():
    out = render(update_record(), eval_index(), titles(), stats(), {"status": "unavailable"})
    assert "## Retractions" not in out
