"""Available-addon catalog (``addons.json``) with remote refresh.

The browseable Available list is authored in ``ichalaunch/data/addons.json``.
That file is published on GitHub; launchers prefer a fresh remote copy, then an
appdata cache, then the bundled copy shipped with the build.

Latest-release download counts are written onto that published file by the
hourly tokened catalog job (``tools/enrich_catalog_downloads.py``). Clients
read those fields from the same list GET — they do not call GitHub per addon.
Stamped fields persist in the appdata catalog cache so offline / 15-min TTL
keeps working.

Update tip SHAs still live in ``addon_tips.json`` (see ``tip_index``). Both are
refreshed on the same periodic update-check cadence.
"""

from __future__ import annotations

import json
import re
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
        _apply_published_release_downloads(remote)
        try:
            write_catalog_file(catalog_cache_path(), remote)
        except OSError as exc:
            log.debug("Could not cache addon catalog: %s", exc)
        return _remember(remote, "remote")

    cached = load_catalog_file(catalog_cache_path())
    if catalog_entry_count(cached) > 0:
        _apply_published_release_downloads(cached)
        try:
            write_catalog_file(catalog_cache_path(), cached)
        except OSError as exc:
            log.debug("Could not rewrite addon catalog cache: %s", exc)
        return _remember(cached, "cache")

    bundled = load_bundled_catalog()
    if catalog_entry_count(bundled) > 0:
        _apply_published_release_downloads(bundled)
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
        _apply_published_release_downloads(cached)
        return _remember(cached, "cache")
    bundled = load_bundled_catalog()
    if catalog_entry_count(bundled) > 0:
        _apply_published_release_downloads(bundled)
        return _remember(bundled, "bundled")
    return empty_catalog()


def _apply_published_release_downloads(entries: list[dict[str, Any]]) -> None:
    """Keep master-list download fields; never fan out to GitHub."""
    try:
        from ichalaunch.addons.release_downloads import apply_published_download_stamps

        apply_published_download_stamps(entries)
    except Exception as exc:  # noqa: BLE001
        log.debug("Addon release-download stamps skipped: %s", exc)


def clear_catalog_cache() -> None:
    global _loaded
    _loaded = None


# ---------------------------------------------------------------------------
# Raven / Turtle-approved mark (addon-row raven icon + Ravencraft filter)
# ---------------------------------------------------------------------------
# Catalog boolean (preferred). Aliases kept for older rows / tags.
TURTLE_CUSTOM_FLAG = "turtle_custom"
TURTLE_CUSTOM_FLAGS = frozenset(
    {"turtle_custom", "turtle_wow_custom", "custom_turtle"}
)
RAVENCRAFT_CATEGORY = "Ravencraft"
ALL_CATEGORIES_LABEL = "All categories"

# Name/folder: Turtle WoW, TWoW, word-boundary TW, TW-prefixed compounds, "Turtle…".
# Avoid bare "tw" inside words (e.g. Between / Network).
_TURTLE_CUSTOM_NAME_RE = re.compile(
    r"(?:"
    r"Turtle\s*WoW|"
    r"TurtleWoW|"
    r"TWoW|"
    r"\(TW\)|"
    r"\[TW\]|"
    r"(?<![A-Za-z0-9])TW(?![A-Za-z])|"
    r"(?:^|[\-_/\s])TW(?=[A-Z0-9_\-]|$)|"
    r"Turtle"
    r")",
    re.IGNORECASE,
)
# Strong custom phrases (original badge heuristic).
_TURTLE_CUSTOM_DESC_RE = re.compile(
    r"(?:"
    r"custom[\-\s]?made for turtle|"
    r"custom for turtle|"
    r"built for Turtle\s*WoW|"
    r"built for TurtleWoW|"
    r"Made for TWoW|"
    r"Made for Turtle\s*WoW|"
    r"Made for TurtleWoW"
    r")",
    re.IGNORECASE,
)
# Explicit Turtle WoW / TWoW mentions (name, description, notes, tags).
_TURTLE_WOW_MENTION_RE = re.compile(
    r"(?:turtle[\s\-]*wow|\btwow\b|\bt[\s\-]+wow\b)",
    re.IGNORECASE,
)
_MENTION_TEXT_FIELDS = ("name", "folder", "description", "notes")


