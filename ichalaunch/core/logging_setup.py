"""Logging and crash reporting to AppData."""

from __future__ import annotations

import faulthandler
import logging
import platform
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ichalaunch import __version__
from ichalaunch.config.settings import appdata_root

if TYPE_CHECKING:
    from types import TracebackType

_CRASH_LOG_MAX_BYTES = 1_000_000
_CRASH_LOG_BACKUP_COUNT = 3
_FAULT_FILE: object | None = None


def log_dir() -> Path:
    return appdata_root() / "logs"


def crash_log_path() -> Path:
    return log_dir() / "crash.log"


def app_log_path() -> Path:
    return log_dir() / "ichalaunch.log"


def _rotate_log(path: Path) -> None:
    if not path.exists() or path.stat().st_size < _CRASH_LOG_MAX_BYTES:
        return
    for i in range(_CRASH_LOG_BACKUP_COUNT, 1, -1):
        older = path.with_name(f"{path.name}.{i - 1}")
        newer = path.with_name(f"{path.name}.{i}")
        if older.exists():
            newer.unlink(missing_ok=True)
            older.replace(newer)
    path.replace(path.with_name(f"{path.name}.1"))


def _runtime_versions() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        from PySide6.QtCore import qVersion

        versions["qt"] = qVersion()
    except Exception:  # noqa: BLE001
        versions["qt"] = "unknown"
    try:
        import PySide6

        versions["pyside6"] = getattr(PySide6, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        versions["pyside6"] = "unknown"
    if getattr(sys, "frozen", False):
        versions["runtime"] = "pyinstaller"
    else:
        versions["runtime"] = "source"
    return versions


def write_crash_report(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    context: str = "unhandled exception",
) -> None:
    """Append a structured crash report to ``%LOCALAPPDATA%/IchaLaunch/logs/crash.log``."""
    log_dir().mkdir(parents=True, exist_ok=True)
    path = crash_log_path()
    _rotate_log(path)

    versions = _runtime_versions()
    lines = [
        "",
        "=" * 72,
        f"timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"context: {context}",
        f"app_version: {__version__}",
        f"python: {versions['python']}",
        f"pyside6: {versions['pyside6']}",
        f"qt: {versions['qt']}",
        f"platform: {versions['platform']}",
        f"runtime: {versions['runtime']}",
        f"exception: {exc_type.__name__}: {exc}",
        "-" * 72,
        "".join(traceback.format_exception(exc_type, exc, tb)).rstrip(),
        "=" * 72,
        "",
    ]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def setup_logging() -> logging.Logger:
    log_dir().mkdir(parents=True, exist_ok=True)
    log_file = app_log_path()
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


def _enable_faulthandler() -> None:
    global _FAULT_FILE
    log_dir().mkdir(parents=True, exist_ok=True)
    _FAULT_FILE = crash_log_path().open("a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(file=_FAULT_FILE, all_threads=True)


def _excepthook(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    """Log unhandled errors (including Qt slot exceptions) without aborting."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc, tb)
        return
    write_crash_report(exc_type, exc, tb)
    log.error("Unhandled exception", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    write_crash_report(
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
        context=f"thread exception ({args.thread.name})",
    )
    log.error(
        "Unhandled thread exception in %s",
        args.thread.name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def install_crash_handlers() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _enable_faulthandler()


log = setup_logging()
install_crash_handlers()
