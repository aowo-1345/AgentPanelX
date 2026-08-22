"""Application-wide Loguru configuration."""

import sys
from contextlib import suppress
from pathlib import Path
from threading import Lock

from loguru import logger

_CONFIGURATION_LOCK = Lock()
_configured_directory: Path | None = None
_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}"


def configure_logging(log_directory: Path = Path(".logs")) -> None:
    """Configure human-readable console and daily file sinks once per location."""

    global _configured_directory
    resolved = log_directory.resolve()
    with _CONFIGURATION_LOCK:
        if _configured_directory == resolved:
            return
        logger.remove()
        logger.add(sys.stderr, format=_FORMAT, colorize=False)
        try:
            logger.add(
                resolved / "agentplanex-{time:YYYY-MM-DD}.log",
                format=_FORMAT,
                rotation="00:00",
                retention="3 days",
                encoding="utf-8",
                delay=False,
                catch=True,
            )
        except Exception as error:
            _fallback_diagnostic(error)
        _configured_directory = resolved


def _fallback_diagnostic(error: Exception) -> None:
    with suppress(Exception):
        sys.stderr.write(
            "AgentPanelX file logging is unavailable; continuing with console logs "
            f"({type(error).__name__}).\n"
        )
