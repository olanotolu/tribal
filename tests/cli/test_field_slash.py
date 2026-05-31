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


def test_slash_field_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "FIELD OK"

    monkeypatch.setattr("tribal_cli.fieldwork.handle_field_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/field list") is True
    assert calls == ["/field list"]
    assert cli._console_lines == ["FIELD OK"]


def test_slash_field_observe_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "OBSERVED OK"

    monkeypatch.setattr("tribal_cli.fieldwork.handle_field_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/field observe field_1 --adapter calendar-json --input events.json") is True
    assert calls == ["/field observe field_1 --adapter calendar-json --input events.json"]
    assert cli._console_lines == ["OBSERVED OK"]
