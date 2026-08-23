"""Persistent application settings."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator  # Path used by default_addons_path_for

APP_DIR_NAME = "IchaLaunch"

# Automatic (startup/silent) update-scan cooldown — also the Settings slider default.
AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT = 60
AUTO_SCAN_COOLDOWN_MINUTES_MIN = 15
AUTO_SCAN_COOLDOWN_MINUTES_MAX = 24 * 60  # 24 hours
AUTO_SCAN_COOLDOWN_MINUTES_STEP = 15


def clamp_auto_scan_cooldown_minutes(value: Any) -> int:
    """Clamp/snap cooldown minutes to the Settings slider range (15 min … 24 h)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT
    step = AUTO_SCAN_COOLDOWN_MINUTES_STEP
    n = max(AUTO_SCAN_COOLDOWN_MINUTES_MIN, min(AUTO_SCAN_COOLDOWN_MINUTES_MAX, n))
    # Snap to step (prefer nearest; ties round up).
    snapped = int(round(n / step) * step)
    return max(AUTO_SCAN_COOLDOWN_MINUTES_MIN, min(AUTO_SCAN_COOLDOWN_MINUTES_MAX, snapped))


def format_auto_scan_cooldown_label(minutes: int) -> str:
    """Human label for the Settings slider, e.g. ``15 min``, ``1 hour``, ``6 hours``."""
    mins = clamp_auto_scan_cooldown_minutes(minutes)
    if mins < 60:
        return f"{mins} min"
    if mins % 60 == 0:
        hours = mins // 60
        return "1 hour" if hours == 1 else f"{hours} hours"
    # e.g. 90 → 1.5 hours
    hours = mins / 60
    text = f"{hours:.1f}".rstrip("0").rstrip(".")
    return f"{text} hours"


def appdata_root() -> Path:
    base = Path.home() / "AppData" / "Local" / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return appdata_root() / "settings.json"


DEFAULTS: dict[str, Any] = {
    "game_path": "",
    # Linux launch. Empty proton path means "resolve and then pin".
    "linux_umu_path": "",
    "linux_proton_path": "",
    "linux_use_latest_proton": False,
    "linux_wineprefix": "",
    "addons_path": "",
    "vanillafixes_enabled": True,
    "minimize_on_launch": False,
    "close_on_launch": False,
    # Unified: covers both addon and client-mod quiet checks on launch.
    "check_updates_on_startup": True,
    # Legacy keys kept for migration from older settings.json files.
    "check_addon_updates_on_startup": False,
    "check_mod_updates_on_startup": True,
    "addon_no_token_startup_migrated_v1": False,
    # Minutes between automatic/startup addon+mod update scans (manual always runs).
    "auto_scan_cooldown_minutes": AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT,
    "auto_install_updates": False,
    "github_token": "",
    "last_addon_update_check": None,
    "last_mod_update_check": None,
    "last_launcher_release_check": None,
    "cached_launcher_release": None,
    # Persisted unauthenticated addon update-scan queue (folders + hour budget).
    "addon_update_scan_queue": None,
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
    "user_set_mods": [],
    "window_geometry": None,
    "dismissed_dll_security_exclusion_hint": False,
    "dll_security_exclusion_hint_shown": False,
}


_PATH_KEYS = ("game_path", "addons_path")

_LEGACY_MOD_ALIASES: dict[str, str] = {
    "darker_nights": "hd_patch_n",
}


def _stored_path_value(value: Any) -> str:
    return str(value or "").strip()


def _preserve_loaded_paths(merged: dict[str, Any], loaded: dict[str, Any]) -> None:
    """Never let empty defaults or bad merges wipe saved folder paths."""
    for key in _PATH_KEYS:
        loaded_val = _stored_path_value(loaded.get(key))
        if loaded_val and not _stored_path_value(merged.get(key)):
            merged[key] = loaded_val


