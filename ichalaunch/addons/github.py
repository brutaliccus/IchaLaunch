"""GitHub addon install / update helpers."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import copy_tree, extract_zip, find_toc_roots
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import download_bytes_cb, download_file
from ichalaunch.game.launcher import detect_game

ProgressCb = Callable[[str], None]
UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/vnd.github+json"}

RATE_LIMIT_STATUS = "GitHub rate limit hit — add a token in Settings or try later"
STARTUP_CHECK_COOLDOWN_SEC = 30 * 60

# Updated after each GitHub API response (None if header missing).
_last_rate_remaining: int | None = None


def iso_date_today() -> str:
    """UTC calendar date as YYYY-MM-DD for install/update stamps."""
    return datetime.now(timezone.utc).date().isoformat()


class GitHubRateLimitError(Exception):
    """GitHub REST API rate limit exceeded."""


@dataclass
class AddonUpdateCheckResult:
    updates: list[dict[str, Any]] = field(default_factory=list)
    rate_limited: bool = False
    skipped_recent: bool = False
    status_message: str | None = None


def github_headers() -> dict[str, str]:
    headers = dict(UA)
    token = (settings.get("github_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _looks_like_rate_limit(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        try:
            if int(remaining) == 0:
                return True
        except ValueError:
            pass
    if response.status_code == 403:
        text = (response.text or "").lower()
        if "rate limit" in text or "api rate limit exceeded" in text:
            return True
    return False


def _note_rate_headers(response: requests.Response) -> None:
    global _last_rate_remaining
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    try:
        _last_rate_remaining = int(remaining)
    except ValueError:
        pass


def rate_limit_exhausted() -> bool:
    return _last_rate_remaining is not None and _last_rate_remaining <= 0


def github_get(url: str, *, timeout: int = 30) -> requests.Response:
    """GET a GitHub API URL with auth headers; raise on rate limit."""
    r = requests.get(url, headers=github_headers(), timeout=timeout)
    _note_rate_headers(r)
    if _looks_like_rate_limit(r):
        raise GitHubRateLimitError(RATE_LIMIT_STATUS)
    r.raise_for_status()
    return r


def parse_github_url(url: str) -> tuple[str, str] | None:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url)
    if not m:
        return None
    return m.group(1), m.group(2)


def github_latest_commit(owner: str, repo: str, branch: str | None = None) -> dict[str, Any]:
    if not branch:
        r = github_get(f"https://api.github.com/repos/{owner}/{repo}")
        branch = r.json().get("default_branch") or "main"
        if rate_limit_exhausted():
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
    r = github_get(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}")
    data = r.json()
    commit = data.get("commit") or {}
    commit_date = (
        (commit.get("committer") or {}).get("date")
        or (commit.get("author") or {}).get("date")
        or ""
    )
    return {
        "sha": data["sha"],
        "branch": branch,
        "message": commit.get("message", ""),
        "date": commit_date,
    }


def normalize_addon_name(name: str) -> str:
    for suffix in ("-master", "-main", "-dev"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _addon_install_meta(
    *,
    folder: str,
    owner: str,
    repo: str,
    branch: str,
    sha: str,
    url: str,
    commit_date: str = "",
    match_kind: str = "exact",
) -> dict[str, Any]:
    """Build metadata for a successful install/update, preserving installed_at."""
    from ichalaunch.core.detect import merge_addon_meta, resolve_catalog_entry

    prev = settings.installed_addons.get(folder) or {}
    today = iso_date_today()
    payload: dict[str, Any] = {
        "repository": f"{owner}/{repo}",
        "branch": branch,
        "installed_commit": sha,
        "source": "github",
        "url": url,
        "updated_at": today,
        "installed_at": prev.get("installed_at") or today,
    }
    if commit_date:
        # Store YYYY-MM-DD when possible
        payload["commit_date"] = str(commit_date)[:10]
    # Fill name/description/category from turtle_wiki catalog when known
    cat, kind = resolve_catalog_entry(folder)
    kind = match_kind if match_kind else (kind or "exact")
    enriched = merge_addon_meta(folder, {**prev, **payload}, cat, match_kind=kind)
    # Prefer github tracking fields from this install
    for key in ("repository", "branch", "installed_commit", "url", "updated_at", "installed_at", "commit_date", "source"):
        if payload.get(key):
            enriched[key] = payload[key]
    return enriched


def _record_pack_install(
    *,
    installed: list[str],
    owner: str,
    repo: str,
    branch: str,
    sha: str,
    url: str,
    commit_date: str,
    preferred_primary: str | None = None,
) -> str:
    """Write settings for a multi-folder (or single) GitHub install as one managed pack."""
    from ichalaunch.core.detect import pick_pack_primary

    if not installed:
        raise FileNotFoundError("No addon folders installed")

    # Drop stale settings keys for this repo that are no longer on disk after reinstall
    repo_key = f"{owner}/{repo}".lower()
    installed_lower = {n.lower() for n in installed}
    for existing, meta in list(settings.installed_addons.items()):
        if existing.lower() in installed_lower:
            continue
        existing_repo = str(meta.get("repository") or "").strip().lower()
        if existing_repo == repo_key:
            settings.remove_installed_addon(existing)

    primary = preferred_primary if preferred_primary in installed else None
    if not primary and preferred_primary:
        primary = next((n for n in installed if n.lower() == preferred_primary.lower()), None)
    if not primary:
        # Build a temporary meta map for primary selection
        stub = {f: {"repository": f"{owner}/{repo}", "url": url} for f in installed}
        primary = pick_pack_primary(installed, stub, preferred=preferred_primary)

    for name in installed:
        kind = "exact" if name == primary else "prefix"
        meta = _addon_install_meta(
            folder=name,
            owner=owner,
            repo=repo,
            branch=branch,
            sha=sha,
            url=url,
            commit_date=commit_date,
            match_kind=kind,
        )
        if len(installed) > 1:
            if name == primary:
                meta["folders"] = sorted(installed, key=str.lower)
                meta.pop("managed_by", None)
                meta["name"] = meta.get("name") if meta.get("name") and meta["name"] != name else name
                # Prefer catalog display name for primary
                from ichalaunch.core.detect import resolve_catalog_entry

                cat, ckind = resolve_catalog_entry(primary)
                if ckind == "exact" and cat and cat.get("name"):
                    meta["name"] = cat["name"]
            else:
                meta["managed_by"] = primary
                meta["name"] = name
                meta.pop("folders", None)
        else:
            meta.pop("managed_by", None)
            meta.pop("folders", None)
        settings.set_installed_addon(name, meta)

    log.info(
        "Installed addon pack %s (%s) from %s/%s",
        primary,
        ", ".join(sorted(installed)),
        owner,
        repo,
    )
    return primary if len(installed) == 1 else f"{primary} ({len(installed)} modules)"


def install_from_github(url: str, folder_name: str | None = None, progress: ProgressCb | None = None) -> str:
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("Not a valid GitHub repository URL")
    owner, repo = parsed
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game path not set")

    if progress:
        progress("Fetching repository info...")
    meta = github_latest_commit(owner, repo)
    branch = meta["branch"]
    commit_date = meta.get("date") or ""
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"

    with tempfile.TemporaryDirectory(prefix="icha_addon_") as tmp:
        work = Path(tmp)
        if progress:
            progress(f"Downloading {owner}/{repo}@{branch}...")
        zpath = download_file(
            zip_url, work / "addon.zip", progress=download_bytes_cb(progress)
        )
        extracted = extract_zip(zpath, work / "extract")
        roots = find_toc_roots(extracted)
        if not roots and any(extracted.glob("*.toc")):
            roots = [extracted]
        if not roots:
            # Fall back: any .toc parent (deeper nesting)
            candidates = [p for p in extracted.rglob("*.toc")]
            parents = {c.parent for c in candidates}
            roots = sorted(parents, key=lambda p: (len(p.parts), p.name.lower()))
        if not roots:
            raise FileNotFoundError("No .toc files found in repository")

        addons_dir = game / "Interface" / "AddOns"
        addons_dir.mkdir(parents=True, exist_ok=True)

        # Always keep each TOC root's real folder name — never rename children to catalog folder.
        installed: list[str] = []
        for root in roots:
            name = normalize_addon_name(root.name)
            if name.lower() in ("master", "main"):
                name = repo
            dest = addons_dir / name
            if dest.exists():
                shutil.rmtree(dest)
            copy_tree(root, dest)
            installed.append(name)

        # Single-root installs may use catalog folder_name as the destination name
        if len(installed) == 1 and folder_name:
            only = installed[0]
            wanted = normalize_addon_name(folder_name)
            if wanted and wanted.lower() != only.lower():
                src = addons_dir / only
                dest = addons_dir / wanted
                if dest.exists():
                    shutil.rmtree(dest)
                src.rename(dest)
                installed = [wanted]

        preferred = None
        if folder_name:
            preferred = normalize_addon_name(folder_name)
        elif any(normalize_addon_name(n).lower() == repo.lower() for n in installed):
            preferred = next(n for n in installed if normalize_addon_name(n).lower() == repo.lower())

        return _record_pack_install(
            installed=installed,
            owner=owner,
            repo=repo,
            branch=branch,
            sha=meta["sha"],
            url=url,
            commit_date=commit_date,
            preferred_primary=preferred,
        )

def _addon_has_repo(meta: dict[str, Any]) -> bool:
    repo = meta.get("repository")
    if isinstance(repo, str) and "/" in repo.strip():
        return True
    url = meta.get("url") or ""
    return bool(parse_github_url(str(url)))


def recently_checked_addon_updates(cooldown_sec: int = STARTUP_CHECK_COOLDOWN_SEC) -> bool:
    raw = settings.get("last_addon_update_check")
    if not raw:
        return False
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < cooldown_sec


def _mark_addon_update_check_time() -> None:
    settings.set("last_addon_update_check", time.time())


def check_addon_updates(*, respect_cooldown: bool = False) -> AddonUpdateCheckResult:
    """Check installed GitHub addons for newer commits.

    On rate limit (403/429 or X-RateLimit-Remaining=0), stops further API calls
    for this run and returns any updates found so far.
    Child modules of a multi-folder pack (managed_by) are skipped — only the
    primary entry is checked / listed for Update.
    """
    if respect_cooldown and recently_checked_addon_updates():
        return AddonUpdateCheckResult(skipped_recent=True)

    updates: list[dict[str, Any]] = []
    checked = 0
    rate_limited = False

    for folder, meta in settings.installed_addons.items():
        if meta.get("managed_by"):
            continue
        if not _addon_has_repo(meta):
            continue
        repo = meta.get("repository")
        if not repo or "/" not in str(repo):
            parsed = parse_github_url(str(meta.get("url") or ""))
            if not parsed:
                continue
            owner, name = parsed
            repo = f"{owner}/{name}"
        else:
            owner, name = str(repo).split("/", 1)

        if rate_limit_exhausted():
            rate_limited = True
            break

        try:
            remote = github_latest_commit(owner, name, meta.get("branch"))
            checked += 1
        except GitHubRateLimitError:
            rate_limited = True
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Update check failed for %s: %s", folder, exc)
            continue

        if remote["sha"] != meta.get("installed_commit"):
            updates.append(
                {
                    "folder": folder,
                    "repository": repo,
                    "local": meta.get("installed_commit", "")[:7],
                    "remote": remote["sha"][:7],
                    "url": meta.get("url") or f"https://github.com/{repo}",
                    "branch": remote["branch"],
                }
            )

        if rate_limit_exhausted():
            rate_limited = True
            break

    _mark_addon_update_check_time()

    if rate_limited:
        log.warning(
            "GitHub rate limit hit during addon update check "
            "(%d addon(s) checked, %d update(s) found). %s",
            checked,
            len(updates),
            RATE_LIMIT_STATUS,
        )
        return AddonUpdateCheckResult(
            updates=updates,
            rate_limited=True,
            status_message=RATE_LIMIT_STATUS,
        )

    return AddonUpdateCheckResult(updates=updates)


def _pack_folders(folder: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Return all Interface/AddOns folders belonging to this managed pack."""
    meta = meta if meta is not None else (settings.installed_addons.get(folder) or {})
    # If this is a child, resolve to parent pack
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by:
        parent_meta = settings.installed_addons.get(managed_by) or {}
        return _pack_folders(managed_by, parent_meta)

    folders = meta.get("folders")
    if isinstance(folders, list) and folders:
        return [str(f) for f in folders if f]
    # Also include any settings entries that declare managed_by == folder
    extras = [
        f
        for f, m in settings.installed_addons.items()
        if str(m.get("managed_by") or "").lower() == folder.lower()
    ]
    if extras:
        return sorted({folder, *extras}, key=str.lower)
    return [folder]


