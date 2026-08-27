"""Vanilla Tweaks option schemas and CLI argv.

V2 (``vanilla_tweaks``) is tubtubs/vanilla-tweaks.
Old (``vanilla_tweaks_old``) is pinned brndd/vanilla-tweaks 1.6.0.

https://github.com/tubtubs/vanilla-tweaks
https://github.com/brndd/vanilla-tweaks
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

VANILLA_TWEAKS_ID = "vanilla_tweaks"
VANILLA_TWEAKS_OLD_ID = "vanilla_tweaks_old"
TWEAKS_PAIR: tuple[str, str] = (VANILLA_TWEAKS_ID, VANILLA_TWEAKS_OLD_ID)
TWEAKS_PAIR_SET = frozenset(TWEAKS_PAIR)

TWEAKS_SOURCE_ID = "tubtubs"
TWEAKS_SOURCE_TUBTUBS = "tubtubs"
TWEAKS_SOURCE_BRNDD = "brndd"
TWEAKS_REPO = "tubtubs/vanilla-tweaks"
TWEAKS_OLD_REPO = "brndd/vanilla-tweaks"
TWEAKS_OLD_GIT = "https://github.com/brndd/vanilla-tweaks"
TWEAKS_V2_GIT = "https://github.com/tubtubs/vanilla-tweaks"
TWEAKS_OLD_PIN_URL = (
    "https://github.com/brndd/vanilla-tweaks/releases/download/"
    "v1.6.0/vanilla-tweaks_v1.6.0_x86_64-pc-windows-gnu.zip"
)

# V2: farclip / frill / nameplates / LAA / camera-skip / custom glues / blue moon
# are on. SuperWoW-covered extras (FoV, background sound, channels, quickloot) are off.
VANILLA_TWEAKS_DEFAULTS: dict[str, Any] = {
    "farclip": True,
    # Stock farclip cap is 777. 10000 is the patcher's allowed ceiling, not the preset.
    "farclip_value": 777.0,
    "frilldistance": True,
    "frilldistance_value": 300.0,
    "nameplatedistance": True,
    "nameplatedistance_value": 41.0,
    "largeaddressaware": True,
    "cameraskipfix": True,
    "customglues": True,
    "bluemoon": True,
    "fov_patch": False,
    "fov": 1.925,
    "sound_in_background": False,
    "soundchannels_patch": False,
    "soundchannels": 64,
    "quickloot": False,
    "crossfactionresfix": False,
    "maxcameradistance_patch": False,
    "maxcameradistance": 50.0,
    # UI-only: lets the V2 modal unlock the SuperWoW-covered column.
    "superwow_override": False,
}

# brndd 1.6.0 clap defaults: FoV / sound / channels / quickloot are ON.
# No custom glues, blue moon, or cross-faction resurrect.
# Farclip default is the stock cap (777); 10000 is only the patcher ceiling.
VANILLA_TWEAKS_OLD_DEFAULTS: dict[str, Any] = {
    "farclip": True,
    "farclip_value": 777.0,
    "frilldistance": True,
    "frilldistance_value": 300.0,
    "nameplatedistance": True,
    "nameplatedistance_value": 41.0,
    "largeaddressaware": True,
    "cameraskipfix": True,
    "fov_patch": True,
    "fov": 1.925,
    "sound_in_background": True,
    "soundchannels_patch": True,
    "soundchannels": 64,
    "quickloot": True,
    "maxcameradistance_patch": False,
    "maxcameradistance": 50.0,
}

_BOOL_KEYS = frozenset(
    {
        "farclip",
        "frilldistance",
        "nameplatedistance",
        "largeaddressaware",
        "cameraskipfix",
        "customglues",
        "bluemoon",
        "fov_patch",
        "sound_in_background",
        "soundchannels_patch",
        "quickloot",
        "crossfactionresfix",
        "maxcameradistance_patch",
        "superwow_override",
    }
)
# Persisted with the V2 options but never passed to the patcher, so they are
# ignored by the repatch fingerprint and change detection.
VANILLA_TWEAKS_UI_ONLY_KEYS = frozenset({"superwow_override"})
_OLD_BOOL_KEYS = frozenset(
    {
        "farclip",
        "frilldistance",
        "nameplatedistance",
        "largeaddressaware",
        "cameraskipfix",
        "fov_patch",
        "sound_in_background",
        "soundchannels_patch",
        "quickloot",
        "maxcameradistance_patch",
    }
)
_FLOAT_KEYS = frozenset(
    {
        "farclip_value",
        "frilldistance_value",
        "nameplatedistance_value",
        "fov",
        "maxcameradistance",
    }
)
_INT_KEYS = frozenset({"soundchannels"})
# Optional (off in V2) — SuperWoW typically covers this column.
VANILLA_TWEAKS_OPTIONAL_KEYS = frozenset(
    {
        "fov_patch",
        "sound_in_background",
        "soundchannels_patch",
        "quickloot",
        "crossfactionresfix",
        "maxcameradistance_patch",
    }
)
# Vanilla / TBC / modern — the only values the modal offers.
SOUND_CHANNEL_CHOICES: tuple[int, ...] = (12, 32, 64)

# tubtubs-only clap names — never pass these to brndd 1.6.0.
TUBTUBS_ONLY_FLAGS = frozenset(
    {
        "--fov-patch",
        "--sound-in-background",
        "--soundchannels-patch",
        "--quickloot",
        "--crossfactionresfix",
        "--no-customgluespatch",
        "--no-bluemoonpatch",
    }
)


def superwow_is_active() -> bool:
    """True when SuperWoW is desired or SuperWoWhook.dll is on disk."""
    from ichalaunch.config.settings import settings
    from ichalaunch.game.launcher import detect_game
    from ichalaunch.mods.installer import detect_actual_state

    if settings.desired_mods.get("superwow"):
        return True
    # Pass the live settings path so tests that swap the settings singleton
    # are not pinned to launcher.detect_game's import-time settings binding.
    game = detect_game(settings.game_path)
    if not game:
        return False
    try:
        return bool(detect_actual_state(game).get("superwow"))
    except Exception:  # noqa: BLE001
        return False


_FLOAT_BOUNDS: dict[str, tuple[float, float]] = {
    "farclip_value": (100.0, 10000.0),
    "frilldistance_value": (1.0, 2000.0),
    "nameplatedistance_value": (1.0, 41.0),
    "fov": (0.5, 3.0),
    "maxcameradistance": (1.0, 50.0),
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed != parsed:  # NaN
        parsed = default
    return max(lo, min(hi, parsed))


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(hi, parsed))


def snap_sound_channels(value: Any) -> int:
    """Nearest of 12 / 32 / 64 (Vanilla, TBC, modern). Ties pick the lower."""
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return 64
    return min(SOUND_CHANNEL_CHOICES, key=lambda choice: (abs(choice - parsed), choice))


def _normalize_schema(
    raw: Any,
    defaults: dict[str, Any],
    bool_keys: frozenset[str],
) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key, default in defaults.items():
        if key in bool_keys:
            out[key] = _as_bool(src.get(key, default), bool(default))
        elif key in _FLOAT_KEYS:
            lo, hi = _FLOAT_BOUNDS[key]
            out[key] = _as_float(src.get(key, default), float(default), lo, hi)
        elif key == "soundchannels":
            raw_ch = src.get(key, default)
            out[key] = snap_sound_channels(raw_ch if raw_ch is not None else default)
        elif key in _INT_KEYS:
            out[key] = _as_int(src.get(key, default), int(default), 1, 999)
        else:
            out[key] = src.get(key, default)
    return out


def normalize_vanilla_tweaks_options(raw: Any) -> dict[str, Any]:
    """Fill missing keys with V2 defaults and clamp numeric fields."""
    return _normalize_schema(raw, VANILLA_TWEAKS_DEFAULTS, _BOOL_KEYS)


def normalize_vanilla_tweaks_old_options(raw: Any) -> dict[str, Any]:
    """Fill missing keys with brndd 1.6.0 defaults and clamp numeric fields."""
    return _normalize_schema(raw, VANILLA_TWEAKS_OLD_DEFAULTS, _OLD_BOOL_KEYS)


def _patch_relevant(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in normalized.items()
        if key not in VANILLA_TWEAKS_UI_ONLY_KEYS
    }


def options_equal(left: Any, right: Any) -> bool:
    return _patch_relevant(
        normalize_vanilla_tweaks_options(left)
    ) == _patch_relevant(normalize_vanilla_tweaks_options(right))


def old_options_equal(left: Any, right: Any) -> bool:
    return normalize_vanilla_tweaks_old_options(
        left
    ) == normalize_vanilla_tweaks_old_options(right)


def _fingerprint(normalized: dict[str, Any]) -> str:
    blob = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def options_fingerprint(raw: Any = None) -> str:
    """Stable hash of normalized V2 options — used to skip no-op re-patches."""
    return _fingerprint(_patch_relevant(normalize_vanilla_tweaks_options(raw)))


def old_options_fingerprint(raw: Any = None) -> str:
    """Stable hash of normalized brndd 1.6.0 options."""
    return _fingerprint(normalize_vanilla_tweaks_old_options(raw))


def tweaks_install_stamp(options: Any = None) -> dict[str, str]:
    """installed_mods fields that mark a tubtubs catalog apply."""
    return {
        "tweaks_source": TWEAKS_SOURCE_TUBTUBS,
        "repo": TWEAKS_REPO,
        "options_fingerprint": options_fingerprint(options),
    }


def tweaks_old_install_stamp(options: Any = None) -> dict[str, str]:
    """installed_mods fields that mark a chosen brndd 1.6.0 apply."""
    return {
        "tweaks_source": TWEAKS_SOURCE_BRNDD,
        "repo": TWEAKS_OLD_REPO,
        "options_fingerprint": old_options_fingerprint(options),
    }


def meta_tweaks_source(meta: Any) -> str | None:
    """``tubtubs`` / ``brndd`` from install metadata, or None if unknown."""
    if not isinstance(meta, dict) or not meta:
        return None
    source = str(meta.get("tweaks_source") or "").strip().lower()
    if source in {TWEAKS_SOURCE_TUBTUBS, TWEAKS_SOURCE_BRNDD}:
        return source
    repo = str(meta.get("repo") or "").strip().lower()
    url = str(meta.get("url") or meta.get("source_url") or "").lower()
    blob = f"{repo} {url}"
    if "tubtubs/vanilla-tweaks" in blob:
        return TWEAKS_SOURCE_TUBTUBS
    if "brndd/vanilla-tweaks" in blob:
        return TWEAKS_SOURCE_BRNDD
    return None


def installed_tweaks_source() -> str | None:
    """Which patcher last stamped ``installed_mods``, if identifiable."""
    from ichalaunch.config.settings import settings

    installed = settings.installed_mods
    old_src = meta_tweaks_source(installed.get(VANILLA_TWEAKS_OLD_ID))
    v2_src = meta_tweaks_source(installed.get(VANILLA_TWEAKS_ID))
    # A stray Old/brndd record must not outrank an identified tubtubs apply.
    if v2_src == TWEAKS_SOURCE_TUBTUBS:
        return TWEAKS_SOURCE_TUBTUBS
    if old_src == TWEAKS_SOURCE_BRNDD:
        return TWEAKS_SOURCE_BRNDD
    if v2_src == TWEAKS_SOURCE_BRNDD:
        return TWEAKS_SOURCE_BRNDD
    if old_src == TWEAKS_SOURCE_TUBTUBS:
        return TWEAKS_SOURCE_TUBTUBS
    return old_src or v2_src


def leftover_brndd_under_v2() -> dict[str, Any] | None:
    """Pre-1.3.0 brndd stamp still stored on ``vanilla_tweaks``."""
    from ichalaunch.config.settings import settings

    meta = settings.installed_mods.get(VANILLA_TWEAKS_ID) or {}
    if meta_tweaks_source(meta) == TWEAKS_SOURCE_BRNDD:
        return dict(meta) if isinstance(meta, dict) else None
    return None


def vanilla_tweaks_is_ours(mid: str) -> bool:
    """True when this launcher (or the user via the Client tab) owns *mid*."""
    from ichalaunch.config.settings import settings

    if settings.desired_mods.get(mid):
        return True
    if mid in settings.user_set_mods:
        return True
    if mid in settings.installed_mods:
        return True
    if (
        mid == VANILLA_TWEAKS_OLD_ID
        and leftover_brndd_under_v2() is not None
        and (
            settings.desired_mods.get(VANILLA_TWEAKS_OLD_ID)
            or VANILLA_TWEAKS_OLD_ID in settings.user_set_mods
        )
    ):
        return True
    return False


def vanilla_tweaks_detects_as(mid: str, exe_differs: bool) -> bool:
    """Which Tweaks id the patched exe should light — never both."""
    if mid not in TWEAKS_PAIR_SET or not exe_differs:
        return False
    from ichalaunch.config.settings import settings

    desired = settings.desired_mods
    disk = installed_tweaks_source()
    want_v2 = bool(desired.get(VANILLA_TWEAKS_ID))
    want_old = bool(desired.get(VANILLA_TWEAKS_OLD_ID))

    if disk == TWEAKS_SOURCE_TUBTUBS:
        return mid == VANILLA_TWEAKS_ID and vanilla_tweaks_is_ours(VANILLA_TWEAKS_ID)
    if disk == TWEAKS_SOURCE_BRNDD:
        old_meta = settings.installed_mods.get(VANILLA_TWEAKS_OLD_ID)
        chosen_old = meta_tweaks_source(old_meta) == TWEAKS_SOURCE_BRNDD
        leftover = leftover_brndd_under_v2() is not None and not chosen_old
        # Chosen Old stays Old even if the user later checks V2 (switch, not migrate).
        if chosen_old:
            return mid == VANILLA_TWEAKS_OLD_ID and vanilla_tweaks_is_ours(
                VANILLA_TWEAKS_OLD_ID
            )
        # Unintentional leftover on the V2 id: credit V2 so force-migrate runs.
        if leftover and want_old and not want_v2:
            return mid == VANILLA_TWEAKS_OLD_ID and vanilla_tweaks_is_ours(
                VANILLA_TWEAKS_OLD_ID
            )
        if leftover:
            return mid == VANILLA_TWEAKS_ID and vanilla_tweaks_is_ours(
                VANILLA_TWEAKS_ID
            )
        return mid == VANILLA_TWEAKS_OLD_ID and vanilla_tweaks_is_ours(
            VANILLA_TWEAKS_OLD_ID
        )
    # Missing tweaks_source is not Old — leftover / unknown stamps credit V2
    # unless the user only asked for Old.
    if want_old and not want_v2:
        return mid == VANILLA_TWEAKS_OLD_ID and vanilla_tweaks_is_ours(
            VANILLA_TWEAKS_OLD_ID
        )
    return mid == VANILLA_TWEAKS_ID and vanilla_tweaks_is_ours(VANILLA_TWEAKS_ID)


def vanilla_tweaks_needs_repatch(meta: Any, options: Any = None) -> bool:
    """True when launcher-owned Tweaks is not the current tubtubs+options apply.

    Missing ``tweaks_source`` (brndd leftover, backfill, or wiped meta) is stale.
    A tubtubs stamp with a matching fingerprint is current. Empty fingerprint on
    an already-stamped tubtubs record is treated as current so we do not loop.
    """
    if not isinstance(meta, dict) or not meta:
        return True
    source = meta_tweaks_source(meta)
    is_tubtubs = source == TWEAKS_SOURCE_TUBTUBS
    if not is_tubtubs:
        return True
    stored = str(meta.get("options_fingerprint") or "").strip()
    if not stored:
        return False
    return stored != options_fingerprint(options)


def vanilla_tweaks_old_needs_repatch(meta: Any, options: Any = None) -> bool:
    """True when Old is not the current brndd 1.6.0+options apply.

    A chosen brndd stamp is current when the fingerprint matches (or is empty).
    A tubtubs stamp is always stale — switching tools must re-patch.
    """
    if not isinstance(meta, dict) or not meta:
        leftover = leftover_brndd_under_v2()
        if leftover is not None:
            meta = leftover
        else:
            return True
    source = meta_tweaks_source(meta)
    if source == TWEAKS_SOURCE_TUBTUBS:
        return True
    if source != TWEAKS_SOURCE_BRNDD:
        return True
    stored = str(meta.get("options_fingerprint") or "").strip()
    if not stored:
        return False
    return stored != old_options_fingerprint(options)


def preferred_tweaks_variant(
    desired: dict[str, Any] | None = None,
    *,
    prefer: str | None = None,
) -> str | None:
    """Which Tweaks id to keep when both are somehow desired."""
    from ichalaunch.config.settings import settings

    desired = desired if desired is not None else settings.desired_mods
    wanted = [mid for mid in TWEAKS_PAIR if desired.get(mid)]
    if not wanted:
        return None
    if len(wanted) == 1:
        return wanted[0]
    if prefer in TWEAKS_PAIR_SET and prefer in wanted:
        return prefer
    for mid in reversed(settings.user_set_mods):
        if mid in wanted:
            return mid
    return VANILLA_TWEAKS_ID


def _fmt_f32(value: float) -> str:
    text = f"{float(value):.6g}"
    return text if text else "0"


def vanilla_tweaks_argv(options: dict[str, Any] | None = None) -> list[str]:
    """CLI flags only (no infile / outfile). Matches tubtubs V2 clap names."""
    opts = normalize_vanilla_tweaks_options(options)
    argv: list[str] = []

    if opts["farclip"]:
        argv.extend(["--farclip", _fmt_f32(opts["farclip_value"])])
    else:
        argv.append("--no-farclip")

    if opts["frilldistance"]:
        argv.extend(["--frilldistance", _fmt_f32(opts["frilldistance_value"])])
    else:
        argv.append("--no-frilldistance")

    if opts["nameplatedistance"]:
        argv.extend(["--nameplatedistance", _fmt_f32(opts["nameplatedistance_value"])])
    else:
        argv.append("--no-nameplatedistance")

    if not opts["largeaddressaware"]:
        argv.append("--no-largeaddressaware")
    if not opts["cameraskipfix"]:
        argv.append("--no-cameraskipfix")
    if not opts["customglues"]:
        argv.append("--no-customgluespatch")
    if not opts["bluemoon"]:
        argv.append("--no-bluemoonpatch")

    if opts["fov_patch"]:
        argv.extend(["--fov-patch", "--fov", _fmt_f32(opts["fov"])])
    if opts["sound_in_background"]:
        argv.append("--sound-in-background")
    if opts["soundchannels_patch"]:
        argv.extend(["--soundchannels-patch", "--soundchannels", str(opts["soundchannels"])])
    if opts["quickloot"]:
        argv.append("--quickloot")
    if opts["crossfactionresfix"]:
        argv.append("--crossfactionresfix")
    if opts["maxcameradistance_patch"]:
        argv.extend(["--maxcameradistance", _fmt_f32(opts["maxcameradistance"])])
    return argv


def vanilla_tweaks_old_argv(options: dict[str, Any] | None = None) -> list[str]:
    """CLI flags for brndd 1.6.0 (opt-out ``--no-*``, no tubtubs-only names)."""
    opts = normalize_vanilla_tweaks_old_options(options)
    argv: list[str] = []

    if opts["farclip"]:
        argv.extend(["--farclip", _fmt_f32(opts["farclip_value"])])
    else:
        argv.append("--no-farclip")

    if opts["frilldistance"]:
        argv.extend(["--frilldistance", _fmt_f32(opts["frilldistance_value"])])
    else:
        argv.append("--no-frilldistance")

    if opts["nameplatedistance"]:
        argv.extend(["--nameplatedistance", _fmt_f32(opts["nameplatedistance_value"])])
    else:
        argv.append("--no-nameplatedistance")

    if not opts["largeaddressaware"]:
        argv.append("--no-largeaddressaware")
    if not opts["cameraskipfix"]:
        argv.append("--no-cameraskipfix")

    if opts["fov_patch"]:
        argv.extend(["--fov", _fmt_f32(opts["fov"])])
    else:
        argv.append("--no-fov")

    if not opts["sound_in_background"]:
        argv.append("--no-sound-in-background")

    if opts["soundchannels_patch"]:
        argv.extend(["--soundchannels", str(opts["soundchannels"])])
    else:
        argv.append("--no-soundchannels")

    if not opts["quickloot"]:
        argv.append("--no-quickloot")

    if opts["maxcameradistance_patch"]:
        argv.extend(["--maxcameradistance", _fmt_f32(opts["maxcameradistance"])])
    return argv


def vanilla_tweaks_command(
    patcher: str | Path,
    infile: str | Path,
    options: dict[str, Any] | None = None,
) -> list[str]:
    """Full argv: patcher + V2 flags + WoW.exe (or stock backup) path."""
    return [str(patcher), *vanilla_tweaks_argv(options), str(infile)]


def vanilla_tweaks_old_command(
    patcher: str | Path,
    infile: str | Path,
    options: dict[str, Any] | None = None,
) -> list[str]:
    """Full argv: patcher + brndd 1.6.0 flags + WoW.exe (or stock backup) path."""
    return [str(patcher), *vanilla_tweaks_old_argv(options), str(infile)]


def tweaks_patch_command(
    mod_id: str,
    patcher: str | Path,
    infile: str | Path,
    options: dict[str, Any] | None = None,
) -> list[str]:
    """Patcher argv for the selected Tweaks variant."""
    if mod_id == VANILLA_TWEAKS_OLD_ID:
        return vanilla_tweaks_old_command(patcher, infile, options)
    return vanilla_tweaks_command(patcher, infile, options)


def vanilla_tweaks_infile(
    game: Path,
    wow: Path,
    backup_name: str = "WoW-OriginalBackup.exe",
) -> Path:
    """Feed the stock backup to the patcher so option changes do not stack."""
    if game.is_dir():
        needle = backup_name.lower()
        try:
            for candidate in game.iterdir():
                if candidate.is_file() and candidate.name.lower() == needle:
                    return candidate
        except OSError:
            pass
    return wow
