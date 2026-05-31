"""Genesis birth command for Tribal.

Genesis is the one-time identity scaffold for a Tribal home. It creates the
smallest useful Tribal ontology: tribe, soul, empty lemma store, and lineage.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tribal_cli import __version__ as TRIBAL_VERSION
from tribal_cli.default_soul import DEFAULT_SOUL_MD
from tribal_constants import get_tribal_home
from utils import atomic_json_write, atomic_yaml_write


SCHEMA_VERSION = 1
DEFAULT_TRIBE_ID = "local"
DEFAULT_LAW: dict[str, Any] = {
    "schema_version": 1,
    "canon": {"auto_promote": False},
    "folklore": {"allow_drafts": True, "max_drafts_per_council": 3},
    "taboos": [
        "no fake certainty",
        "no canon without ritual",
        "no elder authority on newborn tribes",
    ],
    "mirofish": {"status": "planned_v2"},
}


class GenesisConfirmationError(ValueError):
    """Raised when a rebirth request lacks the exact confirmation phrase."""


@dataclass
class GenesisResult:
    status: str
    home: Path
    birth: dict[str, Any]
    created: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    archive_path: str | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_domain(domain: str | None) -> str:
    value = (domain or DEFAULT_TRIBE_ID).strip()
    return value or DEFAULT_TRIBE_ID


def _git_output(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return value or None


def _parse_upstream() -> dict[str, str]:
    upstream_path = _repo_root() / "UPSTREAM.md"
    values: dict[str, str] = {}
    if upstream_path.exists():
        for raw in upstream_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            key, value = line[2:].split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            values[key] = value.strip().strip("`")
    license_value = values.get("license", "")
    if "," in license_value:
        license_value = license_value.split(",", 1)[0].strip()
    return {
        "repository": values.get("upstream_repository", ""),
        "source_commit": values.get("source_commit", ""),
        "project": values.get("upstream_project", ""),
        "copyright_holder": values.get("upstream_author/copyright_holder", ""),
        "license": license_value,
        "attribution": "Derived under the upstream MIT license; see UPSTREAM.md.",
    }


def _birth_id(now: datetime) -> str:
    return f"gen_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}"


def _build_birth(
    tribe_id: str,
    *,
    mode: str,
    now: datetime,
    parent_birth_id: str | None = None,
    archive_path: str | None = None,
) -> dict[str, Any]:
    root = _repo_root()
    birth: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "birth_id": _birth_id(now),
        "tribe_id": tribe_id,
        "born_at": _iso_z(now),
        "tribal_version": TRIBAL_VERSION,
        "tribal_commit": _git_output(["rev-parse", "HEAD"], root),
        "repo_url": _git_output(["remote", "get-url", "origin"], root),
        "mode": mode,
        "upstream": _parse_upstream(),
        "seasoning": {"mirofish": "planned_v2", "status": "not_run"},
    }
    if parent_birth_id:
        birth["parent_birth_id"] = parent_birth_id
    if archive_path:
        birth["archive_path"] = archive_path
    return birth


def _tribe_payload(birth: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tribe_id": birth["tribe_id"],
        "name": birth["tribe_id"],
        "domain": birth["tribe_id"],
        "status": "born",
        "genesis_id": birth["birth_id"],
        "born_at": birth["born_at"],
        "ontology": {
            "objects": ["tribe", "soul", "law", "lore", "lemma", "lineage", "council", "role"],
            "actions": ["remember", "validate", "ritualize", "season", "convene"],
        },
    }


def _lineage_event(birth: dict[str, Any]) -> dict[str, Any]:
    event = {
        "event": "genesis.birth",
        "birth_id": birth["birth_id"],
        "tribe_id": birth["tribe_id"],
        "timestamp": birth["born_at"],
        "mode": birth["mode"],
        "upstream": birth["upstream"],
        "seasoning": birth["seasoning"],
    }
    if birth.get("parent_birth_id"):
        event["parent_birth_id"] = birth["parent_birth_id"]
    if birth.get("archive_path"):
        event["archive_path"] = birth["archive_path"]
    return event


def _write_text_if_missing(path: Path, content: str, created: list[str], rel: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(rel)


def _write_lineage(path: Path, birth: dict[str, Any], *, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_lineage_event(birth), ensure_ascii=False, sort_keys=True) + "\n"
    mode = "a" if append and path.exists() else "w"
    with path.open(mode, encoding="utf-8") as f:
        f.write(line)


def _ensure_birth_files(
    home: Path,
    birth: dict[str, Any],
    *,
    overwrite: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    repaired: list[str] = []

    home.mkdir(parents=True, exist_ok=True)

    genesis_path = home / "genesis.json"
    if overwrite or not genesis_path.exists():
        atomic_json_write(genesis_path, birth, indent=2)
        created.append("genesis.json")

    tribe_path = home / "tribe.yaml"
    if overwrite or not tribe_path.exists():
        atomic_yaml_write(tribe_path, _tribe_payload(birth), sort_keys=False)
        (created if overwrite else repaired).append("tribe.yaml")

    soul_path = home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
        (created if overwrite else repaired).append("SOUL.md")

    lore_path = home / "lore" / "lemmas.jsonl"
    _write_text_if_missing(
        lore_path,
        "",
        repaired if genesis_path.exists() and not overwrite else created,
        "lore/lemmas.jsonl",
    )

    law_path = home / "law.yaml"
    if not law_path.exists():
        atomic_yaml_write(law_path, DEFAULT_LAW, sort_keys=False)
        (created if overwrite else repaired).append("law.yaml")

    lineage_path = home / "lineage.jsonl"
    lineage_existed = lineage_path.exists()
    if overwrite or not lineage_existed:
        _write_lineage(lineage_path, birth, append=lineage_existed)
    if not lineage_existed:
        (created if overwrite else repaired).append("lineage.jsonl")

    if not overwrite:
        created = [p for p in created if p != "genesis.json"]
    return created, repaired


def _load_birth(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _archive_existing(home: Path, now: datetime, birth: dict[str, Any]) -> str:
    archive_rel = Path("archives") / "genesis" / _stamp(now)
    archive = home / archive_rel
    if archive.exists():
        archive_rel = archive_rel.with_name(f"{archive_rel.name}_{birth['birth_id']}")
        archive = home / archive_rel
    archive.mkdir(parents=True, exist_ok=False)

    for name in ("genesis.json", "tribe.yaml", "SOUL.md", "law.yaml", "lineage.jsonl"):
        src = home / name
        if src.exists():
            shutil.copy2(src, archive / name)
    lore_src = home / "lore"
    if lore_src.exists():
        shutil.copytree(lore_src, archive / "lore")
    return archive_rel.as_posix()


def _clear_rebirth_targets(home: Path) -> None:
    for name in ("genesis.json", "tribe.yaml", "SOUL.md", "law.yaml", "lineage.jsonl"):
        path = home / name
        if path.exists():
            path.unlink()
    lore = home / "lore"
    if lore.exists():
        shutil.rmtree(lore)


def _assert_rebirth_confirmed(current_birth: dict[str, Any], confirm: str | None) -> None:
    tribe_id = current_birth.get("tribe_id") or DEFAULT_TRIBE_ID
    expected = f"REBIRTH {tribe_id}"
    if confirm != expected:
        raise GenesisConfirmationError(
            f"Rebirth refused. Type {expected} to continue."
        )


def run_genesis(
    *,
    domain: str | None = None,
    home: str | Path | None = None,
    mode: str = "cli",
    rebirth: bool = False,
    confirm: str | None = None,
    now: datetime | None = None,
) -> GenesisResult:
    """Birth or inspect a Tribal home."""
    home_path = Path(home) if home is not None else get_tribal_home()
    now = now or _utc_now()
    tribe_id = _normalize_domain(domain)
    genesis_path = home_path / "genesis.json"
    current_birth = _load_birth(genesis_path)

    if current_birth and not rebirth:
        created, repaired = _ensure_birth_files(home_path, current_birth, overwrite=False)
        return GenesisResult(
            status="already_born",
            home=home_path,
            birth=current_birth,
            created=created,
            repaired=repaired,
        )

    if current_birth and rebirth:
        _assert_rebirth_confirmed(current_birth, confirm)
        archive_path = _archive_existing(home_path, now, current_birth)
        _clear_rebirth_targets(home_path)
        birth = _build_birth(
            tribe_id,
            mode=mode,
            now=now,
            parent_birth_id=current_birth["birth_id"],
            archive_path=archive_path,
        )
        created, _repaired = _ensure_birth_files(home_path, birth, overwrite=True)
        return GenesisResult(
            status="reborn",
            home=home_path,
            birth=birth,
            created=created,
            archive_path=archive_path,
        )

    birth = _build_birth(tribe_id, mode=mode, now=now)
    created, _repaired = _ensure_birth_files(home_path, birth, overwrite=True)
    return GenesisResult(status="born", home=home_path, birth=birth, created=created)


def render_genesis(result: GenesisResult, *, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {
                "status": result.status,
                "home": str(result.home),
                "birth": result.birth,
                "created": result.created,
                "repaired": result.repaired,
                "archive_path": result.archive_path,
            },
            indent=2,
            ensure_ascii=False,
        )

    status_label = {
        "born": "born",
        "already_born": "already born",
        "reborn": "reborn",
    }.get(result.status, result.status)
    lines = [
        "TRIBAL GENESIS",
        "",
        f"Status: {status_label}",
        f"Tribe: {result.birth.get('tribe_id', DEFAULT_TRIBE_ID)}",
        f"Home: {result.home}",
        f"Birth ID: {result.birth.get('birth_id', '')}",
        f"Born at: {result.birth.get('born_at', '')}",
        f"Tribal: v{result.birth.get('tribal_version', '')} @ {str(result.birth.get('tribal_commit') or '')[:12]}",
        f"Lineage: {result.birth.get('upstream', {}).get('project', 'upstream')} -> Tribal",
        "MiroFish: planned_v2 (not_run)",
    ]
    if result.archive_path:
        lines.append(f"Archive: {result.archive_path}")
    if result.created:
        lines.append(f"Created: {', '.join(result.created)}")
    if result.repaired:
        lines.append(f"Repaired: {', '.join(result.repaired)}")
    return "\n".join(lines)


def _slash_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="/genesis", add_help=False)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--rebirth", action="store_true", default=False)
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--json", action="store_true", default=False)
    return parser


def handle_genesis_slash_command(command: str) -> str:
    parts = shlex.split(command)
    argv = parts[1:] if parts and parts[0].lstrip("/").lower() == "genesis" else parts
    args = _slash_parser().parse_args(argv)
    result = run_genesis(
        domain=args.domain or DEFAULT_TRIBE_ID,
        mode="slash",
        rebirth=args.rebirth,
        confirm=args.confirm,
    )
    if args.json:
        return render_genesis(result, json_output=True)
    return render_genesis(result)


def cmd_genesis(args: argparse.Namespace) -> int:
    domain = getattr(args, "domain", None)
    json_output = bool(getattr(args, "json", False))
    rebirth = bool(getattr(args, "rebirth", False))
    confirm = getattr(args, "confirm", None)

    if domain is None and not json_output and sys.stdin.isatty():
        entered = input("Tribe/domain [local]: ").strip()
        domain = entered or DEFAULT_TRIBE_ID
    else:
        domain = domain or DEFAULT_TRIBE_ID

    if rebirth and confirm is None and sys.stdin.isatty():
        current = _load_birth(get_tribal_home() / "genesis.json") or {"tribe_id": DEFAULT_TRIBE_ID}
        expected = f"REBIRTH {current.get('tribe_id') or DEFAULT_TRIBE_ID}"
        print(f"Rebirth archives current Genesis state first. Type {expected} to continue.")
        confirm = input("> ").strip()

    try:
        result = run_genesis(
            domain=domain,
            mode="cli",
            rebirth=rebirth,
            confirm=confirm,
        )
    except GenesisConfirmationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(render_genesis(result, json_output=json_output))
    return 0
