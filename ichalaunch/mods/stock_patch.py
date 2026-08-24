"""Official Data/patch-9.mpq health check and user-triggered reacquire.

Turtle/RavenCraft ships ``Data/patch-9.mpq`` as a numeric stock client patch
(~500 MB). Catalog HD letter patches must never own this file. Reacquire is a
separate, explicit download from the share host — never started silently.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from ichalaunch.core.filesystem import (
    ensure_data_writable,
    invalidate_dir_listing,
    resolve_ci,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import status_only
from ichalaunch.game.launcher import detect_game

ProgressCb = Callable[[str], None]

STOCK_PATCH9_NAME = "patch-9.mpq"
STOCK_PATCH9_REL = Path("Data") / STOCK_PATCH9_NAME
# Landing page lists the file; same host serves the bytes (resume-capable).
STOCK_PATCH9_INDEX_URL = "https://share.ichasarmory.quest/"
STOCK_PATCH9_HOST = "share.ichasarmory.quest"
# Content-Length from the share host (2026-08-24). ~483 MiB; users call it ~500 MB.
STOCK_PATCH9_EXPECTED_SIZE = 506_642_995
# Conservative floor so a stub/partial is treated as broken.
STOCK_PATCH9_MIN_BYTES = 400 * 1024 * 1024
STOCK_PATCH9_BANNER_TEXT = "Patch-9 is missing or incomplete."

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


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
    if game_path is None:
        return StockPatch9Status("no_game", None, 0, expected, floor)
    game = Path(game_path)
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
    html: str, base_url: str = STOCK_PATCH9_INDEX_URL
) -> str | None:
    """Pick the landing-page href for ``patch-9.mpq`` when it stays on this host."""
    import re

    text = html or ""
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
            url = urljoin(base_url or STOCK_PATCH9_INDEX_URL, href)
        if _is_share_host(url):
            return url
    return None


def resolve_stock_patch9_url(*, html: str | None = None) -> str:
    """Direct file URL on the share host. Parses the index; same-host fallback."""
    text = html
    if text is None:
        try:
            r = requests.get(STOCK_PATCH9_INDEX_URL, timeout=15, headers=_UA)
            if r.ok:
                text = r.text
        except requests.RequestException as exc:
            log.info("patch-9 index fetch failed: %s", exc)
            text = None
    found = patch9_url_from_index_html(text or "", STOCK_PATCH9_INDEX_URL)
    if found:
        return found
    return urljoin(STOCK_PATCH9_INDEX_URL, STOCK_PATCH9_NAME)


def stock_patch9_source(url: str | None = None) -> dict[str, object]:
    """Catalog-shaped source so reacquire reuses ``_download_source``."""
    return {
        "type": "raw",
        "url": url or urljoin(STOCK_PATCH9_INDEX_URL, STOCK_PATCH9_NAME),
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
    if not game:
        raise FileNotFoundError("Game not installed / path not set")
    expected = STOCK_PATCH9_EXPECTED_SIZE if expected_size is None else int(expected_size)
    floor = STOCK_PATCH9_MIN_BYTES if min_bytes is None else int(min_bytes)
    status = inspect_stock_patch9(game, expected_size=expected, min_bytes=floor)
    if status.state == "ok" and not force:
        raise RuntimeError("patch-9.mpq is already present and complete")
    dest = status.path if status.path is not None else game / STOCK_PATCH9_REL
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = download_url or resolve_stock_patch9_url()
    if not _is_share_host(url):
        url = urljoin(STOCK_PATCH9_INDEX_URL, STOCK_PATCH9_NAME)
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


def _is_share_host(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return host == STOCK_PATCH9_HOST or host.endswith("." + STOCK_PATCH9_HOST)
