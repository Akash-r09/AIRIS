"""
Logging configuration for AIRIS.

Call `configure_logging()` exactly once, at application startup (done in
`backend/app/main.py`). Every other module obtains a logger via
`get_logger(__name__)` — never via `print()`.
"""

from __future__ import annotations

import logging
import logging.config
import sys

_CONFIGURED = False

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str = "INFO") -> None:
    """
    Configures root logging for the whole process. Idempotent — safe to
    call more than once (e.g. in tests); only the first call takes effect
    unless `force=True` semantics are needed, in which case reset
    `_CONFIGURED` explicitly.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    normalized_level = level.upper()
    if normalized_level not in _VALID_LEVELS:
        normalized_level = "INFO"

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": _LOG_FORMAT,
                "datefmt": _DATE_FORMAT,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": sys.stdout,
            },
        },
        "root": {
            "level": normalized_level,
            "handlers": ["console"],
        },
        "loggers": {
            # Quiet down noisy third-party libraries at DEBUG unless we
            # explicitly want them; app code always logs at the configured
            # level.
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)
    _CONFIGURED = True

    logging.getLogger(__name__).info(
        "Logging configured (level=%s).", normalized_level
    )


def get_logger(name: str) -> logging.Logger:
    """
    Returns a module-scoped logger. Call `configure_logging()` at process
    startup before relying on handler/format configuration; this function
    works either way but formatting only applies once configured.
    """
    return logging.getLogger(name)
