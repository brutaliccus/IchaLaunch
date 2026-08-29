#!/usr/bin/env python3
"""Check or refresh the SHA-256 pins in ``ichalaunch/data/mods.json``.

Usage:
    python tools/pin_mods.py --check              # report drift, exit 1 if any
    python tools/pin_mods.py --update superwow    # re-pin one mod
    python tools/pin_mods.py --update --all       # re-pin everything that drifted

A pin says "these are the exact bytes a maintainer downloaded, tested and
chose". Nothing here tests anything, so ``--update`` is not a rubber stamp: it
records what upstream is serving *right now*. Run the game with the new build
before you commit the new number, or the pin certifies malware just as faithfully
as it certifies a fix.

Why drift is expected, and is not a bug
---------------------------------------
Ten catalog entries resolve through ``github_release_latest``, meaning the
launcher asks GitHub for whatever the newest release is. Pinning those does not
freeze them; it means that when an upstream publishes something new, the install
**refuses** until a maintainer looks at it and re-pins. That refusal is the
feature. ``--check`` is how you find out it is time to look, ideally on a
schedule rather than from a player's bug report.

Two upstreams reuse a rolling tag rather than cutting a new one
---------------------------------------------------------------
``tubtubs/vanilla-tweaks`` publishes to a tag literally named ``tag``, and
``balakethelock/SuperWoW`` to one named ``Release``. Those tags are moved, so
their bytes can change without any version number changing anywhere. They are
the two most likely to drift and the two where drift is least visible upstream.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ichalaunch.mods.verify import expected_digest  # noqa: E402

CATALOG = Path(__file__).resolve().parent.parent / "ichalaunch" / "data" / "mods.json"
UA = {"User-Agent": "IchaLaunch-pin-mods/1.0"}
TIMEOUT = 300


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _latest_asset(source: dict[str, Any]) -> tuple[str, str, bytes]:
    """(tag, asset_name, bytes) for the asset this source would install today."""
    repo = source["repo"]
    want = (source.get("asset_contains") or "").lower()
    skip = (source.get("asset_not_contains") or "").lower()
    release = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest"))
    for asset in release.get("assets", []):
        name = asset["name"].lower()
        if want and want not in name:
            continue
        if skip and skip in name:
            continue
        return release.get("tag_name", "?"), asset["name"], _get(asset["browser_download_url"])
    raise SystemExit(f"{repo}: no release asset matching {want!r}")


def _fixed_url_asset(source: dict[str, Any]) -> tuple[str, str, bytes]:
    url = source["url"]
    return "-", url.rsplit("/", 1)[-1].split("?")[0], _get(url)


def resolve(source: dict[str, Any]) -> tuple[str, str, bytes]:
    stype = source.get("type")
    if stype == "github_release_latest":
        return _latest_asset(source)
    if stype in ("raw", "github_release", "github_zip", "raw_zip"):
        return _fixed_url_asset(source)
    raise SystemExit(f"cannot resolve source type {stype!r} from here")


PINNABLE = ("github_release_latest", "github_release", "raw", "github_zip", "raw_zip")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="mod ids to act on (default: every pinned entry)")
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--update", action="store_true", help="write refreshed pins")
    ap.add_argument("--all", action="store_true", help="with --update, re-pin every drifted entry")
    args = ap.parse_args()
    if not (args.check or args.update):
        ap.error("pass --check or --update")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    wanted = set(args.ids)
    drifted: list[str] = []
    unpinned: list[str] = []
    changed = 0

    for mod in catalog:
        mod_id = str(mod.get("id") or "")
        source = mod.get("source")
        if not isinstance(source, dict) or source.get("type") not in PINNABLE:
            continue
        if wanted and mod_id not in wanted:
            continue
        pinned = expected_digest(source)
        if pinned is None and not (args.update and (args.all or wanted)):
            unpinned.append(mod_id)
            continue
        try:
            tag, name, blob = resolve(source)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"  {mod_id:24s} UNREACHABLE  {exc}")
            drifted.append(mod_id)
            continue
        actual = hashlib.sha256(blob).hexdigest()
        if pinned == actual:
            print(f"  {mod_id:24s} ok           {name} ({tag})")
            continue
        drifted.append(mod_id)
        state = "NEW" if pinned is None else "DRIFTED"
        print(f"  {mod_id:24s} {state:12s} {name} ({tag})")
        if pinned:
            print(f"  {'':24s}   pinned {pinned}")
        print(f"  {'':24s}   actual {actual}")
        if args.update and (args.all or wanted):
            source["sha256"] = actual
            source["pinned_tag"] = tag
            changed += 1

    if unpinned:
        print(f"\nNot pinned yet ({len(unpinned)}): {', '.join(sorted(unpinned))}")
    if changed:
        CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nRewrote {CATALOG.name} with {changed} refreshed pin(s).")
        print("Test the game with these builds before committing.")
    if drifted and not changed:
        print(f"\n{len(drifted)} entr(y/ies) do not match their pin: {', '.join(drifted)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
