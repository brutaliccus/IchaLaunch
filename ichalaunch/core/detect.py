"""Scan game folder for installed addons and client mods."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ichalaunch.addons.github import load_catalog, parse_github_url
from ichalaunch.config.settings import settings
from ichalaunch.game.launcher import detect_game, resolve_addons_dir
from ichalaunch.mods.installer import detect_actual_state, load_mod_catalog


BLIZZARD_PREFIXES = ("Blizzard_", "Turtle_")


def scan_installed_addon_folders(game_path: Path | None = None) -> list[str]:
    """List TOC addon folders under the configured AddOns path.

    When ``game_path`` is passed and settings have no ``addons_path`` override,
    scan ``{game_path}/Interface/AddOns`` (tests / one-offs). Otherwise use
    ``resolve_addons_dir()``.
    """
    if game_path is not None and not settings.addons_path.strip():
        addons_dir = Path(game_path) / "Interface" / "AddOns"
    else:
        addons_dir = resolve_addons_dir(create=False)
    if not addons_dir or not addons_dir.is_dir():
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
    # git@github.com:owner/repo(.git)
    m = re.match(r"git@github\.com:([^/]+)/([^/#?\s]+?)(?:\.git)?/?$", text, re.I)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    # ssh://git@github.com/owner/repo(.git)
    m = re.match(
        r"ssh://git@github\.com/([^/]+)/([^/#?\s]+?)(?:\.git)?/?$",
        text,
        re.I,
    )
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
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


def _git_config_path(addon_folder: Path) -> Path | None:
    """Resolve ``.git/config`` for a folder (handles ``.git`` file gitdir redirects)."""
    git_entry = addon_folder / ".git"
    try:
        if git_entry.is_file():
            text = git_entry.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("gitdir:"):
                    gitdir = stripped.split(":", 1)[1].strip()
                    if not gitdir:
                        return None
                    base = Path(gitdir)
                    if not base.is_absolute():
                        base = (addon_folder / base).resolve()
                    cfg = base / "config"
                    return cfg if cfg.is_file() else None
            return None
        if git_entry.is_dir():
            cfg = git_entry / "config"
            return cfg if cfg.is_file() else None
    except OSError:
        return None
    return None


def read_git_origin_url(addon_folder: str | Path) -> str | None:
    """Return normalized origin remote URL from ``addon_folder/.git/config``, if any.

    Used when an AddOns folder was cloned/copied outside the launcher zip flow and
    catalog/settings lack a repo URL. Strips a trailing ``.git``; prefers
    ``https://github.com/owner/repo`` when the remote is a GitHub URL.
    """
    folder = Path(addon_folder)
    cfg = _git_config_path(folder)
    if cfg is None:
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_origin = False
    raw_url = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            in_origin = section == 'remote "origin"' or section == "remote 'origin'"
            continue
        if not in_origin:
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip().lower() == "url":
            raw_url = value.strip().strip("\"'")
            break

    if not raw_url:
        return None
    page = _github_page_url(raw_url)
    if page:
        return page
    # Non-GitHub remotes: still normalize by dropping a trailing .git
    cleaned = raw_url.rstrip("/")
    if cleaned.lower().endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned or None


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


def resolve_catalog_entry(
    folder: str,
    idx: dict[str, dict[str, Any]] | None = None,
    *,
    include_mods: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """
    Match an Interface/AddOns folder to turtle_wiki (or mod companion) catalog metadata.

    Returns (entry, kind) where kind is "" | "exact" | "prefix".
    Prefix matches (e.g. Bongos_ActionBar → Bongos) are for URL/repo inheritance only —
    they must not overwrite the child folder's display name.
    """
    if idx is None:
        idx = catalog_index()
        if include_mods:
            for k, v in mod_companion_index().items():
                if k not in idx or (v.get("repo") and not (idx.get(k) or {}).get("repo")):
                    idx[k] = v

    needle = (folder or "").strip()
    if not needle:
        return None, ""

    lower = needle.lower()
    for key in (lower, normalize_addon_key(needle)):
        hit = idx.get(key)
        if hit:
            return hit, "exact"

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
            return entry, "exact"
        if len(base_l) >= 3 and (lower.startswith(base_l + "_") or lower.startswith(base_l + "-")):
            if len(base_l) > best_len:
                best = entry
                best_len = len(base_l)
    if best:
        return best, "prefix"
    return None, ""


def match_catalog_entry(
    folder: str,
    idx: dict[str, dict[str, Any]] | None = None,
    *,
    include_mods: bool = True,
) -> dict[str, Any] | None:
    """Match folder to catalog; see resolve_catalog_entry for exact vs prefix."""
    entry, _kind = resolve_catalog_entry(folder, idx, include_mods=include_mods)
    return entry


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
    *,
    match_kind: str = "exact",
) -> dict[str, Any]:
    """
    Merge disk/settings metadata with catalog entry. Never wipe good existing fields;
    fill gaps from catalog (url/repo, description, name, category).

    Prefix matches inherit url/repo (and description/category when empty) but keep
    the real disk folder name — never rename Bongos_ActionBar to "Bongos".
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
    cat_name = str(cat.get("name") or "").strip()
    if match_kind == "prefix":
        # Child module of a multi-folder pack: keep disk folder (or a distinct prev name).
        if prev_name and prev_name.lower() != (cat_name or "").lower() and prev_name.lower() != folder.lower():
            # Unusual custom rename — keep it
            name = prev_name
        else:
            name = folder
    else:
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
            # Prefix children: don't copy the parent's long catalog blurb unless empty
            cat.get("description") if match_kind != "prefix" else "",
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
    # Preserve pack linkage from prior installs
    managed_by = str(prev.get("managed_by") or "").strip()
    if managed_by:
        meta["managed_by"] = managed_by
    folders = prev.get("folders")
    if isinstance(folders, list) and folders:
        meta["folders"] = [str(f) for f in folders if f]
    return meta


