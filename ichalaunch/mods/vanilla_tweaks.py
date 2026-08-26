"""tubtubs/vanilla-tweaks option schema and CLI argv (V2 defaults).

https://github.com/tubtubs/vanilla-tweaks
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

TWEAKS_SOURCE_ID = "tubtubs"
TWEAKS_REPO = "tubtubs/vanilla-tweaks"

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


def normalize_vanilla_tweaks_options(raw: Any) -> dict[str, Any]:
    """Fill missing keys with V2 defaults and clamp numeric fields."""
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key, default in VANILLA_TWEAKS_DEFAULTS.items():
        if key in _BOOL_KEYS:
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


def options_equal(left: Any, right: Any) -> bool:
    return normalize_vanilla_tweaks_options(left) == normalize_vanilla_tweaks_options(
        right
    )


def options_fingerprint(raw: Any = None) -> str:
    """Stable hash of normalized options — used to skip no-op re-patches."""
    blob = json.dumps(
        normalize_vanilla_tweaks_options(raw),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def tweaks_install_stamp(options: Any = None) -> dict[str, str]:
    """installed_mods fields that mark a tubtubs catalog apply."""
    return {
        "tweaks_source": TWEAKS_SOURCE_ID,
        "repo": TWEAKS_REPO,
        "options_fingerprint": options_fingerprint(options),
    }


def vanilla_tweaks_needs_repatch(meta: Any, options: Any = None) -> bool:
    """True when launcher-owned Tweaks is not the current tubtubs+options apply.

    Missing ``tweaks_source`` (brndd leftover, backfill, or wiped meta) is stale.
    A tubtubs stamp with a matching fingerprint is current. Empty fingerprint on
    an already-stamped tubtubs record is treated as current so we do not loop.
    """
    if not isinstance(meta, dict) or not meta:
        return True
    source = str(meta.get("tweaks_source") or "").strip().lower()
    repo = str(meta.get("repo") or "").strip().lower()
    url = str(meta.get("url") or "").lower()
    is_tubtubs = (
        source == TWEAKS_SOURCE_ID
        or repo == TWEAKS_REPO
        or "tubtubs/vanilla-tweaks" in url
    )
    if not is_tubtubs:
        return True
    stored = str(meta.get("options_fingerprint") or "").strip()
    if not stored:
        return False
    return stored != options_fingerprint(options)


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


def vanilla_tweaks_command(
    patcher: str | Path,
    infile: str | Path,
    options: dict[str, Any] | None = None,
) -> list[str]:
    """Full argv: patcher + flags + WoW.exe (or stock backup) path."""
    return [str(patcher), *vanilla_tweaks_argv(options), str(infile)]


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
