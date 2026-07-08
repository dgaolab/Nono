from nono_pi.cli.__main__ import COMMANDS, main


def test_help_lists_commands(capsys):
    rc = main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage: nono-pi" in out
    for name in COMMANDS:
        assert name in out


def test_unknown_command_errors(capsys):
    rc = main(["bogus"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown command" in err


def test_no_args_prints_usage_and_errors():
    assert main([]) == 2
