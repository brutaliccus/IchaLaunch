"""Download official RavenCraft artworks for the HOME art scroll."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

UA = {"User-Agent": "IchaLaunch/1.0"}
PAGE = "https://ravencraft.io/artworks"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ichalaunch" / "ui" / "theme" / "official_artworks"

SKIP_SUBSTR = (
    "favicon",
    "icon-152",
    "icon-192",
    "nav_bottom",
    "web_logo",
)


def stem_key(url: str) -> str:
    name = Path(unquote(urlparse(url).path)).name
    base = re.sub(r"-[A-Za-z0-9_-]{6,}\.(webp|jpe?g|png)$", "", name, flags=re.I)
    if base == name:
        base = Path(name).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_").lower()


def main() -> int:
    r = requests.get(PAGE, headers=UA, timeout=60)
    r.raise_for_status()
    html = r.text
    # Paths may contain spaces (e.g. "Deep in the Green-….webp")
    urls = sorted(
        set(
            re.findall(
                r"https://ravencraft\.io/build/assets/[^\"'<>]+?\.(?:webp|jpe?g|png)",
                html,
                re.I,
            )
        )
    )
    by_stem: dict[str, list[str]] = {}
    for u in urls:
        if any(s in u.lower() for s in SKIP_SUBSTR):
            continue
        by_stem.setdefault(stem_key(u), []).append(u)

    def rank(u: str) -> int:
        low = u.lower()
        if low.endswith((".jpg", ".jpeg")):
            return 0
        if low.endswith(".png"):
            return 1
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    chosen: list[tuple[str, str]] = []
    for stem, variants in sorted(by_stem.items()):
        variants.sort(key=rank)
        chosen.append((stem, variants[0]))

    print(f"Downloading {len(chosen)} artworks -> {OUT}")
    manifest: list[str] = []
    for stem, url in chosen:
        ext = Path(unquote(urlparse(url).path)).suffix.lower() or ".jpg"
        if ext == ".jpeg":
            ext = ".jpg"
        dest = OUT / f"{stem}{ext}"
        if dest.is_file() and dest.stat().st_size > 10_000:
            print(f"  skip existing {dest.name}")
            manifest.append(dest.name)
            continue
        print(f"  {dest.name} <- {url}")
        resp = requests.get(url, headers=UA, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        manifest.append(dest.name)

    (OUT / "manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"Done: {len(manifest)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
