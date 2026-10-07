"""Deterministic local run artifacts stored under Factory v1."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from tools.project.inspector import validate_target_path


SCHEMA_VERSION = 1
DEFAULT_OUTPUT_LIMIT = 32_768
VALID_FINAL_STATUSES = {"completed", "failed", "cancelled"}
SENSITIVE_KEY = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization|cookie)",
    re.I,
)
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization|cookie)\b\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:ghp|github_pat|sk|xox[baprs]|AIza)[-_A-Za-z0-9]{8,}\b"
)
BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)[-_A-Za-z0-9.]{8,}")
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class ArtifactRecorderError(ValueError):
    """An expected artifact path or lifecycle error."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_project_name(name: str) -> str:
    """Return a safe, stable path component for project grouping."""

    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(" .-_").lower()
    normalized = re.sub(r"[-_.]{2,}", "-", normalized)[:64].strip("-.")
    if not normalized or normalized in {".", ".."}:
        return "project"
    if normalized.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        return f"project-{normalized}"
    return normalized


def redact_text(value: str) -> str:
    """Redact common credential forms from a string."""

    for variable in ("TAVILY_API_KEY",):
        secret = os.environ.get(variable, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    value = BEARER_TOKEN.sub(r"\1[REDACTED]", value)
    value = SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return KNOWN_TOKEN.sub("[REDACTED]", value)


def redact_data(value: Any, key: str | None = None) -> Any:
    """Recursively redact known secret-bearing keys and string patterns."""

    if key is not None and SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact_data(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def _safe_artifact_url(value: Any) -> str | None:
    """Remove query/fragment data before persisting a research URL."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        safe = value.split("?", 1)[0].split("#", 1)[0]
    return redact_text(safe)


def _redact_command(value: Any) -> dict[str, Any]:
    command = value if isinstance(value, Mapping) else {"argv": []}
    raw_argv = command.get("argv")
    argv = list(raw_argv) if isinstance(raw_argv, (list, tuple)) else []
    redacted: list[str] = []
    redact_next = False
    for item in argv:
        token = str(item)
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if token.lower() in {"--api-key", "--authorization", "--password", "--secret", "--token"}:
            redacted.append(token)
            redact_next = True
            continue
        if "=" in token and SENSITIVE_KEY.search(token.split("=", 1)[0]):
            redacted.append(token.split("=", 1)[0] + "=[REDACTED]")
            continue
        redacted.append(redact_text(token))
    return {"argv": redacted}


def _cost_fields(source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = source or {}
    return {
        "provider": source.get("provider"),
        "model": source.get("model"),
        "inputTokens": source.get("inputTokens"),
        "outputTokens": source.get("outputTokens"),
        "estimatedCost": source.get("estimatedCost"),
        "searchApiCost": source.get("searchApiCost"),
        "currency": source.get("currency"),
    }


def _bounded_output(value: Any, source_bytes: Any, upstream_truncated: Any, limit: int) -> dict[str, Any]:
    redacted = redact_text(value if isinstance(value, str) else "")
    encoded = redacted.encode("utf-8")
    stored = encoded[:limit]
    text = stored.decode("utf-8", errors="replace")
    try:
        reported_bytes = int(source_bytes)
    except (TypeError, ValueError):
        reported_bytes = len(encoded)
    return {
        "text": text,
        "sourceBytes": max(reported_bytes, len(encoded)),
        "storedBytes": len(stored),
        "truncated": bool(upstream_truncated) or len(encoded) > limit,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


class RunArtifactRecorder:
    """Create and update one local Factory run directory."""

    def __init__(
        self,
        factory_root: str | os.PathLike[str],
        target_path: str | os.PathLike[str],
        *,
        project_name: str | None = None,
        output_limit: int | None = None,
    ) -> None:
        self.factory_root = Path(factory_root).resolve()
        if not self.factory_root.is_dir():
            raise ArtifactRecorderError(f"Factory root does not exist: {self.factory_root}")
        self.target_path = validate_target_path(target_path, factory_root=self.factory_root)
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._closed = False
        self._command_count = 0
        self._command_statuses: dict[str, int] = {}
        self._inspector_recorded = False
        self._browser_event_count = 0
        self._browser_statuses: dict[str, int] = {}
        self._search_event_count = 0
        self._search_statuses: dict[str, int] = {}
        self._fetch_event_count = 0
        self._fetch_statuses: dict[str, int] = {}
        self._research_plan_recorded = False
        self._source_bundle_recorded = False
        self._research_result_recorded = False
        self._authorization_recorded = False
        self._project_change_count = 0
        self._project_change_operations: dict[str, int] = {}

        config = self._artifact_config()
        configured_directory = Path(str(config.get("directory", ".factory-artifacts")))
        if configured_directory.is_absolute():
            raise ArtifactRecorderError("Artifact directory must be relative to Factory root")
        self.artifact_root = (self.factory_root / configured_directory).resolve()
        try:
            self.artifact_root.relative_to(self.factory_root)
        except ValueError as exc:
            raise ArtifactRecorderError("Artifact directory escapes Factory root") from exc

        configured_limit = int(config.get("maxStoredCommandOutputBytes", DEFAULT_OUTPUT_LIMIT))
        self.output_limit = configured_limit if output_limit is None else int(output_limit)
        if self.output_limit <= 0 or self.output_limit > configured_limit:
            raise ArtifactRecorderError(
                f"Output limit must be greater than 0 and no more than {configured_limit} bytes"
            )

        self.repository_name = self.target_path.name
        self.project_name = sanitize_project_name(project_name or self.repository_name)
        self.run_id = self._new_run_id()
        self.run_directory = self.artifact_root / self.project_name / self.run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.run_path = self.run_directory / "run.json"
        self.inspector_path = self.run_directory / "inspector.json"
        self.commands_path = self.run_directory / "commands.jsonl"
        self.summary_path = self.run_directory / "summary.json"
        self.commands_path.touch(exist_ok=False)

        self._metadata: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": self.run_id,
            "status": "running",
            "target": {
                "path": str(self.target_path),
                "repositoryName": redact_text(self.repository_name),
                "projectGroup": self.project_name,
            },
            "timing": {
                "startedAt": _utc_now(),
                "endedAt": None,
                "durationSeconds": None,
            },
            "artifacts": {
                "inspector": "inspector.json",
                "commands": "commands.jsonl",
                "summary": "summary.json",
                "browser": "browser/",
                "search": "search/",
                "research": "research/",
                "authorization": "authorization.json",
                "projectChanges": "project-changes.jsonl",
            },
            "cost": _cost_fields(),
            "authorization": None,
            "failure": None,
        }
        _write_json(self.run_path, self._metadata)

    def _artifact_config(self) -> dict[str, Any]:
        path = self.factory_root / "config" / "defaults.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            artifacts = value.get("artifacts") if isinstance(value, dict) else None
            return artifacts if isinstance(artifacts, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    def _research_config(self) -> dict[str, Any]:
        path = self.factory_root / "config" / "defaults.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            research = value.get("research") if isinstance(value, dict) else None
            return research if isinstance(research, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _new_run_id() -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

    def _ensure_open(self) -> None:
        if self._closed:
            raise ArtifactRecorderError("Run is already finalized")

    def ensure_active(self) -> None:
        """Fail if this run can no longer authorize or record operations."""

        with self._lock:
            self._ensure_open()

    def record_authorization(self, authorization: Mapping[str, Any]) -> Path:
        """Record the run's single immutable target capability grant."""

        with self._lock:
            self._ensure_open()
            if self._authorization_recorded:
                raise ArtifactRecorderError("This run already has a target authorization")
            if authorization.get("runId") != self.run_id:
                raise ArtifactRecorderError("Authorization run ID does not match this run")
            target_root = authorization.get("targetRepositoryRoot")
            if not isinstance(target_root, str) or Path(target_root).resolve() != self.target_path:
                raise ArtifactRecorderError("Authorization target does not match this run")
            safe = redact_data(authorization)
            path = self.run_directory / "authorization.json"
            _write_json(path, safe)
            self._metadata["authorization"] = {
                "authorizationId": safe.get("authorizationId"),
                "capabilities": safe.get("capabilities", []),
                "alwaysBlocked": safe.get("alwaysBlocked", []),
            }
            _write_json(self.run_path, self._metadata)
            self._authorization_recorded = True
            return path

    def record_project_change(self, change: Mapping[str, Any]) -> dict[str, Any]:
        """Record path/hash metadata only; never persist source file contents."""

        with self._lock:
            self._ensure_open()
            if not self._authorization_recorded:
                raise ArtifactRecorderError("Project changes require a recorded authorization")
            relative = str(change.get("path", "")).replace("\\", "/")
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise ArtifactRecorderError("Project change path must be repository-relative")
            self._project_change_count += 1
            operation = str(change.get("operation", "unknown"))
            self._project_change_operations[operation] = (
                self._project_change_operations.get(operation, 0) + 1
            )
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "eventType": "project_change",
                "sequence": self._project_change_count,
                "recordedAt": _utc_now(),
                "authorizationId": redact_data(change.get("authorizationId")),
                "operation": operation,
                "path": redact_text(relative),
                "before": redact_data(change.get("before")),
                "after": redact_data(change.get("after")),
            }
            path = self.run_directory / "project-changes.jsonl"
            path.touch(exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, sort_keys=True))
                stream.write("\n")
            return payload

    def record_inspector(self, result: Mapping[str, Any]) -> Path:
        with self._lock:
            self._ensure_open()
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "recordedAt": _utc_now(),
                "result": redact_data(result),
            }
            _write_json(self.inspector_path, payload)
            self._inspector_recorded = True
            return self.inspector_path

    def record_command(self, result: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            self._command_count += 1
            status = str(result.get("status", "unknown"))
            self._command_statuses[status] = self._command_statuses.get(status, 0) + 1
            event = {
                "schemaVersion": SCHEMA_VERSION,
                "eventType": "command",
                "sequence": self._command_count,
                "recordedAt": _utc_now(),
                "command": _redact_command(result.get("command")),
                "policy": redact_data(result.get("policy", {})),
                "status": status,
                "policyRejected": status == "policy_rejected",
                "exitCode": result.get("exitCode"),
                "timedOut": bool(result.get("timedOut", False)),
                "timing": {
                    "startedAt": result.get("startedAt"),
                    "endedAt": result.get("endedAt"),
                    "durationSeconds": result.get("durationSeconds"),
                },
                "stdout": _bounded_output(
                    result.get("stdout"),
                    result.get("stdoutBytes"),
                    result.get("stdoutTruncated"),
                    self.output_limit,
                ),
                "stderr": _bounded_output(
                    result.get("stderr"),
                    result.get("stderrBytes"),
                    result.get("stderrTruncated"),
                    self.output_limit,
                ),
                "environmentFiltered": bool((result.get("environment") or {}).get("filtered", False)),
                "cost": _cost_fields(result.get("cost") if isinstance(result.get("cost"), Mapping) else None),
            }
            with self.commands_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, sort_keys=True))
                stream.write("\n")
            return event

    def browser_paths(self) -> dict[str, Path]:
        """Create and return run-local browser evidence paths."""

        with self._lock:
            self._ensure_open()
            browser = self.run_directory / "browser"
            screenshots = browser / "screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            events = browser / "browser-events.jsonl"
            events.touch(exist_ok=True)
            return {"directory": browser, "screenshots": screenshots, "events": events}

    def browser_screenshot_path(self, name: str) -> Path:
        paths = self.browser_paths()
        safe_name = sanitize_project_name(name)
        candidate = paths["screenshots"] / f"{safe_name}.png"
        suffix = 2
        while candidate.exists():
            candidate = paths["screenshots"] / f"{safe_name}-{suffix}.png"
            suffix += 1
        return candidate

    def record_browser_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            browser = self.run_directory / "browser"
            screenshots = browser / "screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            events = browser / "browser-events.jsonl"
            events.touch(exist_ok=True)
            self._browser_event_count += 1
            status = str(event.get("status", "unknown"))
            self._browser_statuses[status] = self._browser_statuses.get(status, 0) + 1
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "eventType": "browser",
                "sequence": self._browser_event_count,
                "recordedAt": _utc_now(),
                **redact_data(event),
            }
            with events.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, sort_keys=True))
                stream.write("\n")
            return payload

    def record_search_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Persist only safe search metadata, never headers or result content."""

        with self._lock:
            self._ensure_open()
            search = self.run_directory / "search"
            search.mkdir(parents=True, exist_ok=True)
            events = search / "search-events.jsonl"
            events.touch(exist_ok=True)
            self._search_event_count += 1
            status = str(event.get("status", "unknown"))
            self._search_statuses[status] = self._search_statuses.get(status, 0) + 1

            safe_results: list[dict[str, Any]] = []
            raw_results = event.get("results")
            if isinstance(raw_results, list):
                for result in raw_results[:20]:
                    if isinstance(result, Mapping):
                        safe_results.append(
                            {
                                "title": redact_data(result.get("title")),
                                "url": redact_data(result.get("url")),
                            }
                        )
            usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "eventType": "search",
                "sequence": self._search_event_count,
                "recordedAt": _utc_now(),
                "status": status,
                "query": redact_data(event.get("query")),
                "provider": redact_data(event.get("provider")),
                "searchDepth": redact_data(event.get("searchDepth")),
                "resultCount": event.get("resultCount"),
                "durationSeconds": event.get("durationSeconds"),
                "usage": {
                    "credits": usage.get("credits"),
                    "estimatedCost": usage.get("estimatedCost"),
                    "currency": usage.get("currency"),
                },
                "results": safe_results,
                "error": redact_data(event.get("error")),
            }
            with events.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, sort_keys=True))
                stream.write("\n")
            return payload

    def record_fetch_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Persist safe page-fetch metadata without storing fetched text."""

        with self._lock:
            self._ensure_open()
            research = self.run_directory / "research"
            research.mkdir(parents=True, exist_ok=True)
            events = research / "fetch-events.jsonl"
            events.touch(exist_ok=True)
            self._fetch_event_count += 1
            status = str(event.get("status", "unknown"))
            self._fetch_statuses[status] = self._fetch_statuses.get(status, 0) + 1
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "eventType": "page_fetch",
                "sequence": self._fetch_event_count,
                "recordedAt": _utc_now(),
                "status": status,
                "requestedUrl": _safe_artifact_url(event.get("requestedUrl")),
                "finalUrl": _safe_artifact_url(event.get("finalUrl")),
                "httpStatus": event.get("httpStatus"),
                "contentType": redact_data(event.get("contentType")),
                "textLength": event.get("textLength"),
                "durationSeconds": event.get("durationSeconds"),
                "responseBytes": event.get("responseBytes"),
                "textTruncated": bool(event.get("textTruncated", False)),
                "redirectCount": event.get("redirectCount"),
                "error": redact_data(event.get("error")),
            }
            with events.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, sort_keys=True))
                stream.write("\n")
            return payload

    def record_research_plan(self, plan: Mapping[str, Any]) -> Path:
        with self._lock:
            self._ensure_open()
            research = self.run_directory / "research"
            research.mkdir(parents=True, exist_ok=True)
            path = research / "research-plan.json"
            _write_json(path, redact_data(plan))
            self._research_plan_recorded = True
            return path

    def record_source_bundle(self, bundle: Mapping[str, Any]) -> Path:
        with self._lock:
            self._ensure_open()
            research = self.run_directory / "research"
            research.mkdir(parents=True, exist_ok=True)
            path = research / "source-bundle.json"
            safe_bundle = redact_data(bundle)
            configured = int(
                self._research_config().get("maxSourceBundleBytes", 500_000)
            )
            encoded = (json.dumps(safe_bundle, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(encoded) > configured:
                raise ArtifactRecorderError(
                    f"Source bundle exceeds the configured {configured}-byte limit"
                )
            _write_json(path, safe_bundle)
            self._source_bundle_recorded = True
            return path

    def record_research_result(self, markdown: str) -> Path:
        """Store later Codex synthesis; this recorder never creates that synthesis."""

        if not isinstance(markdown, str):
            raise ArtifactRecorderError("Research result must be Markdown text")
        with self._lock:
            self._ensure_open()
            research = self.run_directory / "research"
            research.mkdir(parents=True, exist_ok=True)
            path = research / "research-result.md"
            safe = redact_text(markdown)
            configured = int(
                self._research_config().get("maxResearchResultBytes", 100_000)
            )
            stored = safe if not safe or safe.endswith("\n") else safe + "\n"
            encoded = stored.encode("utf-8")
            if len(encoded) > configured:
                raise ArtifactRecorderError(
                    f"Research result exceeds the configured {configured}-byte limit"
                )
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(stored)
            os.replace(temporary, path)
            self._research_result_recorded = True
            return path

    def complete(self, status: str = "completed", reason: str | None = None) -> Path:
        if status not in VALID_FINAL_STATUSES:
            raise ArtifactRecorderError(f"Invalid final status: {status}")
        with self._lock:
            self._ensure_open()
            ended_at = _utc_now()
            duration = round(time.monotonic() - self._started_monotonic, 3)
            self._metadata["status"] = status
            self._metadata["timing"]["endedAt"] = ended_at
            self._metadata["timing"]["durationSeconds"] = duration
            self._metadata["failure"] = redact_text(reason) if reason else None
            summary = {
                "schemaVersion": SCHEMA_VERSION,
                "runId": self.run_id,
                "status": status,
                "target": self._metadata["target"],
                "timing": self._metadata["timing"],
                "inspectorRecorded": self._inspector_recorded,
                "commandCount": self._command_count,
                "commandStatuses": dict(sorted(self._command_statuses.items())),
                "browserEventCount": self._browser_event_count,
                "browserStatuses": dict(sorted(self._browser_statuses.items())),
                "searchEventCount": self._search_event_count,
                "searchStatuses": dict(sorted(self._search_statuses.items())),
                "fetchEventCount": self._fetch_event_count,
                "fetchStatuses": dict(sorted(self._fetch_statuses.items())),
                "researchPlanRecorded": self._research_plan_recorded,
                "sourceBundleRecorded": self._source_bundle_recorded,
                "researchResultRecorded": self._research_result_recorded,
                "authorizationRecorded": self._authorization_recorded,
                "projectChangeCount": self._project_change_count,
                "projectChangeOperations": dict(
                    sorted(self._project_change_operations.items())
                ),
                "failure": self._metadata["failure"],
                "cost": _cost_fields(self._metadata.get("cost")),
            }
            _write_json(self.run_path, self._metadata)
            _write_json(self.summary_path, summary)
            self._closed = True
            return self.summary_path

    def fail(self, reason: str) -> Path:
        return self.complete("failed", reason=reason)

    def __enter__(self) -> "RunArtifactRecorder":
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if self._closed:
            return False
        if exc is None:
            self.complete()
        else:
            self.fail(f"{exc_type.__name__}: {exc}" if exc_type else str(exc))
        return False
