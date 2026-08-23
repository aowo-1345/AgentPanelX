"""Persistent local Agent workspaces, Outbox files, and Artifact URIs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from agentplanex.domains.artifact import ArtifactDescriptor

_RUNTIME_DIRECTORY = ".agentplanex"
_WORKSPACE_DIRECTORY = "agent-workspaces"
_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


class AgentWorkspaceError(ValueError):
    """A workspace, conversation reference, or artifact is invalid."""


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    """A validated Artifact path and its observed integrity facts."""

    uri: str
    path: Path
    media_type: str
    size: int
    sha256: str


class _WorkspaceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    agent_id: str
    profile_digest: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AgentWorkspace:
    """One persistent writable workspace bound to a configured Agent profile."""

    workspace_id: str
    agent_id: str
    profile_digest: str
    path: Path


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    """One fresh Outbox location within a persistent Agent workspace."""

    invocation_id: str
    workspace: AgentWorkspace
    result_path: Path


@dataclass(frozen=True, slots=True)
class ManagedAgentInvocation:
    """A request-bound invocation and any previously validated result."""

    activation_id: str
    invocation: AgentInvocation
    result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AgentWorkspaceStore:
    """Create, restore, and validate Agent-owned project-local files."""

    project_path: Path
    response_limit: int
    artifact_limit: int

    def __post_init__(self) -> None:
        if self.response_limit <= 0 or self.artifact_limit <= 0:
            raise ValueError("Agent workspace limits must be positive")

    @property
    def runtime_root(self) -> Path:
        return self.project_path / _RUNTIME_DIRECTORY

    @property
    def workspaces_root(self) -> Path:
        return self.runtime_root / _WORKSPACE_DIRECTORY

    def get_or_create_managed(
        self,
        *,
        agent_id: str,
        profile_digest: str,
        session_key: str,
    ) -> AgentWorkspace:
        """Resolve the deterministic workspace selected by a Session Policy."""
        self._ensure_workspace_root()
        self._ensure_runtime_git_excluded()
        generation = 0
        while True:
            workspace_id = hashlib.sha256(
                json.dumps(
                    {
                        "agent_id": agent_id,
                        "generation": generation,
                        "profile_digest": profile_digest,
                        "session_key": session_key,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:32]
            candidate = self.workspaces_root / workspace_id
            if not (candidate / "quarantine.json").exists():
                break
            generation += 1
        path = self.workspaces_root / workspace_id
        metadata_path = path / "workspace.json"
        if not metadata_path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                for directory in ("activations", "artifacts", "inputs", "workspace"):
                    (path / directory).mkdir(exist_ok=True)
                for directory in ("documents", "outbox"):
                    (path / "workspace" / directory).mkdir(exist_ok=True)
                metadata = _WorkspaceMetadata(
                    version=1,
                    agent_id=agent_id,
                    profile_digest=profile_digest,
                    workspace_id=workspace_id,
                )
                if not metadata_path.exists():
                    self._atomic_write(
                        metadata_path,
                        metadata.model_dump_json(indent=2),
                    )
            except OSError as error:
                raise AgentWorkspaceError("Cannot create managed Agent workspace") from error
        workspace = self._load(workspace_id)
        if workspace.agent_id != agent_id or workspace.profile_digest != profile_digest:
            raise AgentWorkspaceError("Managed Agent workspace binding is invalid")
        return workspace

    def execution_path(self, workspace: AgentWorkspace) -> Path:
        """Return the only Session directory writable by the Agent process."""
        return self._bounded_path(workspace.path, PurePosixPath("workspace"))

    def is_quarantined(self, workspace: AgentWorkspace) -> bool:
        """Check fencing again while the caller holds the Session lock."""
        return self._bounded_path(
            workspace.path,
            PurePosixPath("quarantine.json"),
        ).exists()

    def quarantine_session(self, workspace: AgentWorkspace, reason: str) -> None:
        """Fence a workspace whose prior Codex process may still be alive."""
        path = self._bounded_path(workspace.path, PurePosixPath("quarantine.json"))
        self._atomic_write(
            path,
            json.dumps(
                {"reason": reason[:2_000], "version": 1},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
        )

    @contextmanager
    def lock_session(self, workspace: AgentWorkspace) -> Iterator[None]:
        """Serialize a complete Codex turn for one managed Session."""
        lock_path = self._bounded_path(workspace.path, PurePosixPath("session.lock"))
        try:
            with lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise AgentWorkspaceError("Cannot lock managed Agent session") from error

    def load_managed_thread(self, workspace: AgentWorkspace) -> str | None:
        """Read the Runtime-owned Codex thread binding, when one exists."""
        path = self._bounded_path(workspace.path, PurePosixPath("session.json"))
        if not path.exists():
            return None
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AgentWorkspaceError("Managed Agent session is invalid") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"thread_id", "version"}
            or payload.get("version") != 1
            or not isinstance(payload.get("thread_id"), str)
            or not payload["thread_id"].strip()
        ):
            raise AgentWorkspaceError("Managed Agent session is invalid")
        thread_id = payload["thread_id"]
        assert isinstance(thread_id, str)
        return thread_id

    def save_managed_thread(self, workspace: AgentWorkspace, thread_id: str) -> None:
        """Persist a new thread identity before its first turn can mutate files."""
        if not thread_id.strip():
            raise AgentWorkspaceError("Codex returned an empty thread ID")
        path = self._bounded_path(workspace.path, PurePosixPath("session.json"))
        self._atomic_write(
            path,
            json.dumps(
                {"thread_id": thread_id, "version": 1},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
        )

    def prepare_managed_invocation(
        self,
        workspace: AgentWorkspace,
        *,
        request_key: str,
        request_digest: str,
    ) -> ManagedAgentInvocation:
        """Record request identity or replay its already validated result."""
        if not request_key.strip():
            raise AgentWorkspaceError("External Agent request key must not be empty")
        activation_id = hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:32]
        relative = PurePosixPath("activations") / activation_id
        directory = self._bounded_path(workspace.path, relative)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise AgentWorkspaceError("Cannot create Agent activation") from error
        request_path = directory / "request.json"
        expected = {"request_digest": request_digest, "request_key": request_key}
        if request_path.exists():
            try:
                observed: object = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise AgentWorkspaceError("Agent activation request is invalid") from error
            if observed != expected:
                raise AgentWorkspaceError(
                    "External Agent request key was reused with different input"
                )
        else:
            self._atomic_write(
                request_path,
                json.dumps(expected, ensure_ascii=True, indent=2, sort_keys=True),
            )

        result: dict[str, Any] | None = None
        result_path = directory / "result.json"
        if result_path.exists():
            try:
                envelope: object = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise AgentWorkspaceError("Agent activation result is invalid") from error
            if (
                not isinstance(envelope, dict)
                or envelope.get("request_digest") != request_digest
                or not isinstance(envelope.get("result"), dict)
            ):
                raise AgentWorkspaceError("Agent activation result binding is invalid")
            result = envelope["result"]
        invocation = self.create_invocation(workspace)
        return ManagedAgentInvocation(
            activation_id=activation_id,
            invocation=invocation,
            result=result,
        )

    def publish_managed_result(
        self,
        workspace: AgentWorkspace,
        activation_id: str,
        *,
        request_digest: str,
        result: dict[str, Any],
    ) -> None:
        """Atomically publish a statically validated invocation result."""
        relative = PurePosixPath("activations") / activation_id / "result.json"
        path = self._bounded_path(workspace.path, relative)
        if path.exists():
            raise AgentWorkspaceError("Agent activation result is already published")
        self._atomic_write(
            path,
            json.dumps(
                {"request_digest": request_digest, "result": result},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
        )

    def create_invocation(self, workspace: AgentWorkspace) -> AgentInvocation:
        """Allocate a unique Outbox result path so stale results cannot be reused."""
        invocation_id = uuid4().hex
        outbox = self._bounded_path(
            workspace.path,
            PurePosixPath("workspace") / "outbox",
        )
        result_path = outbox / invocation_id / "result.json"
        try:
            result_path.parent.mkdir(parents=True)
        except OSError as error:
            raise AgentWorkspaceError("Cannot create Agent invocation Outbox") from error
        return AgentInvocation(
            invocation_id=invocation_id,
            workspace=workspace,
            result_path=result_path,
        )

    def resolve_artifact(self, uri: str) -> ResolvedArtifact:
        """Resolve one supported URI to a validated project-local text file."""
        parsed = urlparse(uri)
        decoded_path = unquote(parsed.path)
        published_identity: tuple[str, str] | None = None
        if parsed.scheme == "project" and not parsed.netloc:
            relative = self._safe_relative(decoded_path.lstrip("/"))
            base = self.project_path
        elif parsed.scheme == "artifact" and parsed.netloc == "local":
            relative = self._safe_relative(decoded_path.lstrip("/"))
            parts = relative.parts
            if (
                len(parts) < 4
                or parts[0] != _WORKSPACE_DIRECTORY
                or not _SAFE_ID.fullmatch(parts[1])
                or parts[2] not in {"documents", "artifacts"}
            ):
                raise AgentWorkspaceError("Artifact URI is not an Agent document")
            base = self.runtime_root
            if parts[2] == "artifacts":
                if len(parts) != 5 or not _SAFE_ID.fullmatch(parts[3]):
                    raise AgentWorkspaceError("Published Artifact URI is invalid")
                published_identity = (parts[1], parts[3])
        else:
            raise AgentWorkspaceError(f"Unsupported Artifact URI: {uri}")
        path = self._bounded_path(base, relative)
        content = self._read_valid_text(path, self.artifact_limit)
        resolved = ResolvedArtifact(
            uri=uri,
            path=path,
            media_type=("text/markdown" if path.suffix.lower() == ".md" else "text/plain"),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        if published_identity is not None:
            descriptor = self._published_descriptor(
                *published_identity,
                uri=uri,
            )
            if (
                descriptor.project_relative_path != str(path.relative_to(self.project_path))
                or descriptor.media_type != resolved.media_type
                or descriptor.size != resolved.size
                or descriptor.sha256 != resolved.sha256
            ):
                raise AgentWorkspaceError("Published Artifact integrity check failed")
        return resolved

    def resolve_descriptor(self, descriptor: ArtifactDescriptor) -> ResolvedArtifact:
        """Resolve an Artifact and recheck all published integrity facts."""
        resolved = self.resolve_artifact(descriptor.uri)
        expected_path = self.project_path / descriptor.project_relative_path
        if (
            resolved.path.resolve() != expected_path.resolve()
            or resolved.media_type != descriptor.media_type
            or resolved.size != descriptor.size
            or resolved.sha256 != descriptor.sha256
        ):
            raise AgentWorkspaceError("Artifact descriptor integrity check failed")
        return resolved

    def read_result_json(self, invocation: AgentInvocation) -> dict[str, Any]:
        """Load a newly allocated model-written Outbox result object."""
        relative = PurePosixPath("workspace") / "outbox" / invocation.invocation_id / "result.json"
        result_path = self._bounded_path(invocation.workspace.path, relative)
        if result_path != invocation.result_path:
            raise AgentWorkspaceError("Agent result.json path is invalid")
        content = self._read_valid_text(result_path, self.response_limit)
        try:
            payload: object = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise AgentWorkspaceError("Agent result.json is not valid JSON") from error
        if not isinstance(payload, dict):
            raise AgentWorkspaceError("Agent result.json must contain an object")
        return payload

    def freeze_output_artifact(
        self,
        workspace: AgentWorkspace,
        activation_id: str,
        relative_path: str,
        *,
        expected_name: str,
    ) -> ArtifactDescriptor:
        """Publish exact document bytes at an invocation-scoped immutable URI."""
        declared_relative = self._safe_relative(relative_path)
        if declared_relative != PurePosixPath("documents") / expected_name:
            raise AgentWorkspaceError(f"Agent Contract requires documents/{expected_name}")
        source_relative = PurePosixPath("workspace") / declared_relative
        source = self._bounded_path(workspace.path, source_relative)
        content = self._read_valid_text(source, self.artifact_limit)
        target_relative = PurePosixPath("artifacts") / activation_id / expected_name
        target = self._bounded_path(workspace.path, target_relative)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as artifact_file:
                artifact_file.write(content)
        except FileExistsError:
            existing = self._read_valid_text(target, self.artifact_limit)
            if existing != content:
                raise AgentWorkspaceError(
                    "Published Agent Artifact cannot be overwritten"
                ) from None
        except OSError as error:
            raise AgentWorkspaceError("Cannot publish Agent Artifact") from error
        uri_path = quote(
            f"{_WORKSPACE_DIRECTORY}/{workspace.workspace_id}/{target_relative.as_posix()}",
            safe="/",
        )
        return ArtifactDescriptor(
            uri=f"artifact://local/{uri_path}",
            project_relative_path=str(
                Path(_RUNTIME_DIRECTORY)
                / _WORKSPACE_DIRECTORY
                / workspace.workspace_id
                / target_relative
            ),
            media_type=("text/markdown" if target.suffix.lower() == ".md" else "text/plain"),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def stage_activation_input(
        self,
        workspace: AgentWorkspace,
        activation_id: str,
        name: str,
        content: bytes,
        *,
        media_type: str,
    ) -> ResolvedArtifact:
        """Stage one immutable Runtime-provided input outside the Agent write root."""
        safe_name = self._safe_relative(name)
        if len(safe_name.parts) != 1:
            raise AgentWorkspaceError("Agent input name must be a single path segment")
        relative = PurePosixPath("inputs") / activation_id / safe_name
        path = self._bounded_path(workspace.path, relative)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as input_file:
                input_file.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise AgentWorkspaceError("Agent activation input cannot be overwritten") from None
        except OSError as error:
            raise AgentWorkspaceError("Cannot stage Agent activation input") from error
        return ResolvedArtifact(
            uri=f"activation://{activation_id}/{safe_name.as_posix()}",
            path=path,
            media_type=media_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def _load(self, workspace_id: str) -> AgentWorkspace:
        if not _SAFE_ID.fullmatch(workspace_id):
            raise AgentWorkspaceError("Agent workspace ID is invalid")
        path = self.workspaces_root / workspace_id
        metadata_path = self._bounded_path(
            self.workspaces_root,
            PurePosixPath(workspace_id) / "workspace.json",
        )
        try:
            metadata = _WorkspaceMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise AgentWorkspaceError("Agent workspace metadata is invalid") from error
        if metadata.version != 1 or metadata.workspace_id != workspace_id:
            raise AgentWorkspaceError("Agent workspace metadata does not match its path")
        return AgentWorkspace(
            workspace_id=workspace_id,
            agent_id=metadata.agent_id,
            profile_digest=metadata.profile_digest,
            path=path,
        )

    def _published_descriptor(
        self,
        workspace_id: str,
        activation_id: str,
        *,
        uri: str,
    ) -> ArtifactDescriptor:
        result_path = self._bounded_path(
            self.workspaces_root,
            PurePosixPath(workspace_id) / "activations" / activation_id / "result.json",
        )
        content = self._read_valid_text(result_path, self.response_limit)
        try:
            envelope: object = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise AgentWorkspaceError("Published Artifact result envelope is invalid") from error
        for candidate in self._nested_dicts(envelope):
            if candidate.get("uri") != uri:
                continue
            try:
                return ArtifactDescriptor(**candidate)
            except (TypeError, ValueError) as error:
                raise AgentWorkspaceError("Published Artifact descriptor is invalid") from error
        raise AgentWorkspaceError("Published Artifact descriptor is missing")

    @classmethod
    def _nested_dicts(cls, value: object) -> Iterator[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from cls._nested_dicts(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._nested_dicts(item)

    @staticmethod
    def _safe_relative(value: str) -> PurePosixPath:
        if not value or "\x00" in value or "\\" in value:
            raise AgentWorkspaceError("Artifact path is invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise AgentWorkspaceError("Artifact path must stay inside its namespace")
        return path

    @staticmethod
    def _bounded_path(base: Path, relative: PurePosixPath) -> Path:
        if base.is_symlink():
            raise AgentWorkspaceError("Artifact base path must not be a symlink")
        candidate = base.joinpath(*relative.parts)
        current = base
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise AgentWorkspaceError("Artifact paths must not contain symlinks")
        try:
            candidate.resolve().relative_to(base.resolve())
        except ValueError as error:
            raise AgentWorkspaceError("Artifact path escapes its namespace") from error
        return candidate

    def _ensure_workspace_root(self) -> None:
        try:
            self.runtime_root.mkdir(exist_ok=True)
            if self.runtime_root.is_symlink():
                raise AgentWorkspaceError("Agent runtime directory must not be a symlink")
            self.workspaces_root.mkdir(exist_ok=True)
            if self.workspaces_root.is_symlink():
                raise AgentWorkspaceError("Agent workspaces directory must not be a symlink")
        except OSError as error:
            raise AgentWorkspaceError("Cannot create Agent workspace root") from error

    @staticmethod
    def _read_valid_text(path: Path, limit: int) -> bytes:
        try:
            if not path.is_file() or path.is_symlink():
                raise AgentWorkspaceError(f"Artifact does not exist: {path}")
            content = path.read_bytes()
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AgentWorkspaceError("Artifact must be UTF-8 text") from error
        except OSError as error:
            raise AgentWorkspaceError(f"Artifact cannot be read: {path}") from error
        if not content.strip():
            raise AgentWorkspaceError("Artifact must not be empty")
        if len(content) > limit:
            raise AgentWorkspaceError(f"Artifact exceeds the {limit}-byte limit")
        return content

    def _ensure_runtime_git_excluded(self) -> None:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.project_path),
                    "rev-parse",
                    "--git-path",
                    "info/exclude",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            raise AgentWorkspaceError("Cannot inspect project-local Git exclude") from error
        if result.returncode != 0:
            return
        exclude_path = Path(result.stdout.strip())
        if not exclude_path.is_absolute():
            exclude_path = self.project_path / exclude_path
        try:
            existing = exclude_path.read_text(encoding="utf-8")
            if f"{_RUNTIME_DIRECTORY}/" in existing.splitlines():
                return
            separator = "" if not existing or existing.endswith("\n") else "\n"
            self._atomic_write(
                exclude_path,
                f"{existing}{separator}{_RUNTIME_DIRECTORY}/\n",
            )
        except OSError as error:
            raise AgentWorkspaceError("Cannot update project-local Git exclude") from error

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            try:
                temporary.write_text(content, encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as error:
            raise AgentWorkspaceError(f"Cannot write Agent workspace file: {path}") from error
