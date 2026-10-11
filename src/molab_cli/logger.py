"""
Centralized structured logging and diagnostic observability for MoLab CLI.
Controlled by MOLAB_DEBUG=1 or MOLAB_LOG_LEVEL.
"""

import logging
import os
import sys

_INITIALIZED = False


def setup_logger(name: str = "molab") -> logging.Logger:
    """Configure and return a structured logger for molab-cli."""
    global _INITIALIZED
    logger = logging.getLogger(name)

    if not _INITIALIZED:
        debug_mode = os.getenv("MOLAB_DEBUG", "").strip().lower() in ("1", "true", "yes")
        log_level_env = os.getenv("MOLAB_LOG_LEVEL", "DEBUG" if debug_mode else "INFO").upper()
        log_level = getattr(logging, log_level_env, logging.INFO)

        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(log_level)
        _INITIALIZED = True

    return logger


def get_logger(subsystem: str) -> logging.Logger:
    """Return a scoped logger for a subsystem."""
    return logging.getLogger(f"molab.{subsystem}")
