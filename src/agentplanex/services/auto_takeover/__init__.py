"""Ultra Mode BLOCKED takeover capability with cycle-safe public exports."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentplanex.services.auto_takeover._operation import (
        AutoTakeoverOperation,
        AutoTakeoverOutput,
        AutoTakeoverPayload,
    )
    from agentplanex.services.auto_takeover._service import (
        AutoTakeoverPort,
        AutoTakeoverService,
    )
    from agentplanex.services.auto_takeover.models import AutoTakeoverSnapshot

__all__ = [
    "AutoTakeoverOperation",
    "AutoTakeoverOutput",
    "AutoTakeoverPayload",
    "AutoTakeoverPort",
    "AutoTakeoverService",
    "AutoTakeoverSnapshot",
]


def __getattr__(name: str) -> Any:
    if name in {"AutoTakeoverOperation", "AutoTakeoverOutput", "AutoTakeoverPayload"}:
        from agentplanex.services.auto_takeover import _operation

        return getattr(_operation, name)
    if name in {"AutoTakeoverPort", "AutoTakeoverService"}:
        from agentplanex.services.auto_takeover import _service

        return getattr(_service, name)
    if name == "AutoTakeoverSnapshot":
        from agentplanex.services.auto_takeover import models

        return getattr(models, name)
    raise AttributeError(name)
