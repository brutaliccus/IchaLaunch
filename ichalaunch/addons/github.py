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
from ichalaunch.core.process import download_file
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
) -> dict[str, Any]:
    """Build metadata for a successful install/update, preserving installed_at."""
    from ichalaunch.core.detect import match_catalog_entry, merge_addon_meta

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
    cat = match_catalog_entry(folder)
    enriched = merge_addon_meta(folder, {**prev, **payload}, cat)
    # Prefer github tracking fields from this install
    for key in ("repository", "branch", "installed_commit", "url", "updated_at", "installed_at", "commit_date", "source"):
        if payload.get(key):
            enriched[key] = payload[key]
    return enriched


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
        zpath = download_file(zip_url, work / "addon.zip")
        extracted = extract_zip(zpath, work / "extract")
        roots = find_toc_roots(extracted)
        if not roots and any(extracted.glob("*.toc")):
            roots = [extracted]
        if not roots:
            # multi-addon repos (e.g. Bongos)
            candidates = [p for p in extracted.rglob("*.toc")]
            parents = {c.parent for c in candidates}
            # install each unique parent that looks like an addon package
            installed = []
            addons_dir = game / "Interface" / "AddOns"
            addons_dir.mkdir(parents=True, exist_ok=True)
            for parent in sorted(parents, key=lambda p: p.name):
                name = normalize_addon_name(folder_name or parent.name)
                dest = addons_dir / name
                if dest.exists():
                    shutil.rmtree(dest)
                copy_tree(parent, dest)
                installed.append(name)
                settings.set_installed_addon(
                    name,
                    _addon_install_meta(
                        folder=name,
                        owner=owner,
                        repo=repo,
                        branch=branch,
                        sha=meta["sha"],
                        url=url,
                        commit_date=commit_date,
                    ),
                )
            if not installed:
                raise FileNotFoundError("No .toc files found in repository")
            return ", ".join(installed)

        # Prefer root matching repo name
        preferred = next((r for r in roots if normalize_addon_name(r.name).lower() == repo.lower()), roots[0])
        name = normalize_addon_name(folder_name or preferred.name)
        if name.lower() in ("master", "main"):
            name = repo
        dest = game / "Interface" / "AddOns" / name
        if dest.exists():
            shutil.rmtree(dest)
        copy_tree(preferred, dest)
        settings.set_installed_addon(
            name,
            _addon_install_meta(
                folder=name,
                owner=owner,
                repo=repo,
                branch=branch,
                sha=meta["sha"],
                url=url,
                commit_date=commit_date,
            ),
        )
        log.info("Installed addon %s from %s/%s", name, owner, repo)
        return name


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
    """
    if respect_cooldown and recently_checked_addon_updates():
        return AddonUpdateCheckResult(skipped_recent=True)

    updates: list[dict[str, Any]] = []
    checked = 0
    rate_limited = False

    for folder, meta in settings.installed_addons.items():
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


def update_addon(folder: str, progress: ProgressCb | None = None) -> None:
    meta = settings.installed_addons.get(folder)
    if not meta:
        raise KeyError(folder)
    url = meta.get("url") or f"https://github.com/{meta['repository']}"
    install_from_github(url, folder_name=folder, progress=progress)


def uninstall_addon(folder: str) -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game path not set")
    path = game / "Interface" / "AddOns" / folder
    if path.exists():
        shutil.rmtree(path)
    settings.remove_installed_addon(folder)


def load_catalog() -> list[dict[str, Any]]:
    from ichalaunch.core.paths import data_file

    path = data_file("addons.json")
    return json.loads(path.read_text(encoding="utf-8"))
