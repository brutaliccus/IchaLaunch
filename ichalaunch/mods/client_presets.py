"""Client mod presets — bulk desired_mods profiles for the Client page."""

from __future__ import annotations

from typing import Iterable

from ichalaunch.config.settings import settings
from ichalaunch.game.launcher import sync_vanillafixes_enabled_from_desired
from ichalaunch.mods.installer import load_mod_catalog, reconcile_exclusive_desired_mods

PRESET_NONE = "none"
PRESET_BASIC = "basic"
PRESET_BASIC_PLUS = "basic_plus"
PRESET_HD_AIO = "hd_aio"
PRESET_CUSTOM = "custom"

APPLYABLE_PRESETS = (PRESET_NONE, PRESET_BASIC, PRESET_BASIC_PLUS, PRESET_HD_AIO)

# Four core Client Enhancement DLLs (Client Enhancements category).
CLIENT_ENHANCEMENT_MODS: frozenset[str] = frozenset(
    {"superwow", "nampower", "unitxp", "classic_api"}
)

_BASIC_CORE: frozenset[str] = CLIENT_ENHANCEMENT_MODS | frozenset(
    {
        "vanilla_tweaks",
        "dxvk",
        "auction_query_throttle",
        "no1600x1200",
        "wdb_block",
        "script_memory",
    }
)

_BASIC_PLUS_EXTRA: frozenset[str] = frozenset(
    {
        "perfboost",
        "raid_visuals",
        "pretty_night_sky",
        "epoch_water",
        "fog_pushback",
        "hd_dxvk",
        "vanilla_helpers",
        "hd_patch_i",
        "hd_patch_m",
        "hd_patch_p",
    }
)

_HD_AIO_STANDARD: frozenset[str] = frozenset(
    {
        "fog_pushback",
        "vanilla_helpers",
        "hd_patch_a",
        "hd_patch_b",
        "hd_patch_c",
        "hd_patch_d",
        "hd_patch_e",
        "hd_patch_g",
        "hd_patch_s",
        "hd_patch_t",
    }
)

_HD_AIO_ULTRA: frozenset[str] = frozenset({"hd_patch_t_ultra", "hd_patch_u"})

# Explicitly managed off-slots for exclusive / legacy variants.
_EXPLICIT_OFF: frozenset[str] = frozenset(
    {"vanillafixes", "vanilla_tweaks_old", "hd_patch_t_ultra", "hd_patch_u"}
)


def _catalog_hd_patch_ids() -> frozenset[str]:
    return frozenset(
        str(m["id"])
        for m in load_mod_catalog()
        if str(m.get("id") or "").startswith("hd_patch_")
    )


def preset_managed_mod_ids() -> frozenset[str]:
    """All catalog mod ids any preset may turn on or off."""
    return (
        _BASIC_CORE
        | _BASIC_PLUS_EXTRA
        | _HD_AIO_STANDARD
        | _HD_AIO_ULTRA
        | _EXPLICIT_OFF
        | _catalog_hd_patch_ids()
    )


def preset_desired_mods(preset_id: str, *, hd_ultra: bool = False) -> dict[str, bool]:
    """Target desired state for preset-managed mods only (others untouched)."""
    managed = preset_managed_mod_ids()
    out = {mid: False for mid in managed}
    if preset_id == PRESET_NONE:
        return out
    if preset_id == PRESET_BASIC:
        for mid in _BASIC_CORE:
            out[mid] = True
        return out
    if preset_id == PRESET_BASIC_PLUS:
        for mid in _BASIC_CORE | _BASIC_PLUS_EXTRA:
            out[mid] = True
        return out
    if preset_id == PRESET_HD_AIO:
        for mid in _HD_AIO_STANDARD:
            if mid == "hd_patch_t" and hd_ultra:
                continue
            out[mid] = True
        if hd_ultra:
            out["hd_patch_t_ultra"] = True
            out["hd_patch_u"] = True
        return out
    return out


def _matches_preset(
    desired: dict[str, bool], preset_id: str, *, hd_ultra: bool
) -> bool:
    target = preset_desired_mods(preset_id, hd_ultra=hd_ultra)
    managed = preset_managed_mod_ids()
    for mid in managed:
        if bool(desired.get(mid, False)) != bool(target.get(mid, False)):
            return False
    return True


def detect_matching_preset(
    desired: dict[str, bool] | None = None,
) -> tuple[str, bool]:
    """Return (preset_id, hd_ultra) for *desired*, or (custom, False)."""
    d = dict(desired if desired is not None else settings.desired_mods)
    for preset in (PRESET_NONE, PRESET_BASIC, PRESET_BASIC_PLUS):
        if _matches_preset(d, preset, hd_ultra=False):
            return preset, False
    for ultra in (False, True):
        if _matches_preset(d, PRESET_HD_AIO, hd_ultra=ultra):
            return PRESET_HD_AIO, ultra
    return PRESET_CUSTOM, False


def apply_client_preset(preset_id: str, *, hd_ultra: bool = False) -> dict[str, bool]:
    """Apply *preset_id* to desired_mods for preset-managed mods. Returns changes."""
    if preset_id not in APPLYABLE_PRESETS:
        return {}
    target = preset_desired_mods(preset_id, hd_ultra=hd_ultra)
    managed = preset_managed_mod_ids()
    desired = dict(settings.desired_mods)
    before = dict(desired)
    for mid in managed:
        desired[mid] = bool(target.get(mid, False))
    desired = reconcile_exclusive_desired_mods(desired)
    changes: dict[str, bool] = {}
    for mid in managed:
        want = bool(desired.get(mid, False))
        if bool(before.get(mid, False)) != want:
            changes[mid] = want
        settings.set_desired_mod(mid, want)
    settings.set("client_preset", preset_id)
    settings.set(
        "client_preset_hd_ultra",
        bool(hd_ultra) if preset_id == PRESET_HD_AIO else False,
    )
    sync_vanillafixes_enabled_from_desired(desired)
    return changes


def mark_custom_preset() -> None:
    """Persist Custom after a manual mod toggle."""
    if settings.get("client_preset") != PRESET_CUSTOM:
        settings.set("client_preset", PRESET_CUSTOM)


def validate_preset_catalog_ids() -> list[str]:
    """Return catalog ids referenced by presets that are missing from mods.json."""
    catalog = {m["id"] for m in load_mod_catalog()}
    referenced = set()
    for preset in APPLYABLE_PRESETS:
        for ultra in (False, True):
            referenced.update(preset_desired_mods(preset, hd_ultra=ultra))
    referenced |= preset_managed_mod_ids()
    missing = sorted(mid for mid in referenced if mid and mid not in catalog)
    return missing


def preset_mod_ids_for_tests(preset_id: str, *, hd_ultra: bool = False) -> set[str]:
    """Enabled mod ids for a preset (for smoke tests)."""
    return {mid for mid, on in preset_desired_mods(preset_id, hd_ultra=hd_ultra).items() if on}


def downgrade_extra_mods(from_preset: str, to_preset: str) -> Iterable[str]:
    """Mod ids enabled in *from_preset* but not *to_preset*."""
    from_ids = preset_mod_ids_for_tests(from_preset)
    to_ids = preset_mod_ids_for_tests(to_preset)
    return sorted(from_ids - to_ids)
