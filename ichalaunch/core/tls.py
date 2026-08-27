"""Process-wide TLS CA bundle sanitizer.

Windows installs (PostgreSQL, some Git/curl builds, corporate agents) often
set ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE`` and friends
to a path that later goes missing. Python ``ssl``, ``requests``, and ``urllib``
then fail every HTTPS call — catalog, addons, GitHub, GitLab, updates — each
naming whichever env var that stack inherited.

Only invalid or unreadable paths are replaced. A custom CA file that exists
and is readable (corporate MITM) is kept. After sanitizing, every file-based
CA env var and the default ``ssl`` context use the same readable bundle
(certifi, or that valid custom file).
"""

from __future__ import annotations

import os
import ssl
import sys
from typing import Iterable, MutableMapping

# File-valued CA env vars read by OpenSSL, requests, curl, git, pip, or Node.
CA_FILE_ENV_VARS: tuple[str, ...] = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "PIP_CERT",
    "NODE_EXTRA_CA_CERTS",
)

# Directory of hashed certs (OpenSSL). Never point this at certifi's pem.
CA_DIR_ENV_VARS: tuple[str, ...] = ("SSL_CERT_DIR",)

_SSL_ORIG_CREATE: object | None = None
_PROCESS_CA: str | None = None
_STATUS: str = ""


def _readable_file(path: str) -> bool:
    text = (path or "").strip()
    if not text:
        return False
    try:
        return os.path.isfile(text) and os.access(text, os.R_OK)
    except OSError:
        return False


def _readable_dir(path: str) -> bool:
    text = (path or "").strip()
    if not text:
        return False
    try:
        return os.path.isdir(text) and os.access(text, os.R_OK)
    except OSError:
        return False


def bundled_ca_file() -> str | None:
    """Return certifi's ``cacert.pem`` if it is extractable / readable."""
    candidates: list[str] = []
    try:
        import certifi

        where = certifi.where()
        if where:
            candidates.append(where)
    except Exception:  # noqa: BLE001
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(str(meipass), "certifi", "cacert.pem"))
    seen: set[str] = set()
    for raw in candidates:
        path = os.path.normpath(raw)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if _readable_file(path):
            return path
    return None


def process_ca_file() -> str | None:
    """CA bundle the process should use for HTTPS (after sanitizing)."""
    return _PROCESS_CA


def tls_ca_log_line() -> str:
    return _STATUS


def _first_valid_env_file(names: Iterable[str]) -> str | None:
    for name in names:
        raw = os.environ.get(name)
        if raw and _readable_file(raw):
            return raw.strip()
    return None


def _apply_ssl_defaults(cafile: str) -> None:
    """Force default ``ssl`` / ``urllib`` contexts onto *cafile*."""
    global _SSL_ORIG_CREATE
    if _SSL_ORIG_CREATE is None:
        _SSL_ORIG_CREATE = ssl.create_default_context

    orig = _SSL_ORIG_CREATE
    assert callable(orig)
    cafile_path = cafile

    def create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: None | str | bytes = None,
    ) -> ssl.SSLContext:
        if cafile is None and capath is None and cadata is None:
            cafile = cafile_path
        return orig(purpose, cafile=cafile, capath=capath, cadata=cadata)

    ssl.create_default_context = create_default_context  # type: ignore[method-assign]
    ssl._create_default_https_context = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: create_default_context()
    )


def _apply_requests_defaults(cafile: str) -> None:
    """If requests/urllib3 are already imported, point them at *cafile*."""
    requests_mod = sys.modules.get("requests")
    if requests_mod is not None:
        certs = getattr(requests_mod, "certs", None)
        if certs is not None and hasattr(certs, "where"):
            try:
                certs.where = lambda: cafile  # type: ignore[method-assign]
            except Exception:  # noqa: BLE001
                pass
    urllib3_mod = sys.modules.get("urllib3")
    if urllib3_mod is None:
        return
    try:
        from urllib3.util import ssl_ as urllib3_ssl
    except Exception:  # noqa: BLE001
        return
    for attr in ("DEFAULT_CA_BUNDLE_PATH", "DEFAULT_CERTS"):
        if hasattr(urllib3_ssl, attr):
            try:
                setattr(urllib3_ssl, attr, cafile)
            except Exception:  # noqa: BLE001
                pass


def strip_launcher_ca_env(env: MutableMapping[str, str]) -> list[str]:
    """Drop CA variables that point at this launcher's own bundled certifi.

    sanitize_tls_ca_env() pins this process to a readable CA bundle, and where
    nothing valid was inherited that bundle is the copy of certifi inside the
    frozen build. It lives under the PyInstaller extraction directory, which is
    removed when the launcher exits.

    A launched child outlives the launcher. That is the whole point of the game,
    and closing the launcher when the game starts is a shipped setting. Handing
    that child a CA path which is about to disappear recreates, one process
    along, the exact failure this module exists to repair. umu fetches its own
    runtime over HTTPS, so this breaks a real thing rather than a hypothetical
    one, and it does so only on the machines where the runtime was not already
    cached, which is what makes it hard to reproduce.

    A CA file the user set themselves is deliberately left in place: it is
    readable, it is theirs, and a corporate prefix may well need it. Only the
    launcher's own bundled copy is removed.

    Returns the variable names removed, so the launch log can say so.
    """
    bundled = bundled_ca_file()
    if not bundled:
        return []
    owned = os.path.normcase(os.path.normpath(bundled))
    removed: list[str] = []
    for name in CA_FILE_ENV_VARS:
        raw = env.get(name)
        if not raw:
            continue
        if os.path.normcase(os.path.normpath(raw.strip())) == owned:
            env.pop(name, None)
            removed.append(name)
    return removed


def sanitize_tls_ca_env() -> str | None:
    """Drop missing CA env paths; pin the process to one readable bundle.

    Returns the bundle path in use, or ``None`` if no readable bundle exists.
    """
    global _PROCESS_CA, _STATUS

    replaced: list[str] = []

    for name in CA_DIR_ENV_VARS:
        raw = os.environ.get(name)
        if raw is None:
            continue
        if _readable_dir(raw):
            continue
        os.environ.pop(name, None)
        replaced.append(name)

    process_ca = _first_valid_env_file(CA_FILE_ENV_VARS) or bundled_ca_file()
    _PROCESS_CA = process_ca

    for name in CA_FILE_ENV_VARS:
        raw = os.environ.get(name)
        if raw is not None and _readable_file(raw):
            continue
        if raw is not None:
            os.environ.pop(name, None)
            replaced.append(name)
        if process_ca:
            os.environ[name] = process_ca

    if process_ca:
        _apply_ssl_defaults(process_ca)
        _apply_requests_defaults(process_ca)

    if replaced:
        _STATUS = (
            f"TLS CA: using {process_ca or 'system defaults'} "
            f"(replaced invalid {', '.join(replaced)})"
        )
    elif process_ca:
        _STATUS = f"TLS CA: using {process_ca}"
    else:
        _STATUS = "TLS CA: no readable bundle; left system defaults"
    return process_ca
