import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest


def _utc(day: int = 31, second: int = 0) -> datetime:
    return datetime(2026, 5, day, 12, 0, second, tzinfo=timezone.utc)


def _birth(home):
    from tribal_cli.genesis import run_genesis

    return run_genesis(domain="personal.life", home=home, now=_utc())


def _write_lemmas(home, *lemmas, malformed: str | None = None):
    path = home / "lore" / "lemmas.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(lemma, sort_keys=True) for lemma in lemmas]
    if malformed is not None:
        lines.append(malformed)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _jsonl(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and line.strip().startswith("{")
    ]


def _lemma(lemma_id="tk_ship", *, status="folklore", created_at=None, confirmations=0, falsifications=0):
    return {
        "id": lemma_id,
        "status": status,
        "tribe_id": "personal.life",
        "claim": "Ship the demo.",
        "created_at": created_at or _utc().isoformat().replace("+00:00", "Z"),
        "source": {"type": "council", "council_id": "council_1", "role": "Keeper"},
        "evidence": [{"role": "Scout", "summary": "Ship creates proof."}],
        "falsifiers": ["If the demo ships and nobody cares, weaken this belief."],
        "confidence": "draft",
        "promotion": {
            "status": "unvalidated",
            "confirmed_count": confirmations,
            "falsified_count": falsifications,
            "last_reviewed_at": None,
        },
    }


class TestRitualEngine:
    def test_lore_list_filters_by_status(self, tmp_path):
        from tribal_cli.ritual import run_lore_list

        _birth(tmp_path)
        _write_lemmas(
            tmp_path,
            _lemma("tk_folk", status="folklore"),
            _lemma("tk_canon", status="canon"),
            _lemma("tk_dead", status="falsified"),
        )

        default = run_lore_list(home=tmp_path)
        folklore = run_lore_list(home=tmp_path, status="folklore")
        all_rows = run_lore_list(home=tmp_path, status="all")

        assert [row["id"] for row in default.payload["lemmas"]] == ["tk_folk", "tk_canon"]
        assert [row["id"] for row in folklore.payload["lemmas"]] == ["tk_folk"]
        assert [row["id"] for row in all_rows.payload["lemmas"]] == ["tk_folk", "tk_canon", "tk_dead"]

    def test_lore_show_returns_one_lemma(self, tmp_path):
        from tribal_cli.ritual import run_lore_show

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_show"))

        result = run_lore_show("tk_show", home=tmp_path)

        assert result.status == "shown"
        assert result.payload["lemma"]["claim"] == "Ship the demo."

    def test_confirm_appends_outcome_without_auto_canon(self, tmp_path):
        from tribal_cli.ritual import run_lore_confirm

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship"))

        result = run_lore_confirm(
            "tk_ship",
            evidence="Shipped demo; three users tried it.",
            home=tmp_path,
            now=_utc(second=1),
        )

        assert result.status == "confirmed"
        updated = _jsonl(tmp_path / "lore" / "lemmas.jsonl")[0]
        assert updated["status"] == "folklore"
        assert updated["promotion"]["confirmed_count"] == 1
        assert updated["promotion"]["status"] == "unvalidated"
        assert updated["outcomes"][0]["kind"] == "confirm"
        outcomes = _jsonl(tmp_path / "lore" / "outcomes.jsonl")
        assert outcomes[0]["kind"] == "confirm"
        lineage = _jsonl(tmp_path / "lineage.jsonl")
        assert lineage[-1]["event"] == "lore.confirmed"

    def test_falsify_marks_lemma_falsified(self, tmp_path):
        from tribal_cli.ritual import run_lore_falsify

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship", confirmations=1))

        result = run_lore_falsify(
            "tk_ship",
            evidence="Shipped demo and nobody used it.",
            home=tmp_path,
            now=_utc(second=2),
        )

        assert result.status == "falsified"
        updated = _jsonl(tmp_path / "lore" / "lemmas.jsonl")[0]
        assert updated["status"] == "falsified"
        assert updated["promotion"]["status"] == "falsified"
        assert updated["promotion"]["falsified_count"] == 1
        assert updated["outcomes"][0]["kind"] == "falsify"
        lineage = _jsonl(tmp_path / "lineage.jsonl")
        assert lineage[-1]["event"] == "lore.falsified"

    def test_ritual_review_recommends_keep_promote_falsify_and_stale(self, tmp_path):
        from tribal_cli.ritual import run_ritual_review

        _birth(tmp_path)
        old = (_utc() - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        _write_lemmas(
            tmp_path,
            _lemma("tk_keep", confirmations=1),
            _lemma("tk_promote", confirmations=2),
            _lemma("tk_falsify", confirmations=2, falsifications=1),
            _lemma("tk_stale", created_at=old),
        )

        result = run_ritual_review(home=tmp_path, now=_utc())
        recommendations = {row["lemma_id"]: row["recommendation"] for row in result.payload["recommendations"]}

        assert recommendations == {
            "tk_keep": "keep_folklore",
            "tk_promote": "promote_to_canon",
            "tk_falsify": "falsify",
            "tk_stale": "mark_stale",
        }
        lineage = _jsonl(tmp_path / "lineage.jsonl")
        assert lineage[-1]["event"] == "ritual.reviewed"

    def test_missing_genesis_refuses(self, tmp_path):
        from tribal_cli.ritual import RitualNotBornError, run_lore_list

        with pytest.raises(RitualNotBornError, match="Run `tribal genesis` first"):
            run_lore_list(home=tmp_path)

    def test_malformed_lemma_rows_are_preserved_on_rewrite(self, tmp_path):
        from tribal_cli.ritual import run_lore_confirm

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship"), malformed="{not json")

        run_lore_confirm("tk_ship", evidence="Real outcome.", home=tmp_path, now=_utc(second=3))

        text = (tmp_path / "lore" / "lemmas.jsonl").read_text(encoding="utf-8")
        assert "{not json" in text
        assert '"confirmed_count": 1' in text

    def test_empty_evidence_refuses(self, tmp_path):
        from tribal_cli.ritual import RitualError, run_lore_confirm

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship"))

        with pytest.raises(RitualError, match="Evidence is required"):
            run_lore_confirm("tk_ship", evidence="  ", home=tmp_path)

    def test_json_render_contains_ritual_payload(self, tmp_path):
        from tribal_cli.ritual import render_ritual_result, run_lore_list

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship"))

        payload = json.loads(render_ritual_result(run_lore_list(home=tmp_path), json_output=True))

        assert payload["status"] == "listed"
        assert payload["payload"]["lemmas"][0]["id"] == "tk_ship"

    def test_cmd_lore_list_json(self, tmp_path, monkeypatch, capsys):
        from tribal_cli import ritual

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship"))
        monkeypatch.setattr(ritual, "get_tribal_home", lambda: tmp_path)
        args = argparse.Namespace(lore_command="list", status="all", json=True)

        assert ritual.cmd_lore(args) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["payload"]["lemmas"][0]["id"] == "tk_ship"

    def test_cmd_ritual_review_json(self, tmp_path, monkeypatch, capsys):
        from tribal_cli import ritual

        _birth(tmp_path)
        _write_lemmas(tmp_path, _lemma("tk_ship", confirmations=2))
        monkeypatch.setattr(ritual, "get_tribal_home", lambda: tmp_path)
        args = argparse.Namespace(ritual_command="review", json=True)

        assert ritual.cmd_ritual(args) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["payload"]["recommendations"][0]["recommendation"] == "promote_to_canon"
