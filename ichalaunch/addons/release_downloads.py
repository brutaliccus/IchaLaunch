"""Latest-release download counts for the Available (master) addon list.

Architecture
------------
The hourly tokened catalog job (``tools/enrich_catalog_downloads.py``) fetches
GitHub latest-release asset ``download_count`` totals for every **catalog main**
(network-root ``repo``, not nested forks) and writes them onto the published
``addons.json``. Launcher clients then do **one GET** of that list — the same
refresh they already do — and read the stamped fields. Clients must not fan out
``/releases/latest`` for catalog mains.

Published fields (stable names)
-------------------------------
``release_downloads``
    int — sum of ``assets[].download_count`` on the latest GitHub release.
``release_downloads_state``
    ``ok`` (have a latest release), ``none`` (404 / no release), ``error``
    (transient; hourly job keeps the last known count instead of wiping).
``release_downloads_repo``
    ``Owner/Repo`` the count belongs to (always the catalog main).
``release_downloads_at``
    UTC ISO-8601 timestamp of the last successful stamp for that row.

A selected fork is **not** the catalog main. The UI must not show the
master-list count as if it were the fork's (``—`` / omit).

GraphQL batches (40 repos/query) are for the tokened hourly job. The optional
on-disk cache helpers remain for tests and that job; the client path only
applies published fields.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ichalaunch.config.settings import appdata_root
from ichalaunch.core.logging_setup import log

STATE_OK = "ok"
STATE_NONE = "none"
STATE_ERROR = "error"

COUNT_FIELD = "release_downloads"
STATE_FIELD = "release_downloads_state"
REPO_FIELD = "release_downloads_repo"
AT_FIELD = "release_downloads_at"

# Re-check on the same cadence as the master catalog, but never blow the hour budget.
COUNT_TTL_SEC = 15 * 60
ERROR_TTL_SEC = 30 * 60
CACHE_VERSION = 1

UNAUTH_MAX_FETCH_PER_REFRESH = 12
UNAUTH_BUDGET_RESERVE = 8
TOKEN_MAX_FETCH_PER_REFRESH = 200
GRAPHQL_BATCH_SIZE = 40
_REST_TIMEOUT_SEC = 8
_GRAPHQL_TIMEOUT_SEC = 20
_GRAPHQL_URL = "https://api.github.com/graphql"

_GH_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+?)(?:\.git)?(?:/|$)",
    re.I,
)

# Client catalog refresh must never fan out. The hourly job passes live=True
# (or calls ``enrich_catalog_download_fields``) with an explicit token.
_live_fetch_enabled = False

# In-process cache snapshot (path-keyed so tests can isolate).
_loaded: dict[str, Any] | None = None
_loaded_path: str = ""

FetchFn = Callable[[str, str], dict[str, Any] | None]


def downloads_cache_path() -> Path:
    return appdata_root() / "addon_release_downloads.json"


def set_live_download_fetch(enabled: bool) -> None:
    global _live_fetch_enabled
    _live_fetch_enabled = bool(enabled)


def live_download_fetch_enabled() -> bool:
    return bool(_live_fetch_enabled)


@contextmanager
def live_download_fetch_disabled() -> Iterator[None]:
    prev = _live_fetch_enabled
    set_live_download_fetch(False)
    try:
        yield
    finally:
        set_live_download_fetch(prev)


def clear_release_downloads_cache() -> None:
    global _loaded, _loaded_path
    _loaded = None
    _loaded_path = ""


def empty_downloads_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "repos": {}}


def cache_repo_key(full_name: str) -> str:
    return str(full_name or "").strip().lower()


def parse_repo_full_name(raw: Any) -> str:
    """Return ``Owner/Repo`` from a catalog URL or ``owner/repo`` token."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.count("/") == 1 and "://" not in text and " " not in text:
        owner, name = text.split("/", 1)
        owner, name = owner.strip(), name.strip()
        if owner and name:
            return f"{owner}/{name}"
        return ""
    match = _GH_REPO_RE.match(text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return ""


def catalog_main_repo(entry: dict[str, Any] | None) -> str:
    """Network-root / catalog primary ``Owner/Repo`` (never a selected fork)."""
    if not isinstance(entry, dict):
        return ""
    return parse_repo_full_name(
        entry.get("repo") or entry.get("url") or entry.get("repository")
    )


def selected_fork_repo(entry: dict[str, Any] | None) -> str:
    """``Owner/Repo`` of a user-selected fork, else empty (main stays the source)."""
    if not isinstance(entry, dict):
        return ""
    for key in ("selected_repo", "selected_fork_repo"):
        raw = entry.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        parsed = parse_repo_full_name(raw)
        if parsed:
            return parsed
    for fork in entry.get("forks") or []:
        if not isinstance(fork, dict) or not fork.get("selected"):
            continue
        parsed = parse_repo_full_name(fork.get("repo") or fork.get("label"))
        if parsed:
            return parsed
    return ""


def addon_release_repo(entry: dict[str, Any] | None) -> str:
    """Repo whose latest-release downloads the row should show.

    A selected fork wins and never falls back to upstream. Catalog ``repo``
    (network root / main) is used when no fork is selected.
    """
    if not isinstance(entry, dict):
        return ""
    fork = selected_fork_repo(entry)
    if fork:
        return fork
    for key in ("selected_repo", "selected_fork_repo"):
        if key in entry:
            raw = entry.get(key)
            if raw is None or str(raw).strip() == "":
                return ""
    return catalog_main_repo(entry)


def parse_latest_release_download_count(payload: Any) -> int | None:
    """Sum ``assets[].download_count`` from a GitHub latest-release JSON body.

    Returns ``None`` when *payload* is not a release (404 body, empty, etc.).
    A real release with no assets counts as ``0``.
    """
    if not isinstance(payload, dict):
        return None
    assets = payload.get("assets")
    has_release_id = payload.get("id") is not None
    tag = str(payload.get("tag_name") or "").strip()
    if assets is None:
        if has_release_id or tag:
            return 0
        return None
    if not isinstance(assets, list):
        return None
    total = 0
    found_asset = False
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        found_asset = True
        raw = asset.get("download_count")
        if raw is None:
            continue
        try:
            total += int(raw)
        except (TypeError, ValueError):
            continue
    if found_asset or has_release_id or tag or assets == []:
        return total
    return None


def format_download_count(count: int | None) -> str:
    """Compact count for the addon row (``42``, ``1.2k``, ``12k``, ``1.2M``)."""
    if count is None:
        return "—"
    try:
        n = int(count)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return _format_scaled(n, 1000, "k")
    return _format_scaled(n, 1_000_000, "M")


def _format_scaled(n: int, unit: int, suffix: str) -> str:
    value = n / float(unit)
    if value >= 100:
        return f"{int(value)}{suffix}"
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}{suffix}"


