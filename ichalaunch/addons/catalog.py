"""Available-addon catalog (``addons.json``) with remote refresh.

The browseable Available list is authored in ``ichalaunch/data/addons.json``.
That file is published on GitHub; launchers prefer a fresh remote copy, then an
appdata cache, then the bundled copy shipped with the build.

Update tip SHAs still live in ``addon_tips.json`` (see ``tip_index``). Both are
refreshed on the same periodic update-check cadence.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ichalaunch.config.settings import appdata_root, settings
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/brutaliccus/IchaLaunch/master/"
    "ichalaunch/data/addons.json"
)
CATALOG_TTL_SEC = 15 * 60
_FETCH_TIMEOUT_SEC = 8
_UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/json"}

# In-process snapshot: (monotonic_loaded_at, entries, source_label)
_loaded: tuple[float, list[dict[str, Any]], str] | None = None


def catalog_cache_path() -> Path:
    return appdata_root() / "addons_catalog.json"


def bundled_catalog_path() -> Path:
    return data_file("addons.json")


def catalog_url() -> str:
    override = str(settings.get("addon_catalog_url") or "").strip()
    return override or DEFAULT_CATALOG_URL


def empty_catalog() -> list[dict[str, Any]]:
    return []


def _entry_key(entry: dict[str, Any]) -> str:
    return str(entry.get("folder") or entry.get("name") or "").strip().lower()


def normalize_catalog(raw: Any) -> list[dict[str, Any]]:
    """Accept a JSON array of addon entries (same shape as bundled ``addons.json``)."""
    if not isinstance(raw, list):
        return empty_catalog()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not _entry_key(item):
            continue
        out.append(item)
    return out


def parse_catalog_text(text: str) -> list[dict[str, Any]]:
    try:
        return normalize_catalog(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty_catalog()


def load_catalog_file(path: Path) -> list[dict[str, Any]]:
    try:
        return parse_catalog_text(path.read_text(encoding="utf-8"))
    except OSError:
        return empty_catalog()


def catalog_entry_count(entries: list[dict[str, Any]] | None) -> int:
    return len(entries) if isinstance(entries, list) else 0


def write_catalog_file(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def load_bundled_catalog() -> list[dict[str, Any]]:
    """Always read the on-disk bundled ``addons.json`` (for builders / offline)."""
    return load_catalog_file(bundled_catalog_path())


def merge_catalog(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge by folder/name key: overlay wins; base-only entries are kept.

    Used when a partial remote list should extend the bundled catalog. Full
    remote replace (preferred for the published master file) skips this.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in base:
        key = _entry_key(entry)
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = entry
    for entry in overlay:
        key = _entry_key(entry)
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = entry
    return [by_key[k] for k in order]


def _remember(entries: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    global _loaded
    _loaded = (time.monotonic(), entries, source)
    return entries


def current_catalog_source() -> str:
    if _loaded is not None:
        return _loaded[2]
    return ""


def fetch_remote_catalog(url: str | None = None) -> list[dict[str, Any]] | None:
    target = (url or catalog_url()).strip()
    if not target:
        return None
    try:
        r = requests.get(target, headers=_UA, timeout=_FETCH_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.info("Addon catalog fetch failed: %s", exc)
        return None
    if r.status_code != 200 or not (r.text or "").strip():
        log.info("Addon catalog HTTP %s from %s", r.status_code, target)
        return None
    entries = parse_catalog_text(r.text)
    if catalog_entry_count(entries) == 0:
        return None
    return entries


def refresh_catalog(*, force: bool = False) -> list[dict[str, Any]]:
    """Load the best available catalog (remote → appdata → bundled).

    A successful remote fetch **replaces** the in-memory list (remote is the
    canonical Available catalog). On failure, keep cache then bundled so offline
    still works.
    """
    global _loaded
    if _loaded is not None and not force:
        age = time.monotonic() - _loaded[0]
        if age < CATALOG_TTL_SEC and catalog_entry_count(_loaded[1]) > 0:
            return _loaded[1]

    remote = fetch_remote_catalog()
    if remote is not None:
        try:
            write_catalog_file(catalog_cache_path(), remote)
        except OSError as exc:
            log.debug("Could not cache addon catalog: %s", exc)
        return _remember(remote, "remote")

    cached = load_catalog_file(catalog_cache_path())
    if catalog_entry_count(cached) > 0:
        return _remember(cached, "cache")

    bundled = load_bundled_catalog()
    if catalog_entry_count(bundled) > 0:
        return _remember(bundled, "bundled")

    return _remember(empty_catalog(), "empty")


def load_catalog() -> list[dict[str, Any]]:
    """Return the current catalog without requiring a network round-trip.

    Prefers an in-memory snapshot from a prior ``refresh_catalog``, then the
    appdata cache, then the bundled file.
    """
    if _loaded is not None and catalog_entry_count(_loaded[1]) > 0:
        return _loaded[1]
    cached = load_catalog_file(catalog_cache_path())
    if catalog_entry_count(cached) > 0:
        return _remember(cached, "cache")
    bundled = load_bundled_catalog()
    if catalog_entry_count(bundled) > 0:
        return _remember(bundled, "bundled")
    return empty_catalog()


def clear_catalog_cache() -> None:
    global _loaded
    _loaded = None