def _read_settings_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _settings_backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def migrate_legacy_mod_ids(data: dict[str, Any]) -> bool:
    """Rename removed catalog mod ids in persisted settings (one-time on load)."""
    changed = False
    for old_id, new_id in _LEGACY_MOD_ALIASES.items():
        dm = dict(data.get("desired_mods") or {})
        usm = [str(x) for x in (data.get("user_set_mods") or []) if x]
        im = dict(data.get("installed_mods") or {})

        if old_id not in dm and old_id not in usm and old_id not in im:
            continue

        old_on = bool(dm.pop(old_id, False))
        had_user_choice = old_id in usm
        if had_user_choice:
            dm[new_id] = old_on
            usm_new: list[str] = []
            for mid in usm:
                if mid == old_id:
                    if new_id not in usm_new:
                        usm_new.append(new_id)
                else:
                    usm_new.append(mid)
            usm = usm_new
        elif old_on and not dm.get(new_id):
            dm[new_id] = True

        if old_id in im:
            im.setdefault(new_id, im.pop(old_id))

        data["desired_mods"] = dm
        data["user_set_mods"] = usm
        data["installed_mods"] = im
        changed = True
    return changed


def migrate_addon_no_token_startup(data: dict[str, Any]) -> bool:
    """One-time: stop auto addon scans on startup when no GitHub token is saved."""
    if data.get("addon_no_token_startup_migrated_v1"):
        return False
    if (data.get("github_token") or "").strip():
        data["addon_no_token_startup_migrated_v1"] = True
        return False
    data["check_addon_updates_on_startup"] = False
    data["addon_no_token_startup_migrated_v1"] = True
    return True


