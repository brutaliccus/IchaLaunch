"""Shared catalog tip-SHA index (one JSON instead of per-repo probes).

The file is produced by ``tools/build_addon_tips.py`` and optionally published
to GitHub. The launcher prefers a fresh remote copy, then an appdata cache,
then a bundled copy next to ``addons.json``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ichalaunch.addons.git_refs import GitRefs, newest_version_tag, repo_cache_key
from ichalaunch.config.settings import appdata_root, settings
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file

DEFAULT_TIPS_URL = (
    "https://raw.githubusercontent.com/brutaliccus/IchaLaunch/main/"
    "ichalaunch/data/addon_tips.json"
)
TIPS_TTL_SEC = 30 * 60
_FETCH_TIMEOUT_SEC = 8
_UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/json"}

# In-process snapshot: (monotonic_loaded_at, index_dict)
_loaded: tuple[float, dict[str, Any]] | None = None


def tips_cache_path() -> Path:
    return appdata_root() / "addon_tips.json"


def bundled_tips_path() -> Path:
    return data_file("addon_tips.json")


def tips_url() -> str:
    override = str(settings.get("addon_tips_url") or "").strip()
    return override or DEFAULT_TIPS_URL


def empty_index() -> dict[str, Any]:
    return {"generated_at": "", "source": "", "repos": {}}


def normalize_index(raw: Any) -> dict[str, Any]:
    """Accept a dict with ``repos`` or a bare ``owner/repo`` map."""
    if not isinstance(raw, dict):
        return empty_index()
    repos_raw = raw.get("repos")
    if isinstance(repos_raw, dict):
        repos = {str(k).strip().lower(): v for k, v in repos_raw.items() if k}
        return {
            "generated_at": str(raw.get("generated_at") or ""),
            "source": str(raw.get("source") or ""),
            "repos": repos,
        }
    # Bare map of owner/repo -> entry
    if raw and all("/" in str(k) for k in raw if k != "generated_at"):
        repos = {}
        for key, val in raw.items():
            if key in {"generated_at", "source"}:
                continue
            if isinstance(val, dict):
                repos[str(key).strip().lower()] = val
        if repos:
            return {
                "generated_at": str(raw.get("generated_at") or ""),
                "source": str(raw.get("source") or "legacy"),
                "repos": repos,
            }
    return empty_index()


def parse_index_text(text: str) -> dict[str, Any]:
    try:
        return normalize_index(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty_index()


def load_index_file(path: Path) -> dict[str, Any]:
    try:
        return parse_index_text(path.read_text(encoding="utf-8"))
    except OSError:
        return empty_index()


def index_repo_count(index: dict[str, Any] | None) -> int:
    repos = (index or {}).get("repos")
    return len(repos) if isinstance(repos, dict) else 0


def repo_entry_from_refs(refs: GitRefs) -> dict[str, Any]:
    branch = refs.default_branch or ""
    sha = refs.head_sha or (refs.branches.get(branch) if branch else "")
    branches: dict[str, str] = {}
    if branch and sha:
        branches[branch] = sha
    tag = newest_version_tag(refs.tags)
    entry: dict[str, Any] = {
        "default_branch": branch,
        "sha": sha,
        "branches": branches,
    }
    if tag:
        entry["latest_tag"] = tag
    return entry


def build_index(repos: dict[str, dict[str, Any]], *, source: str) -> dict[str, Any]:
    normalized: dict[str, dict[str, Any]] = {}
    for key, val in repos.items():
        normalized[str(key).strip().lower()] = val
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "repos": normalized,
    }


def write_index_file(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _remember(index: dict[str, Any]) -> dict[str, Any]:
    global _loaded
    _loaded = (time.monotonic(), index)
    return index


def current_index() -> dict[str, Any]:
    if _loaded is not None:
        return _loaded[1]
    return empty_index()


def lookup_tip(owner: str, repo: str, branch: str | None = None) -> tuple[str, str] | None:
    """Return ``(sha, branch)`` from the loaded index, or None on miss."""
    repos = current_index().get("repos")
    if not isinstance(repos, dict):
        return None
    entry = repos.get(repo_cache_key(owner, repo))
    if not isinstance(entry, dict):
        return None
    default_branch = str(entry.get("default_branch") or "").strip()
    head = str(entry.get("sha") or "").strip()
    branches = entry.get("branches") if isinstance(entry.get("branches"), dict) else {}
    wanted = (branch or "").strip()
    if wanted:
        sha = str(branches.get(wanted) or "").strip()
        if not sha and wanted == default_branch:
            sha = head
        if not sha:
            return None
        return sha, wanted
    if head:
        return head, default_branch
    return None


def lookup_latest_tag(owner: str, repo: str) -> str:
    repos = current_index().get("repos")
    if not isinstance(repos, dict):
        return ""
    entry = repos.get(repo_cache_key(owner, repo))
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("latest_tag") or "").strip()


def fetch_remote_index(url: str | None = None) -> dict[str, Any] | None:
    target = (url or tips_url()).strip()
    if not target:
        return None
    try:
        r = requests.get(target, headers=_UA, timeout=_FETCH_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.info("Addon tip index fetch failed: %s", exc)
        return None
    if r.status_code != 200 or not (r.text or "").strip():
        log.info("Addon tip index HTTP %s from %s", r.status_code, target)
        return None
    index = parse_index_text(r.text)
    if index_repo_count(index) == 0:
        return None
    if not index.get("source"):
        index["source"] = "remote"
    return index


def refresh_tip_index(*, force: bool = False) -> dict[str, Any]:
    """Load the best available index (remote → appdata → bundled)."""
    global _loaded
    if _loaded is not None and not force:
        age = time.monotonic() - _loaded[0]
        if age < TIPS_TTL_SEC and index_repo_count(_loaded[1]) > 0:
            return _loaded[1]

    remote = fetch_remote_index()
    if remote is not None:
        try:
            write_index_file(tips_cache_path(), remote)
        except OSError as exc:
            log.debug("Could not cache addon tip index: %s", exc)
        return _remember(remote)

    cached = load_index_file(tips_cache_path())
    if index_repo_count(cached) > 0:
        return _remember(cached)

    bundled = load_index_file(bundled_tips_path())
    if index_repo_count(bundled) > 0:
        return _remember(bundled)

    return _remember(empty_index())


def clear_tip_index_cache() -> None:
    global _loaded
    _loaded = None
