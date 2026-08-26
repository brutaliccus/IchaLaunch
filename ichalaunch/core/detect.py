"""Scan game folder for installed addons and client mods."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ichalaunch.addons.github import addon_ignores_updates, catalog_locks_updates, load_catalog, parse_github_url
from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import (
    AddonTocMismatch,
    describe_toc_mismatch,
    folder_toc_files,
    listed_basenames,
    matching_toc_path,
    robust_rmtree,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.game.launcher import (
    detect_game,
    resolve_addons_dir,
    sync_vanillafixes_enabled_from_desired,
)
from ichalaunch.mods.installer import (
    detect_actual_state,
    enforce_vanilla_helpers_for_hd_desired,
    load_mod_catalog,
    reconcile_exclusive_desired_mods,
    reconcile_vanillafixes_dxvk,
)


BLIZZARD_PREFIXES = ("Blizzard_", "Turtle_")


def _classify_toc_dir(
    addons_dir: Path | None, *, skip_blizzard: bool
) -> tuple[list[str], list[AddonTocMismatch]]:
    """Return ``(valid_folders, mismatches)``.

    Valid folders have ``{folder}/{folder}.toc``. The ``.toc`` filename is
    the source of truth — mismatched folders are skipped and the UI can
    offer to rename the folder to that stem.
    """
    if not addons_dir or not addons_dir.is_dir():
        return [], []
    valid: list[str] = []
    mismatched: list[AddonTocMismatch] = []
    try:
        children = sorted(addons_dir.iterdir())
    except OSError:
        return [], []
    for p in children:
        if not p.is_dir():
            continue
        if skip_blizzard and p.name.startswith(BLIZZARD_PREFIXES):
            continue
        if matching_toc_path(p) is not None:
            valid.append(p.name)
            continue
        info = describe_toc_mismatch(p)
        if info is None:
            tocs = folder_toc_files(p)
            if not tocs:
                continue
            # Multiple unrelated .toc files — skip with no rename target.
            info = AddonTocMismatch(
                folder=p,
                current_name=p.name,
                toc_stem="",
                toc_name=tocs[0].name,
            )
        mismatched.append(info)
        # Only suggest a rename when we have a clear primary stem (can_rename).
        # Do not fall back to tocs[0] — that wrongly suggested pfQuest-tbc for
        # multi-TOC leftovers whose primary is pfQuest.
        if info.can_rename:
            log.warning(
                "Skipping addon folder %s — folder name must match the .toc "
                "(rename folder to %s)",
                p.name,
                info.toc_stem,
            )
        else:
            log.warning(
                "Skipping addon folder %s — folder name must match the .toc",
                p.name,
            )
    return valid, mismatched


def _scan_toc_dir(addons_dir: Path | None, *, skip_blizzard: bool) -> list[str]:
    return _classify_toc_dir(addons_dir, skip_blizzard=skip_blizzard)[0]


_TOC_VERSION_RE = re.compile(r"^##\s*Version\s*:\s*(.+?)\s*$", re.I | re.M)
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.I)


def read_addon_toc_version(addon_folder: str | Path) -> str:
    """Return ``## Version`` from the addon's TOC, or ``""`` if missing."""
    folder = Path(addon_folder)
    if not folder.is_dir():
        return ""
    tocs = sorted(folder.glob("*.toc"))
    preferred = folder / f"{folder.name}.toc"
    if preferred.is_file():
        tocs = [preferred, *[t for t in tocs if t.resolve() != preferred.resolve()]]
    for toc in tocs:
        try:
            text = toc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _TOC_VERSION_RE.search(text)
        if not match:
            continue
        ver = match.group(1).strip().strip("\"'")
        ver = re.sub(r"\|c[0-9a-fA-F]{8}", "", ver)
        ver = ver.replace("|r", "").strip()
        if ver:
            return ver
    return ""