def update_addon(folder: str, progress: ProgressCb | None = None) -> None:
    meta = settings.installed_addons.get(folder)
    if not meta:
        # Case-insensitive fallback
        for key, val in settings.installed_addons.items():
            if key.lower() == folder.lower():
                folder, meta = key, val
                break
    if not meta:
        raise KeyError(folder)
    # Updates always target the pack primary
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by:
        folder = managed_by
        meta = settings.installed_addons.get(folder) or meta
    url = meta.get("url") or f"https://github.com/{meta['repository']}"
    # Pass primary folder name only as preferred primary — install keeps all module names
    install_from_github(url, folder_name=folder, progress=progress)


def uninstall_addon(folder: str) -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game path not set")
    meta = settings.installed_addons.get(folder) or {}
    if not meta:
        for key, val in settings.installed_addons.items():
            if key.lower() == folder.lower():
                folder, meta = key, val
                break
    # Removing a child removes the whole pack (same repo install)
    target = str(meta.get("managed_by") or folder).strip() or folder
    parent_meta = settings.installed_addons.get(target) or meta
    folders = _pack_folders(target, parent_meta)
    for name in folders:
        path = game / "Interface" / "AddOns" / name
        if path.exists():
            shutil.rmtree(path)
        settings.remove_installed_addon(name)
    # Ensure primary key cleared even if not in folders list
    settings.remove_installed_addon(target)

def load_catalog() -> list[dict[str, Any]]:
    from ichalaunch.core.paths import data_file

    path = data_file("addons.json")
    return json.loads(path.read_text(encoding="utf-8"))
