"""Repository-confined text mutation API backed by TargetAuthorization."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .authorization import AuthorizationError, TargetAuthorization
from .command_policy import PolicyLevel


DEFAULT_MAX_TEXT_FILE_BYTES = 5_000_000


class ProjectWriteError(ValueError):
    """A requested path or text operation is unsafe or invalid."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ProjectWriter:
    """Create, replace, read, or explicitly delete text files in one target."""

    def __init__(
        self,
        authorization: TargetAuthorization,
        *,
        max_text_file_bytes: int | None = None,
    ) -> None:
        if not isinstance(authorization, TargetAuthorization):
            raise AuthorizationError("A TargetAuthorization is required")
        if max_text_file_bytes is None:
            config_path = authorization.recorder.factory_root / "config" / "defaults.json"
            try:
                defaults = json.loads(config_path.read_text(encoding="utf-8-sig"))
                section = defaults.get("targetAuthorization") if isinstance(defaults, dict) else None
                section = section if isinstance(section, dict) else {}
                configured = section.get(
                    "maxTextFileBytes", DEFAULT_MAX_TEXT_FILE_BYTES
                )
                max_text_file_bytes = int(configured)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                max_text_file_bytes = DEFAULT_MAX_TEXT_FILE_BYTES
        if (
            isinstance(max_text_file_bytes, bool)
            or not isinstance(max_text_file_bytes, int)
            or not 1 <= max_text_file_bytes <= 20_000_000
        ):
            raise ProjectWriteError("max_text_file_bytes must be between 1 and 20000000")
        self.authorization = authorization
        self.repository_root = authorization.repository_root
        self.max_text_file_bytes = int(max_text_file_bytes)

    def _path(self, relative_path: str | os.PathLike[str], *, allow_missing: bool) -> Path:
        if not isinstance(relative_path, (str, os.PathLike)):
            raise ProjectWriteError("Project path must be text or path-like")
        supplied = Path(relative_path)
        if supplied.is_absolute() or supplied.anchor:
            raise ProjectWriteError("Absolute project paths are not allowed")
        parts = supplied.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ProjectWriteError("Project path contains traversal or an empty component")
        if any(part.casefold() == ".git" for part in parts):
            raise ProjectWriteError("Writes inside .git are not allowed")
        if any(":" in part for part in parts):
            raise ProjectWriteError("Colon path components are not allowed")

        candidate = self.repository_root.joinpath(*parts)
        current = self.repository_root
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if current.is_symlink():
                    raise ProjectWriteError("Symlink paths are not allowed for project writes")
                try:
                    current.resolve().relative_to(self.repository_root)
                except ValueError as exc:
                    raise ProjectWriteError("Project path escapes the authorized repository") from exc
                if (
                    current != self.repository_root
                    and current.is_dir()
                    and ((current / ".git").exists() or (current / ".git").is_symlink())
                ):
                    raise ProjectWriteError("Writes into nested Git repositories are not allowed")
        try:
            candidate.parent.resolve().relative_to(self.repository_root)
        except ValueError as exc:
            raise ProjectWriteError("Project path escapes the authorized repository") from exc
        if not allow_missing and not candidate.exists():
            raise ProjectWriteError(f"Project file does not exist: {supplied.as_posix()}")
        return candidate

    def _encode(self, content: str) -> bytes:
        if not isinstance(content, str):
            raise ProjectWriteError("Project writer accepts text content only")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_text_file_bytes:
            raise ProjectWriteError(
                f"Text file exceeds the configured {self.max_text_file_bytes}-byte limit"
            )
        return encoded

    def _read_bytes(self, path: Path) -> bytes:
        if not path.is_file():
            raise ProjectWriteError("Project path must identify a regular file")
        size = path.stat().st_size
        if size > self.max_text_file_bytes:
            raise ProjectWriteError(
                f"Text file exceeds the configured {self.max_text_file_bytes}-byte limit"
            )
        return path.read_bytes()

    def _atomic_write(self, path: Path, encoded: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.resolve().relative_to(self.repository_root)
        except ValueError as exc:
            raise ProjectWriteError("Created directory escaped the authorized repository") from exc
        temporary = path.parent / f".factory-write-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def read_text(self, relative_path: str | os.PathLike[str]) -> str:
        self.authorization.require(PolicyLevel.READ_ONLY)
        path = self._path(relative_path, allow_missing=False)
        try:
            return self._read_bytes(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectWriteError("Project file is not valid UTF-8 text") from exc

    def create_text(self, relative_path: str | os.PathLike[str], content: str) -> dict[str, Any]:
        self.authorization.require(PolicyLevel.PROJECT_WRITE)
        path = self._path(relative_path, allow_missing=True)
        if path.exists():
            raise ProjectWriteError("Refusing to replace an existing file during create")
        encoded = self._encode(content)
        self._atomic_write(path, encoded)
        return self._record_change("create", path, None, encoded)

    def replace_text(
        self,
        relative_path: str | os.PathLike[str],
        content: str,
        *,
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        self.authorization.require(PolicyLevel.PROJECT_WRITE)
        path = self._path(relative_path, allow_missing=False)
        before = self._read_bytes(path)
        try:
            previous_text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectWriteError("Project file is not valid UTF-8 text") from exc
        if expected_text is not None and previous_text != expected_text:
            raise ProjectWriteError("Existing file changed since it was read")
        encoded = self._encode(content)
        self._atomic_write(path, encoded)
        return self._record_change("replace", path, before, encoded)

    def delete_file(
        self,
        relative_path: str | os.PathLike[str],
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        self.authorization.require(PolicyLevel.PROJECT_WRITE)
        if confirm is not True:
            raise AuthorizationError("File deletion requires confirm=True for this operation")
        path = self._path(relative_path, allow_missing=False)
        before = self._read_bytes(path)
        path.unlink()
        return self._record_change("delete", path, before, None)

    def _record_change(
        self,
        operation: str,
        path: Path,
        before: bytes | None,
        after: bytes | None,
    ) -> dict[str, Any]:
        relative = path.relative_to(self.repository_root).as_posix()
        return self.authorization.recorder.record_project_change(
            {
                "authorizationId": self.authorization.authorization_id,
                "operation": operation,
                "path": relative,
                "before": {
                    "bytes": len(before),
                    "sha256": _digest(before),
                } if before is not None else None,
                "after": {
                    "bytes": len(after),
                    "sha256": _digest(after),
                } if after is not None else None,
            }
        )
