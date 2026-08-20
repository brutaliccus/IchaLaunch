"""Scan game folder for installed addons and client mods."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ichalaunch.addons.github import load_catalog, parse_github_url
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


def normalize_addon_key(value: str) -> str:
    """Case-insensitive key with separators stripped for fuzzy catalog match."""
    return re.sub(r"[\s_\-]+", "", (value or "").strip().lower())


def _github_page_url(raw: str) -> str:
    """Normalize a GitHub zip/archive/API URL to https://github.com/owner/repo."""
    text = (raw or "").strip()
    if not text:
        return ""
    # SuperAPI-style: .../SuperAPI/archive/refs/heads/master.zip
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?(?:/|$)",
        text,
        re.I,
    )
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    # raw.githubusercontent.com/owner/repo/...
    m = re.match(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", text, re.I)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    parsed = parse_github_url(text)
    if parsed:
        return f"https://github.com/{parsed[0]}/{parsed[1]}"
    return ""


def _index_put(idx: dict[str, dict[str, Any]], key: str, entry: dict[str, Any]) -> None:
    key = (key or "").strip().lower()
    if not key:
        return
    # Prefer entries that have a repo when colliding
    prev = idx.get(key)
    if prev and prev.get("repo") and not entry.get("repo"):
        return
    idx[key] = entry
    norm = normalize_addon_key(key)
    if norm and norm != key:
        prev_n = idx.get(norm)
        if not (prev_n and prev_n.get("repo") and not entry.get("repo")):
            idx[norm] = entry


def catalog_index() -> dict[str, dict[str, Any]]:
    """Map folder/name (lower + normalized) -> catalog entry."""
    idx: dict[str, dict[str, Any]] = {}
    for e in load_catalog():
        folder = (e.get("folder") or e.get("name") or "").strip()
        name = (e.get("name") or "").strip()
        if folder:
            _index_put(idx, folder, e)
        if name:
            _index_put(idx, name, e)
            _index_put(idx, name.replace(" ", ""), e)
    return idx


def mod_companion_index() -> dict[str, dict[str, Any]]:
    """Map companion addon folder lower -> synthetic catalog-like meta from mods.json."""
    idx: dict[str, dict[str, Any]] = {}
    for mod in load_mod_catalog():
        sources: list[tuple[str, str]] = []
        addon_src = mod.get("addon_source") or {}
        folder = (addon_src.get("folder") or "").strip()
        if folder:
            sources.append((folder, addon_src.get("url") or ""))
        match_folder = (mod.get("addon_folder_match") or "").strip()
        if match_folder:
            # UnitXP ships addon inside the DLL zip — no separate reinstall URL usually
            sources.append((match_folder, ""))
        for folder_name, raw_url in sources:
            repo = _github_page_url(raw_url) if raw_url else ""
            if not repo:
                addon_repo = (addon_src.get("repo") or "").strip()
                if addon_repo and "/" in addon_repo:
                    repo = f"https://github.com/{addon_repo}"
            entry = {
                "name": mod.get("name") or folder_name,
                "folder": folder_name,
                "description": mod.get("description") or "",
                "category": mod.get("category") or "Client",
                "repo": repo,
                "source": "client_mod",
            }
            _index_put(idx, folder_name, entry)
    return idx


def match_catalog_entry(
    folder: str,
    idx: dict[str, dict[str, Any]] | None = None,
    *,
    include_mods: bool = True,
) -> dict[str, Any] | None:
    """
    Match an Interface/AddOns folder to turtle_wiki (or mod companion) catalog metadata.

    Order: exact folder/name → normalized key → longest catalog folder prefix
    (e.g. Bongos_ActionBar → Bongos).
    """
    if idx is None:
        idx = catalog_index()
        if include_mods:
            for k, v in mod_companion_index().items():
                if k not in idx or (v.get("repo") and not (idx.get(k) or {}).get("repo")):
                    idx[k] = v

    needle = (folder or "").strip()
    if not needle:
        return None

    lower = needle.lower()
    for key in (lower, normalize_addon_key(needle)):
        hit = idx.get(key)
        if hit:
            return hit

    # Nested packages from multi-addon repos: Folder_Child → Folder
    # Only use catalog folder/name strings (not normalized index keys).
    candidates: list[tuple[str, dict[str, Any]]] = []
    seen: set[int] = set()
    for entry in idx.values():
        eid = id(entry)
        if eid in seen:
            continue
        seen.add(eid)
        for base in ((entry.get("folder") or "").strip(), (entry.get("name") or "").strip()):
            if base:
                candidates.append((base, entry))

    best: dict[str, Any] | None = None
    best_len = 0
    for base, entry in candidates:
        base_l = base.lower()
        if lower == base_l:
            return entry
        if len(base_l) >= 3 and (lower.startswith(base_l + "_") or lower.startswith(base_l + "-")):
            if len(base_l) > best_len:
                best = entry
                best_len = len(base_l)
    return best


def _nonempty(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        text = str(v).strip()
        if text:
            return text
    return ""


_PLACEHOLDER_CATEGORIES = frozenset({"", "installed", "detected", "general"})
_PLACEHOLDER_DESCRIPTIONS = frozenset(
    {
        "",
        "detected in interface/addons",
    }
)


def _prefer_meta_text(prev_val: Any, cat_val: Any, *fallbacks: Any, placeholders: frozenset[str] | None = None) -> str:
    """Prefer previous non-placeholder text; otherwise catalog; otherwise fallbacks."""
    placeholders = placeholders or frozenset()
    prev_text = str(prev_val or "").strip()
    if prev_text and prev_text.lower() not in placeholders:
        return prev_text
    cat_text = str(cat_val or "").strip()
    if cat_text and cat_text.lower() not in placeholders:
        return cat_text
    return _nonempty(*fallbacks)


def _repository_from_url(url: str) -> str:
    parsed = parse_github_url(url)
    if not parsed:
        return ""
    return f"{parsed[0]}/{parsed[1]}"


def merge_addon_meta(
    folder: str,
    prev: dict[str, Any] | None = None,
    cat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Merge disk/settings metadata with catalog entry. Never wipe good existing fields;
    fill gaps from catalog (url/repo, description, name, category).
    """
    prev = dict(prev or {})
    cat = dict(cat or {})
    cat_repo = _nonempty(cat.get("repo"), cat.get("url"))
    url = _nonempty(prev.get("url"), cat_repo)
    if url:
        url = _github_page_url(url) or url

    repository = _nonempty(prev.get("repository"), _repository_from_url(url))
    has_catalog = bool(cat)
    source = _nonempty(
        prev.get("source"),
        cat.get("source"),
        "turtle_wiki" if has_catalog and cat_repo else ("detected" if not url else "github"),
    )

    prev_name = str(prev.get("name") or "").strip()
    # If stored name is just the folder, allow catalog display name to win
    name = _prefer_meta_text(
        prev_name if prev_name.lower() != folder.lower() else "",
        cat.get("name"),
        folder,
    )

    meta: dict[str, Any] = {
        "source": source,
        "detected": True,
        "name": name,
        "category": _prefer_meta_text(
            prev.get("category"),
            cat.get("category"),
            "Installed",
            placeholders=_PLACEHOLDER_CATEGORIES,
        ),
        "description": _prefer_meta_text(
            prev.get("description"),
            cat.get("description"),
            placeholders=_PLACEHOLDER_DESCRIPTIONS,
        ),
        "repository": repository,
        "branch": prev.get("branch") or "",
        "installed_commit": prev.get("installed_commit") or "",
        "url": url,
    }
    for key in ("installed_at", "updated_at", "commit_date"):
        if prev.get(key):
            meta[key] = prev[key]
    return meta


def sync_installed_addons_from_disk() -> dict[str, Any]:
    """
    Detect addons on disk and merge into settings.installed_addons.
    Keeps existing GitHub tracking metadata when present; fills gaps from catalog.
    """
    folders = scan_installed_addon_folders()
    idx = catalog_index()
    # Overlay mod companion folders (SuperAPI, nampowersettings, …)
    for k, v in mod_companion_index().items():
        existing = idx.get(k)
        if not existing or (v.get("repo") and not existing.get("repo")):
            idx[k] = v

    current = settings.installed_addons
    # Case-insensitive lookup into previous settings
    current_by_lower = {k.lower(): (k, v) for k, v in current.items()}
    merged: dict[str, Any] = {}

    for folder in folders:
        prev_pair = current_by_lower.get(folder.lower())
        prev = dict(prev_pair[1]) if prev_pair else {}
        cat = match_catalog_entry(folder, idx, include_mods=False)
        merged[folder] = merge_addon_meta(folder, prev, cat)

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
