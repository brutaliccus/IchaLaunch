"""Client-mod catalog (``mods.json``) with signed remote refresh.

Client-tab update nags read this file only. A live copy is fetched from public
master the same way ``addons.json`` is: ``fetch_verified_text`` (GET the JSON
and its ``.sig``), or it is not used. Failure falls through to an AppData cache
of a previously verified copy, then to the copy bundled in the signed EXE.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ichalaunch.config.settings import appdata_root, settings
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file
from ichalaunch.core.signed_fetch import fetch_verified_text

DEFAULT_MODS_URL = (
    "https://raw.githubusercontent.com/brutaliccus/IchaLaunch/master/"
    "ichalaunch/data/mods.json"
)
MODS_TTL_SEC = 15 * 60
_FETCH_TIMEOUT_SEC = 8
_UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/json"}

# In-process snapshot: (monotonic_loaded_at, entries, source_label)
_loaded: tuple[float, list[dict[str, Any]], str] | None = None


def mods_cache_path() -> Path:
    return appdata_root() / "mods_catalog.json"


def bundled_mods_path() -> Path:
    return data_file("mods.json")


def mods_url() -> str:
    override = str(settings.get("mod_catalog_url") or "").strip()
    return override or DEFAULT_MODS_URL


def empty_mod_catalog() -> list[dict[str, Any]]:
    return []


def normalize_mod_catalog(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return empty_mod_catalog()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not str(item.get("id") or "").strip():
            continue
        out.append(item)
    return out


def parse_mod_catalog_text(text: str) -> list[dict[str, Any]]:
    try:
        return normalize_mod_catalog(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty_mod_catalog()


def load_mod_catalog_file(path: Path) -> list[dict[str, Any]]:
    try:
        return parse_mod_catalog_text(path.read_text(encoding="utf-8"))
    except OSError:
        return empty_mod_catalog()


def catalog_mod_count(entries: list[dict[str, Any]] | None) -> int:
    return len(entries) if isinstance(entries, list) else 0


def write_mod_catalog_file(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def load_bundled_mod_catalog() -> list[dict[str, Any]]:
    return load_mod_catalog_file(bundled_mods_path())


def _remember(entries: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    global _loaded
    _loaded = (time.monotonic(), entries, source)
    return entries


def current_mod_catalog_source() -> str:
    if _loaded is not None:
        return _loaded[2]
    return ""


def fetch_remote_mod_catalog(url: str | None = None) -> list[dict[str, Any]] | None:
    target = (url or mods_url()).strip()
    if not target:
        return None
    text = fetch_verified_text(
        target,
        timeout=_FETCH_TIMEOUT_SEC,
        headers=_UA,
        label="client mod catalog",
    )
    if text is None:
        return None
    entries = parse_mod_catalog_text(text)
    if catalog_mod_count(entries) == 0:
        return None
    return entries


def refresh_mod_catalog(*, force: bool = False) -> list[dict[str, Any]]:
    """Load the best available catalog (verified remote → cache → bundled)."""
    global _loaded
    if _loaded is not None and not force:
        age = time.monotonic() - _loaded[0]
        if age < MODS_TTL_SEC and catalog_mod_count(_loaded[1]) > 0:
            return _loaded[1]

    remote = fetch_remote_mod_catalog()
    if remote is not None:
        try:
            write_mod_catalog_file(mods_cache_path(), remote)
        except OSError as exc:
            log.debug("Could not cache client mod catalog: %s", exc)
        return _remember(remote, "remote")

    cached = load_mod_catalog_file(mods_cache_path())
    if catalog_mod_count(cached) > 0:
        return _remember(cached, "cache")

    bundled = load_bundled_mod_catalog()
    if catalog_mod_count(bundled) > 0:
        return _remember(bundled, "bundled")

    return _remember(empty_mod_catalog(), "empty")


def load_published_mod_catalog() -> list[dict[str, Any]]:
    """Current catalog without a network round-trip (memory → cache → bundled)."""
    if _loaded is not None and catalog_mod_count(_loaded[1]) > 0:
        return _loaded[1]
    cached = load_mod_catalog_file(mods_cache_path())
    if catalog_mod_count(cached) > 0:
        return _remember(cached, "cache")
    bundled = load_bundled_mod_catalog()
    if catalog_mod_count(bundled) > 0:
        return _remember(bundled, "bundled")
    return empty_mod_catalog()


def clear_mod_catalog_cache() -> None:
    global _loaded
    _loaded = None
