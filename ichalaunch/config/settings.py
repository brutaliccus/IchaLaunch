"""Persistent application settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

APP_DIR_NAME = "IchaLaunch"


def appdata_root() -> Path:
    base = Path.home() / "AppData" / "Local" / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return appdata_root() / "settings.json"


DEFAULTS: dict[str, Any] = {
    "game_path": "",
    "vanillafixes_enabled": True,
    "minimize_on_launch": False,
    "close_on_launch": False,
    # Unified: covers both addon and client-mod quiet checks on launch.
    "check_updates_on_startup": True,
    # Legacy keys kept for migration from older settings.json files.
    "check_addon_updates_on_startup": True,
    "check_mod_updates_on_startup": True,
    "auto_install_updates": False,
    "github_token": "",
    "last_addon_update_check": None,
    "last_mod_update_check": None,
    "desired_mods": {
        "vanilla_tweaks": False,
        "vanillafixes": True,
        "dxvk": False,
        "superwow": False,
        "nampower": False,
        "unitxp": False,
        "perfboost": False,
        "no1600x1200": False,
        "wdb_block": True,
    },
    "installed_addons": {},
    "installed_mods": {},
    "user_mods": [],
    "window_geometry": None,
}


class Settings:
    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        path = settings_path()
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    merged = dict(DEFAULTS)
                    merged.update(loaded)
                    # deep-merge desired_mods / installed_addons / installed_mods
                    dm = dict(DEFAULTS["desired_mods"])
                    dm.update(loaded.get("desired_mods") or {})
                    merged["desired_mods"] = dm
                    ia = dict(DEFAULTS["installed_addons"])
                    ia.update(loaded.get("installed_addons") or {})
                    merged["installed_addons"] = ia
                    im = dict(DEFAULTS.get("installed_mods") or {})
                    im.update(loaded.get("installed_mods") or {})
                    merged["installed_mods"] = im
                    um = loaded.get("user_mods")
                    if isinstance(um, list):
                        merged["user_mods"] = um
                    else:
                        merged["user_mods"] = list(DEFAULTS.get("user_mods") or [])
                    # Migrate older dual startup toggles into one setting.
                    if "check_updates_on_startup" not in loaded:
                        addon_on = bool(loaded.get("check_addon_updates_on_startup", True))
                        mod_on = bool(loaded.get("check_mod_updates_on_startup", True))
                        merged["check_updates_on_startup"] = addon_on or mod_on
                    self._data = merged
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULTS)

    def check_updates_on_startup(self) -> bool:
        return bool(self.get("check_updates_on_startup", True))

    def set_check_updates_on_startup(self, enabled: bool) -> None:
        """Persist the unified startup check flag and keep legacy keys in sync."""
        enabled = bool(enabled)
        self._data["check_updates_on_startup"] = enabled
        self._data["check_addon_updates_on_startup"] = enabled
        self._data["check_mod_updates_on_startup"] = enabled
        self.save()

    def save(self) -> None:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    @property
    def game_path(self) -> str:
        return str(self._data.get("game_path") or "")

    @game_path.setter
    def game_path(self, value: str) -> None:
        self.set("game_path", value)

    @property
    def desired_mods(self) -> dict[str, bool]:
        return dict(self._data.get("desired_mods") or {})

    def set_desired_mod(self, mod_id: str, enabled: bool) -> None:
        mods = self.desired_mods
        mods[mod_id] = enabled
        self.set("desired_mods", mods)

    @property
    def installed_addons(self) -> dict[str, Any]:
        return dict(self._data.get("installed_addons") or {})

    def set_installed_addon(self, folder: str, meta: dict[str, Any]) -> None:
        addons = self.installed_addons
        merged = dict(addons.get(folder) or {})
        merged.update(meta)
        addons[folder] = merged
        self.set("installed_addons", addons)

    def remove_installed_addon(self, folder: str) -> None:
        addons = self.installed_addons
        addons.pop(folder, None)
        self.set("installed_addons", addons)

    @property
    def installed_mods(self) -> dict[str, Any]:
        return dict(self._data.get("installed_mods") or {})

    def set_installed_mod(self, mod_id: str, meta: dict[str, Any]) -> None:
        mods = self.installed_mods
        merged = dict(mods.get(mod_id) or {})
        merged.update(meta)
        mods[mod_id] = merged
        self.set("installed_mods", mods)

    def remove_installed_mod(self, mod_id: str) -> None:
        mods = self.installed_mods
        mods.pop(mod_id, None)
        self.set("installed_mods", mods)

    @property
    def user_mods(self) -> list[dict[str, Any]]:
        raw = self._data.get("user_mods") or []
        return [dict(m) for m in raw if isinstance(m, dict) and m.get("id")]

    def set_user_mod(self, mod: dict[str, Any]) -> None:
        """Insert or replace a user-defined client mod entry by id."""
        mid = mod.get("id")
        if not mid:
            raise ValueError("user mod requires id")
        mods = [m for m in self.user_mods if m.get("id") != mid]
        mods.append(dict(mod))
        self.set("user_mods", mods)

    def remove_user_mod(self, mod_id: str) -> None:
        mods = [m for m in self.user_mods if m.get("id") != mod_id]
        self.set("user_mods", mods)


# singleton
settings = Settings()
