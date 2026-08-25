"""Build ichalaunch/data/addon_tips.json from the addon + mod catalogs.

Uses git smart-HTTP ref discovery (not the GitHub REST API). Run locally to
test the catalog index path, or from CI to publish a shared file.

  python tools/build_addon_tips.py
  python tools/build_addon_tips.py --limit 20
  python tools/build_addon_tips.py --sleep 0.15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.addons.git_refs import fetch_git_refs  # noqa: E402
from ichalaunch.addons.catalog import load_bundled_catalog  # noqa: E402
from ichalaunch.addons.github import (  # noqa: E402
    catalog_locks_updates,
    parse_github_url,
)
from ichalaunch.addons.tip_index import (  # noqa: E402
    build_index,
    repo_entry_from_refs,
    write_index_file,
)

OUT = ROOT / "ichalaunch" / "data" / "addon_tips.json"
_MODS_JSON = ROOT / "ichalaunch" / "data" / "mods.json"
_GITHUB_URL_RE = re.compile(
    r"https?://(?:raw\.)?github(?:usercontent)?\.com/([^/]+)/([^/]+)/",
    re.I,
)


def _add_repo(
    seen: set[str],
    out: list[tuple[str, str]],
    owner: str,
    name: str,
) -> None:
    owner = (owner or "").strip()
    name = (name or "").strip()
    if not owner or not name:
        return
    key = f"{owner.lower()}/{name.lower()}"
    if key in seen:
        return
    seen.add(key)
    out.append((owner, name))


def _catalog_repos(*, include_locked: bool = False) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entry in load_bundled_catalog():
        if not include_locked and catalog_locks_updates(entry):
            continue
        parsed = parse_github_url(str(entry.get("repo") or entry.get("url") or ""))
        if not parsed:
            continue
        _add_repo(seen, out, parsed.owner, parsed.repo)
        # Nested catalog forks share tip SHAs with the primary row's picker.
        for fork in entry.get("forks") or []:
            if not isinstance(fork, dict):
                continue
            f_parsed = parse_github_url(str(fork.get("repo") or fork.get("url") or ""))
            if f_parsed:
                _add_repo(seen, out, f_parsed.owner, f_parsed.repo)
    return out


def _mod_catalog_repos() -> list[tuple[str, str]]:
    """GitHub repos referenced by client mods (fixes, tweaks, HD patches, …)."""
    if not _MODS_JSON.is_file():
        return []
    try:
        mods = json.loads(_MODS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(mods, list):
        return []

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for mod in mods:
        if not isinstance(mod, dict):
            continue
        source = mod.get("source")
        if not isinstance(source, dict):
            continue
        repo = str(source.get("repo") or "").strip()
        if repo and "/" in repo:
            owner, name = repo.split("/", 1)
            _add_repo(seen, out, owner, name)
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        parsed = parse_github_url(url)
        if parsed:
            _add_repo(seen, out, parsed.owner, parsed.repo)
            continue
        m = _GITHUB_URL_RE.match(url)
        if m:
            _add_repo(seen, out, m.group(1), m.group(2))
    return out


def _merge_repos(
    addon_repos: list[tuple[str, str]],
    mod_repos: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    seen = {f"{o.lower()}/{n.lower()}" for o, n in addon_repos}
    merged = list(addon_repos)
    for owner, name in mod_repos:
        key = f"{owner.lower()}/{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        merged.append((owner, name))
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build catalog tip-SHA index from addons.json + mods.json"
    )
    ap.add_argument("--output", type=Path, default=OUT, help="JSON output path")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N unique repos (0 = all)")
    ap.add_argument("--sleep", type=float, default=0.05, help="Pause between repo fetches")
    ap.add_argument(
        "--include-locked",
        action="store_true",
        help="Also index pinned / updates:false catalog entries",
    )
    ap.add_argument(
        "--addons-only",
        action="store_true",
        help="Skip mods.json repos (addon catalog only)",
    )
    ap.add_argument(
        "--mods-only",
        action="store_true",
        help="Only mods.json repos (skip addons.json)",
    )
    args = ap.parse_args()

    if args.addons_only and args.mods_only:
        print("Choose at most one of --addons-only and --mods-only", file=sys.stderr)
        return 2

    addon_repos = [] if args.mods_only else _catalog_repos(include_locked=bool(args.include_locked))
    mod_repos = [] if args.addons_only else _mod_catalog_repos()
    repos = _merge_repos(addon_repos, mod_repos)
    if args.limit and args.limit > 0:
        repos = repos[: int(args.limit)]

    entries: dict[str, dict] = {}
    failed = 0
    for i, (owner, name) in enumerate(repos, start=1):
        key = f"{owner}/{name}".lower()
        refs = fetch_git_refs(owner, name, use_cache=False)
        if refs is None or not (refs.head_sha or refs.branches):
            failed += 1
            print(f"[{i}/{len(repos)}] FAIL {key}", file=sys.stderr)
        else:
            entries[key] = repo_entry_from_refs(refs)
            print(
                f"[{i}/{len(repos)}] {key} "
                f"{entries[key].get('sha', '')[:10]} "
                f"{entries[key].get('default_branch', '')} "
                f"tag={entries[key].get('latest_tag', '')}"
            )
        if args.sleep and i < len(repos):
            time.sleep(max(0.0, float(args.sleep)))

    index = build_index(entries, source="git-upload-pack")
    write_index_file(Path(args.output), index)
    print(
        f"Wrote {len(entries)} repos ({failed} failed, "
        f"{len(addon_repos)} addon + {len(mod_repos)} mod sources) -> {args.output}"
    )
    return 0 if entries else 1


if __name__ == "__main__":
    raise SystemExit(main())
