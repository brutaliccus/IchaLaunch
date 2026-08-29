"""Opt-in Discord Rich Presence for IchaLaunch.

Disabled by default. When enabled and Discord desktop is running, the
bold ``Playing X`` header is the Developer Portal application name
(cached by Discord; not set from Python). This module never sends the
word IchaLaunch in details/state/large_text.

While only the launcher is open, details is ``In Launcher``. With no
character snapshot in-game, details and state are omitted so Discord
shows only ``Playing {App Name}``. Character text is two rows:
``Name <Guild>`` on details (no zone, no dash) and the zone alone on
state. Level, class, and faction append to details when they still
fit (``Name <Guild> · Lvl XX Class · Faction``). If zone is missing,
state falls back to that level line. Do not send ``small_image`` /
``small_text``: Discord always punches a large circular knockout
around the overlay and there is no RPC flag to shrink or remove it.
Race stays on the snapshot JSON but is not shown in Discord text.
Never sends paths, tokens, or coordinates.

Create an application at https://discord.com/developers/applications
(name it RavenCraft) and paste the numeric Application ID
into DISCORD_APPLICATION_ID.

File protocol (written by ichalaunch_discord.dll, read only here):
  %LOCALAPPDATA%\\IchaLaunch\\discord_wow_status.json
Broadcast filters (written by the launcher, read by the helper):
  %LOCALAPPDATA%\\IchaLaunch\\discord_broadcast_flags
  ASCII integer bitmask: name=1 guild=2 faction=4 class=8 level=16 zone=32.
  Missing file means all six fields (63). Unchecked fields are omitted from
  the JSON and from Discord RPC.
  {
    "v": 1,
    "ts": <unix seconds>,
    "ok": true,
    "in_world": true,
    "name": "Thrall",
    "zone": "Orgrimmar",
    "level": 24,
    "faction": "horde",
    "class": "Shaman",
    "guild": "Frostwolf Clan",
    "race": "Orc",
    "build": 5875,
    "err": ""
  }
Stale when ts is older than STATUS_MAX_AGE_SEC, ok is false, or not in_world.
The DLL must not talk to Discord; pypresence stays in the launcher.
Rich Presence art assets (Developer Portal, case-sensitive):
  large image — ``ravencraft`` (512×512+), hover ``RavenCraft``.
  Do not upload or send a small image: Discord always cuts a circular
  halo out of the large crest. Faction is details text instead.
  Class keys (``hunter``, ``shaman``, …) are unused: Discord RPC has
  only one image slot we use (large) and cannot place art in state.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("ichalaunch")

SETTING_KEY = "discord_rich_presence_enabled"
CHARACTER_STATUS_KEY = "discord_rich_presence_character_status"
PROMPTED_KEY = "discord_presence_prompted"
BROADCAST_FIELDS_KEY = "discord_broadcast_fields"

# Same order as Settings → Privacy and the one-shot opt-in dialog.
BROADCAST_FIELD_KEYS = ("name", "guild", "faction", "class", "level", "zone")
BROADCAST_FIELD_LABELS = {
    "name": "Name",
    "guild": "Guild",
    "faction": "Faction",
    "class": "Class",
    "level": "Level",
    "zone": "Zone",
}
# Compact flags word the helper DLL reads from discord_broadcast_flags.
BROADCAST_FLAG_BITS = {
    "name": 1 << 0,
    "guild": 1 << 1,
    "faction": 1 << 2,
    "class": 1 << 3,
    "level": 1 << 4,
    "zone": 1 << 5,
}
BROADCAST_FLAGS_ALL = (1 << len(BROADCAST_FIELD_KEYS)) - 1
BROADCAST_FLAGS_FILENAME = "discord_broadcast_flags"

DISCORD_PRESENCE_OPT_IN_TITLE = "Show your activity on Discord?"
DISCORD_PRESENCE_OPT_IN_TEXT = (
    "Would you like to turn on Discord activity broadcasting?\n"
    "\n"
    "When enabled, Discord can show that you are playing and, if you choose, "
    "in-game details such as your name, guild, faction, class, level, and zone.\n"
    "\n"
    "You can opt in at any time in Settings → Privacy."
)

# Stock 1.12.1 + common Turtle/RavenCraft extras. Unknown race → no icon.
_ALLIANCE_RACES = frozenset({1, 3, 4, 7, 11, 16})
_HORDE_RACES = frozenset({2, 5, 6, 8, 9, 10})
_FACTION_LABELS = {
    "alliance": "Alliance",
    "horde": "Horde",
}

# Vanilla class ids (6 and 10 unused). Unknown → omit.
_CLASS_NAMES = {
    1: "Warrior",
    2: "Paladin",
    3: "Hunter",
    4: "Rogue",
    5: "Priest",
    7: "Shaman",
    8: "Mage",
    9: "Warlock",
    11: "Druid",
}

# Same ids as the faction map. Unknown → omit.
_RACE_NAMES = {
    1: "Human",
    2: "Orc",
    3: "Dwarf",
    4: "Night Elf",
    5: "Undead",
    6: "Tauren",
    7: "Gnome",
    8: "Troll",
    9: "Goblin",
    10: "Blood Elf",
    11: "Draenei",
    16: "High Elf",
}
_CLASS_NAME_SET = frozenset(_CLASS_NAMES.values())
_RACE_NAME_SET = frozenset(_RACE_NAMES.values())
_RACE_ID_BY_NAME = {name: rid for rid, name in _RACE_NAMES.items()}

_DISCORD_FIELD_MAX = 128

# Owner: create an app at https://discord.com/developers/applications
# (name it RavenCraft) and paste the numeric Application ID on the next line.
# The Discord header "Playing X" is that portal name, not these strings.
DISCORD_APPLICATION_ID_PLACEHOLDER = "REPLACE_WITH_DISCORD_APPLICATION_ID"
DISCORD_APPLICATION_ID = "1543077511343374479"

POLL_INTERVAL_MS = 5_000
STATUS_FILENAME = "discord_wow_status.json"
STATUS_MAX_AGE_SEC = 30.0

# Used only when a character snapshot has a name but no level.
STATE_IN_GAME = "Playing RavenCraft right now"
# Launcher idle (game not running). Must not contain the word IchaLaunch.
STATE_IN_LAUNCHER = "In Launcher"

# Developer Portal asset key — must match the uploaded name exactly.
_LARGE_IMAGE_KEY = "ravencraft"
_LARGE_IMAGE_TEXT = "RavenCraft"

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z']{1,23}$")
_ZONE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ':-]{1,63}$")
_GUILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ':-]{1,47}$")

PresenceFactory = Callable[[str], Any]


def application_id_configured() -> bool:
    """True when DISCORD_APPLICATION_ID is a real numeric snowflake."""
    value = str(DISCORD_APPLICATION_ID or "").strip()
    return bool(value) and value != DISCORD_APPLICATION_ID_PLACEHOLDER and value.isdigit()


def presence_enabled() -> bool:
    from ichalaunch.config.settings import settings

    return bool(settings.get(SETTING_KEY, False))


def character_status_enabled() -> bool:
    """True when Discord presence and the nested character-status option are on."""
    from ichalaunch.config.settings import settings

    return bool(settings.discord_presence_dll_enabled())


def normalize_broadcast_fields(value: Any = None) -> dict[str, bool]:
    """All six filters; missing keys default on (helper always sent every field)."""
    from ichalaunch.config.settings import normalize_discord_broadcast_fields

    return normalize_discord_broadcast_fields(value)


def broadcast_fields() -> dict[str, bool]:
    """Current persisted broadcast filters."""
    from ichalaunch.config.settings import settings

    return settings.discord_broadcast_fields()


def broadcast_field_allowed(key: str, fields: dict[str, bool] | None = None) -> bool:
    allowed = fields if fields is not None else broadcast_fields()
    return bool(allowed.get(key, True))


def broadcast_flags_word(fields: dict[str, bool] | None = None) -> int:
    """Compact bitmask the helper DLL reads (name=1, guild=2, … zone=32)."""
    allowed = fields if fields is not None else broadcast_fields()
    word = 0
    for key, bit in BROADCAST_FLAG_BITS.items():
        if allowed.get(key, True):
            word |= bit
    return word


def broadcast_flags_path() -> Path:
    from ichalaunch.config.settings import appdata_root

    return appdata_root() / BROADCAST_FLAGS_FILENAME


def write_broadcast_flags(fields: dict[str, bool] | None = None) -> Path:
    """Atomically write the flags word next to discord_wow_status.json."""
    path = broadcast_flags_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{broadcast_flags_word(fields)}\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, encoding="ascii")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def apply_broadcast_fields(
    status: dict[str, Any] | None,
    fields: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    """Drop disabled keys so Name never reaches RPC when Name is off.

    ``fields is None`` means all six on (the helper's historical default).
    The live session passes ``broadcast_fields()`` from settings.
    """
    if not status:
        return None
    allowed = normalize_broadcast_fields(fields)
    out: dict[str, Any] = {}
    for key, value in status.items():
        if key in BROADCAST_FIELD_KEYS and not allowed.get(key, True):
            continue
        out[key] = value
    return out


def should_prompt_discord_presence_opt_in() -> bool:
    """True once, until Save / No Do Not Show Again, or presence is already on.

    Smoke tests and ``ICHALAUNCH_NO_CRASH_REPORT`` runs never prompt — a
    modal on a fresh config would hang waiting for a click.
    """
    from ichalaunch.config.settings import settings
    from ichalaunch.core.crash_report import reporting_suppressed

    if reporting_suppressed():
        return False
    if settings.discord_presence_prompted():
        return False
    if settings.discord_rich_presence_enabled():
        mark_discord_presence_prompted()
        return False
    return True


def mark_discord_presence_prompted() -> None:
    from ichalaunch.config.settings import settings

    settings.set_discord_presence_prompted(True)


def enable_discord_presence_from_opt_in(fields: dict[str, bool] | None = None) -> None:
    """Save on the first-launch prompt: master on, persist filters, never ask again."""
    from ichalaunch.config.settings import settings

    if fields is not None:
        settings.set_discord_broadcast_fields(fields)
    else:
        settings.sync_discord_broadcast_flags_file()
    settings.set_discord_presence_prompted(True)
    settings.set_discord_rich_presence_enabled(True)
    settings.set_discord_rich_presence_character_status(True)


def decline_discord_presence_opt_in() -> None:
    """No Do Not Show Again: leave broadcasting off, never ask again."""
    from ichalaunch.config.settings import settings

    settings.set_discord_presence_prompted(True)
    if not settings.discord_rich_presence_enabled():
        return
    settings.set_discord_rich_presence_enabled(False)


def faction_for_race(race: int) -> str:
    """Map a 1.12.1 / RavenCraft race id to alliance, horde, or empty."""
    if race in _ALLIANCE_RACES:
        return "alliance"
    if race in _HORDE_RACES:
        return "horde"
    return ""


def class_for_id(class_id: int) -> str:
    """Map a Vanilla class id to a display name, or empty if unknown."""
    return _CLASS_NAMES.get(class_id, "")


def race_for_id(race: int) -> str:
    """Map a 1.12.1 / RavenCraft race id to a display name, or empty if unknown."""
    return _RACE_NAMES.get(race, "")


def wow_status_path() -> Path:
    """Launcher-side snapshot path. Tests use ICHALAUNCH_APPDATA isolation."""
    from ichalaunch.config.settings import appdata_root

    return appdata_root() / STATUS_FILENAME


def _sanitize_name(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or "\\" in text or "/" in text:
        return ""
    if not _NAME_RE.fullmatch(text):
        return ""
    return text


def _sanitize_zone(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or "\\" in text or "/" in text:
        return ""
    if not _ZONE_RE.fullmatch(text):
        return ""
    return text


def _sanitize_level(raw: Any) -> int | None:
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= level <= 80:
        return level
    return None


def _sanitize_faction(raw: Any, race: Any = None) -> str:
    text = str(raw or "").strip().lower()
    if text in _FACTION_LABELS:
        return text
    try:
        return faction_for_race(int(race))
    except (TypeError, ValueError):
        pass
    race_name = _sanitize_race(race)
    if race_name:
        return faction_for_race(_RACE_ID_BY_NAME.get(race_name, 0))
    return ""


def _sanitize_labeled(raw: Any, names: frozenset[str], by_id: dict[int, str]) -> str:
    text = str(raw or "").strip()
    if text in names:
        return text
    try:
        return by_id.get(int(raw), "")
    except (TypeError, ValueError):
        return ""


def _sanitize_class(raw: Any) -> str:
    return _sanitize_labeled(raw, _CLASS_NAME_SET, _CLASS_NAMES)


def _sanitize_race(raw: Any) -> str:
    return _sanitize_labeled(raw, _RACE_NAME_SET, _RACE_NAMES)


def _sanitize_guild(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or "\\" in text or "/" in text:
        return ""
    if text.lower() in {"none", "n/a", "null"}:
        return ""
    if not _GUILD_RE.fullmatch(text):
        return ""
    return text


def _clip_field(text: str) -> str:
    return text[:_DISCORD_FIELD_MAX]


def _name_guild_line(name: str, guild: str) -> str:
    """``Name <Guild>``, ``Name``, or ``<Guild>`` when name is filtered off."""
    if name and guild:
        with_guild = f"{name} <{guild}>"
        if len(with_guild) <= _DISCORD_FIELD_MAX:
            return with_guild
        return _clip_field(name)
    if name:
        return _clip_field(name)
    if guild:
        wrapped = f"<{guild}>"
        if len(wrapped) <= _DISCORD_FIELD_MAX:
            return wrapped
        return _clip_field(guild)
    return ""


def _level_suffix(level: int | None, class_name: str = "", faction: str = "") -> str:
    """``Lvl 26 Shaman · Horde``; class/faction still show when level is off."""
    label = _FACTION_LABELS.get(faction, "")
    if level is not None:
        if class_name and label:
            return f"Lvl {level} {class_name} · {label}"
        if class_name:
            return f"Lvl {level} {class_name}"
        if label:
            return f"Lvl {level} · {label}"
        return f"Lvl {level}"
    if class_name and label:
        return f"{class_name} · {label}"
    if class_name:
        return class_name
    if label:
        return label
    return ""


def _details_line(name: str, guild: str, extra: str = "") -> str:
    """Name and optional guild on the upper row. Never includes zone."""
    base = _name_guild_line(name, guild)
    if extra:
        if not base:
            return _clip_field(extra)
        with_extra = f"{base} · {extra}"
        if len(with_extra) <= _DISCORD_FIELD_MAX:
            return with_extra
    return base


def _state_line(zone: str, fallback: str = "") -> str:
    """Zone on the lower Discord row. Fallback only when zone is missing."""
    if zone:
        return _clip_field(zone)
    if fallback:
        return _clip_field(fallback)
    return _clip_field(STATE_IN_GAME)


def read_wow_status(
    path: Path | None = None,
    *,
    now: float | None = None,
    max_age_sec: float = STATUS_MAX_AGE_SEC,
) -> dict[str, Any] | None:
    """Return a sanitized character snapshot, or None if missing/stale."""
    target = path if path is not None else wow_status_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("ok") is not True:
        return None
    if raw.get("in_world") is False:
        return None
    try:
        ts = float(raw.get("ts"))
    except (TypeError, ValueError):
        return None
    stamp = time.time() if now is None else float(now)
    if stamp - ts > float(max_age_sec) or ts - stamp > 5.0:
        return None
    name = _sanitize_name(raw.get("name"))
    zone = _sanitize_zone(raw.get("zone"))
    level = _sanitize_level(raw.get("level"))
    if not name and not zone and level is None:
        return None
    out: dict[str, Any] = {}
    if name:
        out["name"] = name
    if zone:
        out["zone"] = zone
    if level is not None:
        out["level"] = level
    faction = _sanitize_faction(raw.get("faction"), raw.get("race"))
    if faction:
        out["faction"] = faction
    class_name = _sanitize_class(raw.get("class"))
    if class_name:
        out["class"] = class_name
    guild = _sanitize_guild(raw.get("guild"))
    if guild:
        out["guild"] = guild
    race = _sanitize_race(raw.get("race"))
    if race:
        out["race"] = race
    return out


def format_character_activity(
    status: dict[str, Any] | None,
    fields: dict[str, bool] | None = None,
) -> dict[str, str] | None:
    """Map a sanitized snapshot to short Discord details/state strings."""
    filtered = apply_broadcast_fields(status, fields)
    if not filtered:
        return None
    allowed = normalize_broadcast_fields(fields)
    name = _sanitize_name(filtered.get("name"))
    zone = _sanitize_zone(filtered.get("zone"))
    level = _sanitize_level(filtered.get("level"))
    guild = _sanitize_guild(filtered.get("guild"))
    class_name = _sanitize_class(filtered.get("class"))
    # Do not derive faction from race when Faction is off.
    faction = (
        _sanitize_faction(filtered.get("faction"), filtered.get("race"))
        if allowed.get("faction", True)
        else ""
    )
    extra = _level_suffix(level, class_name, faction)
    if not name and not guild and not extra and not zone:
        return None
    # Discord always punches a large circular knockout around small_image.
    # There is no RPC flag to shrink it — do not send small_image/small_text.
    if zone:
        details = _details_line(name, guild, extra)
        state = _state_line(zone)
    else:
        details = _details_line(name, guild)
        state = _state_line("", extra)
    out: dict[str, str] = {
        "large_image": _LARGE_IMAGE_KEY,
        "large_text": _LARGE_IMAGE_TEXT,
    }
    if details:
        out["details"] = details
    if state:
        out["state"] = state
    return out


def presence_activity(
    *,
    game_running: bool,
    wow_status: dict[str, Any] | None = None,
    fields: dict[str, bool] | None = None,
) -> dict[str, str]:
    """Public Discord strings only — no paths, tokens, or coordinates.

    Idle (launcher open, game not running): ``In Launcher``.
    In-game with a snapshot: character details/state.
    In-game without a snapshot: empty dict so Discord shows only
    ``Playing {App Name}`` (drops the launcher line). Never includes
    the word IchaLaunch.
    """
    if game_running:
        formatted = format_character_activity(wow_status, fields=fields)
        if formatted:
            return formatted
        return {}
    return {
        "details": STATE_IN_LAUNCHER,
        "large_image": _LARGE_IMAGE_KEY,
        "large_text": _LARGE_IMAGE_TEXT,
    }


def configured_game_dir() -> Path | None:
    """Configured WoW folder only — never invent a nearby scan."""
    from ichalaunch.config.settings import settings
    from ichalaunch.game.launcher import detect_game

    game = detect_game()
    if game is not None:
        return game
    raw = str(settings.game_path or "").strip()
    return Path(raw) if raw else None


def configured_game_running() -> bool:
    """True when a WoW client looks running.

    Prefer a lock match for the configured folder. If that misses
    (VanillaFixes, Wow.exe vs WoW.exe, different cwd), any
    WoW.exe / Wow.exe / VanillaFixes.exe process counts as in-game.
    """
    from ichalaunch.core.process import wow_exe_running

    game = configured_game_dir()
    try:
        if game is not None and wow_exe_running(game):
            return True
        return bool(wow_exe_running(None))
    except Exception:  # noqa: BLE001
        return False


def load_presence_class() -> type | None:
    try:
        from pypresence import Presence
    except ImportError:
        return None
    return Presence


class DiscordPresenceSession:
    """Connect / update / clear Discord IPC. Failures are silent."""

    def __init__(self, *, presence_factory: PresenceFactory | None = None) -> None:
        self._presence_factory = presence_factory
        self._rpc: Any = None
        self._connected = False
        self._last_activity: dict[str, str] | None = None
        self._ipc_warned = False

    def tick(self) -> None:
        try:
            if not presence_enabled():
                self.clear()
                return
            if not application_id_configured():
                return
            game_running = configured_game_running()
            status = (
                read_wow_status()
                if game_running and character_status_enabled()
                else None
            )
            activity = presence_activity(
                game_running=game_running,
                wow_status=status,
                fields=broadcast_fields() if character_status_enabled() else None,
            )
            if not self._ensure_connected():
                return
            if activity == self._last_activity:
                return
            self._rpc.update(**activity)
            self._last_activity = dict(activity)
        except Exception:  # noqa: BLE001
            self._drop_connection(silent=True)

    def clear(self) -> None:
        """Clear presence and close IPC. Safe when never connected."""
        rpc = self._rpc
        self._last_activity = None
        self._connected = False
        self._rpc = None
        if rpc is None:
            return
        try:
            rpc.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            rpc.close()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_connected(self) -> bool:
        if self._connected and self._rpc is not None:
            return True
        factory = self._presence_factory
        if factory is None:
            cls = load_presence_class()
            if cls is None:
                return False
            factory = cls
        try:
            rpc = factory(str(DISCORD_APPLICATION_ID).strip())
            if rpc is None:
                return False
            rpc.connect()
        except Exception:  # noqa: BLE001
            if not self._ipc_warned:
                self._ipc_warned = True
                log.debug("Discord Rich Presence unavailable", exc_info=True)
            self._rpc = None
            self._connected = False
            return False
        self._rpc = rpc
        self._connected = True
        return True

    def _drop_connection(self, *, silent: bool = True) -> None:
        rpc = self._rpc
        self._rpc = None
        self._connected = False
        self._last_activity = None
        if rpc is None:
            return
        try:
            rpc.close()
        except Exception:  # noqa: BLE001
            if not silent:
                raise
