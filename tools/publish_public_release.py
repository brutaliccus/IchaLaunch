#!/usr/bin/env python3
"""Publish a signed launcher build to public brutaliccus/IchaLaunch Releases.

Signing stays on this machine (see tools/sign.py). This script only creates
or updates the public GitHub Release and uploads IchaLaunch.exe + .sig.

  python tools/sign.py --key %LOCALAPPDATA%\\IchaLaunch\\signing\\ichalaunch-key1.pem dist\\IchaLaunch.exe
  python tools/publish_public_release.py --tag v1.5.2 --exe dist\\IchaLaunch.exe --sig dist\\IchaLaunch.exe.sig

Optional: copy the live public catalog/tips/home-art into this checkout so
the next bundled fallback matches what clients already fetch:

  python tools/publish_public_release.py --sync-public-data
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO = "brutaliccus/IchaLaunch"
PUBLIC_DATA = (
    "ichalaunch/data/addons.json",
    "ichalaunch/data/addon_tips.json",
    "ichalaunch/data/home_art.json",
    "ichalaunch/data/mods.json",
    "ichalaunch/data/addons.json.sig",
    "ichalaunch/data/addon_tips.json.sig",
    "ichalaunch/data/home_art.json.sig",
    "ichalaunch/data/mods.json.sig",
)


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, check=check, text=True, cwd=ROOT)


def sync_public_data() -> int:
    """Fetch published JSON from public master into this working tree."""
    for rel in PUBLIC_DATA:
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw = subprocess.check_output(
            [
                "git",
                "show",
                f"public/master:{rel}",
            ],
            cwd=ROOT,
        )
        dest.write_bytes(raw)
        print(f"Wrote {rel} from public/master", file=sys.stderr)
    print(
        "Public catalog/tips/home-art copied into this tree. "
        "Commit them before you cut the next EXE if you want the bundled "
        "fallback to match live clients.",
        file=sys.stderr,
    )
    return 0


def publish_release(
    *,
    tag: str,
    exe: Path,
    sig: Path,
    title: str,
    notes: str,
    draft: bool,
) -> int:
    if not exe.is_file():
        print(f"missing exe: {exe}", file=sys.stderr)
        return 2
    if not sig.is_file():
        print(f"missing sig: {sig}", file=sys.stderr)
        return 2

    view = _run(
        ["gh", "release", "view", tag, "--repo", PUBLIC_REPO],
        check=False,
    )
    extra: list[str] = []
    if draft:
        extra.append("--draft")
    if notes:
        extra.extend(["--notes", notes])
    else:
        extra.append("--generate-notes")

    if view.returncode != 0:
        _run(
            [
                "gh",
                "release",
                "create",
                tag,
                str(exe),
                str(sig),
                "--repo",
                PUBLIC_REPO,
                "--title",
                title or tag,
                *extra,
            ]
        )
    else:
        _run(
            [
                "gh",
                "release",
                "upload",
                tag,
                str(exe),
                str(sig),
                "--repo",
                PUBLIC_REPO,
                "--clobber",
            ]
        )
        if not draft:
            _run(
                ["gh", "release", "edit", tag, "--repo", PUBLIC_REPO, "--draft=false"],
                check=False,
            )
    print(f"https://github.com/{PUBLIC_REPO}/releases/tag/{tag}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", help="Public release tag, e.g. v1.5.2")
    ap.add_argument("--exe", type=Path, default=ROOT / "dist" / "IchaLaunch.exe")
    ap.add_argument("--sig", type=Path, default=None, help="Signature path (default: <exe>.sig)")
    ap.add_argument("--title", default="", help="Release title (default = tag)")
    ap.add_argument("--notes", default="", help="Release notes (default: generate from commits)")
    ap.add_argument("--draft", action="store_true", help="Leave the release as a draft")
    ap.add_argument(
        "--sync-public-data",
        action="store_true",
        help="Copy live signed catalogs (JSON + .sig) from public/master",
    )
    args = ap.parse_args()

    if args.sync_public_data and not args.tag:
        _run(["git", "fetch", "public", "master"], check=False)
        return sync_public_data()

    if not args.tag:
        ap.error("--tag is required unless only --sync-public-data is set")

    if args.sync_public_data:
        _run(["git", "fetch", "public", "master"], check=False)
        sync_public_data()

    sig = args.sig if args.sig is not None else Path(str(args.exe) + ".sig")
    return publish_release(
        tag=str(args.tag).strip(),
        exe=args.exe,
        sig=sig,
        title=str(args.title or "").strip(),
        notes=str(args.notes or ""),
        draft=bool(args.draft),
    )


if __name__ == "__main__":
    raise SystemExit(main())
