import json
from datetime import datetime, timezone

import pytest
import yaml


def _utc(second: int = 0) -> datetime:
    return datetime(2026, 5, 31, 12, 0, second, tzinfo=timezone.utc)


def _jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _birth(home):
    from tribal_cli.genesis import run_genesis

    return run_genesis(domain="personal.life", home=home, now=_utc())


class TestTribeCouncilRuntime:
    def test_tribe_ask_refuses_before_genesis(self, tmp_path):
        from tribal_cli.tribe import TribeNotBornError, run_tribe_ask

        with pytest.raises(TribeNotBornError, match="Run `tribal genesis` first"):
            run_tribe_ask("Should I take Thursday at 4?", home=tmp_path, delegate_runner=lambda _args: "{}")

    def test_tribe_ask_convenes_roles_and_persists_folklore(self, tmp_path):
        from tribal_cli.tribe import render_tribe_result, run_tribe_ask

        _birth(tmp_path)
        calls = []

        def fake_delegate(args):
            calls.append(args)
            if "tasks" in args:
                assert [task["role_name"] for task in args["tasks"]] == ["Scout", "Elder", "Oracle"]
                elder = args["tasks"][1]
                assert "No existing lore" in elder["context"]
                return json.dumps({
                    "results": [
                        {
                            "task_index": 0,
                            "status": "completed",
                            "duration_seconds": 0.1,
                            "summary": json.dumps({
                                "summary": "Scout sees a crowded Thursday afternoon.",
                                "draft_lemmas": [{
                                    "claim": "Thursday afternoons tend to be poor deep-work windows.",
                                    "confidence": 0.72,
                                    "status": "canon",
                                }],
                            }),
                        },
                        {
                            "task_index": 1,
                            "status": "completed",
                            "duration_seconds": 0.1,
                            "summary": json.dumps({
                                "summary": "Elder found no lore yet and refuses borrowed authority.",
                                "draft_lemmas": [],
                            }),
                        },
                        {
                            "task_index": 2,
                            "status": "completed",
                            "duration_seconds": 0.1,
                            "summary": json.dumps({
                                "summary": "Oracle projects that accepting creates launch slippage.",
                                "draft_lemmas": [{
                                    "claim": "Late-day meetings can push launch prep into the next day.",
                                    "confidence": 0.66,
                                }],
                            }),
                        },
                    ]
                })
            assert "Skeptic" in args["goal"]
            assert "Scout sees" in args["context"]
            return json.dumps({
                "results": [{
                    "task_index": 0,
                    "status": "completed",
                    "duration_seconds": 0.1,
                    "summary": json.dumps({
                        "summary": "Skeptic accepts only folklore; no canon is justified.",
                        "draft_lemmas": [],
                    }),
                }]
            })

        result = run_tribe_ask(
            "Should I take Thursday at 4?",
            home=tmp_path,
            delegate_runner=fake_delegate,
            now=_utc(),
        )

        assert result.status == "convened"
        assert len(calls) == 2
        assert result.council["tribe_id"] == "personal.life"
        assert [role["name"] for role in result.council["roles"]] == ["Scout", "Elder", "Oracle", "Skeptic"]
        assert result.council["consensus"]["confidence"] == "draft"
        assert result.council["draft_lemmas"]

        council_events = _jsonl(tmp_path / "council" / "sessions.jsonl")
        assert council_events[0]["council_id"] == result.council["council_id"]
        lineage_events = _jsonl(tmp_path / "lineage.jsonl")
        assert lineage_events[-1]["event"] == "council.convened"
        assert lineage_events[-1]["council_id"] == result.council["council_id"]

        lemmas = _jsonl(tmp_path / "lore" / "lemmas.jsonl")
        assert {lemma["status"] for lemma in lemmas} == {"folklore"}
        assert {lemma["promotion"]["status"] for lemma in lemmas} == {"unvalidated"}
        assert all(lemma["source"]["council_id"] == result.council["council_id"] for lemma in lemmas)

        payload = json.loads(render_tribe_result(result, json_output=True))
        assert payload["council"]["lineage_event_id"] == result.council["lineage_event_id"]
        assert payload["council"]["draft_lemmas"] == result.council["draft_lemmas"]

    def test_tribe_status_and_roles_read_home(self, tmp_path):
        from tribal_cli.tribe import render_tribe_result, run_tribe_roles, run_tribe_status

        _birth(tmp_path)
        (tmp_path / "lore" / "lemmas.jsonl").write_text(
            '{"id":"tk_1","status":"folklore"}\n',
            encoding="utf-8",
        )
        (tmp_path / "council").mkdir()
        (tmp_path / "council" / "sessions.jsonl").write_text('{"council_id":"c_1"}\n', encoding="utf-8")

        status = run_tribe_status(home=tmp_path)
        roles = run_tribe_roles(home=tmp_path)

        assert status.status == "status"
        assert status.council["tribe_id"] == "personal.life"
        assert status.council["lore_count"] == 1
        assert status.council["council_count"] == 1
        assert [role["name"] for role in roles.council["roles"]] == ["Scout", "Elder", "Oracle", "Skeptic", "Keeper"]
        assert "personal.life" in render_tribe_result(status)

    def test_law_limits_draft_lemmas(self, tmp_path):
        from tribal_cli.tribe import run_tribe_ask

        _birth(tmp_path)
        law = yaml.safe_load((tmp_path / "law.yaml").read_text(encoding="utf-8"))
        law["folklore"]["max_drafts_per_council"] = 1
        (tmp_path / "law.yaml").write_text(yaml.safe_dump(law), encoding="utf-8")

        def fake_delegate(args):
            if "tasks" in args:
                drafts = [
                    {"claim": "One.", "confidence": 0.5},
                    {"claim": "Two.", "confidence": 0.5},
                ]
                return json.dumps({
                    "results": [
                        {"task_index": 0, "status": "completed", "summary": json.dumps({"summary": "s", "draft_lemmas": drafts})},
                        {"task_index": 1, "status": "completed", "summary": json.dumps({"summary": "e", "draft_lemmas": drafts})},
                        {"task_index": 2, "status": "completed", "summary": json.dumps({"summary": "o", "draft_lemmas": drafts})},
                    ]
                })
            return json.dumps({"results": [{"task_index": 0, "status": "completed", "summary": "skeptic"}]})

        result = run_tribe_ask("What pattern matters?", home=tmp_path, delegate_runner=fake_delegate, now=_utc())

        assert len(result.council["draft_lemmas"]) == 1
        assert len(_jsonl(tmp_path / "lore" / "lemmas.jsonl")) == 1
