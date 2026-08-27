"""Persistent application settings."""

from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator  # Path used by default_addons_path_for

APP_DIR_NAME = "IchaLaunch"

# Automatic (startup / while-open) addon + client update refresh interval.
# Not user-adjustable — catalog checks are one JSON request, then a local compare.
AUTO_SCAN_COOLDOWN_MINUTES = 15
AUTO_SCAN_COOLDOWN_SEC = AUTO_SCAN_COOLDOWN_MINUTES * 60


def _user_home() -> Path:
    return Path.home()


def _windows_appdata_root() -> Path:
    return _user_home() / "AppData" / "Local" / APP_DIR_NAME


def _xdg_config_home() -> Path:
    raw = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if raw:
        return Path(raw)
    return _user_home() / ".config"


def _linux_xdg_root() -> Path:
    return _xdg_config_home() / APP_DIR_NAME


def _linux_legacy_appdata_root() -> Path:
    return _user_home() / "AppData" / "Local" / APP_DIR_NAME


def _settings_mtime(root: Path) -> float:
    path = root / "settings.json"
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return 0.0


def _copy_appdata_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif (
                not target.exists()
                or item.stat().st_mtime > target.stat().st_mtime
            ):
                shutil.copy2(item, target)
        except OSError:
            continue


def _migrate_linux_appdata(legacy: Path, xdg: Path) -> None:
    """Move off ~/AppData/Local when that tree is the live one.

    An older checkout may already have a stale ``~/.config/IchaLaunch``.
    Prefer whichever ``settings.json`` is newer so we do not roll users back.
    """
    try:
        legacy_exists = legacy.is_dir()
    except OSError:
        legacy_exists = False
    if not legacy_exists:
        return
    legacy_m = _settings_mtime(legacy)
    xdg_m = _settings_mtime(xdg)
    if legacy_m <= 0 and xdg_m <= 0:
        return
    if xdg_m > legacy_m:
        return
    try:
        _copy_appdata_tree(legacy, xdg)
    except OSError:
        return


def appdata_root() -> Path:
    if sys.platform == "win32":
        base = _windows_appdata_root()
        base.mkdir(parents=True, exist_ok=True)
        return base
    xdg = _linux_xdg_root()
    _migrate_linux_appdata(_linux_legacy_appdata_root(), xdg)
    xdg.mkdir(parents=True, exist_ok=True)
    return xdg


def settings_path() -> Path:
    return appdata_root() / "settings.json"


