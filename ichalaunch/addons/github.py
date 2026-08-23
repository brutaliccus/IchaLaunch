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
from typing import Any, Callable, NamedTuple
from urllib.parse import urlparse

import requests

from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import copy_tree, extract_zip, find_toc_roots, safe_remove
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import download_bytes, download_bytes_cb, status_only
from ichalaunch.game.launcher import detect_game, ensure_addons_dir, resolve_addons_dir

ProgressCb = Callable[[str], None]
UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/vnd.github+json"}

RATE_LIMIT_STATUS = "GitHub rate limit hit — add a token in Settings or try later"
WAITING_RATE_LIMIT_STATUS = "Waiting for GitHub rate limit…"
GITHUB_TOKEN_REJECTED_MSG = "GitHub token rejected — clear or update it in Settings"
# Automatic (startup/silent) rescans are skipped if the last scan was within this window.
# Default only — live value comes from settings.auto_scan_cooldown_sec().
STARTUP_CHECK_COOLDOWN_SEC = 60 * 60
# Unauthenticated GitHub REST allows ~60 requests/hour; we pace scans to match.
UNAUTH_API_BUDGET_PER_HOUR = 60
UNAUTH_BUDGET_WINDOW_SEC = 60 * 60
_SCAN_QUEUE_KEY = "addon_update_scan_queue"
_URL_REACH_CACHE_TTL_SEC = 10 * 60
_URL_REACH_TIMEOUT_SEC = 2.5

# Updated after each GitHub API response (None if header missing).
_last_rate_remaining: int | None = None
_last_rate_reset_epoch: int | None = None
# In-process unauthenticated budget (synced from settings at scan start).
_budget_window_start: float | None = None
_budget_window_used: int = 0
# url -> (monotonic_ts, reachable)
_url_reach_cache: dict[str, tuple[float, bool]] = {}
_token_rejected_pending: bool = False


class ParsedGitHubUrl(NamedTuple):
    """owner/repo plus optional release tag from /releases/tag/ or /releases/download/."""

    owner: str
    repo: str
    tag: str | None = None


def iso_date_today() -> str:
    """UTC calendar date as YYYY-MM-DD for install/update stamps."""
    return datetime.now(timezone.utc).date().isoformat()


class GitHubRateLimitError(Exception):
    """GitHub REST API rate limit exceeded."""


class GitHubBudgetExhaustedError(GitHubRateLimitError):
    """Local unauthenticated hourly API budget exhausted (queued scan resumes later)."""


@dataclass
class AddonUpdateCheckResult:
    updates: list[dict[str, Any]] = field(default_factory=list)
    rate_limited: bool = False
    skipped_recent: bool = False
    status_message: str | None = None
    queued: bool = False
    checked_count: int = 0
    total_count: int = 0
    resume_after_sec: int | None = None


def has_github_token() -> bool:
    return bool((settings.get("github_token") or "").strip())


# Hosts that may receive Authorization: Bearer <github_token>. HTTPS only.
# Never include github.io (third-party Pages) or arbitrary image CDNs.
_GITHUB_AUTH_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "www.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
})


def may_send_github_token(url: str) -> bool:
    """True only for HTTPS GitHub hosts — never third-party or plaintext HTTP."""
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    return host in _GITHUB_AUTH_HOSTS or host.endswith(".githubusercontent.com")


def github_headers(url: str = "") -> dict[str, str]:
    """GitHub REST headers. Token is attached only for HTTPS GitHub hosts.

    Always pass the real request URL. An empty or third-party URL never
    receives ``Authorization`` — including plaintext ``http://``.
    """
    headers = dict(UA)
    token = (settings.get("github_token") or "").strip()
    if token and may_send_github_token(url):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def note_github_token_rejected() -> None:
    """Record that the stored token was rejected so the UI can warn once."""
    global _token_rejected_pending
    _token_rejected_pending = True
    log.warning(GITHUB_TOKEN_REJECTED_MSG)


def take_github_token_warning() -> str | None:
    """Return a one-shot user-facing warning when the token was rejected."""
    global _token_rejected_pending
    if not _token_rejected_pending:
        return None
    _token_rejected_pending = False
    return GITHUB_TOKEN_REJECTED_MSG


