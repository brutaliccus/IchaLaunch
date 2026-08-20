"""Game detection, install, and launch."""

from __future__ import annotations

from pathlib import Path

from ichalaunch.config.settings import settings
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import launch_exe
from ichalaunch.core.filesystem import is_protected_path

# Placeholder until Ravencraft publishes official client mirrors.
# Users can also point at an existing Turtle/Capybara client.
GAME_DOWNLOAD_URL = ""  # filled later when host is available
GAME_DOWNLOAD_NOTE = (
    "Official Ravencraft client download is not published yet. "
    "Point IchaLaunch at an existing 1.18 client folder "
    "(Turtle / Capybara / Ravencraft), or place WoW.exe in the chosen folder."
)


def detect_game(path: str | Path | None = None) -> Path | None:
    p = Path(path or settings.game_path or "")
    if not p or not str(p):
        return None
    wow = p / "WoW.exe"
    if wow.exists():
        return p
    return None


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


def install_game_stub(dest: Path) -> str:
    """
    Until Ravencraft hosts a client zip, 'install' means:
    create folder structure and instruct the user to place/copy client files,
    or use Browse to pick an existing install.
    """
    dest.mkdir(parents=True, exist_ok=True)
    ok, msg = validate_install_location(dest)
    if not ok:
        raise ValueError(msg)
    settings.game_path = str(dest)
    marker = dest / ".ichalaunch" / "install_pending.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(GAME_DOWNLOAD_NOTE + "\n", encoding="utf-8")
    return GAME_DOWNLOAD_NOTE
