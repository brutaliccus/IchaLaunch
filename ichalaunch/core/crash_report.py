"""Opt-in crash / ERROR reporting via the Cloudflare submit Worker.

Disabled by default (``crash_reporting_enabled``). When enabled, posts a
redacted log excerpt as a comment on the sticky Windows or Linux crash-log
GitHub issue (OS-routed; not a new issue per report). Fire-and-forget —
never blocks the UI hard; failures are silent or one soft log line.
"""

from __future__ import annotations

import logging
import platform
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import requests

from ichalaunch import __version__
from ichalaunch.config import settings as settings_mod

# Same workers.dev host as catalog submit; dedicated path.
CRASH_REPORT_URL = "https://ichalaunch-addon-submit.ichalaunch.workers.dev/crash"

_UA = {
    "User-Agent": f"IchaLaunch/{__version__}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_TIMEOUT_SEC = 12

# Client-side rate limits (Worker also rate-limits).
_MIN_INTERVAL_SEC = 300.0  # 5 minutes between POSTs
_MAX_PER_HOUR = 3
_ERROR_DEBOUNCE_SEC = 20.0
_MAX_LOG_CHARS = 8_000
_MAX_CRASH_CHARS = 12_000
_MAX_SUMMARY = 200

_lock = threading.Lock()
_last_send_mono = 0.0
_send_times: deque[float] = deque()
_error_timer: threading.Timer | None = None
_pending_error_summary = ""
_install_handler_done = False

# Obvious secrets — never send raw tokens / PATs / Discord auth.
_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(
        r"(?i)\b(mfa\.[A-Za-z0-9_-]{20,}|[\w-]{24}\.[\w-]{6}\.[\w-]{27,}|"
        r"[\w-]{26}\.[\w-]{6}\.[\w-]{38})\b"
    ),  # Discord-ish token shapes
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+"),
    re.compile(r'(?i)("github_token"\s*:\s*")[^"]*(")'),
    re.compile(r"(?i)(github[_ ]?token\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(discord[_ ]?(token|bot)\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(api[_ ]?key\s*[:=]\s*)\S+"),
)


CRASH_REPORTING_OPT_IN_KEY = "crash_reporting_opt_in_prompted_v1"

CRASH_REPORTING_OPT_IN_TITLE = "Help improve IchaLaunch?"

CRASH_REPORTING_OPT_IN_TEXT = (
    "Would you like to turn on optional crash and error reporting?\n"
    "\n"
    "When enabled, IchaLaunch can send redacted log excerpts to the maintainer "
    "when something crashes or a serious error is logged. This helps fix bugs.\n"
    "\n"
    "Reporting is off by default and completely optional. No Discord tokens or "
    "GitHub PATs are included. You can change this anytime in Settings → Privacy."
)


def crash_reporting_enabled() -> bool:
    """True only when the user explicitly opted in (default off)."""
    return bool(settings_mod.settings.get("crash_reporting_enabled", False))


def should_prompt_crash_reporting_opt_in() -> bool:
    """True once, until the first-launch opt-in prompt is answered or skipped."""
    if bool(settings_mod.settings.get(CRASH_REPORTING_OPT_IN_KEY, False)):
        return False
    # Already on (e.g. toggled in Settings before the prompt) — never nag.
    if crash_reporting_enabled():
        mark_crash_reporting_opt_in_prompted()
        return False
    return True


def mark_crash_reporting_opt_in_prompted() -> None:
    """Persist that the one-shot opt-in prompt has been shown."""
    settings_mod.settings.set(CRASH_REPORTING_OPT_IN_KEY, True)


def enable_crash_reporting_from_opt_in() -> None:
    """Accept the first-launch prompt: enable reporting and never ask again."""
    settings_mod.settings.set("crash_reporting_enabled", True)
    mark_crash_reporting_opt_in_prompted()


def crash_report_url() -> str:
    return CRASH_REPORT_URL


def redact_secrets(text: str) -> str:
    """Strip obvious tokens / PATs from log text before upload."""
    out = text or ""
    # Standalone token shapes → full redact.
    out = _SECRET_RES[0].sub("[REDACTED]", out)
    out = _SECRET_RES[1].sub("[REDACTED]", out)
    out = _SECRET_RES[2].sub("[REDACTED]", out)
    # Prefixed assignments / headers — keep the key, drop the value.
    out = _SECRET_RES[3].sub(r"\1\2[REDACTED]", out)
    out = _SECRET_RES[4].sub(r"\1[REDACTED]\2", out)
    out = _SECRET_RES[5].sub(r"\1[REDACTED]", out)
    out = _SECRET_RES[6].sub(r"\1[REDACTED]", out)
    out = _SECRET_RES[7].sub(r"\1[REDACTED]", out)
    # Soften absolute paths that often embed usernames.
    out = re.sub(
        r"(?i)([A-Z]:\\(?:Users|home)\\)([^\\/\s]+)(\\?)",
        r"\1[user]\3",
        out,
    )
    out = re.sub(
        r"(?i)(/home/)([^/\s]+)(/?)",
        r"\1[user]\3",
        out,
    )
    out = re.sub(
        r'(?i)((?:game_path|addons_path|linux_wineprefix)\s*[:=]\s*")[^"]*(")',
        r"\1[REDACTED]\2",
        out,
    )
    return out


