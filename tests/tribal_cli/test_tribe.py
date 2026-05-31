import json
from types import SimpleNamespace
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

    def test_tribe_ask_convenes_roles_and_persists_folklore(self, tmp_path, monkeypatch):
        import cli as cli_module
        from tribal_cli.tribe import render_tribe_result, run_tribe_ask

        _birth(tmp_path)
        monkeypatch.setitem(cli_module.CLI_CONFIG, "delegation", {
            "max_iterations": 50,
            "child_timeout_seconds": 600,
        })
        calls = []

        def fake_delegate(args):
            assert cli_module.CLI_CONFIG["delegation"]["max_iterations"] == 1
            assert cli_module.CLI_CONFIG["delegation"]["child_timeout_seconds"] == 30
            calls.append(args)
            if "tasks" in args:
                assert [task["role_name"] for task in args["tasks"]] == ["Scout", "Elder", "Oracle"]
                assert args["tasks"][0]["toolsets"] == []
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

        assert cli_module.CLI_CONFIG["delegation"]["max_iterations"] == 50
        assert cli_module.CLI_CONFIG["delegation"]["child_timeout_seconds"] == 600
        assert result.status == "convened"
        assert len(calls) == 2
        assert result.council["tribe_id"] == "personal.life"
        assert [role["name"] for role in result.council["roles"]] == ["Scout", "Elder", "Oracle", "Skeptic", "Keeper"]
        assert result.council["roles"][-1]["summary"].startswith("Call:")
        assert result.council["consensus"]["decision"]["call"]
        assert result.council["consensus"]["decision"]["experiment"]
        assert result.council["consensus"]["falsifiers"]
        assert result.council["consensus"]["confidence"] == "draft"
        assert result.council["draft_lemmas"]

        council_events = _jsonl(tmp_path / "council" / "sessions.jsonl")
        assert council_events[0]["council_id"] == result.council["council_id"]
        lineage_events = _jsonl(tmp_path / "lineage.jsonl")
        assert lineage_events[-1]["event"] == "council.convened"
        assert lineage_events[-1]["council_id"] == result.council["council_id"]

        lemmas = _jsonl(tmp_path / "lore" / "lemmas.jsonl")
        assert len(lemmas) == 1
        assert {lemma["status"] for lemma in lemmas} == {"folklore"}
        assert {lemma["promotion"]["status"] for lemma in lemmas} == {"unvalidated"}
        assert all(lemma["source"]["council_id"] == result.council["council_id"] for lemma in lemmas)
        assert lemmas[0]["source"]["role"] == "Keeper"
        assert lemmas[0]["falsifiers"] == result.council["consensus"]["falsifiers"]

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

    def test_lore_context_is_compact(self):
        from tribal_cli.tribe import _lore_context

        context = _lore_context([{
            "id": "tk_big",
            "status": "folklore",
            "claim": "A" * 500 + "\n" + "B" * 500,
            "evidence": [{"large": "ignored"}],
        }])

        assert "tk_big" in context
        assert "evidence" not in context
        assert len(context) < 500

    def test_role_summary_parses_fenced_json(self):
        from tribal_cli.tribe import _parse_role_summary

        summary, payload = _parse_role_summary(
            'The council is too certain.\n```json\n{"summary":"Push harder.","draft_lemmas":["Consensus needs dissent."]}\n```'
        )

        assert summary == "Push harder."
        assert payload["draft_lemmas"] == ["Consensus needs dissent."]

    def test_keeper_closes_with_skeptic_falsifier(self):
        from tribal_cli.tribe import _build_closure

        roles = [
            {"name": "Scout", "summary": "The framing says chasing investors vs. locked in shipping. Ship the demo."},
            {"name": "Elder", "summary": "Ship the demo. Demo-first fundraising wins when proof is missing."},
            {"name": "Oracle", "summary": "Ship the demo. The artifact creates leverage."},
            {
                "name": "Skeptic",
                "summary": (
                    "What if the demo ships and nobody cares? "
                    "If it is three weeks from done, the calculus flips. "
                    "The binary frame may itself be the false choice."
                ),
            },
        ]

        closure = _build_closure("Ship or meet?", roles)

        assert closure["decision"]["call"] == "Ship the demo."
        assert "shortest feedback loop" in closure["decision"]["experiment"]
        assert "demo ships and nobody cares" in closure["falsifiers"][0]
        assert any("three weeks from done" in item for item in closure["falsifiers"])
        assert closure["keeper_role"]["name"] == "Keeper"

    def test_empty_skeptic_gets_fallback_challenge(self):
        from tribal_cli.tribe import _ensure_skeptic_challenge

        roles = [
            {"name": "Scout", "summary": "Ship the demo.", "status": "completed"},
            {"name": "Elder", "summary": "Ship the demo.", "status": "completed"},
            {"name": "Oracle", "summary": "Ship the demo.", "status": "completed"},
            {"name": "Skeptic", "summary": "", "status": "timeout"},
        ]

        updated = _ensure_skeptic_challenge(
            "Should I ship the Tribal demo?",
            roles,
        )

        skeptic = updated[-1]
        assert skeptic["name"] == "Skeptic"
        assert skeptic["status"] == "fallback"
        assert "demo ships and nobody cares" in skeptic["summary"]

    def test_falsifiers_are_deduplicated(self):
        from tribal_cli.tribe import _extract_falsifiers

        falsifiers = _extract_falsifiers(
            "What if the council is solving the wrong problem? "
            "Treat the decision as an experiment and define the result that would prove it wrong."
        )

        assert falsifiers == ["If the council is solving the wrong problem, weaken or revise this council decision."]

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

    def test_tribe_ask_with_simulate_attaches_oracle_receipt(self, tmp_path, monkeypatch):
        import tribal_cli.tribe as tribe_mod
        from tribal_cli.tribe import run_tribe_ask

        _birth(tmp_path)

        def fake_oracle_simulate(**kwargs):
            assert kwargs["scenario"] == "Should I ship the demo?"
            assert kwargs["source_council_id"].startswith("council_")
            return SimpleNamespace(payload={"simulation": {
                "oracle_id": "oracle_1",
                "provider": "mirofish",
                "status": "completed",
                "assumptions": ["The demo can ship this week."],
                "weighted_outcomes": [{"label": "Warm intros", "probability": 0.7}],
                "deciding_factor": "First-session clarity.",
                "signal_to_watch": "Three users ask for access within seven days.",
            }})

        monkeypatch.setattr(tribe_mod, "run_oracle_simulate", fake_oracle_simulate)

        def fake_delegate(args):
            if "tasks" in args:
                oracle = args["tasks"][2]
                assert "MiroFish Oracle receipt" in oracle["context"]
                assert "oracle_1" in oracle["context"]
                assert "Three users ask for access" in oracle["context"]
                return json.dumps({
                    "results": [
                        {"task_index": 0, "status": "completed", "summary": json.dumps({"summary": "Ship the demo.", "draft_lemmas": []})},
                        {"task_index": 1, "status": "completed", "summary": json.dumps({"summary": "No lore yet.", "draft_lemmas": []})},
                        {"task_index": 2, "status": "completed", "summary": json.dumps({"summary": "Oracle simulation favors shipping.", "draft_lemmas": []})},
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
            simulate=True,
            now=_utc(),
        )

        assert result.council["oracle"]["oracle_id"] == "oracle_1"
        assert result.council["consensus"]["oracle"]["signal_to_watch"] == "Three users ask for access within seven days."
        experiments = _jsonl(tmp_path / "fieldwork" / "experiments.jsonl")
        assert experiments[0]["oracle_id"] == "oracle_1"
        assert experiments[0]["signal_to_watch"] == "Three users ask for access within seven days."

    def test_cli_tribe_ask_uses_real_init_path(self, tmp_path, monkeypatch, capsys):
        import tribal_cli.tribe as tribe_mod

        _birth(tmp_path)
        monkeypatch.setattr(tribe_mod, "get_tribal_home", lambda: tmp_path)
        init_calls = []

        class FakeCLI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.agent = None

            def _ensure_runtime_credentials(self):
                return True

            def _resolve_turn_agent_config(self, _message):
                return {
                    "model": "deepseek-v4-pro",
                    "runtime": {"provider": "deepseek"},
                    "request_overrides": {"reasoning": "minimal"},
                    "signature": ("deepseek-v4-pro", "deepseek"),
                }

            def _init_agent(self, **kwargs):
                init_calls.append(kwargs)
                self.agent = object()
                return True

        monkeypatch.setattr("cli.TribalCLI", FakeCLI)
        monkeypatch.setattr(
            tribe_mod,
            "run_tribe_ask",
            lambda question, agent: SimpleNamespace(
                status="convened",
                home=tmp_path,
                council={
                    "tribe_id": "personal.life",
                    "council_id": "council_test",
                    "question": question,
                    "roles": [],
                    "consensus": {"answer": "ship the demo"},
                    "draft_lemmas": [],
                },
            ),
        )

        code = tribe_mod.cmd_tribe(SimpleNamespace(
            tribe_command="ask",
            question=["Should", "I", "ship", "the", "demo?"],
            json=False,
            toolsets=None,
            model=None,
            provider=None,
            max_turns=None,
            ignore_rules=False,
        ))

        assert code == 0
        assert init_calls == [{
            "model_override": "deepseek-v4-pro",
            "runtime_override": {"provider": "deepseek"},
            "request_overrides": {"reasoning": "minimal"},
        }]
        assert "ship the demo" in capsys.readouterr().out
