#!/usr/bin/env python3
"""Enrich addons.json ``forks[]`` with non-archived GitHub forks.

Discovers forks for each catalog ``repo``, skips archived/disabled forks and
any fork already present as the primary ``repo`` or in ``forks[]``, then merges
new entries under the nested ``forks[]`` schema (``label``, ``repo``).

  python tools/enrich_catalog_forks.py
  python tools/enrich_catalog_forks.py --limit 50
  python tools/enrich_catalog_forks.py --dry-run
  python tools/enrich_catalog_forks.py --max-age-days 1825
  python tools/enrich_catalog_forks.py --resume

Token: ``GITHUB_TOKEN`` / ``GH_TOKEN``, else ``gh auth token``.
Cache/resume state: ``tools/.cache/fork_enrich_cache.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "ichalaunch" / "data" / "addons.json"
CACHE_DIR = ROOT / "tools" / ".cache"
CACHE_PATH = CACHE_DIR / "fork_enrich_cache.json"

UA = {
    "User-Agent": "IchaLaunch-fork-enrich/1.0",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
PER_PAGE = 100
MAX_PAGES = 20


def _resolve_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    try:
        out = subprocess.check_output(
            ["gh", "auth", "token"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return (out or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _canonical_owner_repo(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) lowercased from a GitHub URL or owner/repo string."""
    text = (url or "").strip()
    if not text:
        return None
    if text.count("/") == 1 and "://" not in text and " " not in text:
        owner, name = text.split("/", 1)
        owner, name = owner.strip(), name.strip().removesuffix(".git")
        if owner and name:
            return owner.lower(), name.lower()
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host not in {"github.com", "www.github.com"}:
        return None
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0].lower(), parts[1].removesuffix(".git").lower()