def _repo_group_key(meta: dict[str, Any]) -> str:
    repo = str(meta.get("repository") or "").strip().lower()
    if repo and "/" in repo:
        return f"repo:{repo}"
    url = _github_page_url(str(meta.get("url") or ""))
    if url:
        return f"url:{url.lower()}"
    return ""


def pick_pack_primary(
    members: list[str],
    merged: dict[str, Any],
    *,
    preferred: str | None = None,
) -> str:
    """Choose the root folder for a multi-module pack."""
    by_lower = {f.lower(): f for f in members}
    if preferred and preferred.lower() in by_lower:
        return by_lower[preferred.lower()]

    # Prefer catalog folder when that folder is among members
    for folder in members:
        cat, kind = resolve_catalog_entry(folder, include_mods=False)
        if not cat:
            continue
        cat_folder = (cat.get("folder") or cat.get("name") or "").strip()
        if cat_folder and cat_folder.lower() in by_lower:
            return by_lower[cat_folder.lower()]

    # Prefer folder matching repository name
    for folder in members:
        meta = merged.get(folder) or {}
        repo = str(meta.get("repository") or "")
        if "/" in repo:
            repo_name = repo.split("/", 1)[1]
            if repo_name.lower() == folder.lower():
                return folder

    return sorted(members, key=lambda f: (len(f), f.lower()))[0]