def catalog_has_turtle_custom_flag(entry: dict[str, Any] | None) -> bool:
    """True when the catalog boolean or tag already marks this row."""
    if not isinstance(entry, dict):
        return False
    for key in TURTLE_CUSTOM_FLAGS:
        if entry.get(key) is True:
            return True
    tags = entry.get("tags")
    if isinstance(tags, (list, tuple, set)):
        for tag in tags:
            if str(tag).strip().lower() in TURTLE_CUSTOM_FLAGS:
                return True
    return False


def mentions_turtle_wow(entry: dict[str, Any] | None) -> bool:
    """True when name, folder, description, notes, or tags mention Turtle WoW / TWoW."""
    if not isinstance(entry, dict):
        return False
    for field in _MENTION_TEXT_FIELDS:
        text = str(entry.get(field) or "")
        if text and _TURTLE_WOW_MENTION_RE.search(text):
            return True
    tags = entry.get("tags")
    if isinstance(tags, (list, tuple, set)):
        for tag in tags:
            if tag is None:
                continue
            if _TURTLE_WOW_MENTION_RE.search(str(tag)):
                return True
    return False


def is_turtle_wow_custom_addon(entry: dict[str, Any] | None) -> bool:
    """True when the raven icon should show (flag, name heuristic, or Turtle WoW mention)."""
    if not isinstance(entry, dict):
        return False
    if catalog_has_turtle_custom_flag(entry):
        return True
    for field in ("name", "folder"):
        text = str(entry.get(field) or "").strip()
        if text and _TURTLE_CUSTOM_NAME_RE.search(text):
            return True
    desc = str(entry.get("description") or "")
    if desc and _TURTLE_CUSTOM_DESC_RE.search(desc):
        return True
    return mentions_turtle_wow(entry)


def apply_turtle_custom_flags(
    dest: dict[str, Any] | None,
    source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Copy catalog raven-mark booleans onto *dest* (installed row construction)."""
    if not isinstance(dest, dict) or not isinstance(source, dict):
        return dest
    for key in TURTLE_CUSTOM_FLAGS:
        if source.get(key) is True:
            dest[key] = True
    return dest


def _insert_turtle_custom_flag(entry: dict[str, Any]) -> None:
    """Set ``turtle_custom: true`` after ``source`` when that key exists."""
    if entry.get(TURTLE_CUSTOM_FLAG) is True:
        return
    if "source" not in entry:
        entry[TURTLE_CUSTOM_FLAG] = True
        return
    rebuilt: dict[str, Any] = {}
    inserted = False
    for key, value in entry.items():
        rebuilt[key] = value
        if key == "source" and TURTLE_CUSTOM_FLAG not in rebuilt:
            rebuilt[TURTLE_CUSTOM_FLAG] = True
            inserted = True
    if not inserted:
        rebuilt[TURTLE_CUSTOM_FLAG] = True
    entry.clear()
    entry.update(rebuilt)


def annotate_turtle_custom_flags(entries: list[dict[str, Any]] | None) -> int:
    """Set ``turtle_custom`` on entries that mention Turtle WoW / TWoW.

    Existing True flags / alias tags are left in place. Returns how many rows
    were newly marked.
    """
    if not entries:
        return 0
    newly = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if catalog_has_turtle_custom_flag(entry):
            continue
        if mentions_turtle_wow(entry):
            _insert_turtle_custom_flag(entry)
            newly += 1
    return newly


def is_ravencraft_category(cat_filter: str | None) -> bool:
    text = str(cat_filter or "").strip().lower().replace(" ", "")
    return text == "ravencraft"


def entry_matches_category(
    entry: dict[str, Any] | None,
    cat_filter: str | None,
    *extras: dict[str, Any] | None,
) -> bool:
    """Match a catalog/installed row against the category dropdown.

    ``Ravencraft`` is a virtual filter for raven-marked addons, not a catalog
    ``category`` value.
    """
    if not cat_filter or cat_filter == ALL_CATEGORIES_LABEL:
        return True
    if is_ravencraft_category(cat_filter):
        if is_turtle_wow_custom_addon(entry):
            return True
        for extra in extras:
            if is_turtle_wow_custom_addon(extra):
                return True
        return False
    cat = str((entry or {}).get("category") or "General")
    return cat == cat_filter