def _browse_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def _parse_pushed_at(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "repos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "repos": {}}
    if not isinstance(data, dict):
        return {"version": 1, "repos": {}}
    repos = data.get("repos")
    if not isinstance(repos, dict):
        data["repos"] = {}
    return data


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_catalog(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("addons.json must be a JSON array")
    return [item for item in raw if isinstance(item, dict)]


def write_catalog(path: Path, catalog: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class GitHubClient:
    def __init__(self, token: str, *, sleep: float = 0.05) -> None:
        self.token = token
        self.sleep = max(0.0, float(sleep))
        self.session = requests.Session()
        self.session.headers.update(UA)
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.remaining: int | None = None
        self.reset_epoch: int | None = None

    def _note_rate(self, r: requests.Response) -> None:
        rem = r.headers.get("X-RateLimit-Remaining")
        reset = r.headers.get("X-RateLimit-Reset")
        try:
            self.remaining = int(rem) if rem is not None else self.remaining
        except ValueError:
            pass
        try:
            self.reset_epoch = int(reset) if reset is not None else self.reset_epoch
        except ValueError:
            pass

    def _wait_if_needed(self) -> None:
        if self.remaining is not None and self.remaining <= 5 and self.reset_epoch:
            now = int(time.time())
            wait = max(0, self.reset_epoch - now) + 2
            if wait > 0:
                print(
                    f"Rate limit low (remaining={self.remaining}); sleeping {wait}s…",
                    file=sys.stderr,
                )
                time.sleep(wait)

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
        self._wait_if_needed()
        r = self.session.get(url, params=params, timeout=45)
        self._note_rate(r)
        if r.status_code == 403 and "rate limit" in (r.text or "").lower():
            reset = self.reset_epoch or (int(time.time()) + 60)
            wait = max(5, reset - int(time.time()) + 2)
            print(f"Hit rate limit; sleeping {wait}s…", file=sys.stderr)
            time.sleep(wait)
            r = self.session.get(url, params=params, timeout=45)
            self._note_rate(r)
        if self.sleep:
            time.sleep(self.sleep)
        return r

    def list_forks(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Return raw fork payloads (dicts) for owner/repo."""
        out: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            r = self.get(
                f"https://api.github.com/repos/{owner}/{repo}/forks",
                params={"per_page": PER_PAGE, "sort": "stargazers", "page": page},
            )
            if r.status_code in (404, 451):
                return []
            if r.status_code == 401:
                raise SystemExit("GitHub token rejected (HTTP 401).")
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            out.extend(item for item in data if isinstance(item, dict))
            if len(data) < PER_PAGE:
                break
        return out


def _fork_score(item: dict[str, Any]) -> tuple[int, float]:
    stars = int(item.get("stargazers_count") or 0)
    watchers = int(item.get("watchers_count") or item.get("watchers") or 0)
    pushed = _parse_pushed_at(str(item.get("pushed_at") or ""))
    ts = pushed.timestamp() if pushed else 0.0
    return (stars + watchers, ts)


def filter_active_forks(
    items: list[dict[str, Any]],
    *,
    primary: tuple[str, str],
    existing: set[tuple[str, str]],
    max_age_days: int | None,
    min_stars: int,
    max_forks: int,
) -> list[dict[str, str]]:
    """Build catalog ``forks[]`` rows from API fork payloads."""
    now = datetime.now(timezone.utc)
    candidates: list[tuple[tuple[int, float], dict[str, str]]] = []

    for item in items:
        if bool(item.get("archived")):
            continue
        if bool(item.get("disabled")):
            continue
        full = str(item.get("full_name") or "").strip()
        html = str(item.get("html_url") or "").strip()
        key = _canonical_owner_repo(html or full)
        if not key:
            continue
        if key == primary or key in existing:
            continue

        stars = int(item.get("stargazers_count") or 0)
        if min_stars > 0 and stars < min_stars:
            # Still allow recent activity when min_stars is set via age window.
            pushed = _parse_pushed_at(str(item.get("pushed_at") or ""))
            if max_age_days is None or pushed is None:
                continue
            age = (now - pushed).total_seconds() / 86400.0
            if age > max_age_days:
                continue
        elif max_age_days is not None:
            pushed = _parse_pushed_at(str(item.get("pushed_at") or ""))
            if pushed is None:
                continue
            age = (now - pushed).total_seconds() / 86400.0
            # Prefer recent OR starred; keep if within window OR has stars.
            if age > max_age_days and stars < 1:
                continue

        owner, name = key
        # Preserve original casing from API when available.
        api_full = full if "/" in full else f"{owner}/{name}"
        api_owner, api_name = api_full.split("/", 1)
        # Owner-only label when fork repo name matches primary; else owner/repo.
        label = api_owner if name == primary[1] else api_full
        row = {"label": label, "repo": _browse_url(api_owner, api_name)}
        candidates.append((_fork_score(item), row))

    candidates.sort(key=lambda t: (-t[0][0], -t[0][1], t[1]["label"].lower()))
    if max_forks > 0:
        candidates = candidates[:max_forks]
    return [row for _, row in candidates]


def existing_fork_keys(entry: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    primary = _canonical_owner_repo(str(entry.get("repo") or entry.get("url") or ""))
    if primary:
        keys.add(primary)
    for fork in entry.get("forks") or []:
        if not isinstance(fork, dict):
            continue
        key = _canonical_owner_repo(str(fork.get("repo") or fork.get("url") or ""))
        if key:
            keys.add(key)
    return keys


def merge_forks(
    entry: dict[str, Any],
    new_rows: list[dict[str, str]],
) -> int:
    """Append *new_rows* into entry['forks']; return count added."""
    if not new_rows:
        return 0
    existing = existing_fork_keys(entry)
    forks = list(entry.get("forks") or [])
    if not isinstance(forks, list):
        forks = []
    added = 0
    for row in new_rows:
        key = _canonical_owner_repo(row["repo"])
        if not key or key in existing:
            continue
        forks.append({"label": row["label"], "repo": row["repo"]})
        existing.add(key)
        added += 1
    if added:
        entry["forks"] = forks
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--cache", type=Path, default=CACHE_PATH)
    ap.add_argument("--limit", type=int, default=0, help="Process at most N catalog entries")
    ap.add_argument("--offset", type=int, default=0, help="Skip first N catalog entries")
    ap.add_argument("--sleep", type=float, default=0.05, help="Pause after each API call")
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=1825,
        help="Prefer forks pushed within N days (or with stars). 0 = all non-archived",
    )
    ap.add_argument("--min-stars", type=int, default=0, help="Minimum stargazers (0 = off)")
    ap.add_argument(
        "--max-forks-per-entry",
        type=int,
        default=40,
        help="Cap newly considered forks per entry (0 = unlimited)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Do not write addons.json")
    ap.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Reuse fork list cache (default)",
    )
    ap.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore cached fork lists and refetch",
    )
    ap.add_argument(
        "--checkpoint-every",
        type=int,
        default=25,
        help="Write catalog every N processed entries (0 = only at end)",
    )
    ap.add_argument(
        "--force-refresh",
        action="store_true",
        help="Refetch even when cache has this owner/repo",
    )
    args = ap.parse_args()

    token = _resolve_token()
    if not token:
        print(
            "No GitHub token. Set GITHUB_TOKEN/GH_TOKEN or run `gh auth login`.",
            file=sys.stderr,
        )
        return 2

    catalog = load_catalog(args.catalog)
    cache = load_cache(args.cache) if not args.no_resume else {"version": 1, "repos": {}}
    cache_repos: dict[str, Any] = cache.setdefault("repos", {})

    client = GitHubClient(token, sleep=args.sleep)
    max_age = None if int(args.max_age_days) <= 0 else int(args.max_age_days)

    # Unique primary repos in catalog order (first occurrence wins).
    work: list[tuple[int, str, str]] = []  # (catalog_index, owner, repo)
    seen_primary: set[str] = set()
    for idx, entry in enumerate(catalog):
        primary = _canonical_owner_repo(str(entry.get("repo") or entry.get("url") or ""))
        if not primary:
            continue
        key = f"{primary[0]}/{primary[1]}"
        if key in seen_primary:
            continue
        seen_primary.add(key)
        work.append((idx, primary[0], primary[1]))

    if args.offset:
        work = work[max(0, int(args.offset)) :]
    if args.limit and args.limit > 0:
        work = work[: int(args.limit)]

    processed = 0
    api_fetches = 0
    cache_hits = 0
    entries_touched = 0
    forks_added = 0
    forks_seen_active = 0
    errors = 0

    print(
        f"Enriching {len(work)} unique primary repos "
        f"(catalog size {len(catalog)}, dry_run={args.dry_run})"
    )

    for i, (idx, owner, repo) in enumerate(work, start=1):
        cache_key = f"{owner}/{repo}".lower()
        entry = catalog[idx]
        primary = (owner, repo)

        try:
            if (
                not args.force_refresh
                and not args.no_resume
                and cache_key in cache_repos
                and isinstance(cache_repos[cache_key], dict)
                and "forks" in cache_repos[cache_key]
            ):
                raw_forks = cache_repos[cache_key].get("forks") or []
                cache_hits += 1
            else:
                raw_forks = client.list_forks(owner, repo)
                api_fetches += 1
                # Store a slim cache payload (fields we need for filtering).
                slim = []
                for item in raw_forks:
                    slim.append(
                        {
                            "full_name": item.get("full_name"),
                            "html_url": item.get("html_url"),
                            "archived": bool(item.get("archived")),
                            "disabled": bool(item.get("disabled")),
                            "pushed_at": item.get("pushed_at"),
                            "stargazers_count": item.get("stargazers_count"),
                            "watchers_count": item.get("watchers_count"),
                        }
                    )
                cache_repos[cache_key] = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "forks": slim,
                }
                raw_forks = slim
                save_cache(args.cache, cache)

            existing = existing_fork_keys(entry)
            rows = filter_active_forks(
                list(raw_forks) if isinstance(raw_forks, list) else [],
                primary=primary,
                existing=existing,
                max_age_days=max_age,
                min_stars=int(args.min_stars),
                max_forks=int(args.max_forks_per_entry),
            )
            forks_seen_active += len(rows)
            added = merge_forks(entry, rows)
            if added:
                entries_touched += 1
                forks_added += added
            processed += 1
            rem = f" remaining={client.remaining}" if client.remaining is not None else ""
            print(
                f"[{i}/{len(work)}] {cache_key}: "
                f"+{added} forks ({len(rows)} candidates){rem}"
            )
        except requests.HTTPError as exc:
            errors += 1
            print(f"[{i}/{len(work)}] FAIL {cache_key}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[{i}/{len(work)}] FAIL {cache_key}: {exc}", file=sys.stderr)

        every = int(args.checkpoint_every)
        if (
            not args.dry_run
            and every > 0
            and processed > 0
            and processed % every == 0
        ):
            write_catalog(args.catalog, catalog)
            save_cache(args.cache, cache)
            print(f"  checkpoint: wrote {args.catalog} (+{forks_added} forks so far)")

    if not args.dry_run:
        write_catalog(args.catalog, catalog)
        save_cache(args.cache, cache)

    with_forks = sum(1 for e in catalog if e.get("forks"))
    nested = sum(len(e.get("forks") or []) for e in catalog)
    print(
        f"Done. processed={processed} api_fetches={api_fetches} cache_hits={cache_hits} "
        f"entries_touched={entries_touched} forks_added={forks_added} "
        f"errors={errors} catalog_with_forks={with_forks} nested_forks={nested}"
    )
    return 0 if errors == 0 or forks_added > 0 or processed > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
