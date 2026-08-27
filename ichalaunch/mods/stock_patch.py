"""Official Data/patch-9.mpq health check and user-triggered reacquire.

Turtle/RavenCraft ships ``Data/patch-9.mpq`` as a numeric stock client patch
(~500 MB). Catalog HD letter patches must never own this file. Reacquire is a
separate, explicit download from the repo-hosted release asset — never started
silently.

Temporary host: GitHub release tag ``stock-patch-9`` (see
``ichalaunch/data/stock_patch9.json``). To retire this feature, delete that
release and the catalog file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

from ichalaunch.core.filesystem import (
    ensure_data_writable,
    invalidate_dir_listing,
    resolve_ci,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file
from ichalaunch.core.process import status_only
from ichalaunch.game.launcher import detect_game, has_wow_exe

ProgressCb = Callable[[str], None]

STOCK_PATCH9_NAME = "patch-9.mpq"
STOCK_PATCH9_REL = Path("Data") / STOCK_PATCH9_NAME
STOCK_PATCH9_CATALOG_NAME = "stock_patch9.json"
# Dedicated prerelease so this ~483 MiB MPQ can be deleted without touching
# launcher version tags. GitHub rejects 500 MB git blobs.
STOCK_PATCH9_RELEASE_TAG = "stock-patch-9"
STOCK_PATCH9_DOWNLOAD_URL = (
    "https://github.com/brutaliccus/IchaLaunch/releases/download/"
    f"{STOCK_PATCH9_RELEASE_TAG}/{STOCK_PATCH9_NAME}"
)
STOCK_PATCH9_REPO_PATH_PREFIX = "/brutaliccus/ichalaunch/releases/download/"
# Content-Length of the official MPQ (2026-08-24 / local RavenCraft copy).
STOCK_PATCH9_EXPECTED_SIZE = 506_642_995
# Conservative floor so a stub/partial is treated as broken.
STOCK_PATCH9_MIN_BYTES = 400 * 1024 * 1024
STOCK_PATCH9_BANNER_TEXT = "Patch-9 is missing or incomplete."

# Only github.com is pinned to this repo's path prefix. The two
# githubusercontent CDN hosts are accepted on ANY path, because a release
# asset redirect lands on an opaque, signed, per-download path that we cannot
# predict or match. That is safe today for one reason only: the catalog
# (ichalaunch/data/stock_patch9.json) is bundled with the app, so the URL this
# check guards is always one we shipped. If the catalog ever moves to a network
# fetch, a hostile response could name any file on GitHub and these two entries
# would wave it through. Two details would make that worse: the href match in
# patch9_url_from_index_html is a substring test, so a name like
# patch-9.mpq.exe passes it, and the ~483 MiB result is checked against a size
# floor only, never a hash. Tighten this before that happens, do not loosen it.
_GITHUB_ASSET_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)


@dataclass(frozen=True)
class StockPatch9Status:
    state: str  # ok | missing | too_small | no_game
    path: Path | None
    size: int
    expected_size: int
    min_bytes: int

    @property
    def needs_reacquire(self) -> bool:
        return self.state in ("missing", "too_small")


def stock_patch9_catalog_path() -> Path:
    return data_file(STOCK_PATCH9_CATALOG_NAME)


def load_stock_patch9_catalog() -> dict[str, object]:
    """Read the rip-out catalog. Missing/corrupt file falls back to constants."""
    try:
        raw = json.loads(stock_patch9_catalog_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def stock_patch9_download_url() -> str:
    """Canonical HTTPS URL. Catalog ``url`` wins when it stays on this repo."""
    url = str(load_stock_patch9_catalog().get("url") or "").strip()
    if url and _is_allowed_patch9_host(url):
        return url
    return STOCK_PATCH9_DOWNLOAD_URL


def stock_patch9_size_floor(
    expected_size: int = STOCK_PATCH9_EXPECTED_SIZE,
    min_bytes: int = STOCK_PATCH9_MIN_BYTES,
) -> int:
    """Byte floor for a complete patch-9. Catalog size wins when present."""
    floor = max(0, int(min_bytes))
    try:
        expected = int(expected_size or 0)
    except (TypeError, ValueError):
        expected = 0
    if expected > 0:
        # Well under catalog size (80%), but never looser than the stub floor
        # and never above the catalog size itself.
        floor = min(expected, max(floor, expected * 4 // 5))
    return floor


def classify_stock_patch9(
    exists: bool,
    size: int,
    *,
    expected_size: int = STOCK_PATCH9_EXPECTED_SIZE,
    min_bytes: int = STOCK_PATCH9_MIN_BYTES,
) -> str:
    """Return ``missing``, ``too_small``, or ``ok`` from presence + byte size."""
    if not exists:
        return "missing"
    try:
        nbytes = int(size)
    except (TypeError, ValueError):
        return "too_small"
    if nbytes < stock_patch9_size_floor(expected_size, min_bytes):
        return "too_small"
    return "ok"


def should_offer_stock_patch9_reacquire(status: StockPatch9Status | None) -> bool:
    return bool(status is not None and status.needs_reacquire)


def stock_patch9_path(game_path: Path | None) -> Path | None:
    """On-disk ``Data/patch-9.mpq`` (any casing), or None if missing."""
    if game_path is None:
        return None
    found = resolve_ci(Path(game_path), STOCK_PATCH9_REL)
    if found is not None and found.is_file():
        return found
    return None


def inspect_stock_patch9(
    game_path: Path | None,
    *,
    expected_size: int | None = None,
    min_bytes: int | None = None,
) -> StockPatch9Status:
    expected = STOCK_PATCH9_EXPECTED_SIZE if expected_size is None else int(expected_size)
    floor = STOCK_PATCH9_MIN_BYTES if min_bytes is None else int(min_bytes)
    if game_path is None or not str(game_path).strip():
        return StockPatch9Status("no_game", None, 0, expected, floor)
    game = Path(game_path)
    if not has_wow_exe(game):
        return StockPatch9Status("no_game", None, 0, expected, floor)
    found = stock_patch9_path(game)
    dest = found if found is not None else game / STOCK_PATCH9_REL
    if found is None:
        return StockPatch9Status("missing", dest, 0, expected, floor)
    try:
        size = found.stat().st_size
    except OSError:
        return StockPatch9Status("missing", found, 0, expected, floor)
    state = classify_stock_patch9(
        True, size, expected_size=expected, min_bytes=floor
    )
    return StockPatch9Status(state, found, size, expected, floor)


def patch9_url_from_index_html(
    html: str, base_url: str | None = None
) -> str | None:
    """Pick an href for ``patch-9.mpq`` when it stays on an allowed host."""
    import re

    text = html or ""
    base = base_url or stock_patch9_download_url()
    for m in re.finditer(
        r"""href=["']([^"']*patch-9\.mpq[^"']*)["']""",
        text,
        re.I,
    ):
        href = (m.group(1) or "").strip()
        if not href:
            continue
        if href.startswith("//"):
            url = "https:" + href
        elif href.startswith("http"):
            url = href
        else:
            url = urljoin(base, href)
        if _is_allowed_patch9_host(url):
            return url
    return None


