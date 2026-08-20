"""Scan game folder for installed addons and client mods."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ichalaunch.addons.github import load_catalog
from ichalaunch.config.settings import settings
from ichalaunch.game.launcher import detect_game
from ichalaunch.mods.installer import detect_actual_state, load_mod_catalog


BLIZZARD_PREFIXES = ("Blizzard_", "Turtle_")


def scan_installed_addon_folders(game_path: Path | None = None) -> list[str]:
    game = game_path or detect_game()
    if not game:
        return []
    addons_dir = game / "Interface" / "AddOns"
    if not addons_dir.is_dir():
        return []
    folders = []
    for p in sorted(addons_dir.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith(BLIZZARD_PREFIXES):
            continue
        # must have a .toc somewhere at root
        if not any(p.glob("*.toc")):
            continue
        folders.append(p.name)
    return folders


def catalog_index() -> dict[str, dict[str, Any]]:
    """Map folder/name lower -> catalog entry."""
    idx: dict[str, dict[str, Any]] = {}
    for e in load_catalog():
        folder = (e.get("folder") or e.get("name") or "").strip()
        if folder:
            idx[folder.lower()] = e
        name = (e.get("name") or "").strip()
        if name:
            idx[name.lower()] = e
    return idx


def sync_installed_addons_from_disk() -> dict[str, Any]:
    """
    Detect addons on disk and merge into settings.installed_addons.
    Keeps existing GitHub tracking metadata when present.
    """
    folders = scan_installed_addon_folders()
    idx = catalog_index()
    current = settings.installed_addons
    merged: dict[str, Any] = {}

    for folder in folders:
        prev = current.get(folder) or {}
        cat = idx.get(folder.lower())
        meta = {
            "source": prev.get("source") or ("turtle_wiki" if cat else "detected"),
            "detected": True,
            "name": (cat or {}).get("name") or folder,
            "category": (cat or {}).get("category") or "Installed",
            "description": (cat or {}).get("description") or "",
            "repository": prev.get("repository") or "",
            "branch": prev.get("branch") or "",
            "installed_commit": prev.get("installed_commit") or "",
            "url": prev.get("url")
            or (cat or {}).get("repo")
            or "",
        }
        # Preserve install/update stamps across disk resync
        for key in ("installed_at", "updated_at", "commit_date"):
            if prev.get(key):
                meta[key] = prev[key]
        # If catalog has repo and we don't track commits yet, store url for updates later
        if cat and cat.get("repo") and not meta.get("url"):
            meta["url"] = cat["repo"]
        if cat and cat.get("repo") and not meta.get("repository"):
            # owner/repo
            parts = cat["repo"].rstrip("/").split("/")
            if len(parts) >= 2:
                meta["repository"] = f"{parts[-2]}/{parts[-1]}"
        merged[folder] = meta

    settings.set("installed_addons", merged)
    return merged


def sync_desired_mods_from_disk() -> dict[str, bool]:
    """Set desired_mods checkboxes to match what is actually installed."""
    game = detect_game()
    if not game:
        return settings.desired_mods
    actual = detect_actual_state(game)
    # Only sync keys we know about in catalog
    known = {m["id"] for m in load_mod_catalog()}
    desired = settings.desired_mods
    for mod_id, present in actual.items():
        if mod_id in known:
            desired[mod_id] = bool(present)
    settings.set("desired_mods", desired)
    if "vanillafixes" in actual:
        settings.set("vanillafixes_enabled", bool(actual.get("vanillafixes")))
    return desired


def full_resync() -> dict[str, Any]:
    addons = sync_installed_addons_from_disk()
    mods = sync_desired_mods_from_disk()
    return {"addons": addons, "mods": mods}
