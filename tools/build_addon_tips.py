"""Build ichalaunch/data/addon_tips.json from the addon catalog.

Uses git smart-HTTP ref discovery (not the GitHub REST API). Run locally to
test the catalog index path, or from CI to publish a shared file.

  python tools/build_addon_tips.py
  python tools/build_addon_tips.py --limit 20
  python tools/build_addon_tips.py --sleep 0.15
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.addons.git_refs import fetch_git_refs  # noqa: E402
from ichalaunch.addons.github import (  # noqa: E402
    catalog_locks_updates,
    load_catalog,
    parse_github_url,
)
from ichalaunch.addons.tip_index import (  # noqa: E402
    build_index,
    repo_entry_from_refs,
    write_index_file,
)

OUT = ROOT / "ichalaunch" / "data" / "addon_tips.json"


def _catalog_repos(*, include_locked: bool = False) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entry in load_catalog():
        if not include_locked and catalog_locks_updates(entry):
            continue
        parsed = parse_github_url(str(entry.get("repo") or entry.get("url") or ""))
        if not parsed:
            continue
        key = f"{parsed.owner.lower()}/{parsed.repo.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append((parsed.owner, parsed.repo))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build addon tip-SHA index from the catalog")
    ap.add_argument("--output", type=Path, default=OUT, help="JSON output path")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N unique repos (0 = all)")
    ap.add_argument("--sleep", type=float, default=0.05, help="Pause between repo fetches")
    ap.add_argument(
        "--include-locked",
        action="store_true",
        help="Also index pinned / updates:false catalog entries",
    )
    args = ap.parse_args()

    repos = _catalog_repos(include_locked=bool(args.include_locked))
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
            print(f"[{i}/{len(repos)}] {key} {entries[key].get('sha', '')[:10]} {entries[key].get('default_branch', '')}")
        if args.sleep and i < len(repos):
            time.sleep(max(0.0, float(args.sleep)))

    index = build_index(entries, source="git-upload-pack")
    write_index_file(Path(args.output), index)
    print(f"Wrote {len(entries)} repos ({failed} failed) -> {args.output}")
    return 0 if entries else 1


if __name__ == "__main__":
    raise SystemExit(main())
