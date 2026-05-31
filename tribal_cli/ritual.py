"""Ritual canon engine for Tribal lore.

Ritual is the review layer after councils write folklore. It records lived
outcomes, preserves falsifiers, and recommends which lemmas stay folklore,
become canon, go stale, or get falsified.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tribal_constants import get_tribal_home
from tribal_cli.genesis import DEFAULT_LAW
from utils import atomic_replace, atomic_yaml_write


SCHEMA_VERSION = 1
DEFAULT_RITUAL_LAW: dict[str, Any] = {
    "canon_min_confirmations": 2,
    "canon_requires_no_open_falsifiers": True,
    "stale_after_days": 45,
    "max_promotions_per_review": 3,
}


class RitualError(RuntimeError):
    """Raised when a Ritual command cannot complete."""


class RitualNotBornError(RitualError):
    """Raised when Ritual needs Genesis but the home is unborn."""


@dataclass
class RitualResult:
    status: str
    home: Path
    payload: dict[str, Any]


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


def _require_birth(home: Path) -> dict[str, Any]:
    birth = _read_json(home / "genesis.json")
    if not birth:
        raise RitualNotBornError("Run `tribal genesis` first.")
    return birth


def _ensure_law(home: Path) -> dict[str, Any]:
    law_path = home / "law.yaml"
    if not law_path.exists():
        atomic_yaml_write(law_path, DEFAULT_LAW, sort_keys=False)
        return dict(DEFAULT_LAW)
    law = _read_yaml(law_path)
    return law or dict(DEFAULT_LAW)


def _ritual_law(law: dict[str, Any]) -> dict[str, Any]:
    raw = law.get("ritual") if isinstance(law.get("ritual"), dict) else {}
    merged = dict(DEFAULT_RITUAL_LAW)
    merged.update(raw)
    try:
        merged["canon_min_confirmations"] = max(1, int(merged["canon_min_confirmations"]))
    except (TypeError, ValueError):
        merged["canon_min_confirmations"] = DEFAULT_RITUAL_LAW["canon_min_confirmations"]
    try:
        merged["stale_after_days"] = max(1, int(merged["stale_after_days"]))
    except (TypeError, ValueError):
        merged["stale_after_days"] = DEFAULT_RITUAL_LAW["stale_after_days"]
    try:
        merged["max_promotions_per_review"] = max(0, int(merged["max_promotions_per_review"]))
    except (TypeError, ValueError):
        merged["max_promotions_per_review"] = DEFAULT_RITUAL_LAW["max_promotions_per_review"]
    merged["canon_requires_no_open_falsifiers"] = bool(merged["canon_requires_no_open_falsifiers"])
    return merged


def _read_lemma_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            entries.append({"kind": "raw", "raw": raw})
            continue
        if isinstance(parsed, dict):
            entries.append({"kind": "lemma", "value": parsed})
        else:
            entries.append({"kind": "raw", "raw": raw})
    return entries


def _valid_lemmas(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry["value"] for entry in entries if entry.get("kind") == "lemma"]


def _write_lemma_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            if entry.get("kind") == "lemma":
                f.write(json.dumps(entry["value"], ensure_ascii=False, sort_keys=True) + "\n")
            else:
                f.write(str(entry.get("raw", "")) + "\n")
        f.flush()
    atomic_replace(tmp, path)


def _lemma_summary(lemma: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": lemma.get("id"),
        "status": lemma.get("status", "folklore"),
        "claim": lemma.get("claim", ""),
        "confidence": lemma.get("confidence"),
        "promotion": lemma.get("promotion", {}),
        "falsifiers": lemma.get("falsifiers", []),
        "source": lemma.get("source", {}),
    }


def _find_lemma(entries: list[dict[str, Any]], lemma_id: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("kind") == "lemma" and entry["value"].get("id") == lemma_id:
            return entry["value"]
    raise RitualError(f"Lemma not found: {lemma_id}")


def _promotion(lemma: dict[str, Any]) -> dict[str, Any]:
    promotion = lemma.get("promotion")
    if not isinstance(promotion, dict):
        promotion = {}
        lemma["promotion"] = promotion
    promotion.setdefault("status", "unvalidated")
    promotion.setdefault("confirmed_count", 0)
    promotion.setdefault("falsified_count", 0)
    promotion.setdefault("last_reviewed_at", None)
    return promotion


def _ensure_outcomes(lemma: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = lemma.get("outcomes")
    if not isinstance(outcomes, list):
        outcomes = []
        lemma["outcomes"] = outcomes
    return outcomes


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _outcome_event(
    *,
    lemma: dict[str, Any],
    tribe_id: str,
    kind: str,
    evidence: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": f"out_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}",
        "lemma_id": lemma.get("id"),
        "tribe_id": tribe_id,
        "kind": kind,
        "evidence": evidence,
        "created_at": _iso_z(now),
    }


def _lineage_event(event: str, *, tribe_id: str, lemma_id: str | None, now: datetime, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event": event,
        "lineage_event_id": f"lin_{uuid.uuid4().hex[:10]}",
        "tribe_id": tribe_id,
        "lemma_id": lemma_id,
        "timestamp": _iso_z(now),
    }
    payload.update(extra)
    return payload


def _home(value: str | Path | None) -> Path:
    return Path(value) if value is not None else get_tribal_home()


def run_lore_list(
    *,
    home: str | Path | None = None,
    status: str | None = None,
) -> RitualResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    entries = _read_lemma_entries(home_path / "lore" / "lemmas.jsonl")
    status_filter = (status or "active").strip().lower()
    allowed = {"folklore", "canon"} if status_filter in {"", "active"} else None
    lemmas: list[dict[str, Any]] = []
    for lemma in _valid_lemmas(entries):
        lemma_status = str(lemma.get("status", "folklore"))
        if status_filter == "all" or (allowed and lemma_status in allowed) or lemma_status == status_filter:
            lemmas.append(_lemma_summary(lemma))
    return RitualResult(
        status="listed",
        home=home_path,
        payload={"tribe_id": birth.get("tribe_id", "local"), "status_filter": status_filter, "lemmas": lemmas},
    )


def run_lore_show(
    lemma_id: str,
    *,
    home: str | Path | None = None,
) -> RitualResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    entries = _read_lemma_entries(home_path / "lore" / "lemmas.jsonl")
    lemma = _find_lemma(entries, lemma_id)
    return RitualResult(
        status="shown",
        home=home_path,
        payload={"tribe_id": birth.get("tribe_id", "local"), "lemma": lemma},
    )


def _record_outcome(
    lemma_id: str,
    *,
    evidence: str,
    kind: str,
    home: str | Path | None = None,
    now: datetime | None = None,
) -> RitualResult:
    evidence = (evidence or "").strip()
    if not evidence:
        raise RitualError("Evidence is required.")
    home_path = _home(home)
    birth = _require_birth(home_path)
    tribe_id = str(birth.get("tribe_id") or "local")
    now = now or _utc_now()
    path = home_path / "lore" / "lemmas.jsonl"
    entries = _read_lemma_entries(path)
    lemma = _find_lemma(entries, lemma_id)
    promotion = _promotion(lemma)
    event = _outcome_event(lemma=lemma, tribe_id=tribe_id, kind=kind, evidence=evidence, now=now)
    _ensure_outcomes(lemma).append(event)
    if kind == "confirm":
        promotion["confirmed_count"] = int(promotion.get("confirmed_count") or 0) + 1
        status = "confirmed"
        lineage_kind = "lore.confirmed"
    else:
        promotion["falsified_count"] = int(promotion.get("falsified_count") or 0) + 1
        promotion["status"] = "falsified"
        lemma["status"] = "falsified"
        status = "falsified"
        lineage_kind = "lore.falsified"

    _write_lemma_entries(path, entries)
    _append_jsonl(home_path / "lore" / "outcomes.jsonl", event)
    lineage = _lineage_event(
        lineage_kind,
        tribe_id=tribe_id,
        lemma_id=lemma_id,
        now=now,
        outcome_id=event["outcome_id"],
        evidence=evidence,
    )
    _append_jsonl(home_path / "lineage.jsonl", lineage)
    return RitualResult(status=status, home=home_path, payload={"tribe_id": tribe_id, "lemma": lemma, "outcome": event})


def run_lore_confirm(
    lemma_id: str,
    *,
    evidence: str,
    home: str | Path | None = None,
    now: datetime | None = None,
) -> RitualResult:
    return _record_outcome(lemma_id, evidence=evidence, kind="confirm", home=home, now=now)


def run_lore_falsify(
    lemma_id: str,
    *,
    evidence: str,
    home: str | Path | None = None,
    now: datetime | None = None,
) -> RitualResult:
    return _record_outcome(lemma_id, evidence=evidence, kind="falsify", home=home, now=now)


def _recommendation(lemma: dict[str, Any], law: dict[str, Any], now: datetime) -> tuple[str, str]:
    promotion = _promotion(lemma)
    confirmed = int(promotion.get("confirmed_count") or 0)
    falsified = int(promotion.get("falsified_count") or 0)
    if falsified > 0 or lemma.get("status") == "falsified":
        return "falsify", f"{falsified} falsifying outcome(s) recorded."

    created = _parse_time(lemma.get("created_at"))
    if created is not None:
        age_days = (now - created).days
        if age_days >= int(law["stale_after_days"]):
            return "mark_stale", f"{age_days} days old; stale threshold is {law['stale_after_days']} days."

    min_confirmations = int(law["canon_min_confirmations"])
    if confirmed >= min_confirmations and lemma.get("status") != "canon":
        return "promote_to_canon", f"{confirmed} confirmations meet canon threshold of {min_confirmations}."

    return "keep_folklore", f"{confirmed} confirmation(s); canon requires {min_confirmations}."


def run_ritual_review(
    *,
    home: str | Path | None = None,
    now: datetime | None = None,
) -> RitualResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    law = _ritual_law(_ensure_law(home_path))
    now = now or _utc_now()
    entries = _read_lemma_entries(home_path / "lore" / "lemmas.jsonl")
    tribe_id = str(birth.get("tribe_id") or "local")
    recommendations: list[dict[str, Any]] = []
    promotions = 0

    for lemma in _valid_lemmas(entries):
        status = str(lemma.get("status", "folklore"))
        if status not in {"folklore", "canon", "falsified"}:
            continue
        recommendation, reason = _recommendation(lemma, law, now)
        if recommendation == "promote_to_canon":
            if promotions >= int(law["max_promotions_per_review"]):
                recommendation = "keep_folklore"
                reason = "Promotion limit reached for this ritual review."
            else:
                promotions += 1
        _promotion(lemma)["last_reviewed_at"] = _iso_z(now)
        recommendations.append({
            "lemma_id": lemma.get("id"),
            "claim": lemma.get("claim", ""),
            "status": status,
            "recommendation": recommendation,
            "reason": reason,
            "open_falsifiers": lemma.get("falsifiers", []),
        })

    review = {
        "schema_version": SCHEMA_VERSION,
        "review_id": f"rit_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}",
        "tribe_id": tribe_id,
        "reviewed_at": _iso_z(now),
        "recommendations": recommendations,
    }
    _write_lemma_entries(home_path / "lore" / "lemmas.jsonl", entries)
    _append_jsonl(home_path / "ritual" / "reviews.jsonl", review)
    _append_jsonl(
        home_path / "lineage.jsonl",
        _lineage_event(
            "ritual.reviewed",
            tribe_id=tribe_id,
            lemma_id=None,
            now=now,
            review_id=review["review_id"],
            recommendations=len(recommendations),
        ),
    )
    return RitualResult(status="reviewed", home=home_path, payload=review)


def render_ritual_result(result: RitualResult, *, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {"status": result.status, "home": str(result.home), "payload": result.payload},
            indent=2,
            ensure_ascii=False,
        )
    if result.status == "listed":
        lines = ["TRIBAL LORE", ""]
        lemmas = result.payload.get("lemmas", [])
        if not lemmas:
            lines.append("No lore found.")
        for lemma in lemmas:
            lines.append(f"{lemma.get('id')}: [{lemma.get('status')}] {lemma.get('claim')}")
        return "\n".join(lines)
    if result.status == "shown":
        lemma = result.payload["lemma"]
        lines = [
            "TRIBAL LORE",
            "",
            f"ID: {lemma.get('id')}",
            f"Status: {lemma.get('status', 'folklore')}",
            f"Claim: {lemma.get('claim', '')}",
            f"Falsifiers: {len(lemma.get('falsifiers') or [])}",
            f"Outcomes: {len(lemma.get('outcomes') or [])}",
        ]
        return "\n".join(lines)
    if result.status in {"confirmed", "falsified"}:
        lemma = result.payload["lemma"]
        outcome = result.payload["outcome"]
        label = "CONFIRMED" if result.status == "confirmed" else "FALSIFIED"
        return "\n".join([
            f"LORE {label}",
            "",
            f"Lemma: {lemma.get('id')}",
            f"Status: {lemma.get('status', 'folklore')}",
            f"Evidence: {outcome.get('evidence')}",
        ])
    if result.status == "reviewed":
        lines = ["TRIBAL RITUAL", ""]
        recommendations = result.payload.get("recommendations", [])
        if not recommendations:
            lines.append("No folklore to review.")
        for row in recommendations:
            lines.append(f"{row.get('lemma_id')}: {row.get('recommendation')} -- {row.get('reason')}")
        return "\n".join(lines)
    return json.dumps(result.payload, ensure_ascii=False, indent=2)


def _slash_parts(command: str, canonical: str) -> list[str]:
    parts = shlex.split(command)
    if parts and parts[0].lstrip("/").lower() == canonical:
        return parts[1:]
    return parts


def handle_lore_slash_command(command: str) -> str:
    parts = _slash_parts(command, "lore")
    json_output = "--json" in parts
    parts = [part for part in parts if part != "--json"]
    try:
        if not parts:
            return render_ritual_result(run_lore_list(), json_output=json_output)
        if parts[0] == "show" and len(parts) >= 2:
            return render_ritual_result(run_lore_show(parts[1]), json_output=json_output)
        if parts[0] == "list":
            status = None
            if "--status" in parts:
                idx = parts.index("--status")
                if idx + 1 < len(parts):
                    status = parts[idx + 1]
            return render_ritual_result(run_lore_list(status=status), json_output=json_output)
    except RitualError as exc:
        return str(exc)
    return "Usage: /lore [list|show <lemma-id>]"


def handle_outcome_slash_command(command: str) -> str:
    parts = _slash_parts(command, "outcome")
    if len(parts) < 2:
        return "Usage: /outcome <lemma-id> <evidence>"
    lemma_id = parts[0]
    evidence = " ".join(parts[1:])
    try:
        return render_ritual_result(run_lore_confirm(lemma_id, evidence=evidence))
    except RitualError as exc:
        return str(exc)


def handle_falsify_slash_command(command: str) -> str:
    parts = _slash_parts(command, "falsify")
    if len(parts) < 2:
        return "Usage: /falsify <lemma-id> <evidence>"
    lemma_id = parts[0]
    evidence = " ".join(parts[1:])
    try:
        return render_ritual_result(run_lore_falsify(lemma_id, evidence=evidence))
    except RitualError as exc:
        return str(exc)


def handle_ritual_slash_command(command: str) -> str:
    parts = _slash_parts(command, "ritual")
    json_output = "--json" in parts
    parts = [part for part in parts if part != "--json"]
    try:
        if not parts or parts[0] == "review":
            return render_ritual_result(run_ritual_review(), json_output=json_output)
    except RitualError as exc:
        return str(exc)
    return "Usage: /ritual review"


def cmd_lore(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "lore_command", None) or "list"
    json_output = bool(getattr(args, "json", False))
    try:
        if subcmd == "list":
            result = run_lore_list(status=getattr(args, "status", None))
        elif subcmd == "show":
            result = run_lore_show(getattr(args, "lemma_id"))
        elif subcmd == "confirm":
            result = run_lore_confirm(getattr(args, "lemma_id"), evidence=getattr(args, "evidence", ""))
        elif subcmd == "falsify":
            result = run_lore_falsify(getattr(args, "lemma_id"), evidence=getattr(args, "evidence", ""))
        else:
            print("Usage: tribal lore <list|show|confirm|falsify>", file=sys.stderr)
            return 2
    except RitualError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_ritual_result(result, json_output=json_output))
    return 0


def cmd_ritual(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "ritual_command", None) or "review"
    json_output = bool(getattr(args, "json", False))
    try:
        if subcmd == "review":
            result = run_ritual_review()
        else:
            print("Usage: tribal ritual review", file=sys.stderr)
            return 2
    except RitualError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_ritual_result(result, json_output=json_output))
    return 0


def main_lore(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tribal lore")
    parser.add_argument("lore_command", choices=["list", "show", "confirm", "falsify"])
    parser.add_argument("lemma_id", nargs="?")
    parser.add_argument("--status", default=None)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--json", action="store_true", default=False)
    return cmd_lore(parser.parse_args(argv))


def main_ritual(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tribal ritual")
    parser.add_argument("ritual_command", choices=["review"])
    parser.add_argument("--json", action="store_true", default=False)
    return cmd_ritual(parser.parse_args(argv))
