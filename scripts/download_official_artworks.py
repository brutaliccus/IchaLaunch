"""Refresh HOME gallery URLs from https://ravencraft.io/artworks.

Hashed Vite asset names change when the site rebuilds. This writes the current
URLs into ``ichalaunch/data/home_art.json``. Images are downloaded at runtime
into appdata, not packed into the exe.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import requests

UA = {"User-Agent": "IchaLaunch/1.0"}
PAGE = "https://ravencraft.io/artworks"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ichalaunch" / "data" / "home_art.json"
SKIP_SUBSTR = (
    "favicon",
    "icon-152",
    "icon-192",
    "nav_bottom",
    "web_logo",
)


def stem_key(url: str) -> str:
    name = Path(unquote(urlsplit(url).path)).name
    base = re.sub(r"-[A-Za-z0-9_-]{6,}\.(webp|jpe?g|png)$", "", name, flags=re.I)
    if base == name:
        base = Path(name).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_").lower()


def encode_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, quote(parts.path, safe="/-_.~"), parts.query, parts.fragment)
    )


def preferred_url(variants: list[str]) -> str:
    def rank(url: str) -> int:
        low = url.lower()
        if low.endswith((".jpg", ".jpeg")):
            return 0
        if low.endswith(".png"):
            return 1
        return 2

    return encode_url(sorted(variants, key=rank)[0])


def main() -> int:
    response = requests.get(PAGE, headers=UA, timeout=60)
    response.raise_for_status()
    found = sorted(
        set(
            re.findall(
                r"https://ravencraft\.io/build/assets/[^\"'<>]+?\.(?:webp|jpe?g|png)",
                response.text,
                re.I,
            )
        )
    )
    by_stem: dict[str, list[str]] = defaultdict(list)
    for url in found:
        if any(skip in url.lower() for skip in SKIP_SUBSTR):
            continue
        by_stem[stem_key(url)].append(url)
    urls = {stem: preferred_url(variants) for stem, variants in by_stem.items()}

    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    slides = raw.get("slides") if isinstance(raw, dict) else raw
    if not isinstance(slides, list):
        raise RuntimeError(f"{MANIFEST} has no slides list")

    updated = 0
    missing: list[str] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        slide_id = str(slide.get("id") or "")
        if slide_id == "zaeya_first_60":
            continue
        url = urls.get(slide_id)
        if not url:
            missing.append(slide_id)
            continue
        if slide.get("url") != url:
            slide["url"] = url
            updated += 1

    MANIFEST.write_text(
        json.dumps({"slides": slides}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Updated {updated} URLs in {MANIFEST.relative_to(ROOT)} ({len(urls)} live artworks)")
    if missing:
        print("No ravencraft.io asset for:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
