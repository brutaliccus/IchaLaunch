"""Nampower login-password encryption via ``WOW_ENCRYPTION_KEY`` (Windows DPAPI).

Nampower reads this env var to unlock the login-screen Encrypt toggle. The
launcher owns the value: it is generated on first enable, stored in settings,
and injected into the game child process only. The key is never logged.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Mapping, MutableMapping

WOW_ENCRYPTION_ENV = "WOW_ENCRYPTION_KEY"
SETTING_ENABLED = "nampower_encrypt_passwords"
SETTING_KEY = "wow_encryption_key"
# token_urlsafe(32) is 43 URL-safe characters — "not really short".
_KEY_BYTES = 32
_MIN_REDACT_LEN = 8

_KEY_JSON_RE = re.compile(r'(?i)("wow_encryption_key"\s*:\s*")[^"]*(")')
_KEY_ASSIGN_RE = re.compile(r"(?i)(wow_encryption_key\s*[:=]\s*)\S+")
_ENV_ASSIGN_RE = re.compile(r"(?i)(WOW_ENCRYPTION_KEY\s*[:=]\s*)\S+")


def generate_encryption_key() -> str:
    return secrets.token_urlsafe(_KEY_BYTES)


def encrypt_enabled() -> bool:
    from ichalaunch.config.settings import settings

    return bool(settings.get(SETTING_ENABLED, False))


def stored_key() -> str:
    try:
        from ichalaunch.config.settings import settings

        return str(settings.get(SETTING_KEY) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def ensure_encryption_key() -> str:
    """Return the persisted key, generating one if the stored value is empty."""
    key = stored_key()
    if key:
        return key
    from ichalaunch.config.settings import settings

    key = generate_encryption_key()
    settings.set(SETTING_KEY, key)
    return key


def set_encrypt_enabled(enabled: bool) -> None:
    """Persist the toggle. First enable generates a key; disable keeps the key."""
    from ichalaunch.config.settings import settings

    enabled = bool(enabled)
    settings.set(SETTING_ENABLED, enabled)
    if enabled:
        ensure_encryption_key()


def regenerate_encryption_key() -> str:
    """Replace the stored key. Previously encrypted passwords become unreadable."""
    from ichalaunch.config.settings import settings

    key = generate_encryption_key()
    settings.set(SETTING_KEY, key)
    return key


def apply_wow_encryption_env(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """Set or strip ``WOW_ENCRYPTION_KEY`` on *env*. Never logs the value."""
    if not encrypt_enabled():
        env.pop(WOW_ENCRYPTION_ENV, None)
        return env
    key = ensure_encryption_key()
    if key:
        env[WOW_ENCRYPTION_ENV] = key
    else:
        env.pop(WOW_ENCRYPTION_ENV, None)
    return env


def child_launch_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy *base* (or ``os.environ``) and apply launcher-owned launch variables."""
    env = dict(os.environ if base is None else base)
    apply_wow_encryption_env(env)
    return env


def redact_encryption_secrets(text: str) -> str:
    """Strip stored-key assignments and the live key value from *text*."""
    out = text or ""
    out = _KEY_JSON_RE.sub(r"\1[REDACTED]\2", out)
    out = _KEY_ASSIGN_RE.sub(r"\1[REDACTED]", out)
    out = _ENV_ASSIGN_RE.sub(r"\1[REDACTED]", out)
    key = stored_key()
    if len(key) >= _MIN_REDACT_LEN:
        out = out.replace(key, "[REDACTED]")
    return out
