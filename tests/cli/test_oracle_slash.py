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


def test_slash_oracle_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "ORACLE OK"

    monkeypatch.setattr("tribal_cli.oracle.handle_oracle_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/oracle status") is True
    assert calls == ["/oracle status"]
    assert cli._console_lines == ["ORACLE OK"]


def test_slash_oracle_simulate_calls_shared_engine(monkeypatch):
    from cli import TribalCLI

    cli = _make_cli_stub()
    calls = []

    def fake_handle(command):
        calls.append(command)
        return "SIMULATED OK"

    monkeypatch.setattr("tribal_cli.oracle.handle_oracle_slash_command", fake_handle)

    assert TribalCLI.process_command(cli, "/oracle simulate Should I ship? --horizon-days 7") is True
    assert calls == ["/oracle simulate Should I ship? --horizon-days 7"]
    assert cli._console_lines == ["SIMULATED OK"]
