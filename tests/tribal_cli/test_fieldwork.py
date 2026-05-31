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


def _seed_lemma(home, lemma_id="tk_ship"):
    payload = {
        "id": lemma_id,
        "status": "folklore",
        "tribe_id": "personal.life",
        "claim": "Ship the demo.",
        "created_at": "2026-05-31T12:00:00Z",
        "source": {"type": "council", "council_id": "council_1", "role": "Keeper"},
        "evidence": [],
        "falsifiers": ["If the demo ships and nobody cares, weaken the decision."],
        "confidence": "draft",
        "promotion": {"status": "unvalidated"},
    }
    path = home / "lore" / "lemmas.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return payload


def _seed_experiment(home, field_id="field_1", lemma_id="tk_ship"):
    payload = {
        "schema_version": 1,
        "field_id": field_id,
        "tribe_id": "personal.life",
        "council_id": "council_1",
        "lemma_id": lemma_id,
        "status": "open",
        "question": "Should I ship the demo?",
        "decision": {
            "call": "Ship the demo.",
            "experiment": "Run the shortest feedback loop.",
            "confidence": "draft",
        },
        "experiment": "Run the shortest feedback loop.",
        "falsifiers": ["If the demo ships and nobody cares, weaken the decision."],
        "window": {"start": "2026-05-31T12:00:00Z", "end": "2026-06-07T12:00:00Z"},
        "sources": ["calendar"],
        "created_at": "2026-05-31T12:00:00Z",
        "closed_at": None,
    }
    path = home / "fieldwork" / "experiments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return payload