class Settings:
    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._allow_empty_paths = False
        self.load()

    def _merge_loaded(self, loaded: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        merged = dict(DEFAULTS)
        merged.update(loaded)
        _preserve_loaded_paths(merged, loaded)
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
        usm = loaded.get("user_set_mods")
        merged["user_set_mods"] = (
            [str(x) for x in usm if x] if isinstance(usm, list) else []
        )
        # Migrate older dual startup toggles into one setting.
        if "check_updates_on_startup" not in loaded:
            addon_on = bool(loaded.get("check_addon_updates_on_startup", True))
            mod_on = bool(loaded.get("check_mod_updates_on_startup", True))
            merged["check_updates_on_startup"] = addon_on or mod_on
        changed = migrate_legacy_mod_ids(merged)
        changed = migrate_addon_no_token_startup(merged) or changed
        _preserve_loaded_paths(merged, loaded)
        prev_cooldown = loaded.get("auto_scan_cooldown_minutes")
        merged["auto_scan_cooldown_minutes"] = clamp_auto_scan_cooldown_minutes(
            merged.get(
                "auto_scan_cooldown_minutes",
                AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT,
            )
        )
        if merged["auto_scan_cooldown_minutes"] != prev_cooldown:
            changed = True
        return merged, changed

    def load(self) -> None:
        path = settings_path()
        loaded = _read_settings_dict(path) if path.exists() else None
        if loaded is None and path.exists():
            loaded = _read_settings_dict(_settings_backup_path(path))
        if loaded is None:
            if path.exists():
                self._data = dict(DEFAULTS)
            return
        merged, changed = self._merge_loaded(loaded)
        self._data = merged
        if changed:
            self.save()

    @contextmanager
    def allow_empty_paths(self) -> Iterator[None]:
        """Allow explicit clears of saved game/addons paths (reset / unlink)."""
        prev = self._allow_empty_paths
        self._allow_empty_paths = True
        try:
            yield
        finally:
            self._allow_empty_paths = prev

    def check_updates_on_startup(self) -> bool:
        return bool(self.get("check_updates_on_startup", True))

    def check_mod_updates_on_startup(self) -> bool:
        return bool(self.get("check_mod_updates_on_startup", True))

    def check_addon_updates_on_startup(self) -> bool:
        return bool(self.get("check_addon_updates_on_startup", False))

    def should_startup_check_addons(self, *, has_token: bool) -> bool:
        """Whether quiet addon update scans should run on launcher startup."""
        if has_token:
            return self.check_updates_on_startup()
        return self.check_addon_updates_on_startup()

    def set_check_updates_on_startup(self, enabled: bool) -> None:
        """Persist the unified startup check flag and keep legacy keys in sync."""
        enabled = bool(enabled)
        self._data["check_updates_on_startup"] = enabled
        self._data["check_mod_updates_on_startup"] = enabled
        # User opt-in for paced unauthenticated addon scans when no token.
        self._data["check_addon_updates_on_startup"] = enabled
        self.save()

    def auto_scan_cooldown_minutes(self) -> int:
        return clamp_auto_scan_cooldown_minutes(
            self.get("auto_scan_cooldown_minutes", AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT)
        )

    def auto_scan_cooldown_sec(self) -> int:
        return self.auto_scan_cooldown_minutes() * 60

    def set_auto_scan_cooldown_minutes(self, minutes: int) -> None:
        self.set("auto_scan_cooldown_minutes", clamp_auto_scan_cooldown_minutes(minutes))

    def save(self) -> None:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, indent=2)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        if path.is_file():
            bak = _settings_backup_path(path)
            try:
                shutil.copy2(path, bak)
            except OSError:
                pass
        os.replace(tmp, path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def _reject_accidental_path_clear(self, key: str, value: Any) -> bool:
        # addons_path may be "" to mean "use default under game_path".
        if key != "game_path":
            return False
        if self._allow_empty_paths:
            return False
        if _stored_path_value(value):
            return False
        return bool(_stored_path_value(self._data.get(key)))

    def set(self, key: str, value: Any) -> None:
        if self._reject_accidental_path_clear(key, value):
            return
        self._data[key] = value
        self.save()

    @staticmethod
    def default_addons_path_for(game_path: str | Path) -> str:
        """Windows-style `{game}/Interface/AddOns` when game_path is set."""
        gp = Path(str(game_path or "").strip())
        if not str(gp):
            return ""
        return str(gp / "Interface" / "AddOns")

    @staticmethod
    def _norm_path(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).resolve())
        except OSError:
            return str(Path(text))

    @property
    def game_path(self) -> str:
        return str(self._data.get("game_path") or "")

    @game_path.setter
    def game_path(self, value: str) -> None:
        """Set game folder; keep AddOns path on the default when user hasn't overridden."""
        new_game = str(value or "").strip()
        if not new_game and self.game_path and not self._allow_empty_paths:
            return
        old_game = self.game_path
        old_addons = self.addons_path
        old_default = self.default_addons_path_for(old_game) if old_game else ""
        new_default = self.default_addons_path_for(new_game) if new_game else ""

        self._data["game_path"] = new_game
        # Empty / matching previous default → track the new game's default AddOns folder.
        if not old_addons or (
            old_default and self._norm_path(old_addons) == self._norm_path(old_default)
        ):
            self._data["addons_path"] = new_default
        elif not new_game:
            # Clearing game path without a custom override: clear default-style empty.
            if self._norm_path(old_addons) == self._norm_path(old_default):
                self._data["addons_path"] = ""
        self.save()

    @property
    def addons_path(self) -> str:
        return str(self._data.get("addons_path") or "")

    @addons_path.setter
    def addons_path(self, value: str) -> None:
        self.set("addons_path", value)

    def reset_addons_path_to_default(self) -> str:
        """Reset AddOns folder to `{game_path}/Interface/AddOns`. Returns the path used."""
        path = self.default_addons_path_for(self.game_path)
        self.addons_path = path
        return path

    def clear_client_link(self) -> None:
        """Forget the saved WoW folder so INSTALL can pick a new location.

        Does not delete any files on disk.
        """
        with self.allow_empty_paths():
            self._data["game_path"] = ""
            self._data["addons_path"] = ""
            self.save()

    def resolved_addons_path(self) -> str:
        """Stored addons_path, or default under game_path when empty."""
        raw = self.addons_path.strip()
        if raw:
            return raw
        return self.default_addons_path_for(self.game_path)

    @property
    def desired_mods(self) -> dict[str, bool]:
        return dict(self._data.get("desired_mods") or {})

    def set_desired_mod(self, mod_id: str, enabled: bool) -> None:
        """Persist an explicit desired-state choice; rescans must not override it."""
        mods = self.desired_mods
        mods[mod_id] = enabled
        self._data["desired_mods"] = mods
        marked = self.user_set_mods
        if mod_id not in marked:
            marked.append(mod_id)
        self._data["user_set_mods"] = marked
        self.save()

    @property
    def user_set_mods(self) -> list[str]:
        """Mod ids the user explicitly toggled — desired state wins over detected."""
        raw = self._data.get("user_set_mods") or []
        return [str(x) for x in raw if x]

    @property
    def installed_addons(self) -> dict[str, Any]:
        return dict(self._data.get("installed_addons") or {})

    def set_installed_addon(self, folder: str, meta: dict[str, Any]) -> None:
        addons = self.installed_addons
        key = folder
        if key not in addons:
            for existing in addons:
                if existing.lower() == str(folder or "").lower():
                    key = existing
                    break
        merged = dict(addons.get(key) or {})
        prev_never = bool(merged.get("never_update"))
        merged.update(meta)
        # Incoming payloads often omit flags; never drop a saved lock.
        if prev_never:
            merged["never_update"] = True
        self._stamp_catalog_never_update(str(key), merged)
        addons[key] = merged
        self.set("installed_addons", addons)

    @staticmethod
    def _stamp_catalog_never_update(folder: str, meta: dict[str, Any]) -> None:
        """Force ``never_update`` for catalog pins (Bagshui ``updates: false``)."""
        from ichalaunch.addons.github import addon_ignores_updates

        if addon_ignores_updates(None, folder, meta):
            meta["never_update"] = True

    def is_addon_never_update(self, folder: str) -> bool:
        """True when this pack is excluded from update checks / Update All."""
        meta = self.installed_addons.get(folder) or {}
        if not meta:
            needle = str(folder or "").lower()
            for key, val in self.installed_addons.items():
                if key.lower() == needle and isinstance(val, dict):
                    folder, meta = key, val
                    break
        from ichalaunch.addons.github import addon_skips_updates

        return addon_skips_updates(str(folder), meta)

    def set_addon_never_update(self, folder: str, enabled: bool) -> None:
        """Persist Never Update on the pack primary (case-insensitive key match)."""
        addons = self.installed_addons
        key = folder
        if key not in addons:
            for existing in addons:
                if existing.lower() == str(folder or "").lower():
                    key = existing
                    break
        meta = dict(addons.get(key) or {})
        # Resolve to pack primary when this is a child module
        managed_by = str(meta.get("managed_by") or "").strip()
        if managed_by:
            key = managed_by
            meta = dict(addons.get(key) or meta)
        if enabled:
            meta["never_update"] = True
        else:
            meta.pop("never_update", None)
        # Catalog pin always wins — Bagshui cannot be unlocked.
        self._stamp_catalog_never_update(str(key), meta)
        addons[key] = meta
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

    def reset_to_defaults(self) -> None:
        """Reset all persisted settings to factory defaults and save."""
        with self.allow_empty_paths():
            self._data = json.loads(json.dumps(DEFAULTS))
            self.save()


def clear_app_data() -> None:
    """Reset launcher settings and in-memory caches. Game/addon files are untouched."""
    settings.reset_to_defaults()
    try:
        from ichalaunch.addons.github import clear_addon_scan_queue, clear_github_url_cache

        clear_addon_scan_queue()
        clear_github_url_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        from ichalaunch.core.filesystem import clear_fs_caches

        clear_fs_caches()
    except Exception:  # noqa: BLE001
        pass


# singleton
settings = Settings()