def resolve_stock_patch9_url(*, html: str | None = None) -> str:
    """Direct file URL on the repo host. Optional HTML parse; catalog fallback."""
    catalog = stock_patch9_download_url()
    found = patch9_url_from_index_html(html or "", catalog)
    if found:
        return found
    return catalog


def stock_patch9_source(url: str | None = None) -> dict[str, object]:
    """Catalog-shaped source so reacquire reuses ``_download_source``."""
    return {
        "type": "raw",
        "url": url or stock_patch9_download_url(),
        "filename": STOCK_PATCH9_NAME,
        "expected_size": STOCK_PATCH9_EXPECTED_SIZE,
    }


def reacquire_stock_patch9(
    game_path: Path | None = None,
    *,
    force: bool = False,
    progress: ProgressCb | None = None,
    expected_size: int | None = None,
    min_bytes: int | None = None,
    download_url: str | None = None,
) -> Path:
    """Download official patch-9 into ``Data/`` via the existing raw-source path.

    Does not start on its own — callers must ask. A healthy-sized file is left
    alone unless *force* (the user clicked Reacquire). An undersized dest is
    replaced so a stub is never left behind.
    """
    from ichalaunch.mods.installer import _download_source

    game = Path(game_path) if game_path else detect_game()
    if not game or not has_wow_exe(game):
        raise FileNotFoundError("Game not installed / path not set")
    expected = STOCK_PATCH9_EXPECTED_SIZE if expected_size is None else int(expected_size)
    floor = STOCK_PATCH9_MIN_BYTES if min_bytes is None else int(min_bytes)
    status = inspect_stock_patch9(game, expected_size=expected, min_bytes=floor)
    if status.state == "no_game":
        raise FileNotFoundError("Game not installed / path not set")
    if status.state == "ok" and not force:
        raise RuntimeError("patch-9.mpq is already present and complete")
    dest = status.path if status.path is not None else game / STOCK_PATCH9_REL
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = download_url or resolve_stock_patch9_url()
    if not _is_allowed_patch9_host(url):
        url = stock_patch9_download_url()
    status_only(progress, "Reacquiring official patch-9.mpq…")
    source = stock_patch9_source(url)
    source["expected_size"] = expected

    # Stage inside Data/ so the final swap is a same-filesystem rename rather
    # than a ~483 MiB copy. Also keeps the download off /tmp, which is tmpfs
    # (i.e. RAM) on most Linux setups.
    try:
        staging_dir: str | None = str(dest.parent)
    except Exception:  # pragma: no cover - defensive
        staging_dir = None

    with tempfile.TemporaryDirectory(
        prefix="ichalaunch-patch9-", dir=staging_dir
    ) as td:
        work = Path(td)
        artifact = _download_source(source, work, progress)
        if not isinstance(artifact, Path) or not artifact.is_file():
            raise RuntimeError("patch-9 download did not produce a file")
        try:
            got = artifact.stat().st_size
        except OSError as exc:
            raise RuntimeError(f"Could not read downloaded patch-9: {exc}") from exc
        if got < stock_patch9_size_floor(expected, floor):
            raise RuntimeError(
                f"Downloaded patch-9 is too small ({got} bytes; "
                f"expected about {expected})"
            )
        if dest.exists():
            ensure_data_writable(dest, game)
        # Atomic swap: the old patch survives until the new one is fully in
        # place. os.replace() is atomic on the same filesystem, so a crash or a
        # full disk can no longer leave a truncated patch-9 behind -- which is
        # the exact state the Reacquire button exists to repair.
        try:
            os.replace(artifact, dest)
        except OSError:
            # Different filesystem (e.g. staging fell back to the default
            # tempdir): stage beside the destination, then swap.
            part = dest.with_name(dest.name + ".part")
            try:
                if part.exists():
                    part.unlink()
                shutil.copy2(artifact, part)
                staged = part.stat().st_size
                if staged != got:
                    raise RuntimeError(
                        f"Staged patch-9 is {staged} bytes; expected {got}"
                    )
                os.replace(part, dest)
            finally:
                if part.exists():
                    try:
                        part.unlink()
                    except OSError:
                        pass
        ensure_data_writable(dest, game)
        invalidate_dir_listing(dest.parent)
        log.info("Reacquired official %s (%s bytes) -> %s", dest.name, got, dest)
        return dest


def _is_allowed_patch9_host(url: str) -> bool:
    """True for this repo's release URL or GitHub's release-asset CDN."""
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    if (parts.scheme or "").lower() != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host == "github.com":
        path = (parts.path or "").lower()
        return path.startswith(STOCK_PATCH9_REPO_PATH_PREFIX)
    return host in _GITHUB_ASSET_HOSTS
