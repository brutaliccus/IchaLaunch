"""Simple logging to AppData."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ichalaunch.config.settings import appdata_root


def setup_logging() -> logging.Logger:
    log_dir = appdata_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ichalaunch.log"
    logger = logging.getLogger("ichalaunch")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


log = setup_logging()


def _excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
    """Log unhandled errors (including Qt slot exceptions) without aborting."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log.error("Unhandled exception", exc_info=(exc_type, exc, tb))


sys.excepthook = _excepthook
