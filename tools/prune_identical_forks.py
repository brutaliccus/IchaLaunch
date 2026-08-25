#!/usr/bin/env python3
"""Remove catalog entries / nested forks that are not ahead of upstream.

Targets incorrectly approved identical forks (Compare ``ahead_by == 0`` or
behind-only vs the network-root default branch). Same diverge rule as
``enrich_catalog_forks.py`` and the addon-submit Worker.

By default only **top-level** ``source=community`` rows that are GitHub forks
are checked (the catalog-approve path). Deleted repos (HTTP 404) are also
removed. Pass ``--nested`` / ``--nested-only`` to drop nested ``forks[]``
entries that fail the ahead check vs their primary listing.

  python tools/prune_identical_forks.py --dry-run
  python tools/prune_identical_forks.py
  python tools/prune_identical_forks.py --source any
  python tools/prune_identical_forks.py --nested-only

Token: ``GITHUB_TOKEN`` / ``GH_TOKEN``, else ``gh auth token``.
Cache: ``tools/.cache/fork_prune_cache.json``.
Removal report: ``tools/.cache/fork_prune_removed.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from enrich_catalog_forks import (  # noqa: E402
    CACHE_DIR,
    DEFAULT_CATALOG,
    GitHubClient,
    _canonical_owner_repo,
    _resolve_token,
    is_fork_ahead,
    load_cache,
    load_catalog,
    save_cache,
    write_catalog,
)

PRUNE_CACHE_PATH = CACHE_DIR / "fork_prune_cache.json"
REMOVED_REPORT_PATH = CACHE_DIR / "fork_prune_removed.json"


def _strip_repo_url(url: str) -> str:
    text = (url or "").strip()
    for marker in ("/releases", "/tree/", "/commit/", "/archive/"):
        idx = text.lower().find(marker)
        if idx > 0:
            text = text[:idx]
            break
    return text.rstrip("/")


def _repo_url_key(url: str) -> tuple[str, str] | None:
    return _canonical_owner_repo(_strip_repo_url(url))


def _owner_name_cased(url: str) -> tuple[str, str] | None:
    """Owner/repo with original URL casing when possible."""
    text = _strip_repo_url(url)
    key = _canonical_owner_repo(text)
    if not key:
        return None
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1].removesuffix(".git")
    return key[0], key[1]


def network_root(meta: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (owner, name, default_branch) for the fork network root."""
    if not isinstance(meta, dict):
        return None
    node = meta
    for key in ("source", "parent"):
        upstream = meta.get(key)
        if isinstance(upstream, dict) and upstream.get("full_name"):
            node = upstream
            break
    full = str(node.get("full_name") or "").strip()
    if "/" not in full:
        return None
    api_owner, api_name = full.split("/", 1)
    branch = str(node.get("default_branch") or meta.get("default_branch") or "").strip()
    return api_owner, api_name, branch or "main"


def fetch_repo_meta(
    client: GitHubClient, owner: str, repo: str
) -> tuple[str, dict[str, Any] | None]:
    """Return (status, meta). status is ok|missing|forbidden|error."""
    r = client.get(f"https://api.github.com/repos/{owner}/{repo}")
    if r.status_code in (404, 451):
        return "missing", None
    if r.status_code == 403:
        return "forbidden", None
    if r.status_code == 401:
        raise SystemExit("GitHub token rejected (HTTP 401).")
    if not r.ok:
        return "error", None
    data = r.json()
    if isinstance(data, dict):
        return "ok", data
    return "error", None


def slim_repo_meta(fetched: dict[str, Any]) -> dict[str, Any]:
    slim: dict[str, Any] = {
        "full_name": fetched.get("full_name"),
        "fork": bool(fetched.get("fork")),
        "default_branch": fetched.get("default_branch"),
        "archived": bool(fetched.get("archived")),
        "disabled": bool(fetched.get("disabled")),
        "parent": None,
        "source": None,
    }
    for ukey in ("parent", "source"):
        up = fetched.get(ukey)
        if isinstance(up, dict) and up.get("full_name"):
            slim[ukey] = {
                "full_name": up.get("full_name"),
                "default_branch": up.get("default_branch"),
            }
    return slim


