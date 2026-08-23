"""Client-mod UX helpers (DLL injection detection, security hints)."""

from __future__ import annotations

from typing import Any

_DLL_INJECTION_KINDS = frozenset({"dll_file", "dll_bundle", "dxvk_cursor"})


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