def format_github_error_message(exc: BaseException) -> str:
    """Turn raw HTTP errors into actionable GitHub API messages."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code == 401:
            return GITHUB_TOKEN_REJECTED_MSG
    text = str(exc)
    if "401" in text and "Unauthorized" in text and "api.github.com" in text:
        return GITHUB_TOKEN_REJECTED_MSG
    return text


def compute_unauth_budget(
    *,
    window_start: float | None,
    window_used: int,
    now: float | None = None,
    budget: int = UNAUTH_API_BUDGET_PER_HOUR,
    window_sec: int = UNAUTH_BUDGET_WINDOW_SEC,
) -> tuple[int, int, float, int]:
    """Return (remaining, reset_in_sec, effective_start, effective_used) for the hour window."""
    ts = time.time() if now is None else float(now)
    used = max(0, int(window_used or 0))
    start = float(window_start) if window_start is not None else ts
    elapsed = ts - start
    if window_start is None or elapsed >= window_sec:
        return budget, 0, ts, 0
    remaining = max(0, budget - used)
    reset_in = max(0, int(window_sec - elapsed))
    return remaining, reset_in, start, used


def format_queued_scan_status(done: int, total: int, resume_after_sec: int) -> str:
    """User-facing status while a paced unauthenticated scan is waiting on budget."""
    total = max(0, int(total))
    done = max(0, min(int(done), total if total else int(done)))
    sec = max(0, int(resume_after_sec))
    if sec <= 0:
        return f"Scanning addons… {done}/{total} (queued; resuming…)"
    mins = max(1, (sec + 59) // 60)
    return f"Scanning addons… {done}/{total} (queued; resumes in ~{mins} min)"


def _load_scan_queue() -> dict[str, Any]:
    raw = settings.get(_SCAN_QUEUE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save_scan_queue(state: dict[str, Any] | None) -> None:
    if not state:
        settings.set(_SCAN_QUEUE_KEY, None)
    else:
        settings.set(_SCAN_QUEUE_KEY, state)


def clear_addon_scan_queue() -> None:
    """Drop any persisted within-scan queue (full pass finished or token present)."""
    global _budget_window_start, _budget_window_used
    _budget_window_start = None
    _budget_window_used = 0
    if settings.get(_SCAN_QUEUE_KEY) is not None:
        _save_scan_queue(None)


def clear_github_url_cache() -> None:
    """Drop in-process GitHub browse URL reachability cache."""
    from ichalaunch.addons.git_refs import clear_git_refs_cache
    from ichalaunch.addons.tip_index import clear_tip_index_cache

    _url_reach_cache.clear()
    clear_github_browse_cache()
    clear_git_refs_cache()
    clear_tip_index_cache()


NO_TOKEN_FORK_TIP = "Add GitHub token in Settings to browse forks and versions"
# GitHub returns up to 100 forks per page; paginate so older/archived forks are not dropped.
_FORK_LIST_PER_PAGE = 100
_FORK_LIST_MAX_PAGES = 5

# Session cache for token-gated fork/version dropdowns (keyed by owner/repo).
_browse_forks_cache: dict[str, list[dict[str, Any]]] = {}
_browse_versions_cache: dict[str, list[str]] = {}


def clear_github_browse_cache() -> None:
    """Drop in-process fork/version browse caches."""
    _browse_forks_cache.clear()
    _browse_versions_cache.clear()


def _repo_cache_key(owner: str, repo: str) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}"


def fork_entry_from_repo_url(url: str, label: str | None = None) -> dict[str, Any]:
    """Build a fork-picker row from a browse URL, owner/repo, or tagged release URL."""
    text = str(url or "").strip()
    if not text:
        return {}
    if text.count("/") == 1 and "://" not in text and " " not in text:
        text = f"https://github.com/{text}"
    parsed = parse_github_url(text)
    if not parsed:
        return {"label": label or text, "repo": text}
    browse = (
        github_tag_page_url(parsed.owner, parsed.repo, parsed.tag)
        if parsed.tag
        else github_browse_url(parsed.owner, parsed.repo)
    )
    lbl = (label or "").strip() or f"{parsed.owner}/{parsed.repo}"
    fe: dict[str, Any] = {
        "label": lbl,
        "repo": browse,
        "owner": parsed.owner,
        "repo_name": parsed.repo,
    }
    if parsed.tag:
        fe["pin_release"] = parsed.tag
    return fe


def catalog_fork_entries(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Static fork choices from a catalog entry (no API)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not entry:
        return out

    def add(fe: dict[str, Any]) -> None:
        key = str(fe.get("repo") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(fe)

    main = fork_entry_from_repo_url(str(entry.get("repo") or ""))
    if main.get("repo"):
        pin = str(entry.get("pin_release") or "").strip()
        if pin and not main.get("pin_release"):
            main["pin_release"] = pin
            parsed = parse_github_url(str(main.get("repo") or ""))
            if parsed and not parsed.tag:
                main["repo"] = github_tag_page_url(parsed.owner, parsed.repo, pin)
        add(main)

    for fork in entry.get("forks") or []:
        if not isinstance(fork, dict):
            continue
        fe = fork_entry_from_repo_url(
            str(fork.get("repo") or ""),
            str(fork.get("label") or "").strip() or None,
        )
        if fork.get("pin_release"):
            fe["pin_release"] = fork.get("pin_release")
            parsed = parse_github_url(str(fe.get("repo") or ""))
            if parsed and not parsed.tag:
                fe["repo"] = github_tag_page_url(
                    parsed.owner, parsed.repo, str(fork.get("pin_release"))
                )
        if fork.get("folder"):
            fe["folder"] = fork.get("folder")
        add(fe)
    return out


def parse_entry_owner_repo(
    entry: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Resolve GitHub owner/repo from catalog or installed addon fields."""
    entry = entry if isinstance(entry, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    for raw in (
        entry.get("repo"),
        entry.get("url"),
        meta.get("url"),
        entry.get("repository"),
        meta.get("repository"),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        if text.count("/") == 1 and "://" not in text and " " not in text:
            owner, name = text.split("/", 1)
            owner, name = owner.strip(), name.strip()
            if owner and name:
                return owner, name
        parsed = parse_github_url(text)
        if parsed:
            return parsed.owner, parsed.repo
    return None


def get_cached_repo_forks(owner: str, repo: str) -> list[dict[str, Any]] | None:
    key = _repo_cache_key(owner, repo)
    hit = _browse_forks_cache.get(key)
    return list(hit) if hit is not None else None


def get_cached_repo_versions(owner: str, repo: str) -> list[str] | None:
    key = _repo_cache_key(owner, repo)
    hit = _browse_versions_cache.get(key)
    return list(hit) if hit is not None else None


def sort_fork_entries(forks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active forks first, then archived; stable alphabetical tie-break within each group."""
    return sorted(
        forks,
        key=lambda f: (bool(f.get("archived")), str(f.get("label") or "").lower()),
    )


def list_repo_forks(owner: str, repo: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """List fork repos for the token-gated browse UI. Results are session-cached."""
    key = _repo_cache_key(owner, repo)
    if use_cache and key in _browse_forks_cache:
        return list(_browse_forks_cache[key])

    forks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_url(url: str, label: str | None = None, *, archived: bool = False) -> None:
        fe = fork_entry_from_repo_url(url, label)
        if archived:
            fe["archived"] = True
        browse = str(fe.get("repo") or "").strip().lower()
        if browse and browse not in seen:
            seen.add(browse)
            forks.append(fe)

    add_url(github_browse_url(owner, repo))

    try:
        r = _github_api_get(
            f"https://api.github.com/repos/{owner}/{repo}",
            timeout=30,
        )
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code == 401:
            raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
        if r.status_code not in (400, 404):
            r.raise_for_status()
            info = r.json()
            if isinstance(info, dict):
                parent = info.get("parent")
                if isinstance(parent, dict):
                    full = str(parent.get("full_name") or "").strip()
                    if "/" in full:
                        po, pr = full.split("/", 1)
                        add_url(github_browse_url(po, pr), full)
                source = info.get("source")
                if isinstance(source, dict):
                    full = str(source.get("full_name") or "").strip()
                    if "/" in full:
                        so, sr = full.split("/", 1)
                        add_url(github_browse_url(so, sr), full)
    except GitHubRateLimitError:
        raise
    except requests.HTTPError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Repo info failed for %s/%s: %s", owner, repo, exc)

    try:
        fork_items: list[dict[str, Any]] = []
        for page in range(1, _FORK_LIST_MAX_PAGES + 1):
            r = _github_api_get(
                f"https://api.github.com/repos/{owner}/{repo}/forks",
                timeout=30,
                params={
                    "per_page": _FORK_LIST_PER_PAGE,
                    "sort": "newest",
                    "page": page,
                },
            )
            _note_rate_headers(r)
            if _looks_like_rate_limit(r):
                raise GitHubRateLimitError(RATE_LIMIT_STATUS)
            if r.status_code == 401:
                raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
            if r.status_code in (400, 404):
                break
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            fork_items.extend(item for item in data if isinstance(item, dict))
            if len(data) < _FORK_LIST_PER_PAGE:
                break
        for item in fork_items:
            full = str(item.get("full_name") or "").strip()
            if "/" not in full:
                continue
            fo, fr = full.split("/", 1)
            add_url(
                github_browse_url(fo, fr),
                full,
                archived=bool(item.get("archived")),
            )
    except GitHubRateLimitError:
        raise
    except requests.HTTPError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Fork list failed for %s/%s: %s", owner, repo, exc)

    forks = sort_fork_entries(forks)
    _browse_forks_cache[key] = list(forks)
    return forks


def list_repo_versions(
    owner: str,
    repo: str,
    *,
    use_cache: bool = True,
    limit: int = 100,
) -> list[str]:
    """Release tags and git tags for the token-gated version picker (session-cached)."""
    key = _repo_cache_key(owner, repo)
    if use_cache and key in _browse_versions_cache:
        return list(_browse_versions_cache[key])

    tags: list[str] = []
    seen: set[str] = set()
    cap = max(1, min(int(limit), 100))

    def add(tag: str) -> None:
        t = str(tag or "").strip()
        if not t:
            return
        low = t.lower()
        if low in seen:
            return
        seen.add(low)
        tags.append(t)

    try:
        r = _github_api_get(
            f"https://api.github.com/repos/{owner}/{repo}/releases",
            timeout=30,
            params={"per_page": cap},
        )
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code == 401:
            raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
        if r.status_code not in (400, 404):
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                for item in data:
                    add(str(item.get("tag_name") or ""))
    except GitHubRateLimitError:
        raise
    except requests.HTTPError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Release list failed for %s/%s: %s", owner, repo, exc)

    try:
        r = _github_api_get(
            f"https://api.github.com/repos/{owner}/{repo}/tags",
            timeout=30,
            params={"per_page": cap},
        )
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code == 401:
            raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
        if r.status_code not in (400, 404):
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                for item in data:
                    add(str(item.get("name") or ""))
    except GitHubRateLimitError:
        raise
    except requests.HTTPError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Tag list failed for %s/%s: %s", owner, repo, exc)

    _browse_versions_cache[key] = list(tags)
    return tags


def addon_install_url_for_choice(
    fork_data: dict[str, Any] | None,
    tag: str | None = None,
) -> str:
    """Build the install URL from fork + optional version tag."""
    fork_data = fork_data if isinstance(fork_data, dict) else {}
    raw = str(fork_data.get("repo") or "").strip()
    parsed = parse_github_url(raw)
    if not parsed:
        return raw
    chosen = str(tag or "").strip() or str(fork_data.get("pin_release") or "").strip()
    if chosen:
        return github_tag_page_url(parsed.owner, parsed.repo, chosen)
    return github_browse_url(parsed.owner, parsed.repo)


def has_pending_addon_scan_queue() -> bool:
    pending = _load_scan_queue().get("pending")
    return isinstance(pending, list) and bool(pending)


def _sync_budget_from_queue(state: dict[str, Any] | None = None) -> None:
    global _budget_window_start, _budget_window_used
    q = state if state is not None else _load_scan_queue()
    remaining, _reset, start, used = compute_unauth_budget(
        window_start=q.get("window_start"),
        window_used=int(q.get("window_used") or 0),
    )
    _budget_window_start = start
    _budget_window_used = used
    # If the hour rolled over, keep effective counters in sync for this process.
    if remaining == UNAUTH_API_BUDGET_PER_HOUR and used == 0:
        _budget_window_start = start
        _budget_window_used = 0


def unauth_budget_remaining(*, now: float | None = None) -> tuple[int, int]:
    """Remaining unauthenticated API calls and seconds until the hour window resets."""
    global _budget_window_start, _budget_window_used
    remaining, reset_in, start, used = compute_unauth_budget(
        window_start=_budget_window_start,
        window_used=_budget_window_used,
        now=now,
    )
    # Keep module state aligned when the window rolls over mid-session.
    _budget_window_start = start
    _budget_window_used = used
    return remaining, reset_in


def _consume_api_budget() -> None:
    """Count one unauthenticated GitHub API call; raise when the local hour budget is empty."""
    global _budget_window_used
    if has_github_token():
        return
    remaining, _reset = unauth_budget_remaining()
    if remaining <= 0:
        raise GitHubBudgetExhaustedError(WAITING_RATE_LIMIT_STATUS)
    _budget_window_used += 1


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
    global _last_rate_remaining, _last_rate_reset_epoch
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        try:
            _last_rate_remaining = int(remaining)
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            _last_rate_reset_epoch = int(reset)
        except ValueError:
            pass


def rate_limit_exhausted() -> bool:
    return _last_rate_remaining is not None and _last_rate_remaining <= 0


def _resume_after_sec_from_headers() -> int | None:
    if _last_rate_reset_epoch is None:
        return None
    return max(0, int(_last_rate_reset_epoch - time.time()))


def _unauth_headers() -> dict[str, str]:
    return dict(UA)


def _github_api_get(url: str, **kwargs: Any) -> requests.Response:
    """GET a GitHub API URL; retry without token when a stored token is rejected."""
    headers = dict(github_headers(url))
    extra_headers = kwargs.pop("headers", None)
    if extra_headers:
        headers.update(extra_headers)
    had_token = "Authorization" in headers
    if not had_token:
        _consume_api_budget()
    r = requests.get(url, headers=headers, **kwargs)
    _note_rate_headers(r)
    if had_token and r.status_code == 401:
        note_github_token_rejected()
        _consume_api_budget()
        retry_headers = _unauth_headers()
        if extra_headers:
            retry_headers.update(extra_headers)
        r = requests.get(url, headers=retry_headers, **kwargs)
        _note_rate_headers(r)
    return r


def github_get(url: str, *, timeout: int = 30) -> requests.Response:
    """GET a GitHub API URL with auth headers; raise on rate limit."""
    r = _github_api_get(url, timeout=timeout)
    if _looks_like_rate_limit(r):
        raise GitHubRateLimitError(RATE_LIMIT_STATUS)
    if r.status_code == 401:
        raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
    r.raise_for_status()
    return r


def github_open(url: str, **kwargs: Any) -> requests.Response:
    """GET with auth and bad-token retry; caller must close the response."""
    r = _github_api_get(url, **kwargs)
    if _looks_like_rate_limit(r):
        r.close()
        raise GitHubRateLimitError(RATE_LIMIT_STATUS)
    if r.status_code == 401:
        r.close()
        raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
    return r


def parse_github_url(url: str) -> ParsedGitHubUrl | None:
    """Parse github.com owner/repo, optionally a release tag.

    Supports:
    - ``https://github.com/owner/repo``
    - ``https://github.com/owner/repo/releases/tag/1.5.16``
    - ``https://github.com/owner/repo/releases/download/TAG/asset.zip``
    - ``https://github.com/owner/repo/archive/refs/tags/TAG.zip``
    """
    raw = (url or "").strip()
    if not raw:
        return None
    # Keep path for tag extraction before stripping .git / trailing slash
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?(?:/|$)",
        raw,
        re.I,
    )
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    tag: str | None = None
    tm = re.search(r"/releases/tag/([^/#?]+)", raw, re.I)
    if tm:
        tag = tm.group(1)
    else:
        dm = re.search(r"/releases/download/([^/]+)/", raw, re.I)
        if dm:
            tag = dm.group(1)
        else:
            am = re.search(r"/archive/refs/tags/([^/#?]+?)(?:\.zip|\.tar\.gz)?(?:$|[?#])", raw, re.I)
            if am:
                tag = am.group(1)
    if tag:
        # URL-decode common encodings; leave exotic tags as-is
        try:
            from urllib.parse import unquote

            tag = unquote(tag)
        except Exception:  # noqa: BLE001
            pass
        tag = tag.strip().rstrip("/") or None
    return ParsedGitHubUrl(owner, repo, tag)


def github_browse_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def github_tag_archive_url(owner: str, repo: str, tag: str) -> str:
    """Source zip for a git tag (same as the green Code → Download ZIP at a tag)."""
    return f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"


def github_tag_page_url(owner: str, repo: str, tag: str) -> str:
    return f"https://github.com/{owner}/{repo}/releases/tag/{tag}"


def github_url_reachable_cached(url: str) -> bool | None:
    """Return cached reachability or None if unknown / expired."""
    key = (url or "").strip().rstrip("/").lower()
    if not key:
        return False
    hit = _url_reach_cache.get(key)
    if not hit:
        return None
    ts, ok = hit
    if (time.monotonic() - ts) > _URL_REACH_CACHE_TTL_SEC:
        return None
    return ok


def github_url_reachable(url: str, *, timeout: float = _URL_REACH_TIMEOUT_SEC) -> bool:
    """Lightweight HEAD/GET probe for a browse URL. Results are cached briefly."""
    text = (url or "").strip()
    if not text:
        return False
    key = text.rstrip("/").lower()
    cached = github_url_reachable_cached(text)
    if cached is not None:
        return cached

    headers = {
        "User-Agent": "IchaLaunch/0.1",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    ok = False
    try:
        r = requests.head(text, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code == 405 or r.status_code == 403:
            r = requests.get(
                text,
                headers={**headers, "Range": "bytes=0-0"},
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            )
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
        ok = 200 <= r.status_code < 400
    except requests.RequestException:
        ok = False
    _url_reach_cache[key] = (time.monotonic(), ok)
    return ok


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


def github_remote_tip(owner: str, repo: str, branch: str | None = None) -> dict[str, Any]:
    """Latest commit SHA without spending REST quota when possible.

    Order: catalog tip index → git upload-pack / ls-remote → commits Atom → REST.
    """
    from ichalaunch.addons.git_refs import fetch_commit_atom_sha, fetch_git_refs
    from ichalaunch.addons.tip_index import lookup_tip

    wanted = str(branch or "").strip() or None
    hit = lookup_tip(owner, repo, wanted)
    if hit:
        sha, resolved = hit
        return {"sha": sha, "branch": resolved or wanted or "", "message": "", "date": ""}

    refs = fetch_git_refs(owner, repo)
    if refs is not None:
        sha = refs.tip_sha(wanted)
        resolved = wanted or refs.default_branch or ""
        if sha:
            return {"sha": sha, "branch": resolved, "message": "", "date": ""}

    atom_sha = fetch_commit_atom_sha(owner, repo, wanted)
    if atom_sha:
        return {"sha": atom_sha, "branch": wanted or "", "message": "", "date": ""}

    return github_latest_commit(owner, repo, branch=wanted)


def _commits_match(left: str, right: str) -> bool:
    a, b = (left or "").strip().lower(), (right or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def should_report_addon_update(
    *,
    local_commit: str = "",
    remote_commit: str = "",
    local_version: str = "",
    remote_version: str = "",
) -> bool:
    """True only when remote is known to be newer. Missing data is not an update.

    Tracked installs (local commit SHA present) compare commits. Copied/unknown
    addons with no install SHA compare TOC/version vs a GitHub tag. Empty local
    commit must not be treated as ``0`` / older than remote.
    """
    local_c = (local_commit or "").strip()
    remote_c = (remote_commit or "").strip()
    if local_c and remote_c:
        return not _commits_match(local_c, remote_c)
    local_v = (local_version or "").strip()
    remote_v = (remote_version or "").strip()
    if local_v and remote_v:
        from ichalaunch.core.self_update import is_newer

        return is_newer(remote_v, local_v)
    return False


def catalog_pin_tag(entry: dict[str, Any] | None) -> str:
    """Pinned GitHub release tag from catalog ``pin_release`` or a tagged repo URL."""
    if not entry:
        return ""
    pin = str(entry.get("pin_release") or "").strip()
    if pin:
        return pin
    parsed = parse_github_url(str(entry.get("repo") or entry.get("url") or ""))
    if parsed and parsed.tag:
        return str(parsed.tag).strip()
    return ""


def catalog_locks_updates(entry: dict[str, Any] | None) -> bool:
    """True when the catalog entry must not track GitHub latest.

    Honors ``updates: false``, ``ignore_updates`` / ``never_update``,
    ``pin_release``, or a ``repo`` URL that already includes a release tag.
    """
    if not entry:
        return False
    if entry.get("updates") is False:
        return True
    if entry.get("ignore_updates") is True or entry.get("never_update") is True:
        return True
    return bool(catalog_pin_tag(entry))


def addon_ignores_updates(
    entry: dict[str, Any] | None,
    folder: str,
    meta: dict[str, Any] | None = None,
) -> bool:
    """True if catalog ``updates`` is false, ``pin_release`` is set, or saved ``never_update``.

    Catalog pin always applies — even with empty settings (copied folder / first scan).
    When *entry* is omitted, the turtle_wiki catalog is resolved from *folder*.
    """
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("never_update"):
        return True
    if catalog_locks_updates(entry):
        return True
    if not entry and folder:
        from ichalaunch.core.detect import resolve_catalog_entry

        cat, kind = resolve_catalog_entry(folder, include_mods=False)
        if kind == "exact" and catalog_locks_updates(cat):
            return True
    return False


def addon_skips_updates(
    folder: str,
    meta: dict[str, Any] | None = None,
    *,
    catalog_entry: dict[str, Any] | None = None,
    catalog_kind: str = "",
) -> bool:
    """True when this installed addon should never be offered an auto-update."""
    meta = meta if isinstance(meta, dict) else {}
    entry = catalog_entry if catalog_kind != "prefix" else None
    if addon_ignores_updates(entry, folder, meta):
        return True
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by:
        parent = settings.installed_addons.get(managed_by) or {}
        if addon_ignores_updates(None, managed_by, parent):
            return True
    return False


def _catalog_pin_for_install(owner: str, repo: str, folder_name: str | None) -> str:
    """Catalog pin_release for this GitHub repo (empty if the catalog tracks latest)."""
    from ichalaunch.core.detect import resolve_catalog_entry

    if folder_name:
        cat, kind = resolve_catalog_entry(folder_name, include_mods=False)
        if kind == "exact":
            pin = catalog_pin_tag(cat)
            if pin:
                return pin
    owner_l, repo_l = owner.lower(), repo.lower()
    for entry in load_catalog():
        other = parse_github_url(str(entry.get("repo") or entry.get("url") or ""))
        if not other:
            continue
        if other.owner.lower() == owner_l and other.repo.lower() == repo_l:
            return catalog_pin_tag(entry)
    return ""


def github_latest_version_tag(owner: str, repo: str) -> str | None:
    """Latest GitHub release tag, else newest git tag. None if unknown / 404.

    Prefers the catalog tip index, releases Atom, and advertised git tags so
    copied-addon scans do not spend REST quota.
    """
    from ichalaunch.addons.git_refs import (
        fetch_git_refs,
        fetch_releases_atom_tag,
        newest_version_tag,
    )
    from ichalaunch.addons.tip_index import lookup_latest_tag

    indexed = lookup_latest_tag(owner, repo)
    if indexed:
        return indexed
    atom_tag = fetch_releases_atom_tag(owner, repo)
    if atom_tag:
        return atom_tag
    refs = fetch_git_refs(owner, repo)
    if refs is not None:
        tag = newest_version_tag(refs.tags)
        if tag:
            return tag

    full = f"{owner}/{repo}"
    try:
        latest_url = f"https://api.github.com/repos/{full}/releases/latest"
        r = _github_api_get(latest_url, timeout=30)
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code != 404:
            if r.status_code == 401:
                raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
            r.raise_for_status()
            tag = str((r.json() or {}).get("tag_name") or "").strip()
            if tag:
                return tag
    except GitHubRateLimitError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        log.warning("Latest release lookup failed for %s: %s", full, exc)

    try:
        tags_url = f"https://api.github.com/repos/{full}/tags"
        r = _github_api_get(tags_url, timeout=30, params={"per_page": 1})
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code == 404:
            return None
        if r.status_code == 401:
            raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            tag = str(data[0].get("name") or "").strip()
            if tag:
                return tag
    except GitHubRateLimitError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        log.warning("Tag lookup failed for %s: %s", full, exc)
    return None


_README_MAX_CHARS = 180_000
_IMG_MD_RE = re.compile(
    r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))",
    re.MULTILINE,
)
_IMG_HTML_RE = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*([\"'])([^\"']+)\1[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_REF_IMG_RE = re.compile(
    r"^(\s*\[[^\]]+\]:\s*)(\S+)(.*)$",
    re.MULTILINE,
)


def fetch_repo_readme(owner: str, repo: str, branch: str | None = None) -> dict[str, str] | None:
    """Fetch README markdown for preview. Returns {markdown, base_url, cache_dir} or None."""
    import base64

    full = f"{owner}/{repo}"
    url = f"https://api.github.com/repos/{full}/readme"
    try:
        r = _github_api_get(
            url,
            timeout=30,
            params={"ref": branch} if branch else None,
        )
        _note_rate_headers(r)
        if _looks_like_rate_limit(r):
            raise GitHubRateLimitError(RATE_LIMIT_STATUS)
        if r.status_code == 404:
            return None
        if r.status_code == 401:
            raise requests.HTTPError(GITHUB_TOKEN_REJECTED_MSG, response=r)
        r.raise_for_status()
        data = r.json()
    except GitHubRateLimitError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        log.warning("README fetch failed for %s: %s", full, exc)
        return None

    try:
        raw = base64.b64decode(data.get("content") or "")
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("README decode failed for %s: %s", full, exc)
        return None
    if not text.strip():
        return None
    if len(text) > _README_MAX_CHARS:
        text = text[:_README_MAX_CHARS] + "\n\n… (README truncated for preview)"

    path = str(data.get("path") or "README.md").replace("\\", "/")
    parent = str(Path(path).parent).replace("\\", "/")
    readme_dir = ""
    if parent and parent != ".":
        readme_dir = parent.strip("/") + "/"

    use_branch = branch or "main"
    base_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{use_branch}/{readme_dir}"
    md = rewrite_readme_media(
        text, owner=owner, repo=repo, branch=use_branch, readme_dir=readme_dir
    )
    cache_dir = Path(tempfile.mkdtemp(prefix="ichalaunch-readme-"))
    md = localize_readme_images(md, cache_dir=cache_dir)
    return {"markdown": md, "base_url": base_url, "cache_dir": str(cache_dir)}


_IMG_URL_COLLECT_RE = re.compile(
    r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
_MAX_README_IMAGES = 48
_MAX_IMAGE_BYTES = 6 * 1024 * 1024
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def localize_readme_images(markdown: str, *, cache_dir: Path) -> str:
    """Download README images to disk for QTextBrowser; drop broken/unsupported ones.

    Qt's markdown viewer often cannot load remote HTTPS images (blank page icons).
    Local ``file://`` paths work; SVG and failed downloads are pruned.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": UA.get("User-Agent", "IchaLaunch/0.1"),
        "Accept": "image/*,*/*;q=0.8",
    }
    token = (settings.get("github_token") or "").strip()

    url_map: dict[str, str | None] = {}  # remote -> local uri or None (prune)
    found: list[str] = []
    for m in _IMG_URL_COLLECT_RE.finditer(markdown or ""):
        u = (m.group(1) or m.group(2) or "").strip()
        if u and u not in found:
            found.append(u)

    for i, remote in enumerate(found[:_MAX_README_IMAGES]):
        if remote.startswith("data:"):
            url_map[remote] = remote
            continue
        if remote.startswith("file:"):
            url_map[remote] = remote
            continue
        lower = remote.lower().split("?", 1)[0]
        if lower.endswith(".svg") or lower.endswith(".mp4") or lower.endswith(".webm"):
            url_map[remote] = None
            continue
        try:
            req_headers = dict(headers)
            if token and may_send_github_token(remote):
                req_headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(remote, headers=req_headers, timeout=20, stream=True)
            if resp.status_code != 200:
                url_map[remote] = None
                continue
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "svg" in ctype:
                url_map[remote] = None
                continue
            ext = ".png"
            for candidate in _IMAGE_EXTS:
                if lower.endswith(candidate) or candidate[1:] in ctype:
                    ext = candidate
                    break
            if "gif" in ctype:
                ext = ".gif"
            elif "webp" in ctype:
                ext = ".webp"
            elif "jpeg" in ctype or "jpg" in ctype:
                ext = ".jpg"
            data = resp.content
            if not data or len(data) > _MAX_IMAGE_BYTES:
                url_map[remote] = None
                continue
            dest = cache_dir / f"img_{i:03d}{ext}"
            dest.write_bytes(data)
            url_map[remote] = dest.resolve().as_uri()
        except Exception as exc:
            log.debug("README image fetch failed %s: %s", remote, exc)
            url_map[remote] = None

    # Mark remaining (over cap) for prune
    for remote in found[_MAX_README_IMAGES:]:
        url_map[remote] = None

    def _replace_md(m: re.Match[str]) -> str:
        url = m.group(2)
        mapped = url_map.get(url, url)
        if mapped is None:
            return ""
        if mapped == url and url.startswith(("http://", "https://", "//")):
            # Never downloaded (shouldn't happen) — prune rather than blank icon
            return ""
        return m.group(1) + mapped + m.group(3)

    def _replace_html(m: re.Match[str]) -> str:
        url = m.group(2)
        mapped = url_map.get(url, url)
        if mapped is None:
            return ""
        if mapped == url and url.startswith(("http://", "https://", "//")):
            return ""
        return f'<img src="{mapped}" />'

    def _replace_ref(m: re.Match[str]) -> str:
        url = m.group(2)
        if url not in url_map:
            return m.group(0)
        mapped = url_map[url]
        if mapped is None:
            return ""
        return f"{m.group(1)}{mapped}{m.group(3)}"

    out = _IMG_MD_RE.sub(_replace_md, markdown)
    out = _IMG_HTML_RE.sub(_replace_html, out)
    out = _REF_IMG_RE.sub(_replace_ref, out)
    # Collapse leftover blank lines from pruned images
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def cleanup_readme_cache(cache_dir: str | Path | None) -> None:
    if not cache_dir:
        return
    try:
        shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:
        pass



def _absolute_repo_media_url(
    src: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    readme_dir: str = "",
) -> str:
    """Resolve README-relative / github.com blob paths to raw.githubusercontent.com."""
    url = (src or "").strip()
    if not url or url.startswith(("data:", "http://", "https://", "//")):
        if url.startswith("//"):
            return "https:" + url
        # github.com/owner/repo/blob/branch/path → raw
        m = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$",
            url,
            re.IGNORECASE,
        )
        if m:
            return (
                f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/"
                f"{m.group(3)}/{m.group(4)}"
            )
        return url
    if url.startswith("#"):
        return url
    # Absolute path from repo root
    if url.startswith("/"):
        rel = url.lstrip("/")
    else:
        rel = f"{readme_dir}{url}"
    # Normalize ./ and redundant segments lightly
    parts: list[str] = []
    for part in rel.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{'/'.join(parts)}"


def rewrite_readme_media(
    markdown: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    readme_dir: str = "",
) -> str:
    """Rewrite relative image/media URLs so the preview can load them."""

    def _md_sub(m: re.Match[str]) -> str:
        return m.group(1) + _absolute_repo_media_url(
            m.group(2), owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
        ) + m.group(3)

    def _html_sub(m: re.Match[str]) -> str:
        abs_url = _absolute_repo_media_url(
            m.group(2), owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
        )
        # Rebuild a minimal img tag with the absolute URL
        return f'<img src="{abs_url}" />'

    def _ref_sub(m: re.Match[str]) -> str:
        target = m.group(2)
        # Only rewrite likely image / media refs (skip pure page links without extension)
        lower = target.lower().split("?", 1)[0]
        if lower.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".mp4", ".webm")
        ) or not target.startswith(("http://", "https://", "//", "#", "mailto:")):
            target = _absolute_repo_media_url(
                target, owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
            )
        return f"{m.group(1)}{target}{m.group(3)}"

    out = _IMG_MD_RE.sub(_md_sub, markdown)
    out = _IMG_HTML_RE.sub(_html_sub, out)
    out = _REF_IMG_RE.sub(_ref_sub, out)
    return out


