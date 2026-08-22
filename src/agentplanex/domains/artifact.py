"""Runtime-visible artifact identity and integrity facts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """A validated Agent output exposed across Runtime capabilities."""

    uri: str
    project_relative_path: str
    media_type: str
    size: int
    sha256: str
