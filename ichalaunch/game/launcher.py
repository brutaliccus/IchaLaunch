"""Game detection, install, and launch."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import is_protected_path
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import launch_exe

# Primary: Gofile folder page (CDN links expire — resolve at install time).
# File metadata from the Gofile Properties dialog (do not bake signed CDN URLs).
GAME_DOWNLOAD_URL = "https://gofile.io/d/zrTbjjv1"
GOFILE_FILE_ID = "179cd45c-2ab4-4301-9f98-dcedbff07d07"
GOFILE_FILE_NAME = "twmoa_1181.zip"
GOFILE_STORE = "store-na-phx-4"
GOFILE_EXPECTED_SIZE = 9_829_040_584
GOFILE_MD5 = "b65fb26b56d09e3d45cb72b130a79080"
# Last-resort only — much slower than Gofile when Gofile works.
VIKINGFILE_ZIP_URL = "https://vikingfile.com/d/tnQwCPOJDA/twmoa_1181.zip"
CLIENT_ZIP_MIRRORS: tuple[str, ...] = (
    GAME_DOWNLOAD_URL,
    VIKINGFILE_ZIP_URL,
)
GAME_DOWNLOAD_NOTE = (
    "INSTALL opens Gofile in your browser. Click Download for twmoa_1181.zip "
    "(a VPN may be required); the launcher watches Downloads, then extracts "
    "into the folder you choose. That folder becomes the game home and AddOns path."
)


def detect_game(path: str | Path | None = None) -> Path | None:
    p = Path(path or settings.game_path or "")
    if not p or not str(p):
        return None
    wow = p / "WoW.exe"
    if wow.exists():
        return p
    return None


def resolve_addons_dir(*, create: bool = False) -> Path | None:
    """Return the configured Interface/AddOns folder (or default under game path).

    When ``create`` is True, ensures the folder exists (mkdir parents).
    Returns None if neither addons_path nor a valid game path is available.
    """
    raw = settings.resolved_addons_path()
    if not raw:
        return None
    addons = Path(raw)
    if create:
        addons.mkdir(parents=True, exist_ok=True)
        return addons
    return addons


def ensure_addons_dir() -> Path:
    """Resolve AddOns dir and create it; raises if game/addons path cannot be determined."""
    addons = resolve_addons_dir(create=True)
    if addons is None:
        raise FileNotFoundError("Game path not set — cannot resolve AddOns folder")
    return addons


def discover_game_path_near_launcher() -> Path | None:
    """Locate WoW.exe near the running launcher (EXE dir / cwd).

    Covers launcher sitting in the game root, in ``Game/``, or in
    ``Game/IchaLaunch/`` (and similar parent/child layouts). Does not
    consult or overwrite ``settings.game_path``.
    """
    starts: list[Path] = []
    if getattr(sys, "frozen", False):
        starts.append(Path(sys.executable).resolve().parent)
    try:
        starts.append(Path.cwd().resolve())
    except OSError:
        pass

    candidates: list[Path] = []
    for start in starts:
        candidates.extend(
            [
                start,
                start / "Game",
                start.parent,
                start.parent / "Game",
                start.parent.parent,
            ]
        )

    seen: set[Path] = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "WoW.exe").is_file():
            return resolved
    return None


def ensure_game_path_from_launcher() -> Path | None:
    """If settings lack a valid game folder, auto-fill from nearby WoW.exe."""
    if detect_game() is not None:
        return None
    found = discover_game_path_near_launcher()
    if found is None:
        return None
    settings.game_path = str(found)
    log.info("Auto-detected game path near launcher: %s", found)
    return found


def is_installed() -> bool:
    return detect_game() is not None


def validate_install_location(path: Path) -> tuple[bool, str]:
    if is_protected_path(path):
        return False, (
            "Avoid Program Files, Desktop, Downloads, or Documents — "
            "Windows Controlled Folder Access can block client mods."
        )
    return True, "OK"


def resolve_launch_exe(game_path: Path) -> Path:
    use_vf = bool(settings.get("vanillafixes_enabled", True))
    vf = game_path / "VanillaFixes.exe"
    wow = game_path / "WoW.exe"
    if use_vf and vf.exists():
        return vf
    return wow


def launch_game() -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game not installed / path not set")
    exe = resolve_launch_exe(game)
    log.info("Launching %s", exe)
    launch_exe(exe, cwd=game)


def gofile_content_id(url: str) -> str | None:
    """Return the Gofile share code from a /d/ URL, else None."""
    m = re.search(r"gofile\.io/d/([^/?#]+)", url or "", re.I)
    return m.group(1).strip() if m else None


def gofile_file_link_from_payload(data: dict[str, Any]) -> tuple[str, str]:
    """Pick the best downloadable file from a Gofile contents ``data`` object.

    Prefers zip archives, then the largest file. Uses ``directLink`` when present.
    """
    files: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        raise ValueError("Gofile payload is not an object")
    if str(data.get("type") or "") == "file":
        files.append(data)
    children = data.get("children") or data.get("contents") or {}
    if isinstance(children, dict):
        files.extend(v for v in children.values() if isinstance(v, dict))
    elif isinstance(children, list):
        files.extend(v for v in children if isinstance(v, dict))
    files = [f for f in files if str(f.get("type") or "file") == "file"]
    if not files:
        raise FileNotFoundError("Gofile folder has no files")

    def _rank(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("name") or "").lower()
        is_zip = 1 if name.endswith(".zip") else 0
        size = int(item.get("size") or 0)
        return (is_zip, size)

    best = max(files, key=_rank)
    url = str(best.get("directLink") or best.get("link") or best.get("direct_link") or "").strip()
    name = str(best.get("name") or "client.zip").strip() or "client.zip"
    if not url:
        raise FileNotFoundError("Gofile file has no download link")
    return url, name


def find_wow_exe_dir(root: Path) -> Path | None:
    """Directory that actually contains WoW.exe under ``root`` (itself, one wrapper, or nested)."""
    try:
        base = root.resolve()
    except OSError:
        base = root
    if not base.exists():
        return None
    if (base / "WoW.exe").is_file():
        return base
    try:
        children = [c for c in base.iterdir() if c.name not in (".ichalaunch",)]
    except OSError:
        return None
    dirs = [c for c in children if c.is_dir()]
    if len(dirs) == 1 and (dirs[0] / "WoW.exe").is_file():
        return dirs[0]
    try:
        for wow in base.rglob("WoW.exe"):
            if wow.is_file():
                return wow.parent
    except OSError:
        return None
    return None


def commit_game_home(game_dir: Path) -> Path:
    """Persist ``game_path`` to the folder that contains WoW.exe and ensure AddOns exists."""
    game = Path(game_dir)
    settings.game_path = str(game)
    ensure_addons_dir()
    log.info("Game home set to %s (AddOns %s)", game, settings.resolved_addons_path())
    return game