def preview_addon_repo(url: str) -> dict[str, Any]:
    """Fetch GitHub repo metadata for an Add-from-GitHub confirmation preview.

    Does not download the zip or install anything.
    When the URL includes a release tag, README still comes from the repo;
    install will use the tagged archive.
    """
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError(
            "Not a valid GitHub repository URL. "
            "Example: https://github.com/owner/repo or …/releases/tag/1.2.3"
        )
    owner, repo, tag = parsed.owner, parsed.repo, parsed.tag
    full = f"{owner}/{repo}"
    r = github_get(f"https://api.github.com/repos/{full}")
    data = r.json()
    branch = data.get("default_branch") or "main"
    # Resolve commit for the tag when pinned; otherwise default branch tip.
    commit_ref = tag or branch
    commit = github_latest_commit(owner, repo, branch=commit_ref)

    catalog_hit = None
    for entry in load_catalog():
        repo_url = str(entry.get("repo") or entry.get("url") or "")
        other = parse_github_url(repo_url)
        if other and other.owner.lower() == owner.lower() and other.repo.lower() == repo.lower():
            catalog_hit = entry
            break

    installed_meta = None
    for folder, meta in (settings.installed_addons or {}).items():
        if not isinstance(meta, dict):
            continue
        key = (meta.get("repository") or "").strip().lower()
        if key == full.lower():
            installed_meta = {"folder": folder, **meta}
            break
        ou = parse_github_url(str(meta.get("url") or ""))
        if ou and ou.owner.lower() == owner.lower() and ou.repo.lower() == repo.lower():
            installed_meta = {"folder": folder, **meta}
            break

    sha = str(commit.get("sha") or "")
    date = str(commit.get("date") or "")
    if "T" in date:
        date = date.split("T", 1)[0]
    msg = str(commit.get("message") or "").strip().splitlines()[0] if commit.get("message") else ""

    # README from default branch for preview; try tag ref first when pinned
    readme = None
    if tag:
        readme = fetch_repo_readme(owner, repo, branch=tag)
    if not readme:
        readme = fetch_repo_readme(owner, repo, branch=branch)

    install_url = github_tag_page_url(owner, repo, tag) if tag else github_browse_url(owner, repo)

    return {
        "kind": "addon",
        "url": install_url,
        "full_name": data.get("full_name") or full,
        "description": (data.get("description") or "").strip() or "(no description)",
        "stars": int(data.get("stargazers_count") or 0),
        "default_branch": branch,
        "tag": tag or "",
        "commit_sha": sha[:7] if sha else "",
        "commit_date": date,
        "commit_message": msg[:120],
        "catalog_name": (catalog_hit or {}).get("name"),
        "catalog_folder": (catalog_hit or {}).get("folder"),
        "already_installed": installed_meta is not None,
        "installed_folder": (installed_meta or {}).get("folder"),
        "readme_markdown": (readme or {}).get("markdown") or "",
        "readme_base_url": (readme or {}).get("base_url") or "",
        "readme_cache_dir": (readme or {}).get("cache_dir") or "",
    }