DEFAULTS: dict[str, Any] = {
    "game_path": "",
    # Linux launch. Empty proton path means "resolve and then pin".
    "linux_umu_path": "",
    "linux_proton_path": "",
    "linux_use_latest_proton": False,
    "linux_wineprefix": "",
    # New WoW64: on where the Proton build can honour it, off everywhere else
    # without the user having to know which is which. build_launch_command
    # probes for the 64-bit host and silently keeps the default mode when it is
    # absent, so this default cannot fail a launch -- it only decides what
    # happens on the builds that ship files/bin-wow64.
    #
    # None means "nobody has expressed a preference", and proton.wow64_enabled()
    # resolves it against WOW64_DEFAULT_ON at read time. True/False appear here
    # only once the user ticks the box. save() persists this whole dict, so a
    # literal default would be written into every settings.json on first launch
    # and would then outrank any later change to the default -- see the note on
    # WOW64_DEFAULT_ON in ichalaunch/game/proton.py.
    "linux_use_wow64": None,
    # Pin the client to the cache-rich CCD on dual-CCD X3D parts. Harmless
    # everywhere else: detection returns nothing unless two L3 domains of
    # clearly different size are present. None = unset; see VCACHE_PIN_DEFAULT_ON.
    "pin_to_vcache_ccd": None,
    "addons_path": "",
    # Frame pacing follows the display. None = unset; see FRAME_CAP_DEFAULT_ON.
    # frame_cap_offset is hand-edited (frames below refresh) and bounded in code.
    "frame_cap_from_refresh": None,
    "frame_cap_offset": 3,
    "vanillafixes_enabled": True,
    "minimize_on_launch": False,
    "close_on_launch": False,
    # Nampower login Encrypt toggle. The launcher owns WOW_ENCRYPTION_KEY
    # (Windows DPAPI). Off by default; the key is generated on first enable
    # and kept if the user turns the feature off so re-enabling still works.
    "nampower_encrypt_passwords": False,
    "wow_encryption_key": "",
    # Unified: covers both addon and client-mod quiet checks on launch.
    "check_updates_on_startup": True,
    # Legacy keys kept for migration from older settings.json files.
    "check_addon_updates_on_startup": False,
    "check_mod_updates_on_startup": True,
    "addon_no_token_startup_migrated_v1": False,
    "stock_patch9_collision_migrated_v1": False,
    # Legacy key — ignored; refresh interval is AUTO_SCAN_COOLDOWN_MINUTES.
    "auto_scan_cooldown_minutes": AUTO_SCAN_COOLDOWN_MINUTES,
    "auto_install_updates": False,
    # Prompt to rename addon folders whose names do not match their .toc (disk scans).
    "auto_fix_addon_toc_mismatch": True,
    "github_token": "",
    # Anonymous UUID for rate-limit hints only (no PII). Generated on first use.
    "anonymous_client_id": "",
    # Opt-in: POST crash/ERROR logs to maintainer via Cloudflare Worker (default off).
    "crash_reporting_enabled": False,
    # One-shot first-launch prompt for crash reporting (any answer marks shown).
    "crash_reporting_opt_in_prompted_v1": False,
    "last_addon_update_check": None,
    "last_mod_update_check": None,
    # True while an automatic update check is in flight (or died mid-apply).
    # Cooldown must not skip the next launch when this is still set.
    "addon_update_check_incomplete": False,
    "mod_update_check_incomplete": False,
    "last_launcher_release_check": None,
    "cached_launcher_release": None,
    # Persisted unauthenticated addon update-scan queue (folders + hour budget).
    "addon_update_scan_queue": None,
    "desired_mods": {
        "vanilla_tweaks": True,
        "vanilla_tweaks_old": False,
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
    "dismissed_mpq_patch_warning": False,
    # tubtubs/vanilla-tweaks CLI options. Empty dict uses V2 defaults.
    "vanilla_tweaks_options": {},
    # brndd/vanilla-tweaks 1.6.0 CLI options. Empty dict uses Old defaults.
    "vanilla_tweaks_old_options": {},
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


def migrate_stock_patch9_collision(data: dict[str, Any]) -> bool:
    """Drop auto-seeded Pretty Night Sky state that mistook official patch-9.mpq.

    Detecting Data/patch-9.mpq as this mod seeded desired_mods. Uncheck / Apply /
    Play then deleted Turtle/RavenCraft's stock archive.
    """
    if data.get("stock_patch9_collision_migrated_v1"):
        return False
    data["stock_patch9_collision_migrated_v1"] = True
    mid = "pretty_night_sky"
    usm = {str(x) for x in (data.get("user_set_mods") or []) if x}
    dm = dict(data.get("desired_mods") or {})
    im = dict(data.get("installed_mods") or {})
    if mid in dm and mid not in usm:
        dm.pop(mid, None)
        data["desired_mods"] = dm
    if mid in im:
        im.pop(mid, None)
        data["installed_mods"] = im
    return True


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


def migrate_unsolicited_vanilla_tweaks_old(data: dict[str, Any]) -> bool:
    """Turn off Old unless the user explicitly chose it.

    A prior ``vanilla_tweaks_old: True`` default merged into existing
    ``desired_mods`` and flipped Tweaks to Old on upgrade. Users who only
    had V2 (or never touched Old) stay on V2.
    """
    dm = dict(data.get("desired_mods") or {})
    if not dm.get("vanilla_tweaks_old"):
        return False
    usm = {str(x) for x in (data.get("user_set_mods") or []) if x}
    if "vanilla_tweaks_old" in usm:
        return False
    dm["vanilla_tweaks_old"] = False
    im = data.get("installed_mods") or {}
    if dm.get("vanilla_tweaks") or (isinstance(im, dict) and im.get("vanilla_tweaks")):
        dm["vanilla_tweaks"] = True
    data["desired_mods"] = dm
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
        # Never replace a saved dict wholesale — new catalog keys fill from
        # defaults, then the file wins. Old stays off unless the file said so.
        dm = dict(DEFAULTS["desired_mods"])
        loaded_dm = loaded.get("desired_mods")
        if isinstance(loaded_dm, dict):
            dm.update(loaded_dm)
            if "vanilla_tweaks_old" not in loaded_dm:
                dm["vanilla_tweaks_old"] = False
        merged["desired_mods"] = dm
        ia = dict(DEFAULTS["installed_addons"])
        loaded_ia = loaded.get("installed_addons")
        if isinstance(loaded_ia, dict):
            ia.update(loaded_ia)
        merged["installed_addons"] = ia
        im = dict(DEFAULTS.get("installed_mods") or {})
        loaded_im = loaded.get("installed_mods")
        if isinstance(loaded_im, dict):
            im.update(loaded_im)
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
        vto = loaded.get("vanilla_tweaks_options")
        merged["vanilla_tweaks_options"] = dict(vto) if isinstance(vto, dict) else {}
        vto_old = loaded.get("vanilla_tweaks_old_options")
        merged["vanilla_tweaks_old_options"] = (
            dict(vto_old) if isinstance(vto_old, dict) else {}
        )
        # Migrate older dual startup toggles into one setting.
        if "check_updates_on_startup" not in loaded:
            addon_on = bool(loaded.get("check_addon_updates_on_startup", True))
            mod_on = bool(loaded.get("check_mod_updates_on_startup", True))
            merged["check_updates_on_startup"] = addon_on or mod_on
        changed = migrate_legacy_mod_ids(merged)
        changed = migrate_stock_patch9_collision(merged) or changed
        changed = migrate_addon_no_token_startup(merged) or changed
        changed = migrate_unsolicited_vanilla_tweaks_old(merged) or changed
        _preserve_loaded_paths(merged, loaded)
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

    def should_startup_check_addons(self, *, has_token: bool = False) -> bool:
        """Whether quiet addon update scans should run on launcher startup.

        Token is unused — update checks no longer require a GitHub PAT.
        """
        return self.check_updates_on_startup()

    def set_check_updates_on_startup(self, enabled: bool) -> None:
        """Persist the unified startup check flag and keep legacy keys in sync."""
        enabled = bool(enabled)
        self._data["check_updates_on_startup"] = enabled
        self._data["check_mod_updates_on_startup"] = enabled
        # Keep the legacy addon-only flag in sync with the unified toggle.
        self._data["check_addon_updates_on_startup"] = enabled
        self.save()

    def auto_fix_addon_toc_mismatch(self) -> bool:
        """Whether disk scans should prompt to rename folder/.toc mismatches."""
        return bool(self.get("auto_fix_addon_toc_mismatch", True))

    def set_auto_fix_addon_toc_mismatch(self, enabled: bool) -> None:
        self.set("auto_fix_addon_toc_mismatch", bool(enabled))

    def auto_scan_cooldown_minutes(self) -> int:
        return AUTO_SCAN_COOLDOWN_MINUTES

    def auto_scan_cooldown_sec(self) -> int:
        return AUTO_SCAN_COOLDOWN_SEC

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
        # Explicit never_update in the payload wins (False = clear on reinstall/replace).
        # Omitted key keeps a saved lock — loadstate / partial updates often omit flags.
        incoming_has_never = "never_update" in meta
        incoming_never = bool(meta.get("never_update")) if incoming_has_never else None
        merged.update(meta)
        if incoming_has_never:
            if incoming_never:
                merged["never_update"] = True
            else:
                merged.pop("never_update", None)
        elif prev_never:
            merged["never_update"] = True
        # Empty tag/version from tip installs clear a prior pin (same idea as
        # never_update=False). Omitted keys still preserve existing pins.
        for pin_key in ("tag", "version"):
            if pin_key in meta and not str(meta.get(pin_key) or "").strip():
                merged.pop(pin_key, None)
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

    def retarget_installed_addon(self, old_name: str, new_name: str) -> bool:
        """Move a tracked ``installed_addons`` entry from *old_name* to *new_name*.

        Also rewrites ``managed_by`` / ``folders`` references. Does not overwrite
        an existing entry under *new_name*. Returns True when a key was moved.
        """
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name.lower() == new_name.lower():
            return False
        addons = self.installed_addons
        old_key = next((k for k in addons if k.lower() == old_name.lower()), None)
        if old_key is None:
            return False
        if any(
            k.lower() == new_name.lower() and k.lower() != old_key.lower()
            for k in addons
        ):
            return False
        meta = dict(addons.pop(old_key))
        folders = meta.get("folders")
        if isinstance(folders, list):
            meta["folders"] = [
                new_name if str(f).lower() == old_key.lower() else f for f in folders
            ]
        addons[new_name] = meta
        for key, other in list(addons.items()):
            if not isinstance(other, dict):
                continue
            changed = dict(other)
            dirty = False
            if str(changed.get("managed_by") or "").lower() == old_key.lower():
                changed["managed_by"] = new_name
                dirty = True
            fl = changed.get("folders")
            if isinstance(fl, list):
                updated = [
                    new_name if str(f).lower() == old_key.lower() else f for f in fl
                ]
                if updated != fl:
                    changed["folders"] = updated
                    dirty = True
            if dirty:
                addons[key] = changed
        self.set("installed_addons", addons)
        return True

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

    @property
    def vanilla_tweaks_options(self) -> dict[str, Any]:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_options

        return normalize_vanilla_tweaks_options(self._data.get("vanilla_tweaks_options"))

    def set_vanilla_tweaks_options(self, options: dict[str, Any]) -> None:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_options

        self.set("vanilla_tweaks_options", normalize_vanilla_tweaks_options(options))

    @property
    def vanilla_tweaks_old_options(self) -> dict[str, Any]:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_old_options

        return normalize_vanilla_tweaks_old_options(
            self._data.get("vanilla_tweaks_old_options")
        )

    def set_vanilla_tweaks_old_options(self, options: dict[str, Any]) -> None:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_old_options

        self.set(
            "vanilla_tweaks_old_options",
            normalize_vanilla_tweaks_old_options(options),
        )

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
