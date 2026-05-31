from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_cli_stub():
    from cli import TribalCLI

    cli = TribalCLI.__new__(TribalCLI)
    cli._sudo_state = None
    cli._secret_state = None
    cli._approval_state = None
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._command_running = False
    cli._agent_running = False
    cli._voice_recording = False
    cli._voice_processing = False
    cli._voice_mode = False
    cli._command_spinner_frame = lambda: "o"
    cli._pending_input = SimpleNamespace(put=MagicMock())
    cli._console_lines = []
    cli._console_print = lambda text: cli._console_lines.append(str(text))
    return cli


def test_slash_lore_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "LORE OK"

    monkeypatch.setattr("tribal_cli.ritual.handle_lore_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/lore") is True
    assert calls == ["/lore"]
    assert cli._console_lines == ["LORE OK"]


def test_slash_outcome_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "OUTCOME OK"

    monkeypatch.setattr("tribal_cli.ritual.handle_outcome_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/outcome tk_1 shipped and got users") is True
    assert calls == ["/outcome tk_1 shipped and got users"]
    assert cli._console_lines == ["OUTCOME OK"]


def test_slash_falsify_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "FALSIFY OK"

    monkeypatch.setattr("tribal_cli.ritual.handle_falsify_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/falsify tk_1 nobody used it") is True
    assert calls == ["/falsify tk_1 nobody used it"]
    assert cli._console_lines == ["FALSIFY OK"]


def test_slash_ritual_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "RITUAL OK"

    monkeypatch.setattr("tribal_cli.ritual.handle_ritual_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/ritual review") is True
    assert calls == ["/ritual review"]
    assert cli._console_lines == ["RITUAL OK"]