def get_or_fetch_meta(
    client: GitHubClient,
    owner: str,
    name: str,
    meta_cache: dict[str, Any],
    *,
    force: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Return (status, slim_meta). Cached misses use status ``missing``."""
    key = f"{owner}/{name}".lower()
    cached = meta_cache.get(key) if not force else None
    if isinstance(cached, dict) and cached.get("_status") == "missing":
        return "missing", None
    if isinstance(cached, dict) and "fork" in cached:
        return "ok", cached
    status, fetched = fetch_repo_meta(client, owner, name)
    if status == "missing":
        meta_cache[key] = {"_status": "missing"}
        return "missing", None
    if fetched is None:
        return status, None
    slim = slim_repo_meta(fetched)
    meta_cache[key] = slim
    return "ok", slim


def get_compare_slim(
    client: GitHubClient,
    *,
    root_owner: str,
    root_repo: str,
    root_branch: str,
    fork_owner: str,
    fork_name: str,
    fork_branch: str,
    compare_cache: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    """Slim compare dict, or None when Compare failed (keep entry)."""
    cache_key = f"{fork_owner}/{fork_name}".lower()
    cached = compare_cache.get(cache_key) if not force else None
    if isinstance(cached, dict) and "ahead_by" in cached:
        if cached.get("status") == "unavailable":
            return None
        return cached

    base = (root_branch or "main").strip() or "main"
    head_branch = (fork_branch or base).strip() or base
    head = f"{fork_owner}:{head_branch}"
    payload = client.compare(root_owner, root_repo, base=base, head=head)
    checked = datetime.now(timezone.utc).isoformat()
    if payload is None:
        compare_cache[cache_key] = {
            "ahead_by": 0,
            "behind_by": 0,
            "status": "unavailable",
            "checked_at": checked,
        }
        return None
    slim = {
        "ahead_by": int(payload.get("ahead_by") or 0),
        "behind_by": int(payload.get("behind_by") or 0),
        "status": str(payload.get("status") or ""),
        "checked_at": checked,
        "root": f"{root_owner}/{root_repo}",
        "root_branch": base,
        "fork_branch": head_branch,
    }
    compare_cache[cache_key] = slim
    return slim


def should_prune_fork(
    client: GitHubClient,
    owner: str,
    name: str,
    *,
    meta_cache: dict[str, Any],
    compare_cache: dict[str, Any],
    force: bool = False,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Return (prune?, reason, compare_slim_or_none).

    Non-forks → keep. Compare failures → keep (conservative).
    Forks with ahead_by == 0 / behind-only → prune.
    """
    status, meta = get_or_fetch_meta(client, owner, name, meta_cache, force=force)
    if status == "missing":
        return True, "repo_missing", None
    if meta is None:
        return False, f"meta_{status}", None
    if not bool(meta.get("fork")):
        return False, "not_a_fork", None

    root = network_root(meta)
    if not root:
        return False, "no_root", None
    root_owner, root_repo, root_branch = root
    if root_owner.lower() == owner.lower() and root_repo.lower() == name.lower():
        return False, "is_network_root", None

    fork_branch = str(meta.get("default_branch") or root_branch or "main").strip() or "main"
    slim = get_compare_slim(
        client,
        root_owner=root_owner,
        root_repo=root_repo,
        root_branch=root_branch or "main",
        fork_owner=owner,
        fork_name=name,
        fork_branch=fork_branch,
        compare_cache=compare_cache,
        force=force,
    )
    if slim is None:
        return False, "compare_unavailable", None
    if is_fork_ahead(slim):
        return False, f"ahead_by={slim.get('ahead_by')}", slim
    return (
        True,
        (
            f"not_ahead status={slim.get('status')} "
            f"ahead_by={slim.get('ahead_by')} behind_by={slim.get('behind_by')}"
        ),
        slim,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--cache", type=Path, default=PRUNE_CACHE_PATH)
    ap.add_argument("--report", type=Path, default=REMOVED_REPORT_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--nested",
        action="store_true",
        help="Also prune nested forks[] that are not ahead of their primary",
    )
    ap.add_argument(
        "--nested-only",
        action="store_true",
        help="Skip top-level pruning; only check nested forks[]",
    )
    ap.add_argument(
        "--source",
        default="community",
        help="Top-level source filter (default: community). Use '' or 'any' for all sources",
    )
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--limit", type=int, default=0, help="Max top-level candidates to check")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--checkpoint-every", type=int, default=25)
    args = ap.parse_args()

    token = _resolve_token()
    if not token:
        print(
            "No GitHub token. Set GITHUB_TOKEN/GH_TOKEN or run `gh auth login`.",
            file=sys.stderr,
        )
        return 2

    source_filter = (args.source or "").strip()
    if source_filter.lower() in {"", "any", "*"}:
        source_filter = ""

    catalog = load_catalog(args.catalog)
    cache = load_cache(args.cache)
    meta_cache: dict[str, Any] = cache.setdefault("meta", {})
    compare_cache: dict[str, Any] = cache.setdefault("compare", {})
    if not isinstance(meta_cache, dict):
        meta_cache = {}
        cache["meta"] = meta_cache
    if not isinstance(compare_cache, dict):
        compare_cache = {}
        cache["compare"] = compare_cache

    client = GitHubClient(token, sleep=args.sleep)

    removed_top: list[dict[str, Any]] = []
    kept_ahead: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    removed_nested: list[dict[str, Any]] = []

    candidates: list[tuple[int, dict[str, Any]]] = []
    if not args.nested_only:
        for idx, entry in enumerate(catalog):
            if source_filter and str(entry.get("source") or "") != source_filter:
                continue
            if not _repo_url_key(str(entry.get("repo") or entry.get("url") or "")):
                continue
            candidates.append((idx, entry))

        if args.limit and args.limit > 0:
            candidates = candidates[: int(args.limit)]

    print(
        f"Checking {len(candidates)} top-level entries "
        f"(source_filter={source_filter or 'any'}, dry_run={args.dry_run}, "
        f"nested={args.nested or args.nested_only})"
    )

    drop_indices: set[int] = set()
    checks = 0
    for i, (idx, entry) in enumerate(candidates, start=1):
        cased = _owner_name_cased(str(entry.get("repo") or ""))
        if not cased:
            continue
        api_owner, api_name = cased
        prune, reason, slim = should_prune_fork(
            client,
            api_owner,
            api_name,
            meta_cache=meta_cache,
            compare_cache=compare_cache,
            force=bool(args.force_refresh),
        )
        checks += 1
        row = {
            "index": idx,
            "name": entry.get("name"),
            "repo": entry.get("repo"),
            "source": entry.get("source"),
            "reason": reason,
            "compare": slim,
        }
        rem = f" remaining={client.remaining}" if client.remaining is not None else ""
        if prune:
            drop_indices.add(idx)
            removed_top.append(row)
            print(f"[{i}/{len(candidates)}] REMOVE {api_owner}/{api_name}: {reason}{rem}")
        elif reason.startswith("ahead_by="):
            kept_ahead.append(row)
            print(f"[{i}/{len(candidates)}] KEEP   {api_owner}/{api_name}: {reason}{rem}")
        else:
            skipped.append(row)
            if reason != "not_a_fork":
                print(f"[{i}/{len(candidates)}] SKIP   {api_owner}/{api_name}: {reason}{rem}")

        every = int(args.checkpoint_every)
        if every > 0 and checks % every == 0:
            save_cache(args.cache, cache)

    do_nested = bool(args.nested or args.nested_only)
    if do_nested:
        print("Checking nested forks[] …")
        for idx, entry in enumerate(catalog):
            if idx in drop_indices:
                continue
            forks = entry.get("forks")
            if not isinstance(forks, list) or not forks:
                continue
            primary = _owner_name_cased(str(entry.get("repo") or entry.get("url") or ""))
            if not primary:
                continue
            p_owner, p_name = primary
            p_status, p_meta = get_or_fetch_meta(
                client, p_owner, p_name, meta_cache, force=bool(args.force_refresh)
            )
            if p_meta is None:
                continue
            root_owner, root_repo = p_owner, p_name
            root_branch = str(p_meta.get("default_branch") or "main").strip() or "main"
            primary_key = (p_owner.lower(), p_name.lower())

            keep_forks: list[Any] = []
            changed = False
            for fork in forks:
                if not isinstance(fork, dict):
                    keep_forks.append(fork)
                    continue
                fcased = _owner_name_cased(str(fork.get("repo") or ""))
                if not fcased:
                    keep_forks.append(fork)
                    continue
                fo, fn = fcased
                # Pinned same-repo release variants stay.
                if fork.get("pin_release") and (fo.lower(), fn.lower()) == primary_key:
                    keep_forks.append(fork)
                    continue

                f_status, fmeta = get_or_fetch_meta(
                    client, fo, fn, meta_cache, force=bool(args.force_refresh)
                )
                if f_status == "missing":
                    changed = True
                    removed_nested.append(
                        {
                            "primary": entry.get("repo"),
                            "fork": fork.get("repo"),
                            "label": fork.get("label"),
                            "reason": "repo_missing",
                            "compare": None,
                        }
                    )
                    continue
                if fmeta is None:
                    keep_forks.append(fork)
                    continue
                fork_branch = (
                    str(fmeta.get("default_branch") or root_branch or "main").strip() or "main"
                )
                slim = get_compare_slim(
                    client,
                    root_owner=root_owner,
                    root_repo=root_repo,
                    root_branch=root_branch or "main",
                    fork_owner=fo,
                    fork_name=fn,
                    fork_branch=fork_branch,
                    compare_cache=compare_cache,
                    force=bool(args.force_refresh),
                )
                if slim is None or is_fork_ahead(slim):
                    keep_forks.append(fork)
                    continue
                changed = True
                removed_nested.append(
                    {
                        "primary": entry.get("repo"),
                        "fork": fork.get("repo"),
                        "label": fork.get("label"),
                        "reason": (
                            f"not_ahead status={slim.get('status')} "
                            f"ahead_by={slim.get('ahead_by')}"
                        ),
                        "compare": slim,
                    }
                )
            if changed:
                if keep_forks:
                    entry["forks"] = keep_forks
                else:
                    entry.pop("forks", None)

            if len(removed_nested) and len(removed_nested) % 25 == 0:
                save_cache(args.cache, cache)
                print(f"  nested removed so far: {len(removed_nested)} remaining={client.remaining}")

    new_catalog = [e for i, e in enumerate(catalog) if i not in drop_indices]

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "source_filter": source_filter or None,
        "nested": bool(do_nested),
        "removed_top_count": len(removed_top),
        "kept_ahead_count": len(kept_ahead),
        "skipped_count": len(skipped),
        "removed_nested_count": len(removed_nested),
        "catalog_before": len(catalog),
        "catalog_after": len(new_catalog),
        "removed_top": removed_top,
        "kept_ahead": kept_ahead,
        "skipped": skipped,
        "removed_nested": removed_nested,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    save_cache(args.cache, cache)

    if not args.dry_run:
        write_catalog(args.catalog, new_catalog)

    print(
        f"Done. removed_top={len(removed_top)} kept_ahead={len(kept_ahead)} "
        f"skipped={len(skipped)} removed_nested={len(removed_nested)} "
        f"catalog {len(catalog)} -> {len(new_catalog)} "
        f"report={args.report}"
    )
    if removed_top:
        print("Sample removed:")
        for row in removed_top[:15]:
            print(f"  - {row.get('repo')} ({row.get('reason')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
