"""Load / unload addon folders so vanilla 1.12 does not scan them.

WoW only loads immediate children of ``Interface/AddOns`` that contain a ``.toc``.
Unloaded packs live beside that folder at ``Interface/AddOnsUnloaded/<Folder>``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import (
    is_access_denied,
    is_protected_path,
    matching_toc_path,
    robust_move_tree,
)
from ichalaunch.core.process import wow_exe_may_be_running
from ichalaunch.game.launcher import detect_game, resolve_addons_dir

UNLOADED_SIBLING = "AddOnsUnloaded"
UNLOAD_TOOLTIP = "Unload — keeps files, hidden from the game."
GAME_LOCK_MESSAGE = (
    "Close World of Warcraft and try again. "
    "The game still has this addon folder open."
)
_GENERIC_LOCK_LEAD = (
    "Could not move the addon folder. Close World of Warcraft if it is running, "
    "then retry. "
)
# Explorer and antivirus mean nothing on Linux, and now that wow_exe_running
# actually answers there, this text reaches those users.
_WIN_GENERIC_LOCK_TAIL = "Explorer, antivirus, or Git may also be locking files."
_POSIX_GENERIC_LOCK_TAIL = (
    "Your file manager, a backup tool, or Git may also be locking files."
)
GENERIC_LOCK_MESSAGE = _GENERIC_LOCK_LEAD + (
    _WIN_GENERIC_LOCK_TAIL if sys.platform == "win32" else _POSIX_GENERIC_LOCK_TAIL
)


def resolve_unloaded_addons_dir(*, create: bool = False) -> Path | None:
    """``Interface/AddOnsUnloaded`` next to the configured AddOns folder."""
    addons = resolve_addons_dir(create=False)
    if addons is None:
        return None
    dest = addons.parent / UNLOADED_SIBLING
    if create:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def _toc_folder_names(root: Path | None) -> list[str]:
    if root is None or not root.is_dir():
        return []
    names: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if matching_toc_path(p) is None:
            continue
        names.append(p.name)
    return names


def scan_unloaded_addon_folders() -> list[str]:
    return _toc_folder_names(resolve_unloaded_addons_dir(create=False))


def addon_is_loaded(folder: str, *, addons_dir: Path | None = None) -> bool:
    root = addons_dir if addons_dir is not None else resolve_addons_dir(create=False)
    if root is None or not folder:
        return False
    return (root / folder).is_dir()


def addon_disk_path(
    folder: str,
    *,
    addons_dir: Path | None = None,
    unloaded_dir: Path | None = None,
) -> Path | None:
    """Current on-disk folder (AddOns wins if both exist)."""
    loaded_root = addons_dir if addons_dir is not None else resolve_addons_dir(create=False)
    if loaded_root is not None and folder and (loaded_root / folder).is_dir():
        return loaded_root / folder
    off = unloaded_dir if unloaded_dir is not None else resolve_unloaded_addons_dir(create=False)
    if off is not None and folder and (off / folder).is_dir():
        return off / folder
    return None


def _pack_folder_names(folder: str, installed: dict[str, Any] | None = None) -> list[str]:
    tracked = installed if installed is not None else settings.installed_addons
    meta = tracked.get(folder) or {}
    if not meta:
        for key, val in tracked.items():
            if str(key).lower() == folder.lower() and isinstance(val, dict):
                folder, meta = str(key), val
                break
    managed_by = str(meta.get("managed_by") or "").strip()
    if managed_by:
        return _pack_folder_names(managed_by, tracked)
    folders = meta.get("folders")
    names: list[str] = []
    if isinstance(folders, list) and folders:
        names = [str(f) for f in folders if f]
    extras = [
        f
        for f, m in tracked.items()
        if str(m.get("managed_by") or "").lower() == folder.lower()
    ]
    if extras:
        names = sorted({folder, *names, *extras}, key=str.lower)
    return names or [folder]


def addon_move_error_text(exc: BaseException) -> str:
    """User-facing load/unload error — never a raw WinError traceback."""
    text = str(exc).strip()
    if text in (GAME_LOCK_MESSAGE, GENERIC_LOCK_MESSAGE):
        return text
    if text.startswith("Could not move the addon folder"):
        return text
    if wow_exe_may_be_running(detect_game()):
        return GAME_LOCK_MESSAGE
    if is_access_denied(exc):
        return GENERIC_LOCK_MESSAGE
    if text:
        return f"Could not move the addon folder: {text}"
    return GENERIC_LOCK_MESSAGE


def _persist_loaded(names: list[str], loaded: bool) -> None:
    for name in names:
        meta = dict(settings.installed_addons.get(name) or {})
        meta["loaded"] = bool(loaded)
        settings.set_installed_addon(name, meta)


def set_addon_loaded(
    folder: str,
    loaded: bool,
    *,
    addons_dir: Path | None = None,
    unloaded_dir: Path | None = None,
    installed: dict[str, Any] | None = None,
) -> None:
    """Move a pack between AddOns and AddOnsUnloaded. Does not delete files."""
    loaded_root = addons_dir if addons_dir is not None else resolve_addons_dir(create=False)
    if loaded_root is None:
        raise FileNotFoundError("AddOns path not set")
    off_root = (
        unloaded_dir
        if unloaded_dir is not None
        else (loaded_root.parent / UNLOADED_SIBLING)
    )
    src_root = loaded_root if not loaded else off_root
    dst_root = off_root if not loaded else loaded_root
    names = _pack_folder_names(folder, installed)

    for name in names:
        src = src_root / name
        dst = dst_root / name
        if is_protected_path(src) or is_protected_path(dst) or is_protected_path(dst_root):
            raise PermissionError(f"Protected path — cannot move {name}")
        if not src.is_dir():
            continue
        dst_root.mkdir(parents=True, exist_ok=True)
        try:
            robust_move_tree(src, dst)
        except OSError as exc:
            raise OSError(addon_move_error_text(exc)) from None

    if installed is None:
        _persist_loaded(names, loaded)
    else:
        for name in names:
            meta = dict(installed.get(name) or {})
            meta["loaded"] = bool(loaded)
            installed[name] = meta