def format_addon_preview(info: dict[str, Any]) -> str:
    """Short summary above the README in the addon GitHub confirm dialog."""
    tag = str(info.get("tag") or "").strip()
    ref_line = (
        f"Tag {tag} @ {info.get('commit_sha') or '?'}"
        if tag
        else f"Branch {info.get('default_branch')} @ {info.get('commit_sha') or '?'}"
    )
    lines = [
        f"{info.get('full_name')}  ·  ★ {info.get('stars', 0)}",
        f"{info.get('url')}",
        f"{info.get('description')}",
        ref_line + (f" ({info.get('commit_date')})" if info.get("commit_date") else ""),
    ]
    if info.get("commit_message"):
        lines.append(f"Latest: {info['commit_message']}")
    if info.get("catalog_name"):
        lines.append(f"Catalog match: {info['catalog_name']}")
    if info.get("already_installed"):
        lines.append(
            f"Already installed as: {info.get('installed_folder') or '(tracked)'}"
        )
    return "\n".join(lines)


def normalize_addon_name(name: str, tag: str | None = None) -> str:
    for suffix in ("-master", "-main", "-dev"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if tag:
        # GitHub tag zips extract as ``Repo-1.5.16`` (slashes → hyphens)
        tag_suffix = "-" + str(tag).replace("/", "-").replace("\\", "-")
        if name.endswith(tag_suffix):
            name = name[: -len(tag_suffix)]
        # Also strip trailing ``-v1.2.3`` when tag is ``v1.2.3`` already handled above
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
    tag: str | None = None,
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
        "loaded": True,
    }
    if tag:
        payload["tag"] = tag
        payload["version"] = tag
    if commit_date:
        # Store YYYY-MM-DD when possible
        payload["commit_date"] = str(commit_date)[:10]
    # Fill name/description/category from turtle_wiki catalog when known
    cat, kind = resolve_catalog_entry(folder)
    kind = match_kind if match_kind else (kind or "exact")
    enriched = merge_addon_meta(folder, {**prev, **payload}, cat, match_kind=kind)
    # Prefer github tracking fields from this install
    for key in (
        "repository",
        "branch",
        "installed_commit",
        "url",
        "updated_at",
        "installed_at",
        "commit_date",
        "source",
        "tag",
        "version",
    ):
        if payload.get(key):
            enriched[key] = payload[key]
    if not tag:
        # Fresh branch install should not keep a stale pin from a prior tagged install
        enriched.pop("tag", None)
        if enriched.get("version") == prev.get("tag"):
            enriched.pop("version", None)
        if kind == "exact":
            pin = catalog_pin_tag(cat)
            if pin:
                enriched["tag"] = pin
                enriched["version"] = pin
    # Successful install/update clears Never Update unless the catalog pins this addon
    if kind == "exact" and catalog_locks_updates(cat):
        enriched["never_update"] = True
    else:
        enriched.pop("never_update", None)
    enriched["loaded"] = True
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
    tag: str | None = None,
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
            tag=tag,
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
        "Installed addon pack %s (%s) from %s/%s%s",
        primary,
        ", ".join(sorted(installed)),
        owner,
        repo,
        f"@{tag}" if tag else "",
    )
    return primary if len(installed) == 1 else f"{primary} ({len(installed)} modules)"


