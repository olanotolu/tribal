import json
from datetime import datetime, timezone

import pytest
import yaml

from tribal_cli.default_soul import DEFAULT_SOUL_MD


def _utc(second: int = 0) -> datetime:
    return datetime(2026, 5, 30, 12, 0, second, tzinfo=timezone.utc)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_lineage(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestGenesisBirth:
    def test_first_birth_writes_core_files(self, tmp_path):
        from tribal_cli.genesis import run_genesis

        result = run_genesis(
            domain="hospitality.nyc.fnb",
            home=tmp_path,
            mode="cli",
            now=_utc(),
        )

        assert result.status == "born"

        birth = _read_json(tmp_path / "genesis.json")
        assert birth["schema_version"] == 1
        assert birth["birth_id"].startswith("gen_")
        assert birth["tribe_id"] == "hospitality.nyc.fnb"
        assert birth["mode"] == "cli"
        assert birth["tribal_version"]
        assert birth["tribal_commit"]
        assert birth["repo_url"]
        assert birth["upstream"]["repository"].startswith("https://github.com/NousResearch/")
        assert birth["upstream"]["repository"].endswith("-agent")
        assert birth["upstream"]["source_commit"] == "e7c99651fb608a2be1692a65c75bb9e68793baaf"
        assert birth["upstream"]["license"] == "MIT"
        assert birth["seasoning"] == {"mirofish": "planned_v2", "status": "not_run"}

        tribe = yaml.safe_load((tmp_path / "tribe.yaml").read_text(encoding="utf-8"))
        assert tribe["schema_version"] == 1
        assert tribe["tribe_id"] == "hospitality.nyc.fnb"
        assert tribe["domain"] == "hospitality.nyc.fnb"
        assert tribe["status"] == "born"
        assert tribe["genesis_id"] == birth["birth_id"]
        assert tribe["ontology"]["objects"] == ["tribe", "soul", "law", "lore", "lemma", "lineage", "council", "role"]
        assert tribe["ontology"]["actions"] == ["remember", "validate", "ritualize", "season", "convene"]

        assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL_MD
        assert (tmp_path / "lore" / "lemmas.jsonl").read_text(encoding="utf-8") == ""
        law = yaml.safe_load((tmp_path / "law.yaml").read_text(encoding="utf-8"))
        assert law["schema_version"] == 1
        assert law["canon"]["auto_promote"] is False
        assert law["folklore"]["allow_drafts"] is True
        assert law["folklore"]["max_drafts_per_council"] == 3
        assert law["mirofish"]["status"] == "planned_v2"

        lineage = _read_lineage(tmp_path / "lineage.jsonl")
        assert len(lineage) == 1
        assert lineage[0]["event"] == "genesis.birth"
        assert lineage[0]["birth_id"] == birth["birth_id"]
        assert lineage[0]["tribe_id"] == "hospitality.nyc.fnb"

    def test_second_birth_is_idempotent_and_preserves_user_files(self, tmp_path):
        from tribal_cli.genesis import run_genesis

        run_genesis(domain="local", home=tmp_path, now=_utc())
        birth_before = (tmp_path / "genesis.json").read_text(encoding="utf-8")
        soul_path = tmp_path / "SOUL.md"
        lore_path = tmp_path / "lore" / "lemmas.jsonl"
        law_path = tmp_path / "law.yaml"
        soul_path.write_text("custom soul\n", encoding="utf-8")
        lore_path.write_text('{"claim":"keep me"}\n', encoding="utf-8")
        law_path.write_text("custom: law\n", encoding="utf-8")

        result = run_genesis(domain="changed", home=tmp_path, now=_utc(1))

        assert result.status == "already_born"
        assert (tmp_path / "genesis.json").read_text(encoding="utf-8") == birth_before
        assert soul_path.read_text(encoding="utf-8") == "custom soul\n"
        assert lore_path.read_text(encoding="utf-8") == '{"claim":"keep me"}\n'
        assert law_path.read_text(encoding="utf-8") == "custom: law\n"

    def test_first_birth_preserves_existing_soul_lore_and_lineage(self, tmp_path):
        from tribal_cli.genesis import run_genesis

        (tmp_path / "lore").mkdir(parents=True)
        (tmp_path / "SOUL.md").write_text("already awake\n", encoding="utf-8")
        (tmp_path / "lore" / "lemmas.jsonl").write_text('{"claim":"pre-genesis"}\n', encoding="utf-8")
        (tmp_path / "lineage.jsonl").write_text('{"event":"legacy"}\n', encoding="utf-8")
        (tmp_path / "law.yaml").write_text("custom: law\n", encoding="utf-8")

        result = run_genesis(domain="local", home=tmp_path, now=_utc())

        assert result.status == "born"
        assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "already awake\n"
        assert (tmp_path / "lore" / "lemmas.jsonl").read_text(encoding="utf-8") == '{"claim":"pre-genesis"}\n'
        assert (tmp_path / "law.yaml").read_text(encoding="utf-8") == "custom: law\n"
        lineage = _read_lineage(tmp_path / "lineage.jsonl")
        assert lineage[0]["event"] == "legacy"
        assert lineage[1]["event"] == "genesis.birth"

    def test_idempotent_run_repairs_missing_safe_files(self, tmp_path):
        from tribal_cli.genesis import run_genesis

        first = run_genesis(domain="local", home=tmp_path, now=_utc())
        (tmp_path / "tribe.yaml").unlink()
        (tmp_path / "lineage.jsonl").unlink()
        (tmp_path / "lore" / "lemmas.jsonl").unlink()
        (tmp_path / "law.yaml").unlink()

        result = run_genesis(domain="ignored", home=tmp_path, now=_utc(1))

        assert result.status == "already_born"
        assert sorted(result.repaired) == [
            "law.yaml",
            "lineage.jsonl",
            "lore/lemmas.jsonl",
            "tribe.yaml",
        ]
        assert _read_json(tmp_path / "genesis.json")["birth_id"] == first.birth["birth_id"]
        assert yaml.safe_load((tmp_path / "tribe.yaml").read_text(encoding="utf-8"))["tribe_id"] == "local"
        assert (tmp_path / "lore" / "lemmas.jsonl").read_text(encoding="utf-8") == ""

    def test_rebirth_without_confirmation_refuses_and_preserves_state(self, tmp_path):
        from tribal_cli.genesis import GenesisConfirmationError, run_genesis

        run_genesis(domain="local", home=tmp_path, now=_utc())

        with pytest.raises(GenesisConfirmationError):
            run_genesis(domain="new.domain", home=tmp_path, rebirth=True, now=_utc(1))

        assert not (tmp_path / "archives").exists()
        assert _read_json(tmp_path / "genesis.json")["tribe_id"] == "local"

    def test_rebirth_archives_old_state_and_writes_new_lineage(self, tmp_path):
        from tribal_cli.genesis import run_genesis

        old = run_genesis(domain="old.domain", home=tmp_path, now=_utc())
        (tmp_path / "lore" / "lemmas.jsonl").write_text('{"claim":"old canon"}\n', encoding="utf-8")

        new = run_genesis(
            domain="new.domain",
            home=tmp_path,
            rebirth=True,
            confirm="REBIRTH old.domain",
            now=_utc(1),
        )

        assert new.status == "reborn"
        assert new.birth["parent_birth_id"] == old.birth["birth_id"]
        archive = tmp_path / new.archive_path
        assert archive.is_dir()
        assert _read_json(archive / "genesis.json")["birth_id"] == old.birth["birth_id"]
        assert (archive / "lore" / "lemmas.jsonl").read_text(encoding="utf-8") == '{"claim":"old canon"}\n'
        assert (archive / "law.yaml").is_file()

        assert _read_json(tmp_path / "genesis.json")["tribe_id"] == "new.domain"
        assert (tmp_path / "lore" / "lemmas.jsonl").read_text(encoding="utf-8") == ""
        lineage = _read_lineage(tmp_path / "lineage.jsonl")
        assert len(lineage) == 1
        assert lineage[0]["event"] == "genesis.birth"
        assert lineage[0]["parent_birth_id"] == old.birth["birth_id"]

    def test_json_render_contains_machine_readable_birth(self, tmp_path):
        from tribal_cli.genesis import render_genesis, run_genesis

        result = run_genesis(domain="test.domain", home=tmp_path, now=_utc())

        payload = json.loads(render_genesis(result, json_output=True))

        assert payload["status"] == "born"
        assert payload["birth"]["tribe_id"] == "test.domain"
        assert payload["birth"]["upstream"]["repository"].startswith("https://github.com/NousResearch/")
        assert payload["birth"]["upstream"]["repository"].endswith("-agent")
        assert payload["birth"]["seasoning"]["mirofish"] == "planned_v2"
