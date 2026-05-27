"""structlog JSON logging setup."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure(level: str = "INFO", file_path: str = "/var/log/almc-shield/agent.log",
              destination: str = "file", max_size_mb: int = 50, backup_count: int = 7) -> None:
    """Configure stdlib + structlog with JSON output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = []
    if destination == "stdout":
        handlers.append(logging.StreamHandler(sys.stdout))
    else:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        ))

    formatter = logging.Formatter("%(message)s")
    for h in handlers:
        h.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(log_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
