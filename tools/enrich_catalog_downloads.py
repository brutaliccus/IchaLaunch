#!/usr/bin/env python3
"""Stamp latest-release download counts onto published addons.json mains.

The hourly tokened GitHub Actions job runs this against
``ichalaunch/data/addons.json``. Clients then read counts from that same file
(one GET) — they do not call GitHub per addon.

Only catalog **network-root** ``repo`` values are fetched (not nested
``forks[]``). GraphQL batches of 40; REST fallback if GraphQL is unusable.
Per-repo failures keep the last known ``release_downloads*`` fields.

Published fields (stable):
  release_downloads          int   sum of latest-release asset download_count
  release_downloads_state    ok | none | error
  release_downloads_repo     Owner/Repo the count belongs to
  release_downloads_at       UTC ISO-8601 when this row was last changed

  python tools/enrich_catalog_downloads.py
  python tools/enrich_catalog_downloads.py --dry-run
  python tools/enrich_catalog_downloads.py --limit 20

Token: ``GITHUB_TOKEN`` / ``GH_TOKEN``, else ``gh auth token``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.addons.release_downloads import (  # noqa: E402
    STATE_FIELD,
    catalog_main_repos,
    enrich_catalog_download_fields,
)

DEFAULT_CATALOG = ROOT / "ichalaunch" / "data" / "addons.json"

FetchFn = Callable[[str, str], dict[str, Any] | None]


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


def load_catalog(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("addons.json must be a JSON array")
    return [item for item in raw if isinstance(item, dict)]


def write_catalog(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _count_stamped(entries: list[dict[str, Any]]) -> tuple[int, int]:
    ok = 0
    none = 0
    for entry in entries:
        state = str(entry.get(STATE_FIELD) or "").strip().lower()
        if state == "ok":
            ok += 1
        elif state == "none":
            none += 1
    return ok, none


def enrich_catalog_file(
    path: Path,
    *,
    token: str = "",
    fetch_latest: FetchFn | None = None,
    dry_run: bool = False,
    limit: int = 0,
) -> int:
    """Read *path*, stamp catalog mains, write back unless *dry_run*.

    Returns 0 on success (including partial fetch failures that kept last
    known counts). Returns 2 when a token is required and missing.
    """
    entries = load_catalog(path)
    mains = catalog_main_repos(entries)
    if fetch_latest is None and not (token or "").strip():
        print(
            "No GitHub token. Set GITHUB_TOKEN/GH_TOKEN or run `gh auth login`.",
            file=sys.stderr,
        )
        return 2
    before = json.dumps(entries, sort_keys=True)
    enrich_catalog_download_fields(
        entries,
        token=token,
        fetch_latest=fetch_latest,
        limit=limit,
    )
    after = json.dumps(entries, sort_keys=True)
    ok, none = _count_stamped(entries)
    changed = before != after
    print(
        f"Catalog mains={len(mains)} stamped_ok={ok} stamped_none={none} "
        f"changed={changed} limit={int(limit) or 'all'}"
    )
    if dry_run:
        print("Dry run — not writing", path)
        return 0
    if changed:
        write_catalog(path, entries)
        print("Wrote", path)
    else:
        print("No download-count changes — left", path, "untouched")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--limit", type=int, default=0, help="Fetch at most N catalog mains")
    ap.add_argument("--dry-run", action="store_true", help="Do not write addons.json")
    args = ap.parse_args(argv)
    token = _resolve_token()
    return enrich_catalog_file(
        args.catalog,
        token=token,
        dry_run=bool(args.dry_run),
        limit=int(args.limit or 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
