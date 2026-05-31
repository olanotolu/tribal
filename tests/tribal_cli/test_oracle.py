import argparse
import json
from datetime import datetime, timezone

import pytest


def _utc(second: int = 0) -> datetime:
    return datetime(2026, 5, 31, 12, 0, second, tzinfo=timezone.utc)


def _jsonl(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _birth(home):
    from tribal_cli.genesis import run_genesis

    return run_genesis(domain="personal.life", home=home, now=_utc())


class FakeMiroFishClient:
    def __init__(self, *, health=True, prediction=None, error=None):
        self.health_value = health
        self.prediction = prediction or {
            "provider": "mirofish",
            "status": "completed",
            "mirofish": {"project_id": "proj_1", "simulation_id": "sim_1", "report_id": "report_1"},
            "assumptions": ["The demo can be shipped this week."],
            "weighted_outcomes": [{"label": "Demo creates warm intros", "probability": 0.62}],
            "deciding_factor": "Whether users can try the demo immediately.",
            "signal_to_watch": "At least three qualified users ask for access within seven days.",
            "raw_report": {"report_id": "report_1"},
        }
        self.error = error
        self.health_calls = 0
        self.run_calls = []

    def health(self):
        self.health_calls += 1
        if self.error:
            raise self.error
        return self.health_value

    def run_prediction(self, *, scenario, seed_text, horizon_days, wait):
        self.run_calls.append({
            "scenario": scenario,
            "seed_text": seed_text,
            "horizon_days": horizon_days,
            "wait": wait,
        })
        if self.error:
            raise self.error
        return self.prediction


class TestOracleRuntime:
    def test_oracle_status_reports_not_configured(self, monkeypatch):
        from tribal_cli.oracle import run_oracle_status

        monkeypatch.delenv("TRIBAL_MIROFISH_BASE_URL", raising=False)

        result = run_oracle_status()

        assert result.status == "not_configured"
        assert result.payload["provider"] == "mirofish"
        assert result.payload["configured"] is False

    def test_oracle_status_checks_mirofish_health(self, monkeypatch):
        from tribal_cli.oracle import run_oracle_status

        monkeypatch.setenv("TRIBAL_MIROFISH_BASE_URL", "http://mirofish.local")
        client = FakeMiroFishClient(health=True)

        result = run_oracle_status(client=client)

        assert result.status == "configured"
        assert client.health_calls == 1
        assert result.payload["base_url"] == "http://mirofish.local"

    def test_oracle_status_reports_unreachable(self, monkeypatch):
        from tribal_cli.oracle import run_oracle_status

        monkeypatch.setenv("TRIBAL_MIROFISH_BASE_URL", "http://mirofish.local")

        result = run_oracle_status(client=FakeMiroFishClient(error=RuntimeError("down")))

        assert result.status == "unreachable"
        assert "down" in result.payload["error"]

    def test_mirofish_client_calls_existing_api_shape(self):
        from tribal_cli.oracle import MiroFishClient

        calls = []

        class FakeTransport:
            def get_json(self, path):
                calls.append(("GET", path, None))
                if path == "/health":
                    return {"status": "ok"}
                if path == "/api/graph/task/task_graph":
                    return {"success": True, "data": {"status": "completed", "progress": 100}}
                if path == "/api/graph/project/proj_1":
                    return {"success": True, "data": {"graph_id": "graph_1"}}
                if path == "/api/report/report_1":
                    return {
                        "success": True,
                        "data": {
                            "report_id": "report_1",
                            "assumptions": ["Users can try the demo."],
                            "weighted_outcomes": [{"label": "Warm intros", "probability": 0.7}],
                            "deciding_factor": "First-session clarity.",
                            "signal_to_watch": "Three users ask for access.",
                        },
                    }
                raise AssertionError(path)

            def post_json(self, path, payload):
                calls.append(("POST", path, payload))
                if path == "/api/graph/build":
                    return {"success": True, "data": {"task_id": "task_graph"}}
                if path == "/api/simulation/create":
                    return {"success": True, "data": {"simulation_id": "sim_1"}}
                if path == "/api/simulation/prepare":
                    return {"success": True, "data": {"task_id": "task_prepare", "status": "ready"}}
                if path == "/api/simulation/prepare/status":
                    return {"success": True, "data": {"status": "ready", "progress": 100}}
                if path == "/api/simulation/start":
                    return {"success": True, "data": {"runner_status": "running"}}
                if path == "/api/report/generate":
                    return {
                        "success": True,
                        "data": {"task_id": "task_report", "report_id": "report_1", "status": "generating"},
                    }
                if path == "/api/report/generate/status":
                    return {
                        "success": True,
                        "data": {"status": "completed", "progress": 100, "report_id": "report_1"},
                    }
                raise AssertionError(path)

            def post_multipart(self, path, *, fields, files):
                calls.append(("MULTIPART", path, {"fields": fields, "files": [name for name, *_ in files]}))
                assert "simulation_requirement" in fields
                assert files[0][0] == "files"
                return {"success": True, "data": {"project_id": "proj_1"}}

        client = MiroFishClient("http://mirofish.local", transport=FakeTransport())

        assert client.health() is True
        result = client.run_prediction(
            scenario="Should I ship?",
            seed_text="Seed",
            horizon_days=7,
            wait=True,
        )

        assert result["status"] == "completed"
        assert result["mirofish"]["project_id"] == "proj_1"
        assert result["mirofish"]["simulation_id"] == "sim_1"
        assert result["mirofish"]["report_id"] == "report_1"
        assert result["assumptions"] == ["Users can try the demo."]
        assert [method_path[:2] for method_path in calls] == [
            ("GET", "/health"),
            ("MULTIPART", "/api/graph/ontology/generate"),
            ("POST", "/api/graph/build"),
            ("GET", "/api/graph/task/task_graph"),
            ("GET", "/api/graph/project/proj_1"),
            ("POST", "/api/simulation/create"),
            ("POST", "/api/simulation/prepare"),
            ("POST", "/api/simulation/prepare/status"),
            ("POST", "/api/simulation/start"),
            ("POST", "/api/report/generate"),
            ("POST", "/api/report/generate/status"),
            ("GET", "/api/report/report_1"),
        ]

    def test_oracle_simulate_without_mirofish_writes_text_fallback(self, tmp_path, monkeypatch):
        from tribal_cli.oracle import run_oracle_simulate

        _birth(tmp_path)
        monkeypatch.delenv("TRIBAL_MIROFISH_BASE_URL", raising=False)

        result = run_oracle_simulate(
            "Should I ship the demo?",
            home=tmp_path,
            source_council_id="council_1",
            now=_utc(),
        )

        record = result.payload["simulation"]
        assert record["provider"] == "text_fallback"
        assert record["status"] == "completed"
        assert "No real MiroFish simulation was run" in record["assumptions"][0]
        assert record["source"]["council_id"] == "council_1"
        assert _jsonl(tmp_path / "oracle" / "simulations.jsonl")[0]["oracle_id"] == record["oracle_id"]
        assert _jsonl(tmp_path / "lineage.jsonl")[-1]["event"] == "oracle.simulated"

    def test_oracle_simulate_with_mirofish_writes_receipt(self, tmp_path, monkeypatch):
        from tribal_cli.oracle import run_oracle_simulate

        _birth(tmp_path)
        monkeypatch.setenv("TRIBAL_MIROFISH_BASE_URL", "http://mirofish.local")
        client = FakeMiroFishClient()

        result = run_oracle_simulate(
            "Should I ship the demo?",
            seed_text="Prior council context",
            horizon_days=14,
            wait=True,
            home=tmp_path,
            source_council_id="council_1",
            client=client,
            now=_utc(),
        )

        record = result.payload["simulation"]
        assert client.run_calls == [{
            "scenario": "Should I ship the demo?",
            "seed_text": "Prior council context",
            "horizon_days": 14,
            "wait": True,
        }]
        assert record["provider"] == "mirofish"
        assert record["status"] == "completed"
        assert record["signal_to_watch"] == "At least three qualified users ask for access within seven days."
        assert record["mirofish"]["report_id"] == "report_1"

    def test_oracle_show_reads_record(self, tmp_path, monkeypatch):
        from tribal_cli.oracle import run_oracle_show, run_oracle_simulate

        _birth(tmp_path)
        monkeypatch.delenv("TRIBAL_MIROFISH_BASE_URL", raising=False)
        created = run_oracle_simulate("Should I ship?", home=tmp_path, now=_utc()).payload["simulation"]

        shown = run_oracle_show(created["oracle_id"], home=tmp_path)

        assert shown.payload["simulation"]["oracle_id"] == created["oracle_id"]

    def test_cmd_oracle_status_json(self, monkeypatch, capsys):
        from tribal_cli import oracle

        monkeypatch.delenv("TRIBAL_MIROFISH_BASE_URL", raising=False)

        code = oracle.cmd_oracle(argparse.Namespace(oracle_command="status", json=True))

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "not_configured"
