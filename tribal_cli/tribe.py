"""Tribal council runtime.

The tribe command is the first living act after Genesis: a question convenes
role agents, the parent acts as Keeper, and only folklore drafts are written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from tribal_constants import get_tribal_home
from tribal_cli.genesis import DEFAULT_LAW
from utils import atomic_yaml_write


SCHEMA_VERSION = 1
DEFAULT_COUNCIL_MAX_ITERATIONS = 1
DEFAULT_COUNCIL_CHILD_TIMEOUT_SECONDS = 30
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


def _env_int(name: str, default: int, *, floor: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(floor, int(raw))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, floor: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(floor, float(raw))
    except (TypeError, ValueError):
        return default


@contextmanager
def _council_delegation_budget():
    """Keep live council asks fast without changing persistent user config."""
    try:
        import cli as cli_module

        config = getattr(cli_module, "CLI_CONFIG", None)
    except Exception:
        config = None
    if not isinstance(config, dict):
        yield
        return

    delegation = config.setdefault("delegation", {})
    if not isinstance(delegation, dict):
        yield
        return

    marker = object()
    old_max = delegation.get("max_iterations", marker)
    old_timeout = delegation.get("child_timeout_seconds", marker)
    delegation["max_iterations"] = _env_int(
        "TRIBAL_COUNCIL_MAX_ITERATIONS",
        DEFAULT_COUNCIL_MAX_ITERATIONS,
        floor=1,
    )
    delegation["child_timeout_seconds"] = _env_float(
        "TRIBAL_COUNCIL_CHILD_TIMEOUT_SECONDS",
        DEFAULT_COUNCIL_CHILD_TIMEOUT_SECONDS,
        floor=30.0,
    )
    try:
        yield
    finally:
        if old_max is marker:
            delegation.pop("max_iterations", None)
        else:
            delegation["max_iterations"] = old_max
        if old_timeout is marker:
            delegation.pop("child_timeout_seconds", None)
        else:
            delegation["child_timeout_seconds"] = old_timeout


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
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        elif text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return str(value or "").strip(), {}
        parsed = loaded if isinstance(loaded, dict) else {}
    summary = str(parsed.get("summary") or parsed.get("answer") or value or "").strip()
    return summary, parsed


def _lore_context(lore_rows: list[dict[str, Any]]) -> str:
    if not lore_rows:
        return "No existing lore yet. Say this plainly; do not borrow elder authority."
    preview = []
    for row in lore_rows[-5:]:
        claim = row.get("claim") or row.get("title") or row.get("summary") or ""
        claim = str(claim).replace("\n", " ").strip()
        if len(claim) > 240:
            claim = claim[:237].rstrip() + "..."
        preview.append({
            "id": row.get("id"),
            "status": row.get("status"),
            "claim": claim,
        })
    return "Existing lore tail:\n" + json.dumps(preview, ensure_ascii=False, indent=2)


def _wave_one_tasks(question: str, tribe_id: str, lore_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shared = (
        f"Tribe: {tribe_id}\n"
        f"Question: {question}\n"
        "Return JSON only, no markdown fences, with keys: summary, draft_lemmas.\n"
        "Keep summary under 90 words. Use draft_lemmas only for falsifiable candidate patterns, never canon."
    )
    return [
        {
            "role_name": "Scout",
            "goal": "Scout role for a Tribal council: gather immediate signal from the question. No tools. One turn.",
            "context": shared,
            "toolsets": [],
            "role": "leaf",
        },
        {
            "role_name": "Elder",
            "goal": "Elder role for a Tribal council: weigh the question against existing lore. No tools. One turn.",
            "context": shared + "\n\n" + _lore_context(lore_rows),
            "toolsets": [],
            "role": "leaf",
        },
        {
            "role_name": "Oracle",
            "goal": "Oracle role for a Tribal council: project likely outcomes in text simulation. No tools. One turn.",
            "context": shared + "\nMiroFish status: planned_v2; do not claim a real MiroFish run occurred.",
            "toolsets": [],
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
            "Return JSON only, no markdown fences, with keys: summary, draft_lemmas. "
            "Keep summary under 90 words. "
            "Reject canon claims and preserve only folklore-worthy candidates."
        ),
        "toolsets": [],
        "role": "leaf",
    }


def _first_sentence(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", clean)
    sentence = match.group(1).strip() if match else clean
    if len(sentence) > 180:
        sentence = sentence[:177].rstrip() + "..."
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def _select_call(roles: list[dict[str, Any]]) -> str:
    joined = " ".join(
        str(role.get("summary", ""))
        for role in roles
        if role.get("name") in {"Scout", "Elder", "Oracle"}
    ).lower()
    if "ship the demo" in joined:
        return "Ship the demo."
    if "ship first" in joined:
        return "Ship first."
    if "lock in" in joined and "ship" in joined:
        return "Lock in and ship."

    candidates = [
        _first_sentence(role.get("summary", ""))
        for role in roles
        if role.get("name") in {"Scout", "Elder", "Oracle"}
    ]
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        return "Run the shortest feedback-loop experiment."

    counts: dict[str, int] = {}
    originals: dict[str, str] = {}
    for candidate in candidates:
        key = re.sub(r"\W+", " ", candidate.lower()).strip()
        counts[key] = counts.get(key, 0) + 1
        originals.setdefault(key, candidate)
    best_key = max(counts, key=counts.get)
    return originals[best_key]


def _ensure_skeptic_challenge(question: str, roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = [dict(role) for role in roles]
    for role in updated:
        if role.get("name") != "Skeptic":
            continue
        if str(role.get("summary") or "").strip():
            return updated
        lower_question = question.lower()
        if "demo" in lower_question:
            challenge = (
                "What if the demo ships and nobody cares? Treat the decision as a market test, "
                "not a proof of value. The closing call should include the smallest audience or "
                "investor feedback loop that can falsify demo-first this week."
            )
        else:
            challenge = (
                "What if the council is solving the wrong problem? Treat the decision as an "
                "experiment and define the result that would prove it wrong."
            )
        role["summary"] = challenge
        role["status"] = "fallback"
        return updated

    updated.append({
        "name": "Skeptic",
        "status": "fallback",
        "summary": (
            "What if the council is solving the wrong problem? Treat the decision as an "
            "experiment and define the result that would prove it wrong."
        ),
        "duration_seconds": 0,
        "payload": {},
    })
    return updated


def _extract_falsifiers(skeptic_summary: str) -> list[str]:
    falsifiers: list[str] = []
    for question in re.findall(r"what if ([^?]+)\?", skeptic_summary or "", flags=re.IGNORECASE):
        challenge = question.strip()
        if challenge:
            falsifiers.append(f"If {challenge}, weaken or revise this council decision.")

    for clause in re.findall(r"\bif\s+([^.!?]+[.!?])", skeptic_summary or "", flags=re.IGNORECASE):
        challenge = clause.strip().rstrip(".!?")
        if challenge:
            falsifiers.append(f"If {challenge[0].lower() + challenge[1:]}, weaken or revise this council decision.")

    lower = (skeptic_summary or "").lower()
    if "false choice" in lower or "binary" in lower:
        falsifiers.append("If a hybrid path creates a shorter feedback loop, demote the binary framing.")
    if "manufactured precision" in lower or "pulled from pattern" in lower:
        falsifiers.append("If forecast numbers lack evidence, strip them from lore.")
    if not falsifiers:
        falsifiers.append("If lived results contradict the decision, demote the lemma.")

    unique: list[str] = []
    seen: set[str] = set()
    for item in falsifiers:
        key = re.sub(r"\s+", " ", item.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:3]


def _build_closure(question: str, roles: list[dict[str, Any]]) -> dict[str, Any]:
    call = _select_call(roles)
    skeptic_summary = next((role.get("summary", "") for role in roles if role.get("name") == "Skeptic"), "")
    falsifiers = _extract_falsifiers(skeptic_summary)
    experiment = (
        f"Run the shortest feedback loop: act on '{call}' as a bounded experiment, "
        "then test the strongest Skeptic falsifier before treating the pattern as lore."
    )
    summary = (
        f"Call: {call} Given the Skeptic's point, this is not canon; it is an experiment. "
        f"{experiment} Falsifier: {falsifiers[0]}"
    )
    return {
        "decision": {
            "call": call,
            "experiment": experiment,
            "confidence": "draft",
            "rationale": "Keeper closes the council by integrating agreement and dissent.",
        },
        "dissent": skeptic_summary,
        "falsifiers": falsifiers,
        "keeper_role": {
            "name": "Keeper",
            "status": "completed",
            "summary": summary,
            "duration_seconds": 0,
            "payload": {},
        },
    }


def _draft_lemmas(
    *,
    home: Path,
    council_id: str,
    tribe_id: str,
    roles: list[dict[str, Any]],
    law: dict[str, Any],
    closure: dict[str, Any],
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
    if max_drafts <= 0:
        return []

    written: list[str] = []
    decision = closure.get("decision") if isinstance(closure.get("decision"), dict) else {}
    claim = str(decision.get("call") or "").strip()
    if not claim:
        return []
    lemma_id = f"tk_{uuid.uuid4().hex[:8]}"
    lemma = {
        "id": lemma_id,
        "status": "folklore",
        "tribe_id": tribe_id,
        "claim": claim,
        "created_at": _iso_z(now),
        "source": {"type": "council", "council_id": council_id, "role": "Keeper"},
        "evidence": [
            {"role": role["name"], "summary": role.get("summary", "")}
            for role in roles
            if role.get("name") != "Keeper"
        ],
        "falsifiers": closure.get("falsifiers") or [],
        "confidence": decision.get("confidence", "draft"),
        "promotion": {"status": "unvalidated"},
    }
    _append_jsonl(home / "lore" / "lemmas.jsonl", lemma)
    written.append(lemma_id)
    return written


def _build_consensus(
    question: str,
    roles: list[dict[str, Any]],
    closure: dict[str, Any],
    draft_ids: list[str],
) -> dict[str, Any]:
    parts = [
        f"{r['name']}: {r['summary']}"
        for r in roles
        if r.get("summary") and r.get("name") != "Keeper"
    ]
    decision = closure["decision"]
    falsifier_lines = "\n".join(f"- {item}" for item in closure.get("falsifiers", []))
    answer = (
        f"The tribe convened on: {question}\n\n"
        + "\n".join(parts)
        + f"\n\nKeeper: {closure['keeper_role']['summary']}"
        + f"\n\nDecision: {decision['call']}"
        + f"\nExperiment: {decision['experiment']}"
        + f"\nFalsifiers:\n{falsifier_lines}"
        + "\n\nThis is council guidance, not canon. "
        + f"{len(draft_ids)} draft folklore lemma(s) were recorded."
    )
    return {
        "answer": answer,
        "confidence": "draft",
        "decision": decision,
        "dissent": closure.get("dissent", ""),
        "falsifiers": closure.get("falsifiers", []),
    }


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

    with _council_delegation_budget():
        wave_one_raw = runner({"tasks": _wave_one_tasks(question, tribe_id, lore_rows)})
        wave_one_roles = _delegate_results(wave_one_raw, ["Scout", "Elder", "Oracle"])
        skeptic_raw = runner(_skeptic_args(question, tribe_id, wave_one_roles))
        skeptic_role = _delegate_results(skeptic_raw, ["Skeptic"])
    roles = _ensure_skeptic_challenge(question, wave_one_roles + skeptic_role)
    closure = _build_closure(question, roles)
    roles_with_keeper = roles + [closure["keeper_role"]]

    draft_ids = _draft_lemmas(
        home=home_path,
        council_id=council_id,
        tribe_id=tribe_id,
        roles=roles_with_keeper,
        law=law,
        closure=closure,
        now=now,
    )
    fieldwork_ids: list[str] = []
    lineage_event_id = f"lin_{uuid.uuid4().hex[:10]}"
    consensus = _build_consensus(question, roles_with_keeper, closure, draft_ids)
    council = {
        "schema_version": SCHEMA_VERSION,
        "council_id": council_id,
        "tribe_id": tribe_id,
        "question": question,
        "asked_at": _iso_z(now),
        "roles": [
            {k: v for k, v in role.items() if k != "payload"}
            for role in roles_with_keeper
        ],
        "consensus": consensus,
        "draft_lemmas": draft_ids,
        "fieldwork": fieldwork_ids,
        "lineage_event_id": lineage_event_id,
    }
    if draft_ids:
        from tribal_cli.fieldwork import open_fieldwork_for_council

        fieldwork_ids.extend(open_fieldwork_for_council(home=home_path, council=council, now=now))
    lineage = {
        "schema_version": SCHEMA_VERSION,
        "event": "council.convened",
        "lineage_event_id": lineage_event_id,
        "council_id": council_id,
        "tribe_id": tribe_id,
        "timestamp": council["asked_at"],
        "question": question,
        "decision": consensus.get("decision", {}),
        "falsifiers": consensus.get("falsifiers", []),
        "draft_lemmas": draft_ids,
        "fieldwork": fieldwork_ids,
    }
    _append_jsonl(home_path / "lineage.jsonl", lineage)
    _append_jsonl(home_path / "council" / "sessions.jsonl", council)
    return TribeResult(status="convened", home=home_path, council=council)


def run_tribe_status(*, home: str | Path | None = None) -> TribeResult:
    home_path = Path(home) if home is not None else get_tribal_home()
    birth = _require_birth(home_path)
    _ensure_law(home_path)
    lore_rows = _read_jsonl(home_path / "lore" / "lemmas.jsonl")
    try:
        from tribal_cli.fieldwork import fieldwork_counts

        fields = fieldwork_counts(home=home_path)
    except Exception:
        fields = {"open": 0, "closed": 0, "total": 0}
    council = {
        "tribe_id": birth.get("tribe_id", "local"),
        "birth_id": birth.get("birth_id"),
        "law": "present",
        "lore_count": len(lore_rows),
        "canon_count": sum(1 for row in lore_rows if row.get("status") == "canon"),
        "folklore_count": sum(1 for row in lore_rows if row.get("status") == "folklore"),
        "council_count": _count_jsonl(home_path / "council" / "sessions.jsonl"),
        "fieldwork_count": fields["total"],
        "open_fieldwork_count": fields["open"],
        "closed_fieldwork_count": fields["closed"],
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
            (
                f"Fieldwork: {c.get('fieldwork_count')} total "
                f"({c.get('open_fieldwork_count')} open, {c.get('closed_fieldwork_count')} closed)"
            ),
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
            if not cli._ensure_runtime_credentials():
                return 1
            turn_route = cli._resolve_turn_agent_config(question)
            if not cli._init_agent(
                model_override=turn_route["model"],
                runtime_override=turn_route["runtime"],
                request_overrides=turn_route.get("request_overrides"),
            ):
                return 1
            result = run_tribe_ask(question, agent=cli.agent)
            print(render_tribe_result(result, json_output=json_output))
            return 0
    except (TribeNotBornError, TribeCouncilError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2
