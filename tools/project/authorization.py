"""Run-bound capability authorization for exactly one external Git repository."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .command_policy import POLICY_RANK, PolicyLevel
from .inspector import inspect_project, validate_target_path

if TYPE_CHECKING:
    from tools.artifacts import RunArtifactRecorder


AUTHORIZEABLE_CAPABILITIES = {
    PolicyLevel.READ_ONLY,
    PolicyLevel.BUILD_AND_TEST,
    PolicyLevel.DEPENDENCY_CHANGE,
    PolicyLevel.PROJECT_WRITE,
}
ALWAYS_BLOCKED_CAPABILITIES = {PolicyLevel.EXTERNAL_ACTION, PolicyLevel.DESTRUCTIVE}


class AuthorizationError(PermissionError):
    """A target, run, or capability is not explicitly authorized."""


class TargetAuthorization:
    """Immutable capability grant bound to one recorder run and Git root."""

    __slots__ = (
        "repository_root",
        "capabilities",
        "recorder",
        "run_id",
        "authorization_id",
        "created_at",
        "_sealed",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("TargetAuthorization is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        target: str | os.PathLike[str],
        capabilities: Iterable[PolicyLevel | str],
        *,
        recorder: "RunArtifactRecorder",
    ) -> None:
        from tools.artifacts import RunArtifactRecorder

        if not isinstance(recorder, RunArtifactRecorder):
            raise AuthorizationError("An active RunArtifactRecorder is required")
        recorder.ensure_active()
        try:
            resolved = validate_target_path(target, factory_root=recorder.factory_root)
            inspection = inspect_project(resolved, factory_root=recorder.factory_root)
        except ValueError as exc:
            raise AuthorizationError(str(exc)) from exc
        if not inspection["git"]["isRepository"]:
            raise AuthorizationError("Authorized target must be a Git repository")
        repository_root = Path(inspection["git"]["repositoryRoot"]).resolve()
        if resolved != repository_root:
            raise AuthorizationError(f"Target must be the Git repository root: {repository_root}")
        if inspection["git"]["workingTreeClean"] is not True:
            raise AuthorizationError("Authorized target working tree must be clean at run start")
        if recorder.target_path != repository_root:
            raise AuthorizationError("Authorization target does not match the active Factory run")

        try:
            granted = frozenset(
                capability if isinstance(capability, PolicyLevel) else PolicyLevel(capability)
                for capability in capabilities
            )
        except ValueError as exc:
            raise AuthorizationError(f"Unknown capability: {exc}") from exc
        if not granted:
            raise AuthorizationError("At least one capability must be explicitly authorized")
        blocked = granted.intersection(ALWAYS_BLOCKED_CAPABILITIES)
        if blocked:
            names = ", ".join(sorted(capability.value for capability in blocked))
            raise AuthorizationError(f"Capabilities cannot be authorized: {names}")
        unsupported = granted.difference(AUTHORIZEABLE_CAPABILITIES)
        if unsupported:
            raise AuthorizationError("Authorization contains an unsupported capability")

        self.repository_root = repository_root
        self.capabilities = granted
        self.recorder = recorder
        self.run_id = recorder.run_id
        self.authorization_id = f"auth-{uuid.uuid4().hex}"
        self.created_at = datetime.now(UTC).isoformat()
        recorder.record_authorization(self.to_dict())
        self._sealed = True

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "authorizationId": self.authorization_id,
            "runId": self.run_id,
            "targetRepositoryRoot": str(self.repository_root),
            "capabilities": [
                capability.value
                for capability in sorted(self.capabilities, key=lambda item: POLICY_RANK[item])
            ],
            "alwaysBlocked": [
                PolicyLevel.EXTERNAL_ACTION.value,
                PolicyLevel.DESTRUCTIVE.value,
            ],
            "createdAt": self.created_at,
        }

    def allows(self, capability: PolicyLevel | str) -> bool:
        requested = capability if isinstance(capability, PolicyLevel) else PolicyLevel(capability)
        return requested in self.capabilities

    def require(self, capability: PolicyLevel | str) -> None:
        self.recorder.ensure_active()
        requested = capability if isinstance(capability, PolicyLevel) else PolicyLevel(capability)
        if requested in ALWAYS_BLOCKED_CAPABILITIES:
            raise AuthorizationError(f"{requested.value} is always blocked")
        if requested not in self.capabilities:
            raise AuthorizationError(f"{requested.value} is not authorized for this run")

    def assert_target(self, repository_root: str | os.PathLike[str]) -> None:
        self.recorder.ensure_active()
        candidate = Path(repository_root).resolve()
        if candidate != self.repository_root:
            raise AuthorizationError("Authorization is scoped to a different target repository")