def group_multi_folder_addons(merged: dict[str, Any]) -> dict[str, Any]:
    """
    Collapse multi-module packs into one primary settings entry.

    Children get managed_by=<primary>; the primary stores folders=[...].
    Grouping sources:
      1. Shared repository/url across multiple disk folders
      2. Prefix-catalog children when the parent folder is also on disk
      3. Prior folders lists from a GitHub multi-root install
    """
    if not merged:
        return merged

    # Start from a clean linkage slate (rebuild every sync)
    for folder, meta in list(merged.items()):
        meta = dict(meta)
        meta.pop("managed_by", None)
        prev_folders = meta.get("folders")
        if isinstance(prev_folders, list):
            valid = [f for f in prev_folders if f in merged]
            if len(valid) > 1 and folder in valid:
                meta["folders"] = valid
            else:
                meta.pop("folders", None)
        else:
            meta.pop("folders", None)
        merged[folder] = meta

    # Union-find over folder names
    parent: dict[str, str] = {f: f for f in merged}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # Prefer shorter / catalog-friendly root as temporary parent; finalize later
        if (len(ra), ra.lower()) <= (len(rb), rb.lower()):
            parent[rb] = ra
        else:
            parent[ra] = rb

    # Prior install folders lists
    for folder, meta in merged.items():
        folders = meta.get("folders")
        if isinstance(folders, list) and len(folders) > 1:
            for other in folders:
                if other in merged:
                    union(folder, other)

    # Shared GitHub repo/url
    by_key: dict[str, list[str]] = {}
    for folder, meta in merged.items():
        key = _repo_group_key(meta)
        if key:
            by_key.setdefault(key, []).append(folder)
    for members in by_key.values():
        if len(members) < 2:
            continue
        head = members[0]
        for other in members[1:]:
            union(head, other)

    # Prefix children → parent folder on disk
    for folder in merged:
        cat, kind = resolve_catalog_entry(folder, include_mods=False)
        if kind != "prefix" or not cat:
            continue
        parent_name = (cat.get("folder") or cat.get("name") or "").strip()
        if not parent_name:
            continue
        parent_key = next((k for k in merged if k.lower() == parent_name.lower()), None)
        if parent_key:
            union(folder, parent_key)

    # Collect components
    components: dict[str, list[str]] = {}
    for folder in merged:
        components.setdefault(find(folder), []).append(folder)

    for members in components.values():
        if len(members) < 2:
            # Single folder — drop leftover folders list
            meta = dict(merged[members[0]])
            meta.pop("folders", None)
            meta.pop("managed_by", None)
            merged[members[0]] = meta
            continue

        primary = pick_pack_primary(members, merged)
        all_folders = sorted(set(members), key=str.lower)
        parent_meta = dict(merged[primary])
        parent_meta.pop("managed_by", None)
        parent_meta["folders"] = all_folders
        cat, kind = resolve_catalog_entry(primary, include_mods=False)
        if kind == "exact" and cat and cat.get("name"):
            parent_meta["name"] = cat["name"]
        elif not parent_meta.get("name"):
            parent_meta["name"] = primary
        # Prefer parent catalog description when present
        if kind == "exact" and cat and cat.get("description") and not str(parent_meta.get("description") or "").strip():
            parent_meta["description"] = cat["description"]
        merged[primary] = parent_meta

        for child in all_folders:
            if child == primary:
                continue
            child_meta = dict(merged[child])
            child_meta["managed_by"] = primary
            child_meta.pop("folders", None)
            child_meta["name"] = child
            if not child_meta.get("url") and parent_meta.get("url"):
                child_meta["url"] = parent_meta["url"]
            if not child_meta.get("repository") and parent_meta.get("repository"):
                child_meta["repository"] = parent_meta["repository"]
            if not child_meta.get("installed_commit") and parent_meta.get("installed_commit"):
                child_meta["installed_commit"] = parent_meta["installed_commit"]
            if not child_meta.get("branch") and parent_meta.get("branch"):
                child_meta["branch"] = parent_meta["branch"]
            merged[child] = child_meta

    return merged


def sync_installed_addons_from_disk() -> dict[str, Any]:
    """
    Detect addons on disk and merge into settings.installed_addons.
    Keeps existing GitHub tracking metadata when present; fills gaps from catalog.
    When catalog/settings lack a repo URL, reads ``.git/config`` origin if present
    (manual clones) — never overwrites zip-installed / already-tracked URLs.
    Groups multi-folder packs (shared repo / prefix modules) under one primary entry.
    """
    folders = scan_installed_addon_folders()
    addons_dir = resolve_addons_dir(create=False)
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
        cat, kind = resolve_catalog_entry(folder, idx, include_mods=False)
        meta = merge_addon_meta(folder, prev, cat, match_kind=kind or "exact")
        # Fill missing repo from local git clone metadata only — do not clobber
        # launcher zip installs or prior Open-in-Git / update tracking URLs.
        if addons_dir and not _nonempty(meta.get("url"), meta.get("repository")):
            origin = read_git_origin_url(addons_dir / folder)
            if origin:
                page = _github_page_url(origin) or origin
                meta["url"] = page
                repo = _repository_from_url(page)
                if repo:
                    meta["repository"] = repo
                if str(meta.get("source") or "").strip().lower() in ("", "detected"):
                    meta["source"] = "github"
        merged[folder] = meta

    merged = group_multi_folder_addons(merged)
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