class TestFieldworkRuntime:
    def test_fieldwork_refuses_before_genesis(self, tmp_path):
        from tribal_cli.fieldwork import FieldworkNotBornError, run_field_list

        with pytest.raises(FieldworkNotBornError, match="Run `tribal genesis` first"):
            run_field_list(home=tmp_path)

    def test_tribe_ask_opens_fieldwork_for_draft_lemmas(self, tmp_path):
        from tribal_cli.tribe import run_tribe_ask

        _birth(tmp_path)

        def fake_delegate(args):
            if "tasks" in args:
                return json.dumps({
                    "results": [
                        {"task_index": 0, "status": "completed", "summary": json.dumps({"summary": "Ship the demo.", "draft_lemmas": []})},
                        {"task_index": 1, "status": "completed", "summary": json.dumps({"summary": "No lore yet.", "draft_lemmas": []})},
                        {"task_index": 2, "status": "completed", "summary": json.dumps({"summary": "Shipping creates the proof.", "draft_lemmas": []})},
                    ]
                })
            return json.dumps({
                "results": [{
                    "task_index": 0,
                    "status": "completed",
                    "summary": json.dumps({"summary": "What if nobody cares?", "draft_lemmas": []}),
                }]
            })

        result = run_tribe_ask(
            "Should I ship the demo?",
            home=tmp_path,
            delegate_runner=fake_delegate,
            now=_utc(),
        )

        experiments = _jsonl(tmp_path / "fieldwork" / "experiments.jsonl")
        assert len(experiments) == len(result.council["draft_lemmas"]) == 1
        assert experiments[0]["council_id"] == result.council["council_id"]
        assert experiments[0]["lemma_id"] == result.council["draft_lemmas"][0]
        assert experiments[0]["status"] == "open"
        assert experiments[0]["window"] == {
            "start": "2026-05-31T12:00:00Z",
            "end": "2026-06-07T12:00:00Z",
        }
        lineage_events = [row["event"] for row in _jsonl(tmp_path / "lineage.jsonl")]
        assert "fieldwork.opened" in lineage_events

    def test_field_list_and_show_support_json(self, tmp_path):
        from tribal_cli.fieldwork import render_fieldwork_result, run_field_list, run_field_show

        _birth(tmp_path)
        _seed_experiment(tmp_path)

        listed = run_field_list(home=tmp_path, status="open")
        shown = run_field_show("field_1", home=tmp_path)

        assert listed.status == "listed"
        assert listed.payload["experiments"][0]["field_id"] == "field_1"
        assert shown.payload["experiment"]["lemma_id"] == "tk_ship"
        assert json.loads(render_fieldwork_result(shown, json_output=True))["payload"]["experiment"]["field_id"] == "field_1"

    def test_calendar_json_observe_writes_report_without_mutating_lore(self, tmp_path):
        from tribal_cli.fieldwork import run_field_observe

        _birth(tmp_path)
        _seed_lemma(tmp_path)
        _seed_experiment(tmp_path)
        events_path = tmp_path / "events.json"
        events_path.write_text(
            json.dumps([
                {
                    "title": "Ship Tribal demo",
                    "start": "2026-06-01T10:00:00Z",
                    "end": "2026-06-01T12:00:00Z",
                    "source": "test",
                },
                {
                    "title": "Investor intro",
                    "start": "2026-06-02T15:00:00Z",
                    "end": "2026-06-02T15:30:00Z",
                },
            ]),
            encoding="utf-8",
        )

        result = run_field_observe(
            "field_1",
            adapter="calendar-json",
            input_path=events_path,
            home=tmp_path,
            now=_utc(1),
        )

        report = result.payload["reports"][0]
        assert report["recommendation"] == "recommend_confirm"
        assert report["observations"]["calendar"]["event_count"] == 2
        assert report["observations"]["calendar"]["demo_event_count"] == 1
        assert report["observations"]["calendar"]["investor_event_count"] == 1
        assert _jsonl(tmp_path / "fieldwork" / "reports.jsonl")[0]["report_id"] == report["report_id"]
        lemma = _jsonl(tmp_path / "lore" / "lemmas.jsonl")[0]
        assert lemma["promotion"]["status"] == "unvalidated"
        assert "outcomes" not in lemma

    def test_calendar_json_rejects_malformed_events_without_report(self, tmp_path):
        from tribal_cli.fieldwork import FieldworkError, run_field_observe

        _birth(tmp_path)
        _seed_experiment(tmp_path)
        events_path = tmp_path / "bad-events.json"
        events_path.write_text(json.dumps([{"title": "Missing dates"}]), encoding="utf-8")

        with pytest.raises(FieldworkError, match="Calendar event 1"):
            run_field_observe("field_1", adapter="calendar-json", input_path=events_path, home=tmp_path)

        assert not (tmp_path / "fieldwork" / "reports.jsonl").exists()

    def test_field_report_records_manual_evidence_without_applying_lore(self, tmp_path):
        from tribal_cli.fieldwork import run_field_report

        _birth(tmp_path)
        _seed_lemma(tmp_path)
        _seed_experiment(tmp_path)

        result = run_field_report(
            "field_1",
            evidence="I shipped the demo and three people asked for access.",
            recommend="confirm",
            home=tmp_path,
            now=_utc(2),
        )

        report = result.payload["report"]
        assert report["recommendation"] == "recommend_confirm"
        assert "three people" in report["evidence"]
        lemma = _jsonl(tmp_path / "lore" / "lemmas.jsonl")[0]
        assert lemma["promotion"]["status"] == "unvalidated"

    def test_cmd_field_observe_json(self, tmp_path, monkeypatch, capsys):
        from tribal_cli import fieldwork

        _birth(tmp_path)
        _seed_experiment(tmp_path)
        events_path = tmp_path / "events.json"
        events_path.write_text(
            json.dumps([{"title": "Ship block", "start": "2026-06-01T10:00:00Z", "end": "2026-06-01T11:00:00Z"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(fieldwork, "get_tribal_home", lambda: tmp_path)

        args = argparse.Namespace(
            field_command="observe",
            field_id="field_1",
            all=False,
            adapter="calendar-json",
            input=str(events_path),
            json=True,
        )

        assert fieldwork.cmd_field(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "observed"
        assert payload["payload"]["reports"][0]["recommendation"] == "recommend_confirm"