def popularity_sort_key(entry: dict[str, Any] | None) -> tuple:
    """Known counts first (high → low), then none/zero, unknown last; name tie-break."""
    entry = entry if isinstance(entry, dict) else {}
    name = str(entry.get("name") or entry.get("folder") or "").lower()
    state = str(entry.get(STATE_FIELD) or "").strip().lower()
    raw = entry.get(COUNT_FIELD)
    if state == STATE_OK:
        try:
            return (0, -int(raw), name)
        except (TypeError, ValueError):
            return (0, 0, name)
    if state == STATE_NONE:
        return (0, 0, name)
    return (1, 0, name)


def sort_addons_by_popularity(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not entries:
        return []
    return sorted(entries, key=popularity_sort_key)


def download_badge_text(entry: dict[str, Any] | None) -> str:
    """Row label: formatted count, ``—`` if no release, empty to omit."""
    if not isinstance(entry, dict):
        return ""
    shown = _display_download_entry(entry)
    state = str(shown.get(STATE_FIELD) or "").strip().lower()
    if state == STATE_OK:
        return format_download_count(shown.get(COUNT_FIELD))
    if state == STATE_NONE:
        return "—"
    return ""


def download_badge_tooltip(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    shown = _display_download_entry(entry)
    repo = str(shown.get(REPO_FIELD) or addon_release_repo(shown) or "").strip()
    state = str(shown.get(STATE_FIELD) or "").strip().lower()
    fork_selected = bool(selected_fork_repo(shown))
    if state == STATE_OK:
        try:
            n = int(shown.get(COUNT_FIELD) or 0)
        except (TypeError, ValueError):
            n = 0
        where = repo or "this repository"
        return f"{n:,} downloads of the latest GitHub release ({where})"
    if state == STATE_NONE:
        where = repo or "this repository"
        if fork_selected:
            return (
                f"Latest-release downloads are published for the catalog repo only — "
                f"not taken from upstream for selected fork {where}."
            )
        return f"No GitHub release on {where} — download count unknown."
    return ""


def _display_download_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Copy used for badges: strip inherited main-list counts on a selected fork."""
    shown = dict(entry)
    apply_published_download_stamps([shown])
    return shown


def normalize_downloads_cache(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_downloads_cache()
    repos_raw = raw.get("repos")
    repos: dict[str, Any] = {}
    if isinstance(repos_raw, dict):
        for key, val in repos_raw.items():
            if not key or not isinstance(val, dict):
                continue
            repos[cache_repo_key(str(key))] = dict(val)
    return {"version": CACHE_VERSION, "repos": repos}


def load_downloads_cache(path: Path | None = None) -> dict[str, Any]:
    global _loaded, _loaded_path
    target = path or downloads_cache_path()
    key = str(target)
    if _loaded is not None and _loaded_path == key:
        return _loaded
    try:
        parsed = normalize_downloads_cache(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        parsed = empty_downloads_cache()
    _loaded = parsed
    _loaded_path = key
    return parsed


def write_downloads_cache(cache: dict[str, Any], path: Path | None = None) -> None:
    global _loaded, _loaded_path
    target = path or downloads_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_downloads_cache(cache)
    target.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _loaded = normalized
    _loaded_path = str(target)


def cache_lookup(cache: dict[str, Any] | None, full_name: str) -> dict[str, Any] | None:
    repos = (cache or {}).get("repos")
    if not isinstance(repos, dict):
        return None
    hit = repos.get(cache_repo_key(full_name))
    return dict(hit) if isinstance(hit, dict) else None


def cache_record_fresh(record: dict[str, Any] | None, *, now: float | None = None) -> bool:
    if not isinstance(record, dict):
        return False
    state = str(record.get("state") or "").strip().lower()
    if state not in {STATE_OK, STATE_NONE, STATE_ERROR}:
        return False
    try:
        fetched_at = float(record.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return False
    ts = time.time() if now is None else float(now)
    ttl = ERROR_TTL_SEC if state == STATE_ERROR else COUNT_TTL_SEC
    return (ts - fetched_at) < ttl


def _clear_stamp(entry: dict[str, Any]) -> None:
    entry.pop(COUNT_FIELD, None)
    entry.pop(STATE_FIELD, None)
    entry.pop(REPO_FIELD, None)
    entry.pop(AT_FIELD, None)


def apply_published_download_stamps(
    entries: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Trust master-list ``release_downloads*`` fields. Does not contact GitHub.

    Selected forks never inherit the catalog-main count. Stamps that already
    belong to the selected fork (if any) are kept.
    """
    if not entries:
        return []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fork = selected_fork_repo(entry)
        if fork:
            stamped = str(entry.get(REPO_FIELD) or "").strip()
            if stamped and cache_repo_key(stamped) == cache_repo_key(fork):
                continue
            _clear_stamp(entry)
            entry[STATE_FIELD] = STATE_NONE
            entry[REPO_FIELD] = fork
            continue
        main = catalog_main_repo(entry)
        stamped = str(entry.get(REPO_FIELD) or "").strip()
        if stamped and main and cache_repo_key(stamped) != cache_repo_key(main):
            _clear_stamp(entry)
            continue
        if main and not stamped and str(entry.get(STATE_FIELD) or "").strip():
            entry[REPO_FIELD] = main
    return entries


def stamp_entry_release_downloads(
    entry: dict[str, Any],
    cache: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the cache row for the *currently shown* repo; never reuse another repo."""
    repo = addon_release_repo(entry)
    existing_repo = str(entry.get(REPO_FIELD) or "").strip()
    if existing_repo and cache_repo_key(existing_repo) != cache_repo_key(repo):
        _clear_stamp(entry)
    if not repo:
        _clear_stamp(entry)
        return entry
    hit = cache_lookup(cache, repo)
    if not hit:
        if existing_repo and cache_repo_key(existing_repo) != cache_repo_key(repo):
            _clear_stamp(entry)
        return entry
    state = str(hit.get("state") or "").strip().lower()
    if state == STATE_ERROR:
        return entry
    entry[REPO_FIELD] = repo
    entry[STATE_FIELD] = state
    if state == STATE_OK:
        try:
            entry[COUNT_FIELD] = int(hit.get("count"))
        except (TypeError, ValueError):
            entry.pop(COUNT_FIELD, None)
            entry[STATE_FIELD] = STATE_NONE
    else:
        entry.pop(COUNT_FIELD, None)
        if state != STATE_NONE:
            _clear_stamp(entry)
    return entry


def stamp_catalog_release_downloads(
    entries: list[dict[str, Any]] | None,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not entries:
        return []
    snap = cache if cache is not None else load_downloads_cache()
    for entry in entries:
        if isinstance(entry, dict):
            stamp_entry_release_downloads(entry, snap)
    return entries


def _put_record(
    cache: dict[str, Any],
    full_name: str,
    *,
    state: str,
    count: int | None = None,
    tag: str = "",
    now: float | None = None,
) -> None:
    repos = cache.setdefault("repos", {})
    rec: dict[str, Any] = {
        "state": state,
        "fetched_at": time.time() if now is None else float(now),
    }
    if count is not None:
        rec["count"] = int(count)
    if tag:
        rec["tag"] = tag
    repos[cache_repo_key(full_name)] = rec


def catalog_release_repos(entries: list[dict[str, Any]] | None) -> list[str]:
    """Unique owner/repo values the master list would display, catalog order."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        repo = addon_release_repo(entry)
        key = cache_repo_key(repo)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(repo)
    return out


def catalog_main_repos(entries: list[dict[str, Any]] | None) -> list[str]:
    """Unique catalog network-root repos (never nested forks), catalog order."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        repo = catalog_main_repo(entry)
        key = cache_repo_key(repo)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(repo)
    return out


def stamp_timestamp(now: float | None = None) -> str:
    """UTC ISO-8601 (``...Z``) for ``release_downloads_at``."""
    if now is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(float(now), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_unchanged(
    entry: dict[str, Any],
    *,
    state: str,
    count: int,
    repo: str,
) -> bool:
    if str(entry.get(STATE_FIELD) or "").strip().lower() != state:
        return False
    if cache_repo_key(str(entry.get(REPO_FIELD) or "")) != cache_repo_key(repo):
        return False
    if state == STATE_OK:
        try:
            return int(entry.get(COUNT_FIELD)) == int(count)
        except (TypeError, ValueError):
            return False
    return True


def stamp_catalog_from_payloads(
    entries: list[dict[str, Any]] | None,
    payloads: dict[str, dict[str, Any] | None],
    *,
    errors: set[str] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Write fetch results onto catalog mains. Failed repos keep last known."""
    if not entries:
        return []
    err = {cache_repo_key(x) for x in (errors or set())}
    by_key: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        repo = catalog_main_repo(entry)
        if not repo:
            continue
        by_key.setdefault(cache_repo_key(repo), []).append((repo, entry))

    at = stamp_timestamp(now)
    for full, payload in payloads.items():
        key = cache_repo_key(full)
        if not key or key in err:
            continue
        if payload is None:
            state, count = STATE_NONE, 0
        else:
            parsed = parse_latest_release_download_count(payload)
            if parsed is None:
                state, count = STATE_NONE, 0
            else:
                state, count = STATE_OK, parsed
        for display_repo, entry in by_key.get(key, []):
            if _stamp_unchanged(entry, state=state, count=count, repo=display_repo):
                continue
            entry[REPO_FIELD] = display_repo
            entry[STATE_FIELD] = state
            entry[COUNT_FIELD] = count
            entry[AT_FIELD] = at
    return entries


def enrich_catalog_download_fields(
    entries: list[dict[str, Any]] | None,
    *,
    token: str = "",
    fetch_latest: FetchFn | None = None,
    now: float | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Stamp every catalog MAIN with latest-release totals (hourly job).

    Partial failures keep last known ``release_downloads*`` fields. Nested
    ``forks[]`` are not fetched.
    """
    if not entries:
        return []
    mains = catalog_main_repos(entries)
    if limit and int(limit) > 0:
        mains = mains[: int(limit)]

    payloads: dict[str, dict[str, Any] | None] = {}
    errors: set[str] = set()
    if fetch_latest is not None:
        for full in mains:
            split = _split_owner_repo(full)
            if not split:
                continue
            try:
                payloads[full] = fetch_latest(*split)
            except Exception as exc:  # noqa: BLE001
                log.debug("Catalog download enrich failed for %s: %s", full, exc)
                errors.add(cache_repo_key(full))
    else:
        tok = (token or "").strip()
        if not tok:
            log.info("Catalog download enrich skipped: no GitHub token")
            return entries
        _fetch_enrich_live(mains, tok, payloads, errors)
    return stamp_catalog_from_payloads(entries, payloads, errors=errors, now=now)


def _fetch_enrich_live(
    mains: list[str],
    token: str,
    payloads: dict[str, dict[str, Any] | None],
    errors: set[str],
) -> None:
    from ichalaunch.addons.github import GitHubRateLimitError

    remaining = list(mains)
    while remaining:
        chunk = remaining[:GRAPHQL_BATCH_SIZE]
        remaining = remaining[GRAPHQL_BATCH_SIZE:]
        try:
            got = _fetch_latest_via_graphql(chunk, token=token)
        except GitHubRateLimitError:
            log.info("Catalog download GraphQL rate-limited; keeping last known")
            for full in chunk + remaining:
                errors.add(cache_repo_key(full))
            return
        if got is None:
            remaining = chunk + remaining
            break
        for full, payload in got.items():
            payloads[full] = payload
    if not remaining:
        return
    for index, full in enumerate(remaining):
        split = _split_owner_repo(full)
        if not split:
            continue
        try:
            payloads[full] = _fetch_latest_via_rest(*split, token=token)
        except GitHubRateLimitError:
            log.info("Catalog download REST rate-limited; keeping last known")
            for rest in remaining[index:]:
                errors.add(cache_repo_key(rest))
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("Catalog download REST failed for %s: %s", full, exc)
            errors.add(cache_repo_key(full))


def repos_needing_fetch(
    repos: list[str],
    cache: dict[str, Any],
    *,
    now: float | None = None,
) -> list[str]:
    missing: list[str] = []
    stale: list[tuple[float, str]] = []
    for repo in repos:
        hit = cache_lookup(cache, repo)
        if hit is None:
            missing.append(repo)
        elif not cache_record_fresh(hit, now=now):
            try:
                fetched_at = float(hit.get("fetched_at") or 0)
            except (TypeError, ValueError):
                fetched_at = 0.0
            stale.append((fetched_at, repo))
    stale.sort(key=lambda item: item[0])
    return missing + [repo for _ts, repo in stale]


def _split_owner_repo(full_name: str) -> tuple[str, str] | None:
    text = str(full_name or "").strip()
    if "/" not in text:
        return None
    owner, name = text.split("/", 1)
    owner, name = owner.strip(), name.strip()
    if not owner or not name:
        return None
    return owner, name


def _bearer_headers(token: str, *, accept: str) -> dict[str, str]:
    return {
        "User-Agent": "IchaLaunch-catalog-downloads/1.0",
        "Accept": accept,
        "Authorization": f"Bearer {token}",
    }


def _fetch_latest_via_rest(
    owner: str,
    repo: str,
    *,
    token: str = "",
) -> dict[str, Any] | None:
    from ichalaunch.addons.github import (
        GITHUB_TOKEN_REJECTED_MSG,
        GitHubRateLimitError,
        _github_api_get,
        _looks_like_rate_limit,
    )

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    tok = (token or "").strip()
    if tok:
        import requests

        r = requests.get(
            url,
            headers=_bearer_headers(tok, accept="application/vnd.github+json"),
            timeout=_REST_TIMEOUT_SEC,
        )
    else:
        r = _github_api_get(url, timeout=_REST_TIMEOUT_SEC)
    if _looks_like_rate_limit(r):
        raise GitHubRateLimitError("GitHub rate limit hit while fetching release downloads")
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        raise RuntimeError(GITHUB_TOKEN_REJECTED_MSG)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else None


def _apply_payload(cache: dict[str, Any], full_name: str, payload: dict[str, Any] | None) -> None:
    if payload is None:
        _put_record(cache, full_name, state=STATE_NONE)
        return
    count = parse_latest_release_download_count(payload)
    if count is None:
        _put_record(cache, full_name, state=STATE_NONE)
        return
    tag = str(payload.get("tag_name") or "").strip()
    _put_record(cache, full_name, state=STATE_OK, count=count, tag=tag)


def _graphql_headers(token: str = "") -> dict[str, str]:
    tok = (token or "").strip()
    if tok:
        return _bearer_headers(tok, accept="application/json")
    from ichalaunch.addons.github import github_headers

    return github_headers(_GRAPHQL_URL)


def _fetch_latest_via_graphql(
    full_names: list[str],
    *,
    token: str = "",
) -> dict[str, dict[str, Any] | None] | None:
    """Batch-fetch latest releases. None means GraphQL is unusable (fall back to REST)."""
    import requests

    from ichalaunch.addons.github import (
        GitHubRateLimitError,
        _looks_like_rate_limit,
        has_github_token,
    )

    tok = (token or "").strip()
    if not full_names:
        return {}
    if not tok and not has_github_token():
        return None
    parts: list[str] = []
    for i, full in enumerate(full_names):
        split = _split_owner_repo(full)
        if not split:
            continue
        owner, name = split
        parts.append(
            f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ "
            "latestRelease { tagName releaseAssets(first: 100) { nodes { downloadCount } } } }"
        )
    if not parts:
        return {}
    query = "query { " + " ".join(parts) + " }"
    try:
        r = requests.post(
            _GRAPHQL_URL,
            json={"query": query},
            headers=_graphql_headers(tok),
            timeout=_GRAPHQL_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        log.debug("Release-download GraphQL failed: %s", exc)
        return None
    if _looks_like_rate_limit(r):
        raise GitHubRateLimitError("GitHub rate limit hit while fetching release downloads")
    if r.status_code in (401, 403):
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    out: dict[str, dict[str, Any] | None] = {}
    for i, full in enumerate(full_names):
        if _split_owner_repo(full) is None:
            continue
        node = data.get(f"r{i}")
        if not isinstance(node, dict):
            out[full] = None
            continue
        latest = node.get("latestRelease")
        if not isinstance(latest, dict):
            out[full] = None
            continue
        assets = []
        raw_assets = latest.get("releaseAssets")
        nodes = raw_assets.get("nodes") if isinstance(raw_assets, dict) else None
        if isinstance(nodes, list):
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                assets.append({"download_count": item.get("downloadCount")})
        out[full] = {
            "tag_name": latest.get("tagName") or "",
            "id": 1,
            "assets": assets,
        }
    return out


def _refresh_budget() -> int:
    from ichalaunch.addons.github import has_github_token, unauth_budget_remaining

    if has_github_token():
        return TOKEN_MAX_FETCH_PER_REFRESH
    remaining, _reset = unauth_budget_remaining()
    return max(0, min(UNAUTH_MAX_FETCH_PER_REFRESH, remaining - UNAUTH_BUDGET_RESERVE))


def refresh_release_downloads(
    entries: list[dict[str, Any]] | None,
    *,
    live: bool | None = None,
    cache_path: Path | None = None,
    fetch_latest: FetchFn | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Stamp cached counts, then optionally fetch stale/missing latest-release totals."""
    if not entries:
        return []
    cache = load_downloads_cache(cache_path)
    stamp_catalog_release_downloads(entries, cache)
    do_live = live_download_fetch_enabled() if live is None else bool(live)
    if not do_live:
        return entries

    wanted = repos_needing_fetch(catalog_release_repos(entries), cache, now=now)
    if not wanted:
        return entries

    if fetch_latest is not None:
        limit = len(wanted)
    else:
        try:
            limit = _refresh_budget()
        except Exception:  # noqa: BLE001
            limit = UNAUTH_MAX_FETCH_PER_REFRESH if live_download_fetch_enabled() else 0
    batch = wanted[:limit]
    if not batch:
        log.debug("Release-download fetch skipped (budget empty; using cache)")
        return entries

    dirty = False
    try:
        if fetch_latest is not None:
            for full in batch:
                split = _split_owner_repo(full)
                if not split:
                    continue
                try:
                    payload = fetch_latest(*split)
                except Exception as exc:  # noqa: BLE001
                    log.debug("Release-download fetch failed for %s: %s", full, exc)
                    _put_record(cache, full, state=STATE_ERROR, now=now)
                    dirty = True
                    continue
                _apply_payload(cache, full, payload)
                dirty = True
        else:
            dirty = _fetch_live_batch(cache, batch, now=now)
    except Exception as exc:  # noqa: BLE001
        log.debug("Release-download refresh stopped: %s", exc)

    if dirty:
        try:
            write_downloads_cache(cache, cache_path)
        except OSError as exc:
            log.debug("Could not persist release-download cache: %s", exc)
    stamp_catalog_release_downloads(entries, cache)
    return entries


def _fetch_live_batch(
    cache: dict[str, Any],
    batch: list[str],
    *,
    now: float | None = None,
) -> bool:
    from ichalaunch.addons.github import (
        GitHubBudgetExhaustedError,
        GitHubRateLimitError,
        has_github_token,
    )

    dirty = False
    remaining = list(batch)
    graphql_ok = False
    if has_github_token():
        while remaining:
            chunk = remaining[:GRAPHQL_BATCH_SIZE]
            remaining = remaining[GRAPHQL_BATCH_SIZE:]
            try:
                got = _fetch_latest_via_graphql(chunk)
            except GitHubRateLimitError:
                log.info("Release-download GraphQL hit GitHub rate limit; keeping cache")
                remaining = []
                break
            if got is None:
                remaining = chunk + remaining
                break
            graphql_ok = True
            for full, payload in got.items():
                _apply_payload(cache, full, payload)
                dirty = True
        if not remaining:
            return dirty
        if not graphql_ok:
            # Don't walk the whole token batch over REST (too slow / noisy).
            remaining = remaining[: max(UNAUTH_MAX_FETCH_PER_REFRESH, 20)]

    for full in remaining:
        split = _split_owner_repo(full)
        if not split:
            continue
        owner, name = split
        try:
            payload = _fetch_latest_via_rest(owner, name)
        except GitHubBudgetExhaustedError:
            log.debug("Release-download REST stopped: unauthenticated budget empty")
            break
        except GitHubRateLimitError:
            log.info("Release-download REST hit GitHub rate limit; keeping cache")
            break
        except Exception as exc:  # noqa: BLE001
            log.debug("Release-download REST failed for %s: %s", full, exc)
            _put_record(cache, full, state=STATE_ERROR, now=now)
            dirty = True
            continue
        _apply_payload(cache, full, payload)
        dirty = True
    return dirty
