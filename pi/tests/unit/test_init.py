import os

from nono_pi.cli.init import scaffold, main


def test_scaffold_creates_dirs_and_ledger(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    for d in ("input", "kgs", "draft"):
        assert (out / d).is_dir()
    assert (out / "pi_run.json").exists()


def test_scaffold_is_idempotent(tmp_path):
    out = tmp_path / "proj"
    scaffold(str(out))
    (out / "pi_run.json").write_text('{"schema_version": 1, "goal": "keep me"}')
    scaffold(str(out))  # must not overwrite an existing ledger
    assert "keep me" in (out / "pi_run.json").read_text()


def test_main_returns_zero(tmp_path):
    assert main([str(tmp_path / "proj")]) == 0