def read_local_git_head_sha(addon_folder: str | Path) -> str | None:
    """Return HEAD commit SHA for a real git clone, or None for stub/missing ``.git``."""
    folder = Path(addon_folder)
    git_entry = folder / ".git"
    git_dir: Path | None = None
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
                        base = (folder / base).resolve()
                    git_dir = base
                    break
        elif git_entry.is_dir():
            git_dir = git_entry
    except OSError:
        return None
    if git_dir is None or not git_dir.is_dir():
        return None
    head_path = git_dir / "HEAD"
    try:
        if not head_path.is_file():
            return None
        raw = head_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    if raw.lower().startswith("ref:"):
        ref = raw.split(":", 1)[1].strip()
        if not ref:
            return None
        ref_file = git_dir / ref
        packed = git_dir / "packed-refs"
        sha = ""
        try:
            if ref_file.is_file():
                sha = ref_file.read_text(encoding="utf-8", errors="replace").strip()
            elif packed.is_file():
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith("^"):
                        continue
                    parts = stripped.split()
                    if len(parts) >= 2 and parts[1] == ref:
                        sha = parts[0]
                        break
        except OSError:
            return None
    else:
        sha = raw.split()[0] if raw.split() else ""
    sha = sha.strip()
    if _GIT_SHA_RE.fullmatch(sha):
        return sha
    return None


def _addon_scan_dirs(
    game_path: Path | None = None,
) -> tuple[Path | None, Path | None]:
    if game_path is not None and not settings.addons_path.strip():
        addons_dir = Path(game_path) / "Interface" / "AddOns"
        return addons_dir, addons_dir.parent / "AddOnsUnloaded"
    from ichalaunch.addons.loadstate import resolve_unloaded_addons_dir

    return resolve_addons_dir(create=False), resolve_unloaded_addons_dir(create=False)


def scan_installed_addon_folders(game_path: Path | None = None) -> list[str]:
    """List TOC addon folders under AddOns **and** AddOnsUnloaded.

    Unloaded packs stay on the Addons tab; vanilla only scans ``Interface/AddOns``.
    When ``game_path`` is passed and settings have no ``addons_path`` override,
    scan ``{game_path}/Interface/AddOns`` (tests / one-offs). Otherwise use
    ``resolve_addons_dir()``.

    Only folders whose ``.toc`` filename matches the folder name are returned.
    """
    addons_dir, unloaded_dir = _addon_scan_dirs(game_path)
    loaded = _scan_toc_dir(addons_dir, skip_blizzard=True)
    seen = {n.lower() for n in loaded}
    for name in _scan_toc_dir(unloaded_dir, skip_blizzard=False):
        if name.lower() not in seen:
            loaded.append(name)
            seen.add(name.lower())
    return loaded


def scan_mismatched_toc_addon_folders(
    game_path: Path | None = None,
) -> list[AddonTocMismatch]:
    """Folders that have a ``.toc`` whose name does not match the folder."""
    addons_dir, unloaded_dir = _addon_scan_dirs(game_path)
    out: list[AddonTocMismatch] = []
    seen: set[str] = set()
    for item in (
        _classify_toc_dir(addons_dir, skip_blizzard=True)[1]
        + _classify_toc_dir(unloaded_dir, skip_blizzard=False)[1]
    ):
        key = item.current_name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


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

    When present, this wins over catalog/preloaded repo URLs for Open in Git,
    update checks, and reinstall. Zip installs without ``.git`` return ``None``
    so callers fall back to catalog. Strips a trailing ``.git``; prefers
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


def _origin_urls_match(left: str, right: str) -> bool:
    """True when two remotes are the same GitHub owner/repo (or identical URL)."""
    a = (_github_page_url(left) or left).rstrip("/").lower()
    b = (_github_page_url(right) or right).rstrip("/").lower()
    if a.endswith(".git"):
        a = a[:-4]
    if b.endswith(".git"):
        b = b[:-4]
    return bool(a) and a == b


def _canonical_git_remote_url(origin_url: str) -> str:
    """URL stored in ``remote.origin.url`` (GitHub pages get a trailing ``.git``)."""
    raw = (origin_url or "").strip()
    if not raw:
        return ""
    page = _github_page_url(raw)
    if page:
        return page + ".git"
    cleaned = raw.rstrip("/")
    return cleaned


def _remove_addon_git(addon_folder: Path) -> None:
    """Delete only ``addon_folder/.git`` (file or directory), never the addon root."""
    git_entry = addon_folder / ".git"
    if not git_entry.exists() and not git_entry.is_symlink():
        return
    # Never treat the addon folder itself as the removal target.
    try:
        if git_entry.resolve() == addon_folder.resolve():
            return
    except OSError:
        return
    if git_entry.is_file() or git_entry.is_symlink():
        git_entry.unlink(missing_ok=True)
        return
    robust_rmtree(git_entry)


