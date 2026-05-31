"""Fieldwork evidence layer for Tribal.

Fieldwork turns council decisions into open real-world experiments. V1 keeps
the core provider-agnostic: calendar evidence enters through normalized JSON,
and reports recommend outcomes without mutating lore.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tribal_constants import get_tribal_home


SCHEMA_VERSION = 1
DEFAULT_FIELD_WINDOW_DAYS = 7


class FieldworkError(RuntimeError):
    """Raised when a Fieldwork command cannot complete."""


class FieldworkNotBornError(FieldworkError):
    """Raised when Fieldwork needs Genesis but the home is unborn."""


@dataclass
class FieldworkResult:
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


def _home(value: str | Path | None) -> Path:
    return Path(value) if value is not None else get_tribal_home()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _require_birth(home: Path) -> dict[str, Any]:
    birth = _read_json(home / "genesis.json")
    if not birth:
        raise FieldworkNotBornError("Run `tribal genesis` first.")
    return birth


def _parse_time(value: Any, *, label: str) -> datetime:
    if not value:
        raise FieldworkError(f"{label} is required.")
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FieldworkError(f"{label} must be ISO 8601 datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lineage_event(event: str, *, tribe_id: str, now: datetime, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event": event,
        "lineage_event_id": f"lin_{uuid.uuid4().hex[:10]}",
        "tribe_id": tribe_id,
        "timestamp": _iso_z(now),
    }
    payload.update(extra)
    return payload


def _experiments_path(home: Path) -> Path:
    return home / "fieldwork" / "experiments.jsonl"


def _reports_path(home: Path) -> Path:
    return home / "fieldwork" / "reports.jsonl"


def _field_experiments(home: Path) -> list[dict[str, Any]]:
    return _read_jsonl(_experiments_path(home))


def _find_experiment(home: Path, field_id: str) -> dict[str, Any]:
    for experiment in _field_experiments(home):
        if experiment.get("field_id") == field_id:
            return experiment
    raise FieldworkError(f"Fieldwork experiment not found: {field_id}")


def _recommendation_name(value: str) -> str:
    value = (value or "none").strip().lower()
    if value == "confirm":
        return "recommend_confirm"
    if value == "falsify":
        return "recommend_falsify"
    return "insufficient_evidence"


def open_fieldwork_for_council(
    *,
    home: str | Path,
    council: dict[str, Any],
    now: datetime | None = None,
) -> list[str]:
    home_path = _home(home)
    birth = _require_birth(home_path)
    now = now or _utc_now()
    tribe_id = str(council.get("tribe_id") or birth.get("tribe_id") or "local")
    asked_at = _parse_time(council.get("asked_at") or _iso_z(now), label="council asked_at")
    window_end = asked_at + timedelta(days=DEFAULT_FIELD_WINDOW_DAYS)
    consensus = council.get("consensus") if isinstance(council.get("consensus"), dict) else {}
    decision = consensus.get("decision") if isinstance(consensus.get("decision"), dict) else {}
    oracle = consensus.get("oracle") if isinstance(consensus.get("oracle"), dict) else {}
    opened: list[str] = []

    for lemma_id in council.get("draft_lemmas") or []:
        field_id = f"field_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}"
        experiment_text = str(decision.get("experiment") or "").strip()
        experiment = {
            "schema_version": SCHEMA_VERSION,
            "field_id": field_id,
            "tribe_id": tribe_id,
            "council_id": council.get("council_id"),
            "lemma_id": lemma_id,
            "status": "open",
            "question": council.get("question", ""),
            "decision": decision,
            "experiment": experiment_text,
            "falsifiers": consensus.get("falsifiers") or [],
            "oracle_id": oracle.get("oracle_id"),
            "signal_to_watch": oracle.get("signal_to_watch") or "",
            "window": {"start": _iso_z(asked_at), "end": _iso_z(window_end)},
            "sources": ["calendar"],
            "created_at": _iso_z(now),
            "closed_at": None,
        }
        _append_jsonl(_experiments_path(home_path), experiment)
        _append_jsonl(
            home_path / "lineage.jsonl",
            _lineage_event(
                "fieldwork.opened",
                tribe_id=tribe_id,
                now=now,
                field_id=field_id,
                council_id=council.get("council_id"),
                lemma_id=lemma_id,
            ),
        )
        opened.append(field_id)
    return opened


def run_field_list(
    *,
    home: str | Path | None = None,
    status: str | None = "open",
) -> FieldworkResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    status_filter = (status or "open").strip().lower()
    experiments = _field_experiments(home_path)
    if status_filter != "all":
        experiments = [item for item in experiments if item.get("status", "open") == status_filter]
    return FieldworkResult(
        status="listed",
        home=home_path,
        payload={
            "tribe_id": birth.get("tribe_id", "local"),
            "status_filter": status_filter,
            "experiments": experiments,
        },
    )


def run_field_show(field_id: str, *, home: str | Path | None = None) -> FieldworkResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    experiment = _find_experiment(home_path, field_id)
    reports = [row for row in _read_jsonl(_reports_path(home_path)) if row.get("field_id") == field_id]
    return FieldworkResult(
        status="shown",
        home=home_path,
        payload={"tribe_id": birth.get("tribe_id", "local"), "experiment": experiment, "reports": reports},
    )


def _load_calendar_events(input_path: str | Path) -> list[dict[str, Any]]:
    path = Path(input_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FieldworkError(f"Calendar input not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FieldworkError(f"Calendar input must be JSON: {path}") from exc

    events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise FieldworkError("Calendar input must be a JSON array or an object with an events array.")

    normalized: list[dict[str, Any]] = []
    for idx, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise FieldworkError(f"Calendar event {idx} must be an object.")
        title = str(event.get("title") or event.get("summary") or "").strip()
        if not title:
            raise FieldworkError(f"Calendar event {idx} title is required.")
        start = _parse_time(event.get("start"), label=f"Calendar event {idx} start")
        end = _parse_time(event.get("end"), label=f"Calendar event {idx} end")
        if end <= start:
            raise FieldworkError(f"Calendar event {idx} end must be after start.")
        normalized.append({
            "title": title,
            "start": _iso_z(start),
            "end": _iso_z(end),
            "description": str(event.get("description") or ""),
            "location": str(event.get("location") or ""),
            "attendees": event.get("attendees") if isinstance(event.get("attendees"), list) else [],
            "source": str(event.get("source") or "calendar-json"),
        })
    return normalized


def _calendar_observations(events: list[dict[str, Any]], experiment: dict[str, Any]) -> dict[str, Any]:
    window = experiment.get("window") if isinstance(experiment.get("window"), dict) else {}
    window_start = _parse_time(window.get("start"), label="field window start")
    window_end = _parse_time(window.get("end"), label="field window end")
    in_window: list[dict[str, Any]] = []
    total_minutes = 0
    demo_events = 0
    investor_events = 0
    focus_events = 0
    meeting_events = 0
    meeting_minutes = 0
    demo_keywords = ("demo", "ship", "shipping", "launch", "release", "build", "code")
    investor_keywords = ("investor", "vc", "fundraise", "fundraising", "intro", "pitch")
    focus_keywords = ("focus", "deep work", "maker", "build block")
    meeting_keywords = ("meeting", "call", "sync", "intro")

    for event in events:
        start = _parse_time(event["start"], label="event start")
        end = _parse_time(event["end"], label="event end")
        if end <= window_start or start >= window_end:
            continue
        text = " ".join([
            event.get("title", ""),
            event.get("description", ""),
            event.get("location", ""),
        ]).lower()
        in_window.append(event)
        total_minutes += int((end - start).total_seconds() // 60)
        demo_events += int(any(keyword in text for keyword in demo_keywords))
        investor_events += int(any(keyword in text for keyword in investor_keywords))
        focus_events += int(any(keyword in text for keyword in focus_keywords))
        is_meeting = any(keyword in text for keyword in meeting_keywords)
        meeting_events += int(is_meeting)
        if is_meeting:
            meeting_minutes += int((end - start).total_seconds() // 60)

    observations = {
        "calendar": {
            "event_count": len(in_window),
            "meeting_count": meeting_events,
            "scheduled_hours": round(total_minutes / 60, 2),
            "total_meeting_hours": round(meeting_minutes / 60, 2),
            "focus_event_count": focus_events,
            "free_block_count": 0,
            "demo_event_count": demo_events,
            "investor_event_count": investor_events,
            "window": {"start": _iso_z(window_start), "end": _iso_z(window_end)},
        }
    }
    signal = str(experiment.get("signal_to_watch") or "").strip()
    if signal:
        observations["oracle"] = {
            "oracle_id": experiment.get("oracle_id"),
            "signal_to_watch": signal,
        }
    return observations


def _recommend_from_calendar(experiment: dict[str, Any], observations: dict[str, Any]) -> str:
    calendar = observations.get("calendar") if isinstance(observations.get("calendar"), dict) else {}
    decision = experiment.get("decision") if isinstance(experiment.get("decision"), dict) else {}
    call = str(decision.get("call") or experiment.get("experiment") or "").lower()
    demo_count = int(calendar.get("demo_event_count") or 0)
    investor_count = int(calendar.get("investor_event_count") or 0)
    if any(token in call for token in ("ship", "demo", "launch", "build")):
        if demo_count > 0:
            return "recommend_confirm"
        if investor_count > 0:
            return "recommend_falsify"
    if calendar.get("event_count"):
        return "insufficient_evidence"
    return "insufficient_evidence"


def _evidence_text(experiment: dict[str, Any], observations: dict[str, Any], recommendation: str) -> str:
    calendar = observations.get("calendar") if isinstance(observations.get("calendar"), dict) else {}
    action = {
        "recommend_confirm": "confirming",
        "recommend_falsify": "falsifying",
    }.get(recommendation, "inconclusive")
    return (
        f"Fieldwork calendar evidence for {experiment.get('field_id')}: "
        f"{calendar.get('event_count', 0)} event(s), "
        f"{calendar.get('demo_event_count', 0)} demo/shipping event(s), "
        f"{calendar.get('investor_event_count', 0)} investor event(s), "
        f"{calendar.get('focus_event_count', 0)} focus block(s). "
        + (
            f"Oracle signal to watch: {experiment.get('signal_to_watch')}. "
            if experiment.get("signal_to_watch")
            else ""
        )
        + f"Recommendation: {action} evidence."
    )


def _target_experiments(home: Path, target: str, *, all_open: bool) -> list[dict[str, Any]]:
    if all_open:
        return [item for item in _field_experiments(home) if item.get("status", "open") == "open"]
    return [_find_experiment(home, target)]


def run_field_observe(
    target: str,
    *,
    adapter: str,
    input_path: str | Path,
    home: str | Path | None = None,
    all_open: bool = False,
    now: datetime | None = None,
) -> FieldworkResult:
    if adapter != "calendar-json":
        raise FieldworkError("Only the calendar-json adapter is supported in Fieldwork v1.")
    home_path = _home(home)
    birth = _require_birth(home_path)
    now = now or _utc_now()
    events = _load_calendar_events(input_path)
    reports: list[dict[str, Any]] = []
    for experiment in _target_experiments(home_path, target, all_open=all_open):
        observations = _calendar_observations(events, experiment)
        recommendation = _recommend_from_calendar(experiment, observations)
        report = {
            "schema_version": SCHEMA_VERSION,
            "report_id": f"frep_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}",
            "field_id": experiment.get("field_id"),
            "created_at": _iso_z(now),
            "source": {"type": "calendar-json", "input_label": str(input_path)},
            "observations": observations,
            "recommendation": recommendation,
            "evidence": _evidence_text(experiment, observations, recommendation),
        }
        _append_jsonl(_reports_path(home_path), report)
        _append_jsonl(
            home_path / "lineage.jsonl",
            _lineage_event(
                "fieldwork.observed",
                tribe_id=str(experiment.get("tribe_id") or birth.get("tribe_id") or "local"),
                now=now,
                field_id=experiment.get("field_id"),
                report_id=report["report_id"],
                recommendation=recommendation,
            ),
        )
        reports.append(report)
    return FieldworkResult(status="observed", home=home_path, payload={"reports": reports})


def run_field_report(
    field_id: str,
    *,
    evidence: str,
    recommend: str = "none",
    home: str | Path | None = None,
    now: datetime | None = None,
) -> FieldworkResult:
    evidence = (evidence or "").strip()
    if not evidence:
        raise FieldworkError("Evidence is required.")
    home_path = _home(home)
    birth = _require_birth(home_path)
    experiment = _find_experiment(home_path, field_id)
    now = now or _utc_now()
    recommendation = _recommendation_name(recommend)
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": f"frep_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}",
        "field_id": field_id,
        "created_at": _iso_z(now),
        "source": {"type": "manual", "input_label": "manual"},
        "observations": {"manual": {"evidence": evidence}},
        "recommendation": recommendation,
        "evidence": evidence,
    }
    _append_jsonl(_reports_path(home_path), report)
    _append_jsonl(
        home_path / "lineage.jsonl",
        _lineage_event(
            "fieldwork.reported",
            tribe_id=str(experiment.get("tribe_id") or birth.get("tribe_id") or "local"),
            now=now,
            field_id=field_id,
            report_id=report["report_id"],
            recommendation=recommendation,
        ),
    )
    return FieldworkResult(status="reported", home=home_path, payload={"report": report})


def fieldwork_counts(*, home: str | Path | None = None) -> dict[str, int]:
    home_path = _home(home)
    experiments = _field_experiments(home_path)
    return {
        "open": sum(1 for item in experiments if item.get("status", "open") == "open"),
        "closed": sum(1 for item in experiments if item.get("status") == "closed"),
        "total": len(experiments),
    }


def render_fieldwork_result(result: FieldworkResult, *, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {"status": result.status, "home": str(result.home), "payload": result.payload},
            indent=2,
            ensure_ascii=False,
        )
    if result.status == "listed":
        lines = ["TRIBAL FIELDWORK", ""]
        experiments = result.payload.get("experiments", [])
        if not experiments:
            lines.append("No fieldwork experiments found.")
        for experiment in experiments:
            decision = experiment.get("decision") if isinstance(experiment.get("decision"), dict) else {}
            lines.append(
                f"{experiment.get('field_id')}: [{experiment.get('status')}] "
                f"{experiment.get('lemma_id')} -- {decision.get('call') or experiment.get('experiment')}"
            )
        return "\n".join(lines)
    if result.status == "shown":
        experiment = result.payload["experiment"]
        decision = experiment.get("decision") if isinstance(experiment.get("decision"), dict) else {}
        return "\n".join([
            "TRIBAL FIELDWORK",
            "",
            f"Field: {experiment.get('field_id')}",
            f"Status: {experiment.get('status')}",
            f"Lemma: {experiment.get('lemma_id')}",
            f"Decision: {decision.get('call') or ''}",
            f"Window: {experiment.get('window', {}).get('start')} -> {experiment.get('window', {}).get('end')}",
            f"Reports: {len(result.payload.get('reports') or [])}",
        ])
    if result.status == "observed":
        lines = ["TRIBAL FIELDWORK OBSERVED", ""]
        reports = result.payload.get("reports", [])
        if not reports:
            lines.append("No open fieldwork experiments observed.")
        for report in reports:
            lines.append(f"{report.get('field_id')}: {report.get('recommendation')} -- {report.get('evidence')}")
        return "\n".join(lines)
    if result.status == "reported":
        report = result.payload["report"]
        return "\n".join([
            "TRIBAL FIELDWORK REPORT",
            "",
            f"Field: {report.get('field_id')}",
            f"Recommendation: {report.get('recommendation')}",
            f"Evidence: {report.get('evidence')}",
        ])
    return json.dumps(result.payload, ensure_ascii=False, indent=2)


def handle_field_slash_command(command: str) -> str:
    parts = shlex.split(command)
    argv = parts[1:] if parts and parts[0].lstrip("/").lower() == "field" else parts
    if not argv:
        argv = ["list"]
    json_output = "--json" in argv
    argv = [part for part in argv if part != "--json"]
    subcmd = argv[0].lower()
    rest = argv[1:]
    try:
        if subcmd == "list":
            status = "open"
            if "--status" in rest:
                idx = rest.index("--status")
                if idx + 1 < len(rest):
                    status = rest[idx + 1]
            return render_fieldwork_result(run_field_list(status=status), json_output=json_output)
        if subcmd == "show" and rest:
            return render_fieldwork_result(run_field_show(rest[0]), json_output=json_output)
        if subcmd == "observe":
            all_open = "--all" in rest
            rest = [part for part in rest if part != "--all"]
            target = rest[0] if rest else ""
            adapter = _option_value(rest, "--adapter") or "calendar-json"
            input_path = _option_value(rest, "--input")
            if not input_path:
                return "Usage: /field observe <field-id|--all> --adapter calendar-json --input events.json"
            return render_fieldwork_result(
                run_field_observe(target, adapter=adapter, input_path=input_path, all_open=all_open),
                json_output=json_output,
            )
        if subcmd == "report" and rest:
            field_id = rest[0]
            evidence = _option_value(rest, "--evidence")
            recommend = _option_value(rest, "--recommend") or "none"
            if evidence is None:
                evidence = " ".join(part for part in rest[1:] if not part.startswith("--"))
            return render_fieldwork_result(
                run_field_report(field_id, evidence=evidence, recommend=recommend),
                json_output=json_output,
            )
    except FieldworkError as exc:
        return str(exc)
    return "Usage: /field [list|show <field-id>|observe <field-id|--all>|report <field-id>]"


def _option_value(parts: list[str], flag: str) -> str | None:
    if flag not in parts:
        return None
    idx = parts.index(flag)
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def cmd_field(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "field_command", None) or "list"
    json_output = bool(getattr(args, "json", False))
    try:
        if subcmd == "list":
            result = run_field_list(status=getattr(args, "status", "open"))
        elif subcmd == "show":
            result = run_field_show(getattr(args, "field_id"))
        elif subcmd == "observe":
            result = run_field_observe(
                getattr(args, "field_id", "") or "",
                adapter=getattr(args, "adapter", "calendar-json"),
                input_path=getattr(args, "input"),
                all_open=bool(getattr(args, "all", False)),
            )
        elif subcmd == "report":
            result = run_field_report(
                getattr(args, "field_id"),
                evidence=getattr(args, "evidence", ""),
                recommend=getattr(args, "recommend", "none"),
            )
        else:
            print("Usage: tribal field <list|show|observe|report>", file=sys.stderr)
            return 2
    except FieldworkError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_fieldwork_result(result, json_output=json_output))
    return 0


def main_field(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tribal field")
    subparsers = parser.add_subparsers(dest="field_command")
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", choices=["open", "closed", "all"], default="open")
    list_parser.add_argument("--json", action="store_true", default=False)
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("field_id")
    show_parser.add_argument("--json", action="store_true", default=False)
    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("field_id", nargs="?")
    observe_parser.add_argument("--all", action="store_true", default=False)
    observe_parser.add_argument("--adapter", default="calendar-json")
    observe_parser.add_argument("--input", required=True)
    observe_parser.add_argument("--json", action="store_true", default=False)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("field_id")
    report_parser.add_argument("--evidence", required=True)
    report_parser.add_argument("--recommend", choices=["confirm", "falsify", "none"], default="none")
    report_parser.add_argument("--json", action="store_true", default=False)
    return cmd_field(parser.parse_args(argv))
