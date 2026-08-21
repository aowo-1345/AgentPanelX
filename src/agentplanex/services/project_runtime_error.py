"""Stable failures produced by project-scoped Runtime commands."""


class ProjectRuntimeCommandError(RuntimeError):
    """An expected Runtime command rejection safe for external presentation."""


class FeatureBusyError(ProjectRuntimeCommandError):
    """Another exclusive operation already owns the same Feature Runtime."""

    code = "FEATURE_BUSY"

    def __init__(self, feature_identity: str) -> None:
        super().__init__(f"Feature is already executing: {feature_identity}")
