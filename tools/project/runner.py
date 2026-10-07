"""Controlled, local command runner for one external Factory target."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

try:
    from .command_policy import CommandPolicy, PolicyDecision, PolicyLevel, policy_allows
    from .inspector import InspectorError, inspect_project, validate_target_path
except ImportError:  # Direct execution: python tools/project/runner.py ...
    from command_policy import CommandPolicy, PolicyDecision, PolicyLevel, policy_allows
    from inspector import InspectorError, inspect_project, validate_target_path

if TYPE_CHECKING:
    from .authorization import TargetAuthorization


DEFAULT_RUNNER_CONFIG: dict[str, Any] = {
    "defaultPolicy": "READ_ONLY",
    "allowedPolicies": ["READ_ONLY", "BUILD_AND_TEST"],
    "defaultTimeoutSeconds": 120,
    "maxTimeoutSeconds": 900,
    "maxOutputBytes": 200_000,
    "safeScriptNames": ["test", "lint", "typecheck", "build", "check", "format:check", "test:e2e"],
    "environmentAllowlist": [
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "CI",
        "NODE_ENV",
    ],
}

SENSITIVE_NAME_PATTERN = re.compile(r"(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization)", re.I)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization)\b\s*[:=]\s*)([^\s,;\"'}]+)"
)
TOKEN_PATTERN = re.compile(r"(?i)\b(?:ghp|github_pat|sk|xox[baprs])-[-_A-Za-z0-9]{8,}\b")


class RunnerError(ValueError):
    """An expected runner validation or configuration error."""


@dataclass(frozen=True)
class _Output:
    text: str
    total_bytes: int
    truncated: bool


def _factory_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_runner_config(factory_root: Path | None = None) -> dict[str, Any]:
    root = (factory_root or _factory_root()).resolve()
    path = root / "config" / "defaults.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        runner = value.get("runner") if isinstance(value, dict) else None
        if isinstance(runner, dict):
            config = dict(DEFAULT_RUNNER_CONFIG)
            config.update(runner)
            return config
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_RUNNER_CONFIG)


def _is_sensitive_name(name: str) -> bool:
    return bool(SENSITIVE_NAME_PATTERN.search(name))


def _filtered_environment(config: dict[str, Any], extra_keys: Iterable[str] = ()) -> tuple[dict[str, str], list[str]]:
    allowed = {str(name).upper() for name in config.get("environmentAllowlist", [])}
    for name in extra_keys:
        if not _is_sensitive_name(name):
            allowed.add(name.upper())
    result: dict[str, str] = {}
    omitted: list[str] = []
    for name, value in os.environ.items():
        if name.upper() in allowed and not _is_sensitive_name(name):
            result[name] = value
        else:
            omitted.append(name)
    return result, sorted(omitted)


def _redact_text(value: str) -> str:
    value = SENSITIVE_VALUE_PATTERN.sub(r"\1[REDACTED]", value)
    return TOKEN_PATTERN.sub("[REDACTED]", value)


def _redact_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for token in command:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if _is_sensitive_name(token) or token.lower() in {"--token", "--password", "--secret", "--api-key", "--authorization"}:
            redacted.append(token)
            redact_next = True
            continue
        if "=" in token and _is_sensitive_name(token.split("=", 1)[0]):
            redacted.append(token.split("=", 1)[0] + "=[REDACTED]")
            continue
        redacted.append(_redact_text(token))
    return redacted


def _with_process_local_git_safety(command: Sequence[str], env: dict[str, str], repository_root: Path) -> None:
    """Allow Git to inspect only this explicitly selected repository."""

    executable = Path(command[0].replace("\\", "/")).name.lower().removesuffix(".exe").removesuffix(".cmd") if command else ""
    if executable == "git":
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = str(repository_root)
        env["GIT_OPTIONAL_LOCKS"] = "0"


def _read_output(pipe: Any, limit: int, holder: dict[str, _Output], key: str) -> None:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = pipe.read(65_536)
        if not chunk:
            break
        total += len(chunk)
        if sum(len(item) for item in chunks) < limit:
            remaining = limit - sum(len(item) for item in chunks)
            chunks.append(chunk[:remaining])
    raw = b"".join(chunks)
    holder[key] = _Output(
        text=_redact_text(raw.decode("utf-8", errors="replace")),
        total_bytes=total,
        truncated=total > limit,
    )


def _execute_process(command: Sequence[str], cwd: Path, env: dict[str, str], timeout: float, output_limit: int) -> dict[str, Any]:
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as exc:
        return {
            "status": "process_failed",
            "exitCode": None,
            "timedOut": False,
            "stdout": "",
            "stderr": _redact_text(str(exc)),
            "stdoutBytes": 0,
            "stderrBytes": 0,
            "stdoutTruncated": False,
            "stderrTruncated": False,
            "startedAt": started_at,
            "endedAt": datetime.now(UTC).isoformat(),
            "durationSeconds": round(time.monotonic() - started, 3),
        }

    outputs: dict[str, _Output] = {}
    threads = [
        threading.Thread(target=_read_output, args=(process.stdout, output_limit, outputs, "stdout"), daemon=True),
        threading.Thread(target=_read_output, args=(process.stderr, output_limit, outputs, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for thread in threads:
        thread.join(timeout=2)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()

    stdout = outputs.get("stdout", _Output("", 0, False))
    stderr = outputs.get("stderr", _Output("", 0, False))
    return {
        "status": "timed_out" if timed_out else ("completed" if process.returncode == 0 else "process_failed"),
        "exitCode": process.returncode,
        "timedOut": timed_out,
        "stdout": stdout.text,
        "stderr": stderr.text,
        "stdoutBytes": stdout.total_bytes,
        "stderrBytes": stderr.total_bytes,
        "stdoutTruncated": stdout.truncated,
        "stderrTruncated": stderr.truncated,
        "startedAt": started_at,
        "endedAt": datetime.now(UTC).isoformat(),
        "durationSeconds": round(time.monotonic() - started, 3),
    }


def _summary(result: dict[str, Any]) -> str:
    policy = result["policy"]["level"]
    status = result["status"]
    exit_code = result.get("exitCode")
    command = " ".join(result["command"]["argv"])
    if status == "policy_rejected":
        return f"Blocked {command} as {policy}: {result['policy']['reason']}"
    return f"{status}: {command} [{policy}], exit={exit_code}, {result['durationSeconds']}s"


def run_command(
    target_path: str | os.PathLike[str],
    command: Sequence[str],
    *,
    requested_policy: PolicyLevel | str | None = None,
    timeout_seconds: float | None = None,
    output_limit: int | None = None,
    extra_environment_keys: Iterable[str] = (),
    factory_root: Path | None = None,
    authorization: "TargetAuthorization | None" = None,
) -> dict[str, Any]:
    """Classify and, when permitted, run argv in one target repository."""

    if authorization is not None:
        from .authorization import TargetAuthorization

        if not isinstance(authorization, TargetAuthorization):
            raise RunnerError("authorization must be a TargetAuthorization")
        if factory_root is not None and Path(factory_root).resolve() != authorization.recorder.factory_root:
            raise RunnerError("Factory root does not match the authorization run")
        factory_root = authorization.recorder.factory_root
    config = _load_runner_config(factory_root)
    target = validate_target_path(target_path, factory_root=factory_root)
    inspection = inspect_project(target, factory_root=factory_root)
    if not inspection["git"]["isRepository"]:
        raise RunnerError("Target must be a Git repository")
    repository_root = Path(inspection["git"]["repositoryRoot"]).resolve()
    if repository_root != target:
        raise RunnerError(f"Target must be the Git repository root: {repository_root}")
    if authorization is not None:
        authorization.assert_target(repository_root)

    argv = tuple(command)
    available_scripts = set((inspection.get("node") or {}).get("scripts", {}))
    if authorization is None:
        allowed_policies = {PolicyLevel(value) for value in config.get("allowedPolicies", [])}
    else:
        allowed_policies = {
            capability
            for capability in authorization.capabilities
            if capability in {
                PolicyLevel.READ_ONLY,
                PolicyLevel.BUILD_AND_TEST,
                PolicyLevel.DEPENDENCY_CHANGE,
            }
        }
    policy = CommandPolicy(
        [capability.value for capability in allowed_policies],
        config.get("safeScriptNames"),
    )
    decision: PolicyDecision = policy.classify(argv, available_scripts)
    if requested_policy is not None:
        requested = PolicyLevel(requested_policy)
    elif authorization is not None and decision.level in allowed_policies:
        requested = decision.level
    else:
        requested = PolicyLevel(config.get("defaultPolicy", PolicyLevel.READ_ONLY.value))
    allowed, reason = policy_allows(decision, requested, allowed_policies)
    if decision.level is PolicyLevel.DEPENDENCY_CHANGE and authorization is None:
        allowed = False
        reason = "DEPENDENCY_CHANGE requires an explicit run-bound target authorization"
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "status": "policy_rejected" if not allowed else "pending",
        "target": {"path": str(target), "repositoryRoot": str(repository_root)},
        "workingDirectory": str(repository_root),
        "command": {"argv": _redact_command(argv)},
        "policy": {
            "requested": requested.value,
            "level": decision.level.value,
            "allowed": allowed,
            "reason": reason,
            "commandFamily": decision.command_family,
        },
        "environment": {"filtered": True, "passedKeys": [], "omittedKeys": []},
        "statusDetail": None,
        "authorization": {
            "authorizationId": authorization.authorization_id,
            "runId": authorization.run_id,
        } if authorization is not None else None,
    }
    if not allowed:
        result["statusDetail"] = "No process was started."
        result["summary"] = _summary(result)
        if authorization is not None:
            authorization.recorder.record_command(result)
        return result

    default_timeout = float(config.get("defaultTimeoutSeconds", 120))
    max_timeout = float(config.get("maxTimeoutSeconds", 900))
    timeout = default_timeout if timeout_seconds is None else float(timeout_seconds)
    if timeout <= 0 or timeout > max_timeout:
        raise RunnerError(f"Timeout must be greater than 0 and no more than {max_timeout:g} seconds")
    configured_limit = int(config.get("maxOutputBytes", 200_000))
    limit = configured_limit if output_limit is None else int(output_limit)
    if limit <= 0 or limit > configured_limit:
        raise RunnerError(f"Output limit must be greater than 0 and no more than {configured_limit} bytes")

    env, omitted = _filtered_environment(config, extra_environment_keys)
    _with_process_local_git_safety(argv, env, repository_root)
    result["environment"] = {
        "filtered": True,
        "passedKeys": sorted(env),
        "omittedKeys": omitted,
    }
    process_result = _execute_process(argv, repository_root, env, timeout, limit)
    result.update(process_result)
    result["summary"] = _summary(result)
    if authorization is not None:
        authorization.recorder.record_command(result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one controlled command in an external Git repository.")
    parser.add_argument("target", help="Target repository path, for example ../FindAndBook")
    parser.add_argument("--policy", choices=[level.value for level in PolicyLevel], default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--max-output-bytes", type=int, default=None)
    parser.add_argument("--env", action="append", default=[], help="Explicitly pass one additional non-secret environment key")
    parser.add_argument("--no-summary", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if "--" in raw_args:
        separator = raw_args.index("--")
        runner_args = raw_args[:separator]
        command = raw_args[separator + 1 :]
    else:
        runner_args = raw_args
        command = []
    args = _parser().parse_args(runner_args)
    try:
        result = run_command(
            args.target,
            command,
            requested_policy=args.policy,
            timeout_seconds=args.timeout,
            output_limit=args.max_output_bytes,
            extra_environment_keys=args.env,
        )
    except (InspectorError, RunnerError, ValueError) as exc:
        print(f"Factory runner refused the request: {exc}", file=sys.stderr)
        return 2

    if not args.no_summary:
        print(result["summary"], file=sys.stderr)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    if result["status"] == "completed":
        return 0
    if result["status"] in {"process_failed", "timed_out"}:
        return 1
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
