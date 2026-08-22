"""Persistent local Agent workspaces, Outbox files, and Artifact URIs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from agentplanex.domains.artifact import ArtifactDescriptor

_RUNTIME_DIRECTORY = ".agentplanex"
_WORKSPACE_DIRECTORY = "agent-workspaces"
_CONVERSATION_PREFIX = "apx1."
_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


class AgentWorkspaceError(ValueError):
    """A workspace, conversation reference, or artifact is invalid."""


@dataclass(frozen=True, slots=True)
class ConversationReference:
    """Decoded identity of one Codex thread and writable workspace."""

    agent_id: str
    profile_digest: str
    workspace_id: str
    thread_id: str


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

    def create(self, *, agent_id: str, profile_digest: str) -> AgentWorkspace:
        """Create a new persistent workspace for one Agent conversation."""
        self._ensure_workspace_root()
        self._ensure_runtime_git_excluded()
        workspace_id = uuid4().hex
        path = self.workspaces_root / workspace_id
        (path / "documents").mkdir(parents=True)
        (path / "outbox").mkdir()
        metadata = _WorkspaceMetadata(
            version=1,
            agent_id=agent_id,
            profile_digest=profile_digest,
            workspace_id=workspace_id,
        )
        self._atomic_write(path / "workspace.json", metadata.model_dump_json(indent=2))
        return AgentWorkspace(
            workspace_id=workspace_id,
            agent_id=agent_id,
            profile_digest=profile_digest,
            path=path,
        )

    def restore(
        self,
        *,
        agent_id: str,
        profile_digest: str,
        conversation_id: str,
    ) -> tuple[AgentWorkspace, str]:
        """Restore and validate the workspace and Codex thread in an opaque reference."""
        reference = self.decode_conversation(conversation_id)
        if reference.agent_id != agent_id:
            raise AgentWorkspaceError(
                "conversation_id belongs to a different Agent"
            )
        if reference.profile_digest != profile_digest:
            raise AgentWorkspaceError(
                "conversation_id belongs to an outdated Agent profile"
            )
        workspace = self._load(reference.workspace_id)
        if (
            workspace.agent_id != reference.agent_id
            or workspace.profile_digest != reference.profile_digest
        ):
            raise AgentWorkspaceError("conversation_id workspace binding is invalid")
        return workspace, reference.thread_id

    def create_invocation(self, workspace: AgentWorkspace) -> AgentInvocation:
        """Allocate a unique Outbox result path so stale results cannot be reused."""
        invocation_id = uuid4().hex
        outbox = self._bounded_path(workspace.path, PurePosixPath("outbox"))
        result_path = outbox / invocation_id / "result.json"
        result_path.parent.mkdir(parents=True)
        return AgentInvocation(
            invocation_id=invocation_id,
            workspace=workspace,
            result_path=result_path,
        )

    def encode_conversation(
        self,
        workspace: AgentWorkspace,
        thread_id: str,
    ) -> str:
        """Encode the complete explicit resume identity as an opaque string."""
        if not thread_id.strip():
            raise AgentWorkspaceError("Codex returned an empty thread ID")
        payload = json.dumps(
            {
                "agent_id": workspace.agent_id,
                "profile_digest": workspace.profile_digest,
                "thread_id": thread_id,
                "version": 1,
                "workspace_id": workspace.workspace_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return f"{_CONVERSATION_PREFIX}{encoded}"

    def decode_conversation(self, conversation_id: str) -> ConversationReference:
        """Decode an opaque resume reference without trusting any of its fields."""
        if not conversation_id.startswith(_CONVERSATION_PREFIX):
            raise AgentWorkspaceError("conversation_id has an unsupported format")
        encoded = conversation_id.removeprefix(_CONVERSATION_PREFIX)
        if not encoded or len(encoded) > 8_192:
            raise AgentWorkspaceError("conversation_id has an invalid length")
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded + padding)
            payload: object = json.loads(decoded.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentWorkspaceError("conversation_id is malformed") from error
        if not isinstance(payload, dict) or set(payload) != {
            "agent_id",
            "profile_digest",
            "thread_id",
            "version",
            "workspace_id",
        }:
            raise AgentWorkspaceError("conversation_id payload is invalid")
        if payload.get("version") != 1:
            raise AgentWorkspaceError("conversation_id version is unsupported")
        agent_id = payload.get("agent_id")
        profile_digest = payload.get("profile_digest")
        thread_id = payload.get("thread_id")
        workspace_id = payload.get("workspace_id")
        if not all(
            isinstance(value, str) and value
            for value in (agent_id, profile_digest, thread_id, workspace_id)
        ):
            raise AgentWorkspaceError("conversation_id fields are invalid")
        assert isinstance(agent_id, str)
        assert isinstance(profile_digest, str)
        assert isinstance(thread_id, str)
        assert isinstance(workspace_id, str)
        if not _SAFE_ID.fullmatch(workspace_id):
            raise AgentWorkspaceError("conversation_id workspace is invalid")
        return ConversationReference(
            agent_id=agent_id,
            profile_digest=profile_digest,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )

    def resolve_artifact(self, uri: str) -> ResolvedArtifact:
        """Resolve one supported URI to a validated project-local text file."""
        parsed = urlparse(uri)
        decoded_path = unquote(parsed.path)
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
                or parts[2] != "documents"
            ):
                raise AgentWorkspaceError("Artifact URI is not an Agent document")
            base = self.runtime_root
        else:
            raise AgentWorkspaceError(f"Unsupported Artifact URI: {uri}")
        path = self._bounded_path(base, relative)
        content = self._read_valid_text(path, self.artifact_limit)
        return ResolvedArtifact(
            uri=uri,
            path=path,
            media_type=("text/markdown" if path.suffix.lower() == ".md" else "text/plain"),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def read_result_json(self, invocation: AgentInvocation) -> dict[str, Any]:
        """Load a newly allocated model-written Outbox result object."""
        relative = (
            PurePosixPath("outbox") / invocation.invocation_id / "result.json"
        )
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

    def output_artifact(
        self,
        workspace: AgentWorkspace,
        relative_path: str,
        *,
        expected_name: str,
    ) -> ArtifactDescriptor:
        """Validate and expose one Contract-declared workspace document."""
        relative = self._safe_relative(relative_path)
        if relative != PurePosixPath("documents") / expected_name:
            raise AgentWorkspaceError(
                f"Agent Contract requires documents/{expected_name}"
            )
        path = self._bounded_path(workspace.path, relative)
        content = self._read_valid_text(path, self.artifact_limit)
        uri_path = quote(
            f"{_WORKSPACE_DIRECTORY}/{workspace.workspace_id}/{relative.as_posix()}",
            safe="/",
        )
        return ArtifactDescriptor(
            uri=f"artifact://local/{uri_path}",
            project_relative_path=str(
                Path(_RUNTIME_DIRECTORY)
                / _WORKSPACE_DIRECTORY
                / workspace.workspace_id
                / relative
            ),
            media_type="text/markdown",
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
                raise AgentWorkspaceError(
                    "Agent runtime directory must not be a symlink"
                )
            self.workspaces_root.mkdir(exist_ok=True)
            if self.workspaces_root.is_symlink():
                raise AgentWorkspaceError(
                    "Agent workspaces directory must not be a symlink"
                )
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
        result = subprocess.run(
            ["git", "-C", str(self.project_path), "rev-parse", "--git-path", "info/exclude"],
            check=False,
            capture_output=True,
            text=True,
        )
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
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