def install_from_github(url: str, folder_name: str | None = None, progress: ProgressCb | None = None) -> str:
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("Not a valid GitHub repository URL")
    owner, repo, tag = parsed.owner, parsed.repo, parsed.tag
    # Prefer tag stored on a prior install when reinstall URL was stripped to repo root
    if not tag and folder_name:
        prev = settings.installed_addons.get(folder_name) or {}
        tag = str(prev.get("tag") or "").strip() or None
    if not tag:
        tag = _catalog_pin_for_install(owner, repo, folder_name) or None
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game path not set")

    status_only(progress, "Fetching repository info...")
    if tag:
        meta = github_latest_commit(owner, repo, branch=tag)
        branch = str(meta.get("branch") or tag)
        commit_date = meta.get("date") or ""
        zip_url = github_tag_archive_url(owner, repo, tag)
        store_url = github_tag_page_url(owner, repo, tag)
        label = f"{owner}/{repo}@{tag}"
    else:
        meta = github_latest_commit(owner, repo)
        branch = meta["branch"]
        commit_date = meta.get("date") or ""
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        store_url = github_browse_url(owner, repo)
        label = f"{owner}/{repo}@{branch}"

    with tempfile.TemporaryDirectory(prefix="icha_addon_") as tmp:
        work = Path(tmp)
        status_only(progress, f"Downloading {label}...")
        data = download_bytes(zip_url, progress=download_bytes_cb(progress))
        extracted = extract_zip(data, work / "extract", progress=progress)
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

        addons_dir = ensure_addons_dir()

        # Always keep each TOC root's real folder name — never rename children to catalog folder.
        installed: list[str] = []
        for root in roots:
            name = normalize_addon_name(root.name, tag=tag)
            if name.lower() in ("master", "main"):
                name = repo
            dest = addons_dir / name
            if dest.exists():
                safe_remove(dest)
            copy_tree(root, dest)
            installed.append(name)

        # Single-root installs may use catalog folder_name as the destination name
        if len(installed) == 1 and folder_name:
            only = installed[0]
            wanted = normalize_addon_name(folder_name, tag=tag)
            if wanted and wanted.lower() != only.lower():
                src = addons_dir / only
                dest = addons_dir / wanted
                if dest.exists():
                    safe_remove(dest)
                src.rename(dest)
                installed = [wanted]

        preferred = None
        if folder_name:
            preferred = normalize_addon_name(folder_name, tag=tag)
        elif any(normalize_addon_name(n, tag=tag).lower() == repo.lower() for n in installed):
            preferred = next(
                n for n in installed if normalize_addon_name(n, tag=tag).lower() == repo.lower()
            )

        # Zip/release extract has no .git — record origin so Check Updates / Update
        # and a later import of a *different* repo for the same folder can use it.
        from ichalaunch.core.detect import write_git_origin

        origin_url = github_browse_url(owner, repo)
        for name in installed:
            dest = addons_dir / name
            if not dest.is_dir():
                continue
            try:
                write_git_origin(dest, origin_url)
            except (OSError, ValueError) as exc:
                log.warning("Could not write .git origin for %s: %s", name, exc)

        return _record_pack_install(
            installed=installed,
            owner=owner,
            repo=repo,
            branch=branch,
            sha=meta["sha"],
            url=store_url,
            commit_date=commit_date,
            preferred_primary=preferred,
            tag=tag,
        )

