"""Client-mod UX helpers (DLL injection detection, security hints)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DLL_INJECTION_KINDS = frozenset({"dll_file", "dll_bundle", "dxvk_cursor"})
_MPQ_PATCH_KINDS = frozenset({"mpq_file", "hd_patch"})
_PATCH_MPQ_NAME_RE = re.compile(r"^patch-.+\.mpq$", re.IGNORECASE)


def is_dll_injection_mod(mod: dict[str, Any] | None) -> bool:
    """True when a catalog mod installs hook/injection DLLs via dlls.txt or DLL bundles."""
    if not mod:
        return False
    if mod.get("kind") in _DLL_INJECTION_KINDS:
        return True
    add = (mod.get("dlls_txt") or {}).get("add") or []
    return bool(add)


def dll_security_exclusion_message(game_folder: str) -> str:
    """Body text for the first-time Windows Security exclusion hint."""
    folder = (game_folder or "").strip() or "your WoW folder"
    return (
        "Client mods that inject DLLs (SuperWoW, Nampower, VanillaHelpers, UnitXP, "
        "etc.) copy hook files into your game folder and register them in dlls.txt.\n\n"
        "Windows Defender often blocks or quarantines these DLLs during install — "
        "especially in Downloads, Desktop, or Documents. That can leave a broken "
        "install (crashes, broken talents, mods that won't turn off).\n\n"
        "Before you Apply or Play, add your game folder as a Windows Security "
        "exclusion:\n"
        "1. Open Settings → Privacy & security → Windows Security\n"
        "2. Virus & threat protection → Manage settings\n"
        "3. Exclusions → Add an exclusion → Folder\n"
        "4. Select this folder:\n"
        f"   {folder}\n\n"
        "You can still enable the mod now; use Apply on the Client tab (or Play) "
        "after adding the exclusion so files are not blocked mid-install."
    )


def _looks_like_patch_mpq(name: str) -> bool:
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    return bool(base) and _PATCH_MPQ_NAME_RE.match(base) is not None


def is_mpq_patch_mod(mod: dict[str, Any] | None) -> bool:
    """True for HD graphics / ``patch-*.mpq`` client mods (including Patch-O)."""
    if not mod:
        return False
    if mod.get("kind") in _MPQ_PATCH_KINDS:
        return True
    if str(mod.get("category") or "") == "HD Graphics":
        return True
    if str(mod.get("id") or "").lower().startswith("hd_patch"):
        return True
    names = [
        str(mod.get("destination") or ""),
        str((mod.get("source") or {}).get("filename") or ""),
        str((mod.get("source") or {}).get("asset_contains") or ""),
    ]
    detect = mod.get("detect") or {}
    mpqs = detect.get("data_mpq") if isinstance(detect, dict) else None
    if isinstance(mpqs, list):
        names.extend(str(x) for x in mpqs if x)
    return any(_looks_like_patch_mpq(n) for n in names)


MPQ_PATCH_WARNING_TEXT = "Warning: MPQ patches are known to be potentially unstable."


def should_show_mpq_patch_warning(
    mod: dict[str, Any] | None,
    *,
    enabled: bool,
    dismissed: bool,
) -> bool:
    """True when enabling an HD / patch-*.mpq mod and the user has not opted out."""
    return bool(enabled) and not dismissed and is_mpq_patch_mod(mod)
