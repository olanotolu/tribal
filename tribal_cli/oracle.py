"""Oracle simulation adapter for Tribal.

MiroFish remains an external engine. Tribal talks to it over HTTP, stores a
receipt, and falls back to a text-only Oracle when no simulator is configured.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from tribal_constants import get_tribal_home


SCHEMA_VERSION = 1
DEFAULT_HORIZON_DAYS = 7
DEFAULT_TIMEOUT_SECONDS = 30.0


class OracleError(RuntimeError):
    """Raised when an Oracle command cannot complete."""


class OracleNotBornError(OracleError):
    """Raised when Oracle persistence needs Genesis but the home is unborn."""


class MiroFishError(OracleError):
    """Raised when the MiroFish adapter cannot complete a request."""


@dataclass
class OracleResult:
    status: str
    home: Path | None
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
        raise OracleNotBornError("Run `tribal genesis` first.")
    return birth


def _base_url() -> str | None:
    value = os.getenv("TRIBAL_MIROFISH_BASE_URL", "").strip()
    return value.rstrip("/") if value else None


def _timeout() -> float:
    raw = os.getenv("TRIBAL_MIROFISH_TIMEOUT_SECONDS", "")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class MiroFishHTTPTransport:
    """Small JSON/multipart transport for MiroFish's Flask API."""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except error.URLError as exc:
            raise MiroFishError(str(exc)) from exc
        return json.loads(body or "{}")

    def get_json(self, path: str) -> dict[str, Any]:
        return self._json_request("GET", path)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", path, payload)

    def post_multipart(
        self,
        path: str,
        *,
        fields: dict[str, str],
        files: list[tuple[str, str, bytes, str]],
    ) -> dict[str, Any]:
        boundary = f"----tribal-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        for field_name, filename, content, content_type in files:
            chunks.extend([
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)
        req = request.Request(
            self._url(path),
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                response_body = resp.read().decode("utf-8")
        except error.URLError as exc:
            raise MiroFishError(str(exc)) from exc
        return json.loads(response_body or "{}")


class MiroFishClient:
    """Client for the external MiroFish API shape."""

    def __init__(self, base_url: str, *, transport: Any | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.transport = transport or MiroFishHTTPTransport(self.base_url, timeout=timeout)

    def health(self) -> bool:
        payload = self.transport.get_json("/health")
        return str(payload.get("status", "")).lower() == "ok"

    def run_prediction(
        self,
        *,
        scenario: str,
        seed_text: str,
        horizon_days: int,
        wait: bool,
    ) -> dict[str, Any]:
        project = self.transport.post_multipart(
            "/api/graph/ontology/generate",
            fields={
                "simulation_requirement": scenario,
                "project_name": "Tribal Oracle",
                "additional_context": f"Prediction horizon: {horizon_days} day(s).",
            },
            files=[("files", "tribal-oracle-seed.md", seed_text.encode("utf-8"), "text/markdown")],
        )
        project_id = _data(project).get("project_id")
        if not project_id:
            raise MiroFishError("MiroFish did not return a project_id.")

        build = self.transport.post_json(
            "/api/graph/build",
            {"project_id": project_id, "graph_name": "Tribal Oracle"},
        )
        graph_task_id = _data(build).get("task_id")
        if not wait:
            return {
                "provider": "mirofish",
                "status": "submitted",
                "mirofish": {"project_id": project_id, "graph_task_id": graph_task_id},
            }

        if graph_task_id:
            self._poll_graph_task(graph_task_id)
        graph_project = _data(self.transport.get_json(f"/api/graph/project/{project_id}"))
        graph_id = graph_project.get("graph_id")
        if not graph_id:
            raise MiroFishError("MiroFish graph build completed without a graph_id.")

        sim = _data(self.transport.post_json(
            "/api/simulation/create",
            {"project_id": project_id, "graph_id": graph_id, "enable_twitter": True, "enable_reddit": True},
        ))
        simulation_id = sim.get("simulation_id")
        if not simulation_id:
            raise MiroFishError("MiroFish did not return a simulation_id.")

        prepare = _data(self.transport.post_json(
            "/api/simulation/prepare",
            {"simulation_id": simulation_id, "force_regenerate": False},
        ))
        prepare_task_id = prepare.get("task_id")
        if prepare_task_id or prepare.get("status") != "ready":
            self._poll_prepare(simulation_id, prepare_task_id)

        run_state = _data(self.transport.post_json(
            "/api/simulation/start",
            {
                "simulation_id": simulation_id,
                "platform": "parallel",
                "max_rounds": max(1, int(horizon_days)),
                "enable_graph_memory_update": False,
            },
        ))

        report_start = _data(self.transport.post_json(
            "/api/report/generate",
            {"simulation_id": simulation_id, "force_regenerate": False},
        ))
        report_id = report_start.get("report_id")
        report_task_id = report_start.get("task_id")
        if report_task_id or report_start.get("status") != "completed":
            report_done = self._poll_report(simulation_id, report_task_id)
            report_id = report_done.get("report_id") or report_id
        if not report_id:
            raise MiroFishError("MiroFish did not return a report_id.")

        report = _data(self.transport.get_json(f"/api/report/{report_id}"))
        structured = _prediction_fields(report)
        structured.update({
            "provider": "mirofish",
            "status": "completed",
            "mirofish": {
                "project_id": project_id,
                "graph_task_id": graph_task_id,
                "graph_id": graph_id,
                "simulation_id": simulation_id,
                "prepare_task_id": prepare_task_id,
                "run_state": run_state,
                "report_id": report_id,
            },
            "raw_report": report,
        })
        return structured

    def _poll_graph_task(self, task_id: str) -> dict[str, Any]:
        for _ in range(3):
            payload = _data(self.transport.get_json(f"/api/graph/task/{task_id}"))
            if str(payload.get("status")) in {"completed", "success"} or int(payload.get("progress") or 0) >= 100:
                return payload
            if str(payload.get("status")) == "failed":
                raise MiroFishError(str(payload.get("error") or "MiroFish graph task failed."))
        return payload

    def _poll_prepare(self, simulation_id: str, task_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for _ in range(3):
            payload = _data(self.transport.post_json(
                "/api/simulation/prepare/status",
                {"simulation_id": simulation_id, "task_id": task_id},
            ))
            if str(payload.get("status")) in {"ready", "completed"} or int(payload.get("progress") or 0) >= 100:
                return payload
            if str(payload.get("status")) == "failed":
                raise MiroFishError(str(payload.get("error") or "MiroFish prepare task failed."))
        return payload

    def _poll_report(self, simulation_id: str, task_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for _ in range(3):
            payload = _data(self.transport.post_json(
                "/api/report/generate/status",
                {"simulation_id": simulation_id, "task_id": task_id},
            ))
            if str(payload.get("status")) == "completed" or int(payload.get("progress") or 0) >= 100:
                return payload
            if str(payload.get("status")) == "failed":
                raise MiroFishError(str(payload.get("error") or "MiroFish report task failed."))
        return payload


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("success") is False:
        raise MiroFishError(str(payload.get("error") or "MiroFish request failed."))
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _prediction_fields(report: dict[str, Any]) -> dict[str, Any]:
    content = str(report.get("markdown_content") or report.get("content") or "")
    return {
        "assumptions": _as_list(report.get("assumptions")) or _extract_heading_list(content, "assumptions"),
        "weighted_outcomes": _as_list(report.get("weighted_outcomes")) or _extract_heading_list(content, "outcomes"),
        "deciding_factor": str(report.get("deciding_factor") or _extract_heading_text(content, "deciding factor") or ""),
        "signal_to_watch": str(report.get("signal_to_watch") or _extract_heading_text(content, "signal to watch") or ""),
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_heading_text(content: str, heading: str) -> str:
    if not content:
        return ""
    lower = content.lower()
    idx = lower.find(heading.lower())
    if idx < 0:
        return ""
    tail = content[idx + len(heading):].strip(" :#\n\t")
    return tail.splitlines()[0].strip(" -:") if tail else ""


def _extract_heading_list(content: str, heading: str) -> list[str]:
    text = _extract_heading_text(content, heading)
    return [text] if text else []


def _fallback_prediction(*, reason: str | None = None) -> dict[str, Any]:
    assumptions = ["No real MiroFish simulation was run; this is a text-only Oracle rehearsal."]
    if reason:
        assumptions.append(f"Fallback reason: {reason}")
    return {
        "provider": "text_fallback",
        "status": "completed",
        "mirofish": {},
        "assumptions": assumptions,
        "weighted_outcomes": [],
        "deciding_factor": "The next real-world signal gathered through Fieldwork.",
        "signal_to_watch": "A concrete outcome that confirms or falsifies the council decision.",
        "raw_report": {},
    }


def run_oracle_status(*, client: Any | None = None) -> OracleResult:
    base_url = _base_url()
    if not base_url:
        return OracleResult(
            status="not_configured",
            home=None,
            payload={"provider": "mirofish", "configured": False, "base_url": None},
        )
    client = client or MiroFishClient(base_url, timeout=_timeout())
    try:
        ok = bool(client.health())
    except Exception as exc:
        return OracleResult(
            status="unreachable",
            home=None,
            payload={"provider": "mirofish", "configured": True, "base_url": base_url, "error": str(exc)},
        )
    return OracleResult(
        status="configured" if ok else "unreachable",
        home=None,
        payload={"provider": "mirofish", "configured": True, "base_url": base_url, "healthy": ok},
    )


def run_oracle_simulate(
    scenario: str,
    *,
    seed_path: str | Path | None = None,
    seed_text: str | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    wait: bool = False,
    home: str | Path | None = None,
    source_council_id: str | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> OracleResult:
    scenario = (scenario or "").strip()
    if not scenario:
        raise OracleError("Usage: tribal oracle simulate <scenario>")
    home_path = _home(home)
    birth = _require_birth(home_path)
    now = now or _utc_now()
    tribe_id = str(birth.get("tribe_id") or "local")
    horizon_days = max(1, int(horizon_days or DEFAULT_HORIZON_DAYS))
    seed = _seed_text(scenario, seed_path=seed_path, seed_text=seed_text)
    base_url = _base_url()

    if not base_url:
        prediction = _fallback_prediction()
    else:
        client = client or MiroFishClient(base_url, timeout=_timeout())
        try:
            prediction = client.run_prediction(
                scenario=scenario,
                seed_text=seed,
                horizon_days=horizon_days,
                wait=wait,
            )
        except Exception as exc:
            prediction = _fallback_prediction(reason=str(exc))

    lineage_event_id = f"lin_{uuid.uuid4().hex[:10]}"
    record = {
        "schema_version": SCHEMA_VERSION,
        "oracle_id": f"oracle_{_stamp(now).lower()}_{uuid.uuid4().hex[:8]}",
        "tribe_id": tribe_id,
        "provider": prediction.get("provider", "mirofish"),
        "status": prediction.get("status", "completed"),
        "scenario": scenario,
        "horizon_days": horizon_days,
        "seed_path": str(seed_path) if seed_path else None,
        "source": {
            "type": "council" if source_council_id else "manual",
            "council_id": source_council_id,
        },
        "created_at": _iso_z(now),
        "completed_at": _iso_z(now) if prediction.get("status") == "completed" else None,
        "mirofish": prediction.get("mirofish") if isinstance(prediction.get("mirofish"), dict) else {},
        "assumptions": prediction.get("assumptions") if isinstance(prediction.get("assumptions"), list) else [],
        "weighted_outcomes": (
            prediction.get("weighted_outcomes") if isinstance(prediction.get("weighted_outcomes"), list) else []
        ),
        "deciding_factor": str(prediction.get("deciding_factor") or ""),
        "signal_to_watch": str(prediction.get("signal_to_watch") or ""),
        "raw_report": prediction.get("raw_report") if isinstance(prediction.get("raw_report"), dict) else {},
        "lineage_event_id": lineage_event_id,
    }
    _append_jsonl(home_path / "oracle" / "simulations.jsonl", record)
    _append_jsonl(
        home_path / "lineage.jsonl",
        {
            "schema_version": SCHEMA_VERSION,
            "event": "oracle.simulated",
            "lineage_event_id": lineage_event_id,
            "tribe_id": tribe_id,
            "oracle_id": record["oracle_id"],
            "council_id": source_council_id,
            "provider": record["provider"],
            "status": record["status"],
            "timestamp": _iso_z(now),
        },
    )
    return OracleResult(status="simulated", home=home_path, payload={"simulation": record})


def _seed_text(scenario: str, *, seed_path: str | Path | None, seed_text: str | None) -> str:
    if seed_text is not None:
        return seed_text
    if seed_path:
        return Path(seed_path).read_text(encoding="utf-8")
    return f"# Tribal Oracle Scenario\n\n{scenario}\n"


def run_oracle_show(oracle_id: str, *, home: str | Path | None = None) -> OracleResult:
    home_path = _home(home)
    birth = _require_birth(home_path)
    for row in _read_jsonl(home_path / "oracle" / "simulations.jsonl"):
        if row.get("oracle_id") == oracle_id:
            return OracleResult(
                status="shown",
                home=home_path,
                payload={"tribe_id": birth.get("tribe_id", "local"), "simulation": row},
            )
    raise OracleError(f"Oracle simulation not found: {oracle_id}")


def render_oracle_result(result: OracleResult, *, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {"status": result.status, "home": str(result.home) if result.home else None, "payload": result.payload},
            indent=2,
            ensure_ascii=False,
        )
    if result.status in {"not_configured", "configured", "unreachable"}:
        payload = result.payload
        lines = ["TRIBAL ORACLE", "", f"MiroFish: {result.status}"]
        if payload.get("base_url"):
            lines.append(f"Base URL: {payload.get('base_url')}")
        if payload.get("error"):
            lines.append(f"Error: {payload.get('error')}")
        if result.status == "not_configured":
            lines.append("Set TRIBAL_MIROFISH_BASE_URL to use a real simulation chamber.")
        return "\n".join(lines)
    simulation = result.payload["simulation"]
    lines = [
        "TRIBAL ORACLE",
        "",
        f"Oracle: {simulation.get('oracle_id')}",
        f"Provider: {simulation.get('provider')}",
        f"Status: {simulation.get('status')}",
        f"Scenario: {simulation.get('scenario')}",
    ]
    if simulation.get("deciding_factor"):
        lines.append(f"Deciding factor: {simulation.get('deciding_factor')}")
    if simulation.get("signal_to_watch"):
        lines.append(f"Signal to watch: {simulation.get('signal_to_watch')}")
    return "\n".join(lines)


def handle_oracle_slash_command(command: str) -> str:
    parts = shlex.split(command)
    argv = parts[1:] if parts and parts[0].lstrip("/").lower() == "oracle" else parts
    if not argv:
        argv = ["status"]
    json_output = "--json" in argv
    argv = [part for part in argv if part != "--json"]
    subcmd = argv[0].lower()
    rest = argv[1:]
    try:
        if subcmd == "status":
            return render_oracle_result(run_oracle_status(), json_output=json_output)
        if subcmd == "show" and rest:
            return render_oracle_result(run_oracle_show(rest[0]), json_output=json_output)
        if subcmd == "simulate":
            seed = _option_value(rest, "--seed")
            horizon = int(_option_value(rest, "--horizon-days") or DEFAULT_HORIZON_DAYS)
            wait = "--wait" in rest
            scenario = " ".join(_positional_args(rest, {"--seed", "--horizon-days", "--wait"}))
            return render_oracle_result(
                run_oracle_simulate(scenario, seed_path=seed, horizon_days=horizon, wait=wait),
                json_output=json_output,
            )
    except OracleError as exc:
        return str(exc)
    return "Usage: /oracle status | /oracle simulate <scenario> | /oracle show <oracle-id>"


def _option_value(parts: list[str], flag: str) -> str | None:
    if flag not in parts:
        return None
    idx = parts.index(flag)
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _positional_args(parts: list[str], flags_with_values: set[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for part in parts:
        if skip:
            skip = False
            continue
        if part in flags_with_values:
            skip = part != "--wait"
            continue
        if part.startswith("--"):
            continue
        out.append(part)
    return out


def cmd_oracle(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "oracle_command", None) or "status"
    json_output = bool(getattr(args, "json", False))
    try:
        if subcmd == "status":
            result = run_oracle_status()
        elif subcmd == "show":
            result = run_oracle_show(getattr(args, "oracle_id"))
        elif subcmd == "simulate":
            result = run_oracle_simulate(
                " ".join(getattr(args, "scenario", []) or []),
                seed_path=getattr(args, "seed", None),
                horizon_days=getattr(args, "horizon_days", DEFAULT_HORIZON_DAYS),
                wait=bool(getattr(args, "wait", False)),
            )
        else:
            print("Usage: tribal oracle <status|simulate|show>", file=sys.stderr)
            return 2
    except OracleError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_oracle_result(result, json_output=json_output))
    return 0


def main_oracle(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tribal oracle")
    subparsers = parser.add_subparsers(dest="oracle_command")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true", default=False)
    simulate_parser = subparsers.add_parser("simulate")
    simulate_parser.add_argument("scenario", nargs="+")
    simulate_parser.add_argument("--seed", default=None)
    simulate_parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    simulate_parser.add_argument("--wait", action="store_true", default=False)
    simulate_parser.add_argument("--json", action="store_true", default=False)
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("oracle_id")
    show_parser.add_argument("--json", action="store_true", default=False)
    return cmd_oracle(parser.parse_args(argv))