GIT_REPAIR_STATUS = "Adding missing git folder structure..."


def _addon_has_repo(meta: dict[str, Any]) -> bool:
    repo = meta.get("repository")
    if isinstance(repo, str) and "/" in repo.strip():
        return True
    url = meta.get("url") or ""
    return bool(parse_github_url(str(url)))


def _addon_git_exists(addon_dir: Path) -> bool:
    git_entry = addon_dir / ".git"
    return git_entry.exists() or git_entry.is_symlink()


def _never_update_meta(folder: str, meta: dict[str, Any], installed: dict[str, Any]) -> bool:
    if addon_ignores_updates(None, folder, meta):
        return True
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by:
        parent = installed.get(managed_by) or {}
        if addon_ignores_updates(None, managed_by, parent):
            return True
    return False


def _known_repo_url_for_repair(
    folder: str,
    meta: dict[str, Any],
    installed: dict[str, Any],
) -> str:
    """Resolve a GitHub browse URL from settings, pack parent, or catalog."""
    from ichalaunch.core.detect import resolve_catalog_entry

    url = str(meta.get("url") or "").strip()
    parsed = parse_github_url(url)
    if parsed:
        return github_browse_url(parsed.owner, parsed.repo)
    repo = str(meta.get("repository") or "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        owner, name = owner.strip(), name.strip()
        if owner and name:
            return github_browse_url(owner, name)
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by and managed_by in installed:
        inherited = _known_repo_url_for_repair(managed_by, installed[managed_by], installed)
        if inherited:
            return inherited
    cat, _kind = resolve_catalog_entry(folder)
    if cat:
        raw = str(cat.get("repo") or cat.get("url") or "").strip()
        parsed = parse_github_url(raw)
        if parsed:
            return github_browse_url(parsed.owner, parsed.repo)
    return ""


def repair_missing_addon_git_origins(
    progress: Any = None,
    *,
    addons_dir: Path | None = None,
    installed: dict[str, Any] | None = None,
) -> int:
    """Write ``.git`` for tracked addons that have a known repo but no origin on disk.

    Called from the update-check pass (not a separate button). Existing ``.git``
    folders are left alone. Returns the number of folders repaired.
    """
    from ichalaunch.addons.loadstate import addon_disk_path, resolve_unloaded_addons_dir
    from ichalaunch.core.detect import write_git_origin
    from ichalaunch.core.filesystem import is_protected_path
    from ichalaunch.game.launcher import resolve_addons_dir

    root = addons_dir if addons_dir is not None else resolve_addons_dir(create=False)
    if root is None or not root.is_dir():
        return 0
    tracked = installed if installed is not None else settings.installed_addons
    off = None if addons_dir is not None else resolve_unloaded_addons_dir(create=False)

    names: set[str] = set()
    for p in root.iterdir():
        if p.is_dir():
            names.add(p.name)
    if off is not None and off.is_dir():
        for p in off.iterdir():
            if p.is_dir():
                names.add(p.name)
    for key in tracked:
        names.add(str(key))

    to_repair: list[tuple[Path, str]] = []
    for name in sorted(names, key=str.lower):
        dest = addon_disk_path(name, addons_dir=root, unloaded_dir=off) or (root / name)
        if not dest.is_dir():
            continue
        if is_protected_path(dest):
            continue
        if _addon_git_exists(dest):
            continue
        meta = tracked.get(name) or {}
        if not meta:
            for key, val in tracked.items():
                if str(key).lower() == name.lower() and isinstance(val, dict):
                    meta = val
                    break
        if _never_update_meta(name, meta, tracked):
            continue
        origin = _known_repo_url_for_repair(name, meta, tracked)
        if not origin:
            continue
        to_repair.append((dest, origin))

    if not to_repair:
        return 0

    status_only(progress, GIT_REPAIR_STATUS)

    repaired = 0
    for dest, origin in to_repair:
        if _addon_git_exists(dest):
            continue
        try:
            write_git_origin(dest, origin)
        except (OSError, ValueError) as exc:
            log.warning("Could not repair .git origin for %s: %s", dest.name, exc)
            continue
        if _addon_git_exists(dest):
            repaired += 1
    return repaired


def recently_checked_addon_updates(cooldown_sec: int | None = None) -> bool:
    """True if an automatic scan should skip because the cooldown window is still open.

    *cooldown_sec* defaults to the Settings ``auto_scan_cooldown_minutes`` value
    (``STARTUP_CHECK_COOLDOWN_SEC`` when unset). Manual Check Updates ignores this.
    """
    if cooldown_sec is None:
        cooldown_sec = settings.auto_scan_cooldown_sec()
    raw = settings.get("last_addon_update_check")
    if not raw:
        return False
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < float(cooldown_sec)


def _mark_addon_update_check_time() -> None:
    settings.set("last_addon_update_check", time.time())


def _persist_scan_queue_progress(
    *,
    pending_folders: list[str],
    total: int,
    done_count: int,
    updates: list[dict[str, Any]],
) -> None:
    _save_scan_queue(
        {
            "pending": list(pending_folders),
            "total": int(total),
            "done_count": int(done_count),
            "found_updates": list(updates),
            "window_start": _budget_window_start,
            "window_used": int(_budget_window_used),
        }
    )


def check_addon_updates(
    *,
    respect_cooldown: bool = False,
    progress: Any = None,
) -> AddonUpdateCheckResult:
    """Check installed GitHub addons for newer commits.

    Tip SHAs come from the catalog index, git ref discovery, or Atom feeds —
    not the REST API — so a personal token is not required for a timely scan.
    REST is a last resort; if that path hits the unauthenticated 60/hour
    budget, remaining folders are queued and resumed later.

    Child modules of a multi-folder pack (managed_by) are skipped — only the
    primary entry is checked / listed for Update.
    """
    if (
        respect_cooldown
        and recently_checked_addon_updates()
        and not has_pending_addon_scan_queue()
    ):
        return AddonUpdateCheckResult(skipped_recent=True)

    from ichalaunch.addons.loadstate import addon_disk_path
    from ichalaunch.core.detect import (
        catalog_index,
        overlay_git_origin,
        read_addon_toc_version,
        read_local_git_head_sha,
        resolve_catalog_entry,
    )

    repair_missing_addon_git_origins(progress)

    cat_idx = catalog_index()
    to_check: list[tuple[str, dict[str, Any], str, str, str]] = []
    for folder, meta in settings.installed_addons.items():
        if meta.get("managed_by"):
            continue
        cat, kind = resolve_catalog_entry(folder, cat_idx, include_mods=False)
        if addon_skips_updates(folder, meta, catalog_entry=cat, catalog_kind=kind):
            continue
        # Pinned release-tag installs: reinstall re-fetches the same tag; skip branch tip checks
        if str(meta.get("tag") or "").strip():
            continue
        meta = overlay_git_origin(folder, meta)
        if not _addon_has_repo(meta):
            continue
        repo = meta.get("repository")
        if not repo or "/" not in str(repo):
            parsed = parse_github_url(str(meta.get("url") or ""))
            if not parsed:
                continue
            owner, name = parsed.owner, parsed.repo
            repo = f"{owner}/{name}"
        else:
            owner, name = str(repo).split("/", 1)
        to_check.append((folder, meta, owner, name, str(repo)))

    on_count = getattr(progress, "on_count", None) if progress is not None else None

    # Git/index/atom can finish a full pass; drop leftover REST-hour queues.
    clear_addon_scan_queue()
    try:
        from ichalaunch.addons.tip_index import refresh_tip_index

        refresh_tip_index()
    except Exception as exc:  # noqa: BLE001
        log.debug("Addon tip index refresh skipped: %s", exc)

    work = list(to_check)
    updates: list[dict[str, Any]] = []
    done_count = 0
    total = len(work)

    display_total = max(1, total if total else 1)
    if callable(on_count):
        on_count(done_count, display_total, "Scanning addons…")

    if not to_check and not work:
        clear_addon_scan_queue()
        _mark_addon_update_check_time()
        if callable(on_count):
            on_count(1, 1, "Scanning addons…")
        return AddonUpdateCheckResult(updates=[], checked_count=0, total_count=0)

    checked = 0
    rate_limited = False
    budget_exhausted = False
    remaining_work: list[tuple[str, dict[str, Any], str, str, str]] = []

    for i, (folder, meta, owner, name, repo) in enumerate(work):
        disk = addon_disk_path(folder)
        local_sha = str(meta.get("installed_commit") or "").strip()
        if not local_sha and disk is not None:
            local_sha = read_local_git_head_sha(disk) or ""
        local_ver = ""
        if disk is not None:
            local_ver = read_addon_toc_version(disk)
        if not local_ver:
            local_ver = str(meta.get("version") or "").strip()

        try:
            if local_sha:
                remote = github_remote_tip(owner, name, meta.get("branch"))
                checked += 1
                remote_sha = str(remote.get("sha") or "")
                remote_ver = ""
                branch = str(remote.get("branch") or meta.get("branch") or "")
            else:
                # Copied / no install SHA: TOC vs GitHub tag. Never treat missing
                # commit as older than remote (that marked every copied addon outdated).
                remote_ver = github_latest_version_tag(owner, name) or ""
                checked += 1
                remote_sha = ""
                remote = {"sha": "", "branch": str(meta.get("branch") or "")}
                branch = str(remote.get("branch") or "")
        except GitHubBudgetExhaustedError:
            budget_exhausted = True
            remaining_work = list(work[i:])
            break
        except GitHubRateLimitError:
            rate_limited = True
            remaining_work = list(work[i:])
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Update check failed for %s: %s", folder, exc)
            done_count += 1
            if callable(on_count):
                on_count(done_count, display_total, f"Scanning addons… {done_count}/{total}")
            continue

        if should_report_addon_update(
            local_commit=local_sha,
            remote_commit=remote_sha,
            local_version=local_ver,
            remote_version=remote_ver,
        ):
            local_label = (local_sha[:7] if local_sha else local_ver) or "?"
            remote_label = (remote_sha[:7] if remote_sha else remote_ver) or "?"
            # Replace any prior entry for this folder from an earlier batch.
            updates = [u for u in updates if str(u.get("folder") or "") != folder]
            updates.append(
                {
                    "folder": folder,
                    "repository": repo,
                    "local": local_label,
                    "remote": remote_label,
                    "url": meta.get("url") or f"https://github.com/{repo}",
                    "branch": branch,
                }
            )

        done_count += 1
        if callable(on_count):
            on_count(
                done_count,
                display_total,
                f"Scanning addons… {done_count}/{total}",
            )

        if rate_limit_exhausted():
            # REST fallback already spent the anonymous budget; finish via queue.
            rate_limited = True
            remaining_work = list(work[i + 1 :])
            break

    if remaining_work:
        _, reset_in = unauth_budget_remaining()
        if rate_limited:
            header_reset = _resume_after_sec_from_headers()
            if header_reset is not None:
                reset_in = max(reset_in, header_reset)
            elif reset_in <= 0:
                reset_in = UNAUTH_BUDGET_WINDOW_SEC
        _persist_scan_queue_progress(
            pending_folders=[t[0] for t in remaining_work],
            total=total,
            done_count=done_count,
            updates=updates,
        )
        status = format_queued_scan_status(done_count, total, reset_in)
        log.info(
            "Addon update scan queued (%d/%d done, %d update(s)); resumes in ~%ds%s",
            done_count,
            total,
            len(updates),
            reset_in,
            " (GitHub rate limit)" if rate_limited else "",
        )
        return AddonUpdateCheckResult(
            updates=list(updates),
            rate_limited=rate_limited,
            queued=True,
            checked_count=done_count,
            total_count=total,
            resume_after_sec=reset_in,
            status_message=status,
        )

    # Full pass complete (or authenticated one-shot).
    clear_addon_scan_queue()
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
            updates=list(updates),
            rate_limited=True,
            checked_count=done_count,
            total_count=total,
            status_message=RATE_LIMIT_STATUS,
        )

    return AddonUpdateCheckResult(
        updates=list(updates),
        checked_count=done_count,
        total_count=total,
    )


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
    from ichalaunch.core.detect import overlay_git_origin

    meta = overlay_git_origin(folder, meta)
    tag = str(meta.get("tag") or "").strip()
    if not tag:
        from ichalaunch.core.detect import resolve_catalog_entry

        cat, kind = resolve_catalog_entry(folder, include_mods=False)
        if kind == "exact":
            tag = catalog_pin_tag(cat)
    repo = str(meta.get("repository") or "").strip()
    if tag and "/" in repo:
        owner, name = repo.split("/", 1)
        url = github_tag_page_url(owner, name, tag)
    else:
        url = meta.get("url") or (f"https://github.com/{repo}" if repo else "")
        # Recover tag from stored URL if present
        parsed = parse_github_url(str(url))
        if parsed and parsed.tag:
            url = github_tag_page_url(parsed.owner, parsed.repo, parsed.tag)
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
    addons_dir = resolve_addons_dir(create=False)
    if addons_dir is None:
        raise FileNotFoundError("AddOns path not set")
    from ichalaunch.addons.loadstate import resolve_unloaded_addons_dir

    unloaded_dir = resolve_unloaded_addons_dir(create=False)
    for name in folders:
        for root in (addons_dir, unloaded_dir):
            if root is None:
                continue
            path = root / name
            if path.exists():
                safe_remove(path)
        settings.remove_installed_addon(name)
    # Ensure primary key cleared even if not in folders list
    settings.remove_installed_addon(target)

def load_catalog() -> list[dict[str, Any]]:
    from ichalaunch.core.paths import data_file

    path = data_file("addons.json")
    return json.loads(path.read_text(encoding="utf-8"))
