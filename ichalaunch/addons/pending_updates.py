"""Persist last-known pending addon updates across launcher restarts.

A catalog/tip scan writes a small JSON next to ``settings.json``. Launch
restores those rows immediately — it is not a “no updates” result. The next
15-minute (or manual) refresh replaces the file with the new compare.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ichalaunch.config.settings import settings, settings_path
from ichalaunch.core.logging_setup import log

CACHE_NAME = "addon_pending_updates.json"

_cache_path_override: Path | None = None


def pending_updates_cache_path() -> Path:
    if _cache_path_override is not None:
        return _cache_path_override
    return settings_path().parent / CACHE_NAME


@contextmanager
def isolated_pending_updates_cache(path: Path) -> Iterator[None]:
    """Point the cache file at *path* (tests)."""
    global _cache_path_override
    prev = _cache_path_override
    _cache_path_override = path
    try:
        yield
    finally:
        _cache_path_override = prev


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lookup_installed(
    folder: str,
    installed: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if folder in installed and isinstance(installed[folder], dict):
        return folder, installed[folder]
    needle = folder.lower()
    for key, meta in installed.items():
        if str(key).lower() == needle and isinstance(meta, dict):
            return str(key), meta
    return None


def _ref_matches(left: str, right: str) -> bool:
    a, b = (left or "").strip(), (right or "").strip()
    if not a or not b:
        return False
    from ichalaunch.addons.github import _commits_match

    if _commits_match(a, b):
        return True
    return a.lower().lstrip("v") == b.lower().lstrip("v")


def normalize_pending_row(
    row: dict[str, Any],
    *,
    scanned_at: str,
) -> dict[str, Any] | None:
    folder = str(row.get("folder") or row.get("name") or "").strip()
    if not folder:
        return None
    installed_ref = str(row.get("installed_ref") or row.get("local") or "").strip()
    available_ref = str(row.get("available_ref") or row.get("remote") or "").strip()
    return {
        "folder": folder,
        "repository": str(row.get("repository") or row.get("repo") or "").strip(),
        "installed_ref": installed_ref,
        "available_ref": available_ref,
        "local": str(row.get("local") or "").strip(),
        "remote": str(row.get("remote") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "branch": str(row.get("branch") or "").strip(),
        "scanned_at": str(row.get("scanned_at") or scanned_at),
    }


def replace_pending_updates_cache(
    updates: list[dict[str, Any]],
    *,
    scanned_at: str | None = None,
) -> None:
    """Replace the on-disk cache with this scan's pending rows (including empty)."""
    stamp = scanned_at or _utc_now()
    rows: list[dict[str, Any]] = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        row = normalize_pending_row(item, scanned_at=stamp)
        if row is not None:
            rows.append(row)
    payload = {"scanned_at": stamp, "updates": rows}
    path = pending_updates_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        log.debug("Could not cache pending addon updates: %s", exc)


def load_pending_updates_cache() -> dict[str, Any]:
    path = pending_updates_cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"scanned_at": "", "updates": []}
    if not isinstance(raw, dict):
        return {"scanned_at": "", "updates": []}
    updates = raw.get("updates")
    if not isinstance(updates, list):
        updates = []
    return {
        "scanned_at": str(raw.get("scanned_at") or ""),
        "updates": [u for u in updates if isinstance(u, dict)],
    }


def drop_cached_pending_folder(folder: str) -> None:
    """Remove one addon after a successful Update / Reinstall."""
    needle = str(folder or "").strip().lower()
    if not needle:
        return
    cached = load_pending_updates_cache()
    kept = [
        u
        for u in cached["updates"]
        if str(u.get("folder") or "").strip().lower() != needle
    ]
    if len(kept) == len(cached["updates"]):
        return
    replace_pending_updates_cache(kept, scanned_at=cached.get("scanned_at") or None)


def _still_pending(
    row: dict[str, Any],
    meta: dict[str, Any],
) -> bool:
    """Keep a cached row if it is still installed at the old ref or still behind."""
    current_commit = str(meta.get("installed_commit") or "").strip()
    current_ver = str(meta.get("version") or "").strip()
    current = current_commit or current_ver
    available = str(row.get("available_ref") or row.get("remote") or "").strip()
    cached_installed = str(row.get("installed_ref") or row.get("local") or "").strip()
    if current and available and _ref_matches(current, available):
        return False
    if current and cached_installed and _ref_matches(current, cached_installed):
        return True
    if current and available and not _ref_matches(current, available):
        return True
    if not current:
        return True
    return False


def restore_pending_updates(
    *,
    installed: dict[str, Any] | None = None,
    never_update: Callable[[str], bool] | None = None,
    rewrite: bool = False,
) -> list[dict[str, Any]]:
    """Load cache and drop rows that were removed or already applied.

    Launch must not treat a missing in-memory scan as “no updates.”
    """
    tracked = installed if installed is not None else settings.installed_addons
    skip = never_update
    if skip is None:
        skip = settings.is_addon_never_update
    cached = load_pending_updates_cache()
    kept: list[dict[str, Any]] = []
    for row in cached["updates"]:
        folder = str(row.get("folder") or "").strip()
        if not folder:
            continue
        if skip(folder):
            continue
        hit = _lookup_installed(folder, tracked)
        if hit is None:
            continue
        _key, meta = hit
        if not _still_pending(row, meta):
            continue
        kept.append(row)
    if rewrite:
        prev_folders = [
            str(u.get("folder") or "")
            for u in cached["updates"]
            if u.get("folder")
        ]
        kept_folders = [str(u.get("folder") or "") for u in kept if u.get("folder")]
        if prev_folders != kept_folders:
            replace_pending_updates_cache(
                kept, scanned_at=cached.get("scanned_at") or None
            )
    return kept
