"""Canonical Plan document identity shared by Planning and projections."""

import hashlib
from dataclasses import dataclass

PLAN_DOCUMENT_NAMES = ("architecture.md", "requirements.md", "roadmap.md")


@dataclass(frozen=True, slots=True)
class PlanDocument:
    """One immutable canonical Plan document."""

    name: str
    content: bytes

    def __post_init__(self) -> None:
        if (
            not self.name.strip()
            or self.name in {".", ".."}
            or "/" in self.name
            or "\\" in self.name
        ):
            raise ValueError("Plan document name must be one plain file name")


@dataclass(frozen=True, slots=True)
class PlanSubject:
    """The exact ordered Plan content presented for review and approval."""

    documents: tuple[PlanDocument, ...]

    def __post_init__(self) -> None:
        names = tuple(document.name for document in self.documents)
        if not names:
            raise ValueError("Plan subject must contain at least one document")
        if len(set(names)) != len(names):
            raise ValueError("Plan subject document names must be unique")

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        for document in self.documents:
            name = document.name.encode("utf-8")
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(len(document.content).to_bytes(8, "big"))
            digest.update(document.content)
        return digest.hexdigest()
