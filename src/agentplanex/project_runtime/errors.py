"""Failures exposed by the Project Runtime command boundary."""


class FeatureBusyError(RuntimeError):
    """Another exclusive operation already owns the same Feature Runtime."""

    code = "FEATURE_BUSY"

    def __init__(self, feature_identity: str) -> None:
        super().__init__(f"Feature is already executing: {feature_identity}")