def _git_run(git: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "capture_output": True,
        "timeout": 30,
        "check": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([git, *args], **kwargs)


def _git_init_with_origin(addon_folder: Path, remote_url: str) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    try:
        init = _git_run(git, ["init"], cwd=addon_folder)
        if init.returncode != 0:
            return False
        got = _git_run(git, ["remote", "get-url", "origin"], cwd=addon_folder)
        if got.returncode == 0:
            set_url = _git_run(git, ["remote", "set-url", "origin", remote_url], cwd=addon_folder)
            return set_url.returncode == 0
        added = _git_run(git, ["remote", "add", "origin", remote_url], cwd=addon_folder)
        return added.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _write_minimal_git_config(addon_folder: Path, remote_url: str) -> None:
    git_dir = addon_folder / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = false\n"
        "\tbare = false\n"
        '[remote "origin"]\n'
        f"\turl = {remote_url}\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )


def write_git_origin(addon_folder: str | Path, origin_url: str) -> None:
    """Create or replace ``addon_folder/.git`` so ``read_git_origin_url`` returns *origin_url*.

    Zip/catalog installs do not clone the repo; this writes a real ``git init`` +
    ``remote add origin`` when ``git`` is on PATH, otherwise a minimal
    ``.git/config`` that the existing origin parser already understands.

    If a previous origin points at a *different* repo, ``.git`` is removed and
    rewritten (no prompt). Addon files are not deleted. Same-repo reinstalls
    keep an existing origin that already matches. Only ``addon_folder/.git`` is
    removed — never the addon tree.
    """
    folder = Path(addon_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Addon folder not found: {folder}")
    remote = _canonical_git_remote_url(origin_url)
    if not remote:
        raise ValueError(f"Not a usable git origin URL: {origin_url!r}")

    existing = read_git_origin_url(folder)
    if existing and _origin_urls_match(existing, remote):
        return

    if (folder / ".git").exists() or (folder / ".git").is_symlink():
        _remove_addon_git(folder)

    if _git_init_with_origin(folder, remote):
        return
    _write_minimal_git_config(folder, remote)


def overlay_git_origin(
    folder: str,
    meta: dict[str, Any] | None = None,
    *,
    addons_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a copy of *meta* with ``.git`` origin overriding catalog/settings URLs.

    No-op when the folder has no parseable origin (zip installs keep catalog URLs).
    """
    out = dict(meta or {})
    root = addons_dir if addons_dir is not None else resolve_addons_dir(create=False)
    if not root or not folder:
        return out
    from ichalaunch.addons.loadstate import UNLOADED_SIBLING, addon_disk_path

    disk = addon_disk_path(
        folder,
        addons_dir=root,
        unloaded_dir=root.parent / UNLOADED_SIBLING,
    )
    origin = read_git_origin_url(disk) if disk is not None else read_git_origin_url(Path(root) / folder)
    if not origin:
        return out
    page = _github_page_url(origin) or origin
    out["url"] = page
    repo = _repository_from_url(page)
    if repo:
        out["repository"] = repo
    src = str(out.get("source") or "").strip().lower()
    if src in ("", "detected", "turtle_wiki"):
        out["source"] = "github"
    return out


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
    git_origin: str | None = None,
) -> dict[str, Any]:
    """
    Merge disk/settings metadata with catalog entry. Never wipe good existing fields;
    fill gaps from catalog (url/repo, description, name, category).

    Repo URL precedence when resolving Open in Git / updates / reinstall:
    1. ``git_origin`` from local ``.git/config`` remote.origin.url (if parseable)
    2. Previous settings / installed meta
    3. Catalog / preloaded ``repo`` / ``url``

    Zip installs without ``.git`` leave ``git_origin`` empty and keep catalog URLs.

    Prefix matches inherit url/repo (and description/category when empty) but keep
    the real disk folder name — never rename Bongos_ActionBar to "Bongos".
    """
    prev = dict(prev or {})
    cat = dict(cat or {})
    cat_repo = _nonempty(cat.get("repo"), cat.get("url"))
    origin = _nonempty(git_origin)
    if origin:
        origin = _github_page_url(origin) or origin
    # Local clone origin beats catalog/preloaded (and prior catalog-filled settings).
    url = _nonempty(origin, prev.get("url"), cat_repo)
    if url:
        url = _github_page_url(url) or url

    repository = _nonempty(
        _repository_from_url(origin) if origin else "",
        prev.get("repository") if not origin else "",
        _repository_from_url(url),
    )
    has_catalog = bool(cat)
    source = _nonempty(
        "github" if origin else "",
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
    version = _nonempty(prev.get("version"), prev.get("tag"))
    if version:
        meta["version"] = version
    if prev.get("tag"):
        meta["tag"] = prev.get("tag")
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
    if "loaded" in prev:
        meta["loaded"] = bool(prev.get("loaded"))
    # Disk sync rebuilds this dict from a whitelist — keep Never Update and
    # re-apply catalog ``updates: false`` / pin_release on every detect.
    if prev.get("never_update"):
        meta["never_update"] = True
    if match_kind == "exact" and catalog_locks_updates(cat):
        meta["never_update"] = True
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

    When a folder has a parseable ``.git/config`` ``remote.origin.url``, that URL
    wins over catalog/preloaded repo fields (Open in Git, update checks, reinstall).
    Zip installs without ``.git`` keep catalog/settings URLs unchanged.

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
        from ichalaunch.addons.loadstate import addon_disk_path, addon_is_loaded

        disk = addon_disk_path(folder, addons_dir=addons_dir) if addons_dir else None
        origin = read_git_origin_url(disk) if disk is not None else None
        if addons_dir and origin is None:
            origin = read_git_origin_url(addons_dir / folder)
        meta = merge_addon_meta(
            folder,
            prev,
            cat,
            match_kind=kind or "exact",
            git_origin=origin,
        )
        toc_ver = read_addon_toc_version(disk) if disk is not None else ""
        if toc_ver and not str(meta.get("version") or "").strip():
            meta["version"] = toc_ver
        meta["loaded"] = addon_is_loaded(folder, addons_dir=addons_dir)
        if addon_ignores_updates(cat if kind == "exact" else None, folder, meta):
            meta["never_update"] = True
        merged[folder] = meta

    merged = group_multi_folder_addons(merged)
    for folder, meta in list(merged.items()):
        cat, kind = resolve_catalog_entry(folder, idx, include_mods=False)
        if addon_ignores_updates(cat if kind == "exact" else None, folder, meta):
            stamped = dict(meta)
            stamped["never_update"] = True
            merged[folder] = stamped
    settings.set("installed_addons", merged)
    return merged


def sync_desired_mods_from_disk() -> dict[str, bool]:
    """Seed desired_mods checkboxes from disk for mods the user hasn't toggled.

    Detected state is "actual", not "desired": once the user has explicitly
    checked/unchecked a mod (settings.user_set_mods), a rescan must never flip
    their choice back — that caused the un-removable Darker Nights loop.
    """
    game = detect_game()
    if not game:
        return settings.desired_mods
    if listed_basenames(game) is None:
        log.warning("Skipping desired_mods sync — could not list game folder")
        return settings.desired_mods
    actual = detect_actual_state(game)
    # Only sync keys we know about in catalog
    known = {m["id"] for m in load_mod_catalog()}
    user_set = set(settings.user_set_mods)
    desired = settings.desired_mods
    for mod_id, present in actual.items():
        if mod_id in known and mod_id not in user_set:
            desired[mod_id] = bool(present)
    desired = enforce_vanilla_helpers_for_hd_desired(desired)
    desired = reconcile_exclusive_desired_mods(desired, actual=actual)
    settings.set("desired_mods", desired)
    # Launch uses desired_mods, not disk actual. Stamping vanillafixes_enabled
    # from a missing exe used to uncheck launch while the Client box stayed on.
    sync_vanillafixes_enabled_from_desired(desired)
    return desired


def full_resync() -> dict[str, Any]:
    addons = sync_installed_addons_from_disk()
    mods = sync_desired_mods_from_disk()
    mismatches = scan_mismatched_toc_addon_folders()
    return {
        "addons": addons,
        "mods": mods,
        "skipped_addons": [m.current_name for m in mismatches],
        "mismatches": mismatches,
    }
