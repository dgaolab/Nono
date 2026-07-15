from nono_pi.lib import ledger as L


def test_new_ledger_has_loop_state_and_validates():
    led = L.new_ledger("/out")
    assert led["aims_loop"] == {"status": "pending", "rounds": []}
    assert led["draft_loop"] == {"status": "pending", "rounds": []}
    L.validate_ledger(led)  # must not raise


def test_schema_backward_compatible_with_base_ledger():
    # A ledger written by the base module (no loop keys) must still validate.
    led = L.new_ledger("/out")
    del led["aims_loop"]
    del led["draft_loop"]
    L.validate_ledger(led)


def test_reconcile_leaves_loops_untouched(tmp_path):
    led = L.new_ledger(str(tmp_path))
    led["aims_loop"]["status"] = "in_progress"
    led["aims_loop"]["rounds"].append({"round": 0, "verdicts": {}, "weaknesses": [],
                                        "proposed_revision": "x", "decision": None})
    L.reconcile(str(tmp_path), led)
    assert led["aims_loop"]["status"] == "in_progress"
    assert len(led["aims_loop"]["rounds"]) == 1
