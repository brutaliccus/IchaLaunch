"""Home artwork manifest with remote refresh.

Authored in ``ichalaunch/data/home_art.json`` (same publish path as the addon
catalog). Launchers prefer a fresh remote copy, then an appdata cache, then
the bundled file shipped with the build.

Gallery stills come from ravencraft.io (see ``url`` on each slide) and are
cached under appdata. They are not packed into the exe. Image URLs are fetched
only from allowlisted hosts (this repo's raw GitHub and ravencraft.io).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ichalaunch.config.settings import appdata_root, settings
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file, theme_file

DEFAULT_HOME_ART_URL = (
    "https://raw.githubusercontent.com/brutaliccus/IchaLaunch/master/"
    "ichalaunch/data/home_art.json"
)
HOME_ART_TTL_SEC = 15 * 60
_FETCH_TIMEOUT_SEC = 8
_IMAGE_TIMEOUT_SEC = 20
_UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/json"}
_IMAGE_UA = {"User-Agent": "IchaLaunch/0.1"}

# HTTPS image hosts. raw.githubusercontent.com is further restricted to this repo.
_RAVENCRAFT_HOSTS = frozenset({"ravencraft.io", "www.ravencraft.io"})
_GITHUB_RAW_HOST = "raw.githubusercontent.com"
_GITHUB_RAW_PREFIX = "/brutaliccus/IchaLaunch/"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_ART_DIR = "official_artworks"
_LEGACY_DIR = "talent_bgs"

# In-process snapshot: (monotonic_loaded_at, manifest, source_label)
_loaded: tuple[float, dict[str, Any], str] | None = None


def home_art_cache_path() -> Path:
    return appdata_root() / "home_art.json"


def home_art_image_dir() -> Path:
    return appdata_root() / "home_art"


def bundled_home_art_path() -> Path:
    return data_file("home_art.json")


def home_art_url() -> str:
    override = str(settings.get("home_art_url") or "").strip()
    return override or DEFAULT_HOME_ART_URL


def empty_home_art() -> dict[str, Any]:
    return {"slides": []}


def safe_art_filename(name: str) -> str:
    """Basename only; reject path traversal and non-image suffixes."""
    base = Path(str(name or "")).name.strip()
    if not base or base in {".", ".."}:
        return ""
    if Path(base).suffix.lower() not in _IMAGE_EXTS:
        return ""
    return base


def image_url_allowed(url: str) -> bool:
    """True only for HTTPS ravencraft.io or this repo's raw GitHub paths."""
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in _RAVENCRAFT_HOSTS:
        return True
    if host == _GITHUB_RAW_HOST:
        path = parts.path or ""
        return path.startswith(_GITHUB_RAW_PREFIX)
    return False