def _tail_file(path: Path, max_chars: int) -> str:
    try:
        if not path.is_file():
            return ""
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) <= max_chars:
        return data
    return "… (truncated)\n" + data[-max_chars:]


def _rate_limited() -> bool:
    now = time.monotonic()
    with _lock:
        global _last_send_mono
        while _send_times and now - _send_times[0] > 3600.0:
            _send_times.popleft()
        if len(_send_times) >= _MAX_PER_HOUR:
            return True
        if _last_send_mono and (now - _last_send_mono) < _MIN_INTERVAL_SEC:
            return True
        return False


def _mark_sent() -> None:
    global _last_send_mono
    now = time.monotonic()
    with _lock:
        _last_send_mono = now
        _send_times.append(now)


def _build_payload(
    *,
    kind: str,
    summary: str,
    log_excerpt: str = "",
    crash_excerpt: str = "",
) -> dict[str, Any]:
    from ichalaunch.addons.submit import anonymous_client_id
    from ichalaunch.core.logging_setup import app_log_path, crash_log_path

    if not log_excerpt:
        log_excerpt = _tail_file(app_log_path(), _MAX_LOG_CHARS)
    if not crash_excerpt:
        crash_excerpt = _tail_file(crash_log_path(), _MAX_CRASH_CHARS)

    summary_s = redact_secrets((summary or "").strip())[:_MAX_SUMMARY] or kind
    os_family = (platform.system() or "").strip().lower() or "unknown"
    return {
        "type": "crash",
        "kind": kind if kind in ("crash", "error") else "error",
        "summary": summary_s,
        "launcher_version": __version__,
        "os": platform.platform(),
        "os_family": os_family,
        "python": platform.python_version(),
        "client_id": anonymous_client_id(),
        "log_excerpt": redact_secrets(log_excerpt)[: _MAX_LOG_CHARS + 40],
        "crash_excerpt": redact_secrets(crash_excerpt)[: _MAX_CRASH_CHARS + 40],
    }


def _post_payload(payload: dict[str, Any]) -> None:
    soft = logging.getLogger("ichalaunch")
    try:
        r = requests.post(
            crash_report_url(),
            json=payload,
            headers=_UA,
            timeout=_TIMEOUT_SEC,
        )
        if 200 <= r.status_code < 300:
            soft.debug("Crash report submitted (HTTP %s)", r.status_code)
        else:
            soft.info("Crash report failed (HTTP %s)", r.status_code)
    except Exception:  # noqa: BLE001
        soft.info("Crash report failed (network)")


def _send_async(payload: dict[str, Any]) -> None:
    if _rate_limited():
        return
    _mark_sent()
    t = threading.Thread(
        target=_post_payload,
        args=(payload,),
        name="ichalaunch-crash-report",
        daemon=True,
    )
    t.start()


def report_crash(summary: str = "Unhandled exception") -> None:
    """Best-effort POST after a crash.log write. No-op when opt-in is off."""
    if not crash_reporting_enabled():
        return
    try:
        payload = _build_payload(kind="crash", summary=summary)
        _send_async(payload)
    except Exception:  # noqa: BLE001
        pass


def _flush_pending_error() -> None:
    global _error_timer, _pending_error_summary
    with _lock:
        _error_timer = None
        summary = _pending_error_summary or "Logged ERROR"
        _pending_error_summary = ""
    if not crash_reporting_enabled():
        return
    try:
        payload = _build_payload(kind="error", summary=summary)
        _send_async(payload)
    except Exception:  # noqa: BLE001
        pass


def report_logged_error(summary: str) -> None:
    """Debounced ERROR log → one report. No-op when opt-in is off."""
    if not crash_reporting_enabled():
        return
    global _error_timer, _pending_error_summary
    text = (summary or "").strip() or "Logged ERROR"
    with _lock:
        _pending_error_summary = text[:_MAX_SUMMARY]
        if _error_timer is not None:
            try:
                _error_timer.cancel()
            except Exception:  # noqa: BLE001
                pass
        _error_timer = threading.Timer(_ERROR_DEBOUNCE_SEC, _flush_pending_error)
        _error_timer.daemon = True
        _error_timer.start()


class _OptInErrorHandler(logging.Handler):
    """Forward significant ERROR+ records to the crash reporter (debounced)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        # Avoid feedback from our own soft lines / nested handlers.
        msg = record.getMessage()
        if msg.startswith("Crash report "):
            return
        try:
            summary = f"{record.name}: {msg}"
            if record.exc_info and record.exc_info[0] is not None:
                summary = f"{summary} ({record.exc_info[0].__name__})"
            report_logged_error(summary)
        except Exception:  # noqa: BLE001
            pass


def install_error_report_handler(logger: logging.Logger | None = None) -> None:
    """Attach a once-only ERROR handler (safe to call from logging setup)."""
    global _install_handler_done
    if _install_handler_done:
        return
    _install_handler_done = True
    target = logger or logging.getLogger("ichalaunch")
    handler = _OptInErrorHandler()
    handler.setLevel(logging.ERROR)
    target.addHandler(handler)
