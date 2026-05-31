"""Tribal council runtime.

The tribe command is the first living act after Genesis: a question convenes
role agents, the parent acts as Keeper, and only folklore drafts are written.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from tribal_constants import get_tribal_home
from tribal_cli.genesis import DEFAULT_LAW
from utils import atomic_yaml_write


SCHEMA_VERSION = 1
ROLE_NAMES = ("Scout", "Elder", "Oracle", "Skeptic", "Keeper")


class TribeNotBornError(RuntimeError):
    """Raised when tribe commands need Genesis but the home is unborn."""


class TribeCouncilError(RuntimeError):
    """Raised when a council cannot be convened."""


@dataclass
class TribeResult:
    status: str
    home: Path
    council: dict[str, Any]


DelegateRunner = Callable[[dict[str, Any]], str]


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


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _count_jsonl(path: Path) -> int:
    return len(_read_jsonl(path))


def _ensure_law(home: Path) -> dict[str, Any]:
    law_path = home / "law.yaml"
    if not law_path.exists():
        atomic_yaml_write(law_path, DEFAULT_LAW, sort_keys=False)
        return dict(DEFAULT_LAW)
    law = _read_yaml(law_path)
    return law or dict(DEFAULT_LAW)


def _require_birth(home: Path) -> dict[str, Any]:
    birth = _read_json(home / "genesis.json")
    if not birth:
        raise TribeNotBornError("Run `tribal genesis` first.")
    return birth


def _role_catalog() -> list[dict[str, str]]:
    return [
        {"name": "Scout", "meaning": "Gathers live signal and immediate context."},
        {"name": "Elder", "meaning": "Reads existing lore and admits when canon is empty."},
        {"name": "Oracle", "meaning": "Projects likely outcomes; MiroFish remains planned_v2."},
        {"name": "Skeptic", "meaning": "Attacks weak claims, stale lore, and fake certainty."},
        {"name": "Keeper", "meaning": "Parent process writes lineage and folklore drafts."},
    ]


def _delegate_runner(agent: Any | None, delegate_runner: DelegateRunner | None) -> DelegateRunner:
    if delegate_runner is not None:
        return delegate_runner
    if agent is not None and hasattr(agent, "_dispatch_delegate_task"):
        return agent._dispatch_delegate_task
    raise TribeCouncilError("`/tribe ask` needs an initialized Tribal agent.")


def _delegate_results(raw: str, roles: list[str]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"results": [{"task_index": 0, "status": "error", "summary": raw}]}
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = [{"task_index": 0, "status": "error", "summary": str(raw)}]

    out: list[dict[str, Any]] = []
    for idx, role in enumerate(roles):
        row = next(
            (r for r in rows if isinstance(r, dict) and int(r.get("task_index", -1)) == idx),
            {},
        )
        summary, parsed = _parse_role_summary(row.get("summary", ""))
        out.append(
            {
                "name": role,
                "status": row.get("status", "missing"),
                "summary": summary,
                "duration_seconds": row.get("duration_seconds", 0),
                "payload": parsed,
            }
        )
    return out


def _parse_role_summary(value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, dict):
        parsed = value
    else:
        text = str(value or "").strip()
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return text, {}
        parsed = loaded if isinstance(loaded, dict) else {}
    summary = str(parsed.get("summary") or parsed.get("answer") or value or "").strip()
    return summary, parsed


def _lore_context(lore_rows: list[dict[str, Any]]) -> str:
    if not lore_rows:
        return "No existing lore yet. Say this plainly; do not borrow elder authority."
    preview = lore_rows[-8:]
    return "Existing lore tail:\n" + json.dumps(preview, ensure_ascii=False, indent=2)


def _wave_one_tasks(question: str, tribe_id: str, lore_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shared = (
        f"Tribe: {tribe_id}\n"
        f"Question: {question}\n"
        "Return compact JSON with keys: summary, draft_lemmas.\n"
        "Draft lemmas must be falsifiable candidate patterns, never canon."
    )
    return [
        {
            "role_name": "Scout",
            "goal": "Scout role for a Tribal council: gather live signal and immediate context.",
            "context": shared,
            "toolsets": ["file", "web", "browser"],
            "role": "leaf",
        },
        {
            "role_name": "Elder",
            "goal": "Elder role for a Tribal council: weigh the question against existing lore.",
            "context": shared + "\n\n" + _lore_context(lore_rows),
            "toolsets": ["file"],
            "role": "leaf",
        },
        {
            "role_name": "Oracle",
            "goal": "Oracle role for a Tribal council: project likely outcomes in text simulation.",
            "context": shared + "\nMiroFish status: planned_v2; do not claim a real MiroFish run occurred.",
            "toolsets": ["file"],
            "role": "leaf",
        },
    ]


def _skeptic_args(question: str, tribe_id: str, role_rows: list[dict[str, Any]]) -> dict[str, Any]:
    role_text = "\n".join(f"{r['name']}: {r['summary']}" for r in role_rows)
    return {
        "goal": "Skeptic role for a Tribal council: challenge weak claims and stale certainty.",
        "context": (
            f"Tribe: {tribe_id}\nQuestion: {question}\n\n"
            f"Council so far:\n{role_text}\n\n"
            "Return compact JSON with keys: summary, draft_lemmas. "
            "Reject canon claims and preserve only folklore-worthy candidates."
        ),
        "toolsets": ["file"],
        "role": "leaf",
    }


def _draft_lemmas(
    *,
    home: Path,
    council_id: str,
    tribe_id: str,
    roles: list[dict[str, Any]],
    law: dict[str, Any],
    now: datetime,
) -> list[str]:
    folklore = law.get("folklore") if isinstance(law.get("folklore"), dict) else {}
    if folklore.get("allow_drafts", True) is False:
        return []
    try:
        max_drafts = int(folklore.get("max_drafts_per_council", 3))
    except (TypeError, ValueError):
        max_drafts = 3
    max_drafts = max(0, max_drafts)

    written: list[str] = []
    seen_claims: set[str] = set()
    for role in roles:
        payload = role.get("payload") if isinstance(role.get("payload"), dict) else {}
        drafts = payload.get("draft_lemmas")
        if not isinstance(drafts, list):
            continue
        for draft in drafts:
            if len(written) >= max_drafts:
                return written
            if not isinstance(draft, dict):
                continue
            claim = str(draft.get("claim") or "").strip()
            if not claim or claim in seen_claims:
                continue
            seen_claims.add(claim)
            lemma_id = f"tk_{uuid.uuid4().hex[:8]}"
            lemma = {
                "id": lemma_id,
                "status": "folklore",
                "tribe_id": tribe_id,
                "claim": claim,
                "created_at": _iso_z(now),
                "source": {"type": "council", "council_id": council_id, "role": role["name"]},
                "evidence": [{"role": role["name"], "summary": role.get("summary", "")}],
                "confidence": draft.get("confidence"),
                "promotion": {"status": "unvalidated"},
            }
            _append_jsonl(home / "lore" / "lemmas.jsonl", lemma)
            written.append(lemma_id)
    return written


def _build_consensus(question: str, roles: list[dict[str, Any]], draft_ids: list[str]) -> dict[str, Any]:
    parts = [f"{r['name']}: {r['summary']}" for r in roles if r.get("summary")]
    dissent = next((r["summary"] for r in roles if r["name"] == "Skeptic"), "")
    answer = (
        f"The tribe convened on: {question}\n\n"
        + "\n".join(parts)
        + "\n\nKeeper: This is council guidance, not canon. "
        + f"{len(draft_ids)} draft folklore lemma(s) were recorded."
    )
    return {"answer": answer, "confidence": "draft", "dissent": dissent}


def run_tribe_ask(
    question: str,
    *,
    home: str | Path | None = None,
    agent: Any | None = None,
    delegate_runner: DelegateRunner | None = None,
    now: datetime | None = None,
) -> TribeResult:
    home_path = Path(home) if home is not None else get_tribal_home()
    birth = _require_birth(home_path)
    law = _ensure_law(home_path)
    now = now or _utc_now()
    tribe_id = str(birth.get("tribe_id") or "local")
    runner = _delegate_runner(agent, delegate_runner)
    question = question.strip()
    if not question:
        raise TribeCouncilError("Usage: /tribe ask <question>")

    lore_rows = _read_jsonl(home_path / "lore" / "lemmas.jsonl")
    council_id = f"council_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}"

    wave_one_raw = runner({"tasks": _wave_one_tasks(question, tribe_id, lore_rows)})
    wave_one_roles = _delegate_results(wave_one_raw, ["Scout", "Elder", "Oracle"])
    skeptic_raw = runner(_skeptic_args(question, tribe_id, wave_one_roles))
    skeptic_role = _delegate_results(skeptic_raw, ["Skeptic"])
    roles = wave_one_roles + skeptic_role

    draft_ids = _draft_lemmas(
        home=home_path,
        council_id=council_id,
        tribe_id=tribe_id,
        roles=roles,
        law=law,
        now=now,
    )
    lineage_event_id = f"lin_{uuid.uuid4().hex[:10]}"
    consensus = _build_consensus(question, roles, draft_ids)
    council = {
        "schema_version": SCHEMA_VERSION,
        "council_id": council_id,
        "tribe_id": tribe_id,
        "question": question,
        "asked_at": _iso_z(now),
        "roles": [
            {k: v for k, v in role.items() if k != "payload"}
            for role in roles
        ],
        "consensus": consensus,
        "draft_lemmas": draft_ids,
        "lineage_event_id": lineage_event_id,
    }
    lineage = {
        "schema_version": SCHEMA_VERSION,
        "event": "council.convened",
        "lineage_event_id": lineage_event_id,
        "council_id": council_id,
        "tribe_id": tribe_id,
        "timestamp": council["asked_at"],
        "question": question,
        "draft_lemmas": draft_ids,
    }
    _append_jsonl(home_path / "lineage.jsonl", lineage)
    _append_jsonl(home_path / "council" / "sessions.jsonl", council)
    return TribeResult(status="convened", home=home_path, council=council)


def run_tribe_status(*, home: str | Path | None = None) -> TribeResult:
    home_path = Path(home) if home is not None else get_tribal_home()
    birth = _require_birth(home_path)
    _ensure_law(home_path)
    lore_rows = _read_jsonl(home_path / "lore" / "lemmas.jsonl")
    council = {
        "tribe_id": birth.get("tribe_id", "local"),
        "birth_id": birth.get("birth_id"),
        "law": "present",
        "lore_count": len(lore_rows),
        "canon_count": sum(1 for row in lore_rows if row.get("status") == "canon"),
        "folklore_count": sum(1 for row in lore_rows if row.get("status") == "folklore"),
        "council_count": _count_jsonl(home_path / "council" / "sessions.jsonl"),
        "mirofish": "planned_v2",
    }
    return TribeResult(status="status", home=home_path, council=council)


def run_tribe_roles(*, home: str | Path | None = None) -> TribeResult:
    home_path = Path(home) if home is not None else get_tribal_home()
    _require_birth(home_path)
    return TribeResult(status="roles", home=home_path, council={"roles": _role_catalog()})


def render_tribe_result(result: TribeResult, *, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {"status": result.status, "home": str(result.home), "council": result.council},
            indent=2,
            ensure_ascii=False,
        )
    if result.status == "status":
        c = result.council
        return "\n".join([
            "TRIBAL STATUS",
            "",
            f"Tribe: {c.get('tribe_id')}",
            f"Home: {result.home}",
            f"Genesis: {c.get('birth_id')}",
            f"Law: {c.get('law')}",
            f"Lore: {c.get('lore_count')} total ({c.get('folklore_count')} folklore, {c.get('canon_count')} canon)",
            f"Councils: {c.get('council_count')}",
            "MiroFish: planned_v2",
        ])
    if result.status == "roles":
        lines = ["TRIBAL ROLES", ""]
        for role in result.council["roles"]:
            lines.append(f"{role['name']}: {role['meaning']}")
        return "\n".join(lines)
    c = result.council
    lines = [
        "TRIBAL COUNCIL",
        "",
        f"Council: {c.get('council_id')}",
        f"Tribe: {c.get('tribe_id')}",
        f"Question: {c.get('question')}",
        "",
        c.get("consensus", {}).get("answer", ""),
        "",
        "Roles:",
    ]
    for role in c.get("roles", []):
        lines.append(f"- {role.get('name')}: {role.get('status')} -- {role.get('summary')}")
    lines.append(f"Draft folklore: {len(c.get('draft_lemmas') or [])}")
    return "\n".join(lines)


def handle_tribe_slash_command(command: str, *, agent: Any | None = None) -> str:
    parts = shlex.split(command)
    argv = parts[1:] if parts and parts[0].lstrip("/").lower() == "tribe" else parts
    if not argv:
        return render_tribe_result(run_tribe_status())
    subcmd = argv[0].lower()
    rest = argv[1:]
    json_output = False
    if "--json" in rest:
        rest = [p for p in rest if p != "--json"]
        json_output = True
    if subcmd == "status":
        return render_tribe_result(run_tribe_status(), json_output=json_output)
    if subcmd == "roles":
        return render_tribe_result(run_tribe_roles(), json_output=json_output)
    if subcmd == "ask":
        result = run_tribe_ask(" ".join(rest), agent=agent)
        return render_tribe_result(result, json_output=json_output)
    return "Usage: /tribe ask <question> | /tribe status | /tribe roles"


def cmd_tribe(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "tribe_command", None) or "status"
    json_output = bool(getattr(args, "json", False))
    try:
        if subcmd == "status":
            print(render_tribe_result(run_tribe_status(), json_output=json_output))
            return 0
        if subcmd == "roles":
            print(render_tribe_result(run_tribe_roles(), json_output=json_output))
            return 0
        if subcmd == "ask":
            question = " ".join(getattr(args, "question", []) or []).strip()
            if not question:
                print("Usage: tribal tribe ask <question>", file=sys.stderr)
                return 2
            _require_birth(get_tribal_home())
            from cli import TribalCLI

            toolsets = getattr(args, "toolsets", None)
            if isinstance(toolsets, str):
                toolsets = [t.strip() for t in toolsets.split(",") if t.strip()]
            cli = TribalCLI(
                model=getattr(args, "model", None),
                provider=getattr(args, "provider", None),
                toolsets=toolsets,
                max_turns=getattr(args, "max_turns", None),
                ignore_rules=getattr(args, "ignore_rules", False),
            )
            if not cli._initialize_agent():
                return 1
            result = run_tribe_ask(question, agent=cli.agent)
            print(render_tribe_result(result, json_output=json_output))
            return 0
    except (TribeNotBornError, TribeCouncilError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2