def _as_float(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _as_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def normalize_slide(raw: Any) -> dict[str, Any] | None:
    """One rotation entry. ``hold`` is a multiplier of the 11s base hold."""
    if not isinstance(raw, dict):
        return None
    image = safe_art_filename(str(raw.get("image") or raw.get("file") or ""))
    url = str(raw.get("url") or "").strip()
    if not image and url:
        image = safe_art_filename(url)
    if not image:
        return None
    slide_id = str(raw.get("id") or Path(image).stem).strip()
    if not slide_id:
        return None
    fit = str(raw.get("fit") or "cover").strip().lower()
    if fit not in {"width", "cover"}:
        fit = "cover"
    frame = safe_art_filename(str(raw.get("frame") or ""))
    frame_url = str(raw.get("frame_url") or "").strip()
    return {
        "id": slide_id,
        "image": image,
        "url": url,
        "frame": frame,
        "frame_url": frame_url,
        "hold": _as_float(raw.get("hold"), 1.0),
        "fit": fit,
        "nudge_x": _as_int(raw.get("nudge_x"), 0),
        "nudge_y": _as_int(raw.get("nudge_y"), 0),
        "shrink_w": _as_int(raw.get("shrink_w"), 0),
    }


def normalize_home_art(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        slides_raw = raw
    elif isinstance(raw, dict):
        slides_raw = raw.get("slides")
        if not isinstance(slides_raw, list):
            return empty_home_art()
    else:
        return empty_home_art()
    slides: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in slides_raw:
        slide = normalize_slide(item)
        if slide is None:
            continue
        key = slide["id"].lower()
        if key in seen:
            continue
        seen.add(key)
        slides.append(slide)
    return {"slides": slides}


def parse_home_art_text(text: str) -> dict[str, Any]:
    try:
        return normalize_home_art(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty_home_art()


def load_home_art_file(path: Path) -> dict[str, Any]:
    try:
        return parse_home_art_text(path.read_text(encoding="utf-8"))
    except OSError:
        return empty_home_art()


def home_art_slide_count(manifest: dict[str, Any] | None) -> int:
    slides = (manifest or {}).get("slides")
    return len(slides) if isinstance(slides, list) else 0


def write_home_art_file(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def load_bundled_home_art() -> dict[str, Any]:
    return load_home_art_file(bundled_home_art_path())


def _remember(manifest: dict[str, Any], source: str) -> dict[str, Any]:
    global _loaded
    _loaded = (time.monotonic(), manifest, source)
    return manifest


def current_home_art_source() -> str:
    if _loaded is not None:
        return _loaded[2]
    return ""


def fetch_remote_home_art(url: str | None = None) -> dict[str, Any] | None:
    target = (url or home_art_url()).strip()
    if not target:
        return None
    try:
        r = requests.get(target, headers=_UA, timeout=_FETCH_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.info("Home art fetch failed: %s", exc)
        return None
    if r.status_code != 200 or not (r.text or "").strip():
        log.info("Home art HTTP %s from %s", r.status_code, target)
        return None
    manifest = parse_home_art_text(r.text)
    if home_art_slide_count(manifest) == 0:
        return None
    return manifest


def refresh_home_art(*, force: bool = False) -> dict[str, Any]:
    """Load the best available manifest (remote → appdata → bundled)."""
    global _loaded
    if _loaded is not None and not force:
        age = time.monotonic() - _loaded[0]
        if age < HOME_ART_TTL_SEC and home_art_slide_count(_loaded[1]) > 0:
            return _loaded[1]

    remote = fetch_remote_home_art()
    if remote is not None:
        try:
            write_home_art_file(home_art_cache_path(), remote)
        except OSError as exc:
            log.debug("Could not cache home art: %s", exc)
        return _remember(remote, "remote")

    cached = load_home_art_file(home_art_cache_path())
    if home_art_slide_count(cached) > 0:
        return _remember(cached, "cache")

    bundled = load_bundled_home_art()
    if home_art_slide_count(bundled) > 0:
        return _remember(bundled, "bundled")

    return _remember(empty_home_art(), "empty")


def load_home_art() -> dict[str, Any]:
    """Current manifest without a network round-trip (memory → cache → bundled)."""
    if _loaded is not None and home_art_slide_count(_loaded[1]) > 0:
        return _loaded[1]
    cached = load_home_art_file(home_art_cache_path())
    if home_art_slide_count(cached) > 0:
        return _remember(cached, "cache")
    bundled = load_bundled_home_art()
    if home_art_slide_count(bundled) > 0:
        return _remember(bundled, "bundled")
    return empty_home_art()


def clear_home_art_cache() -> None:
    global _loaded
    _loaded = None


def cached_image_path(filename: str) -> Path:
    return home_art_image_dir() / safe_art_filename(filename)


def bundled_image_path(filename: str) -> Path | None:
    name = safe_art_filename(filename)
    if not name:
        return None
    root = theme_file(name)
    if root.is_file():
        return root
    for folder in (_ART_DIR, _LEGACY_DIR):
        bundled = theme_file(folder, name)
        if bundled.is_file():
            return bundled
    return None


def resolve_image_path(filename: str) -> Path | None:
    """Prefer a downloaded cache copy, then a bundled / theme file."""
    name = safe_art_filename(filename)
    if not name:
        return None
    cached = cached_image_path(name)
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    return bundled_image_path(name)


def download_home_art_image(url: str, dest_name: str) -> Path | None:
    """GET *url* into the appdata image cache. Rejects non-allowlisted hosts."""
    name = safe_art_filename(dest_name)
    if not name:
        return None
    if not image_url_allowed(url):
        log.info("Home art image rejected (host not allowlisted): %s", url)
        return None
    dest = cached_image_path(name)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    try:
        r = requests.get(url, headers=_IMAGE_UA, timeout=_IMAGE_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.info("Home art image fetch failed: %s", exc)
        return None
    if r.status_code != 200 or not r.content:
        log.info("Home art image HTTP %s from %s", r.status_code, url)
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
    except OSError as exc:
        log.debug("Could not cache home art image %s: %s", name, exc)
        return None
    return dest


def fetch_missing_images(manifest: dict[str, Any] | None = None) -> None:
    """Download allowlisted slide / frame URLs that are not on disk yet."""
    art = manifest if manifest is not None else load_home_art()
    for slide in art.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        image = str(slide.get("image") or "")
        url = str(slide.get("url") or "").strip()
        if url and resolve_image_path(image) is None:
            download_home_art_image(url, image)
        frame = str(slide.get("frame") or "")
        frame_url = str(slide.get("frame_url") or "").strip()
        if frame and frame_url and resolve_image_path(frame) is None:
            download_home_art_image(frame_url, frame)


def resolved_slides(manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Slides whose image file is already on disk (cache or bundled)."""
    art = manifest if manifest is not None else load_home_art()
    out: list[dict[str, Any]] = []
    for slide in art.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        path = resolve_image_path(str(slide.get("image") or ""))
        if path is None:
            continue
        out.append(slide)
    return out
