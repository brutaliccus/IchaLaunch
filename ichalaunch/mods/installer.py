"""Client mod catalog + desired-state applicator."""

from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests

from ichalaunch.addons.git_refs import (
    extract_semver_label,
    is_preferred_release_alias,
    is_usable_release_tag,
    is_version_tag,
    looks_like_timestamp_label,
)
from ichalaunch.addons.github import (
    GitHubRateLimitError,
    fetch_repo_readme,
    github_get,
    github_latest_version_tag,
    github_remote_tip,
    parse_github_url,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.backup import create_backup, list_backups, restore_backup
from ichalaunch.core.filesystem import (
    LOCK_AV_VERIFY_MESSAGE,
    LOCK_AV_VERIFY_TITLE,
    copy_file_tolerant,
    ensure_data_writable,
    ensure_writable,
    is_access_denied,
    is_sharing_violation,
    replace_file,
    extract_tar,
    extract_zip,
    note_pending_toc_mismatch,
    place_install_addon_root,
    resolve_install_addon_roots,
    TOC_FOLDER_MISMATCH_MSG,
    invalidate_dir_listing,
    is_lock_or_av_error,
    exact_name_present,
    listed_basenames,
    listed_exact_basenames,
    mirror_dlls_txt_updates,
    name_present,
    PermissionScanResult,
    read_dlls_txt,
    resolve_ci,
    sha256_file,
    scan_game_permissions,
    remove_path_strict,
    safe_remove,
    sanitize_filename,
    update_dlls_txt,
    user_facing_os_error,
    validate_pe_binary,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import (
    _download_headers,
    download_bytes,
    download_bytes_cb,
    download_file,
    file_in_use_hint,
    google_drive_url,
    run_windows_exe,
    status_only,
    wow_exe_running,
)
from ichalaunch.game.launcher import (
    detect_game,
    detect_vf_disk_mode,
    resolve_addons_dir,
    sync_vanillafixes_enabled_from_desired,
    vf_mode_display,
    wow_exe_in,
)

ProgressCb = Callable[[str], None]
UA = {"User-Agent": "IchaLaunch/0.1"}
# Re-entrancy guard: apply is one-shot. A timer must never stack retries.
_APPLY_IN_PROGRESS = False
# Turtle/RavenCraft ships numeric Data patches. Letter slots are community/HD.
_STOCK_DATA_MPQ_RE = re.compile(r"^patch(-[0-9])?\.mpq$", re.IGNORECASE)
LOCAL_BUILD_HINT = "Run: python tools/build_weirdutils.py"


def is_stock_data_mpq(rel: str | Path) -> bool:
    """True for official numeric client patches (``patch.mpq``, ``patch-2``…``patch-9``)."""
    name = Path(str(rel).replace("\\", "/")).name
    return bool(_STOCK_DATA_MPQ_RE.fullmatch(name))


def resolve_local_source_path(source: dict[str, Any]) -> Path:
    """Resolve a catalog ``type=local`` path.

    Frozen builds use the copy packed into the exe (``ichalaunch/data/weirdutils``).
    Source checkouts fall back to the gitignored ``tools/_weirdutils/out`` file.
    """
    import sys

    from ichalaunch.core.paths import data_file, repo_root

    raw = str(source.get("path") or "").strip()
    filename = sanitize_filename(
        str(source.get("filename") or (Path(raw).name if raw else ""))
    )
    if filename.lower().endswith(".dll"):
        bundled = data_file("weirdutils", filename)
        if bundled.is_file():
            return bundled.resolve()
    if not raw:
        raise FileNotFoundError(f"Local mod source is missing a path. {LOCAL_BUILD_HINT}")
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root() / path
    path = path.resolve()
    if not path.is_file():
        if getattr(sys, "frozen", False):
            raise FileNotFoundError(
                f"Bundled WeirdUtils DLL missing: {filename or raw}. Update IchaLaunch."
            )
        raise FileNotFoundError(f"Local WeirdUtils DLL missing: {raw}. {LOCAL_BUILD_HINT}")
    return path


def _local_source_override(source: dict[str, Any]) -> Path | None:
    """Prefer a repo-local DLL when catalog ``local`` or WeirdUtils prebuilt exists."""
    explicit = str(source.get("local") or source.get("path") or "").strip()
    if explicit and source.get("type") != "local":
        try:
            return resolve_local_source_path({"path": explicit})
        except FileNotFoundError:
            pass
    filename = sanitize_filename(
        str(source.get("filename") or Path(str(source.get("url") or "")).name)
    )
    if not filename.lower().endswith(".dll"):
        return None
    from ichalaunch.core.paths import repo_root

    candidate = repo_root() / "tools" / "_weirdutils" / "prebuilt" / filename
    return candidate if candidate.is_file() else None


def _mpq_dest_basename(dest_rel: str | Path) -> str:
    """Game Data basename for an MPQ destination (``Data/patch-Y.mpq`` → ``patch-Y.mpq``)."""
    name = sanitize_filename(Path(str(dest_rel).replace("\\", "/")).name)
    if name and not name.lower().endswith(".mpq"):
        name = f"{name}.mpq"
    return name


def stage_mpq_before_data(artifact: Path, dest_rel: str | Path, work: Path) -> Path:
    """Rename the downloaded MPQ to the install basename before any Data/ copy.

    The Pretty Night Sky host file is still named patch-9.mpq (same as the
    official Turtle/RavenCraft archive). Staging under patch-Z in the work
    directory means Data/ never sees that stock name (patch-Y is Fog Pushback).
    """
    dest_name = _mpq_dest_basename(dest_rel)
    if not dest_name:
        raise RuntimeError("MPQ destination is missing a filename")
    if is_stock_data_mpq(dest_name):
        raise RuntimeError(
            f"Refusing to stage official client MPQ name {dest_name}"
        )
    staged = work / dest_name
    try:
        same = artifact.resolve() == staged.resolve()
    except OSError:
        same = artifact.name.lower() == dest_name.lower()
    if same:
        if is_stock_data_mpq(staged.name):
            raise RuntimeError(
                f"Refusing to copy official client MPQ name {staged.name} into Data"
            )
        return staged
    if is_stock_data_mpq(artifact.name):
        log.info(
            "Renaming downloaded %s → %s before Data copy",
            artifact.name,
            dest_name,
        )
    if staged.exists():
        staged.unlink()
    artifact.replace(staged)
    return staged


# Letter survey (v1.2.7): stock Turtle/RavenCraft is numeric (patch.mpq, patch-2…9).
# Launcher HD/client tweaks already take A B C D E G H I J(detect) L M N O P S T U W Y.
# Unused: F K Q R V X Z. Pretty Night Sky uses Z (latest free). Fog Pushback keeps Y.
_PRETTY_NIGHT_SKY_DEST = "Data/patch-Z.mpq"
_LEGACY_PRETTY_NIGHT_SKY_DEST = "Data/patch-Y.mpq"
_NIGHT_SKY_MIGRATE_MAX_BYTES = 16 * 1024 * 1024
# Legion skybox textures; Fog Pushback is Light*.dbc (never rename that Y).
_NIGHT_SKY_PAYLOAD_MARKERS = (
    b"environments\\stars",
    b"environments/stars",
    b"textures\\stars",
    b"textures/stars",
)
_FOG_PUSHBACK_PAYLOAD_MARKERS = (
    b"light.dbc",
    b"lightparams.dbc",
    b"lightintband.dbc",
    b"lightskybox.dbc",
)


def looks_like_pretty_night_sky_mpq(path: Path) -> bool:
    """True only when *path* looks like the Pretty Night Sky payload, not Fog Pushback."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 4 or size > _NIGHT_SKY_MIGRATE_MAX_BYTES:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if data[:3] != b"MPQ":
        return False
    low = data.lower()
    if any(marker in low for marker in _FOG_PUSHBACK_PAYLOAD_MARKERS):
        return False
    return any(marker in low for marker in _NIGHT_SKY_PAYLOAD_MARKERS)


def migrate_legacy_pretty_night_sky_y(game_path: Path) -> bool:
    """One-time rename of a leftover night-sky ``patch-Y.mpq`` to ``patch-Z.mpq``.

    Fog Pushback owns Y. Only rename when the file is clearly the night-sky
    payload and Z is free. Unknown or fog-like Y is left alone.
    """
    game = Path(game_path)
    if resolve_ci(game, _PRETTY_NIGHT_SKY_DEST) is not None:
        return False
    src = resolve_ci(game, _LEGACY_PRETTY_NIGHT_SKY_DEST)
    if src is None or not src.is_file():
        return False
    if is_stock_data_mpq(src.name) or not looks_like_pretty_night_sky_mpq(src):
        return False
    dest = game / _PRETTY_NIGHT_SKY_DEST
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if src.resolve() == dest.resolve():
            return False
    except OSError:
        pass
    src.replace(dest)
    invalidate_dir_listing(dest.parent)
    log.info("Renamed leftover Pretty Night Sky %s → %s", src.name, dest.name)
    return True


def tweaked_exe_snapshot(game: Path) -> dict[Path, tuple[int, float]]:
    """Every ``*_tweaked.exe`` beside the client, with size and mtime.

    Taken before the patcher runs so its output can be identified by what
    actually changed rather than by guessing the filename it chose.
    """
    out: dict[Path, tuple[int, float]] = {}
    try:
        children = list(game.iterdir())
    except OSError:
        return out
    for child in children:
        if not child.name.lower().endswith("_tweaked.exe"):
            continue
        try:
            if not child.is_file():
                continue
            st = child.stat()
        except OSError:
            continue
        out[child] = (st.st_size, st.st_mtime)
    return out


def patched_exe_from_run(
    game: Path,
    infile: Path,
    wow: Path,
    before: dict[Path, tuple[int, float]],
) -> Path | None:
    """The executable the patcher just wrote, or None if it wrote nothing.

    The patcher takes an input path and no output flag, so the name of its
    output is whatever it derives from that input. Feeding it the stock backup,
    which is what stops option changes from stacking, means the name follows the
    backup and not the client. Named candidates are tried in that order first.

    Only files that appeared or changed during this run are eligible. A
    ``WoW_tweaked.exe`` left behind by an earlier run carries that run's
    options, so installing it would quietly apply the wrong settings.

    Anything else fresh is accepted as a last resort, so a filename change
    upstream degrades into a working install rather than a silent no-op.
    """
    after = tweaked_exe_snapshot(game)
    fresh = {path for path, meta in after.items() if before.get(path) != meta}
    if not fresh:
        return None
    for candidate in (
        game / f"{Path(infile).stem}_tweaked.exe",
        game / f"{Path(wow).stem}_tweaked.exe",
        game / "WoW_tweaked.exe",
    ):
        if candidate in fresh:
            return candidate
    return max(fresh, key=lambda path: after[path][1])


def swap_patched_client_exe(tweaked: Path, wow: Path) -> None:
    """Put the patcher's output in place of the client binary, in one step.

    Unlinking the client first meant that a rename which then failed left the
    game with no client binary at all. That window is not theoretical: the
    patched exe has just been written by an unsigned third-party patcher, which
    is the single most likely moment for antivirus to hold it open, and the
    unlink had already happened by then.

    Path.replace is atomic within a directory on both Windows and Linux, so
    WoW.exe is either the old build or the new one and never absent. A failure
    now raises with the original still in place, which install_mod already
    knows how to roll back.
    """
    tweaked.replace(wow)


def _install_copy(src: Path, dest: Path, game_path: Path | None = None) -> None:
    """Copy into the game tree. DLLs/EXEs are never LoadLibrary'd; lock/AV → OSError skip."""
    if is_stock_data_mpq(dest):
        raise OSError(
            13,
            f"Refusing to overwrite official client MPQ {dest.name}",
            str(dest),
        )
    if dest.suffix.lower() in {".dll", ".exe"}:
        if not copy_file_tolerant(src, dest):
            hint_paths: list[Path] = [dest]
            if game_path is not None:
                hint_paths.append(game_path / "WoW.exe")
            hint = file_in_use_hint(*hint_paths, game_path=game_path)
            log.warning(
                "Replace blocked for %s (wow_running=%s): %s",
                dest.name,
                wow_exe_running(game_path),
                hint,
            )
            raise OSError(
                13,
                f"Could not replace {dest.name} — file in use by another process. {hint}",
                str(dest),
            )
        if game_path is not None:
            ensure_data_writable(dest, game_path)
        invalidate_dir_listing(dest.parent)
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            # Always clear dest attributes — not only Data/*.mpq. GlueXML under
            # Data/Interface/ is often READONLY+HIDDEN+SYSTEM; copy2-in-place
            # then raises PermissionError and was reported as "file in use".
            ensure_writable(dest)
        replace_file(src, dest)
        if dest.exists():
            ensure_writable(dest)
    except OSError as exc:
        sharing = is_sharing_violation(exc) or getattr(exc, "winerror", None) == 225
        if sharing or is_lock_or_av_error(exc) or is_access_denied(exc):
            hint = file_in_use_hint(dest, game_path=game_path)
            running = wow_exe_running(game_path)
            if sharing or running:
                detail = (
                    f"Could not replace {dest.name} — file in use by another process. {hint}"
                )
            else:
                detail = (
                    f"Could not replace {dest.name} — write was denied after clearing the "
                    f"read-only attribute. {hint}"
                )
            raise OSError(
                getattr(exc, "errno", None) or 13,
                detail,
                str(dest),
            ) from exc
        raise
    invalidate_dir_listing(dest.parent)


@dataclass
class ModUpdateCheckResult:
    updates: list[dict[str, Any]] = field(default_factory=list)
    rate_limited: bool = False
    skipped_recent: bool = False
    status_message: str | None = None
    checked: int = 0
    skipped: int = 0


def _data_path() -> Path:
    from ichalaunch.core.paths import data_file

    return data_file("mods.json")


def load_mod_catalog(*, include_hidden: bool = False) -> list[dict[str, Any]]:
    catalog = json.loads(_data_path().read_text(encoding="utf-8"))
    if not include_hidden:
        catalog = [m for m in catalog if not m.get("hidden")]
    seen = {m["id"] for m in catalog if m.get("id")}
    for mod in settings.user_mods:
        mid = mod.get("id")
        if not mid or mid in seen:
            continue
        if not include_hidden and mod.get("hidden"):
            continue
        catalog.append(dict(mod))
        seen.add(mid)
    return catalog


def get_mod(mod_id: str) -> dict[str, Any] | None:
    for m in load_mod_catalog():
        if m["id"] == mod_id:
            return m
    return None


def mod_catalog_map() -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in load_mod_catalog() if m.get("id")}


def _collect_mod_dependencies(
    mod_id: str,
    catalog: dict[str, dict[str, Any]],
    seen: set[str] | None = None,
) -> list[str]:
    """Return dependency mod ids in install order (deps before dependents)."""
    if seen is None:
        seen = set()
    if mod_id in seen or mod_id not in catalog:
        return []
    seen.add(mod_id)
    ordered: list[str] = []
    for dep in catalog[mod_id].get("dependencies") or []:
        if dep not in catalog:
            continue
        ordered.extend(_collect_mod_dependencies(dep, catalog, seen))
        ordered.append(dep)
    return ordered


_HD_PATCH_PREFIX = "hd_patch_"
_HD_PATCH_E_ID = "hd_patch_e"
_FOG_PUSHBACK_ID = "fog_pushback"
# Catalog ``includes`` that already ship inside the parent HD MPQ — do not
# auto-enable the standalone companion. Patch-E bundles fog; patch-Y is
# optional and applied only when the user enables Fog Pushback.
_BUNDLED_IN_PARENT_MPQ: frozenset[str] = frozenset({_FOG_PUSHBACK_ID})
_VANILLA_HELPERS_ID = "vanilla_helpers"
_VANILLAFIXES_ID = "vanillafixes"
_VANILLA_TWEAKS_ID = "vanilla_tweaks"
_VANILLA_TWEAKS_OLD_ID = "vanilla_tweaks_old"
_TWEAKS_IDS = frozenset({_VANILLA_TWEAKS_ID, _VANILLA_TWEAKS_OLD_ID})
_DXVK_ID = "dxvk"
_HD_DXVK_ID = "hd_dxvk"
_DXVK_CURSOR_ID = "dxvk_big_cursor"
# Fingerprint in ichalaunch/data/dxvk.conf — distinguishes HD 2.7.1 from VF-bundled DXVK.
_HD_DXVK_CONF_MARKER = "DXVK 2.7.1"
_HD_DXVK_DLL_MARKER = b"2.7.1"
_DXVK_CURSOR_CONF_MARKER = "enlargeHardwareCursor"
# Last writer of d3d9.dll wins; keep this install order when several are planned.
_D3D9_LAYER_RANK = {_DXVK_ID: 0, _HD_DXVK_ID: 1, _DXVK_CURSOR_ID: 2}
# Destination is lowercase patch-v; Patch-V.mpq is a community WMO override.
_HD_PATCH_C_EXACT_NAMES = ("patch-v.mpq", "patch-C.mpq", "Patch-C.mpq")

def vanillafixes_dxvk_both_enabled(desired: dict[str, bool] | None = None) -> bool:
    """True when regular VanillaFixes and the DXVK bundle are both desired."""
    d = desired if desired is not None else settings.desired_mods
    return bool(d.get(_VANILLAFIXES_ID)) and bool(d.get(_DXVK_ID))


def _reconcile_vf_dxvk_detected(state: dict[str, bool]) -> dict[str, bool]:
    """DXVK ships VanillaFixes.exe — only one catalog mod should read as installed."""
    out = dict(state)
    if out.get(_DXVK_ID):
        out[_VANILLAFIXES_ID] = False
    return out


def _vf_sync_action_log_label(mod_id: str, action: str) -> str | None:
    """Grep-friendly install/remove label for VanillaFixes vs DXVK mod sync."""
    if mod_id == _VANILLAFIXES_ID:
        prefix = "+" if action == "install" else "-"
        return f"{prefix} VanillaFixes (standard)"
    if mod_id == _DXVK_ID:
        if action == "install":
            return "+ VanillaFixes + DXVK (Vulkan)"
        return "- DXVK layer (d3d9.dll, dxvk.conf)"
    return None


def _log_vf_on_disk_summary(game: Path, context: str) -> None:
    log.info("VF on-disk [%s]: %s", context, vf_mode_display(detect_vf_disk_mode(game)))


def _is_alternate_hd_variant(mod_id: str) -> bool:
    """True for non-default HD patch variants that share an MPQ with a sibling."""
    return mod_id.endswith("_less_thicc") or mod_id.endswith("_ultra")


def _variant_on_disk_by_size(
    a: str,
    b: str,
    game_path: Path,
    catalog: dict[str, dict[str, Any]],
) -> str | None:
    """Which of two exclusive variants the file on disk actually is, by its size.

    Variants that share a destination cannot be told apart by filename: both
    patch-T tiers install Data/patch-T.mpq, and so do both patch-L bodies. A
    file the launcher installed itself is identified by its install record, but
    a hand-installed one has no record and was previously attributed by
    guesswork -- which lands on the wrong tier for anyone who downloaded Ultra
    Base from the publisher directly.

    Their published sizes differ, so an exact length match names the variant.
    This is checked BEFORE the install record on purpose: swapping the file by
    hand after installing through the launcher leaves the record stale, and the
    bytes on disk are what the game will actually load.

    Returns None whenever the answer is not unambiguous -- sizes missing, sizes
    equal, destinations different, file unreadable, or a length matching
    neither -- so every existing tie-break path still runs.
    """
    mod_a, mod_b = catalog.get(a) or {}, catalog.get(b) or {}
    size_a, size_b = mod_a.get("size_bytes"), mod_b.get("size_bytes")
    if not size_a or not size_b or size_a == size_b:
        return None
    dest = mod_a.get("destination")
    if not dest or mod_b.get("destination") != dest:
        return None
    found = resolve_ci(game_path, dest)
    if found is None:
        return None
    try:
        actual = found.stat().st_size
    except OSError:
        return None
    if actual == size_a:
        return a
    if actual == size_b:
        return b
    return None


def _pick_exclusive_detect_winner(
    a: str,
    b: str,
    desired: dict[str, bool],
    *,
    game_path: Path | None = None,
) -> str:
    """Pick which conflicting mod id should read as installed when both match disk."""
    if frozenset({a, b}) == _TWEAKS_IDS:
        from ichalaunch.mods.vanilla_tweaks import (
            TWEAKS_SOURCE_BRNDD,
            TWEAKS_SOURCE_TUBTUBS,
            VANILLA_TWEAKS_ID,
            VANILLA_TWEAKS_OLD_ID,
            installed_tweaks_source,
        )

        disk = installed_tweaks_source()
        if disk == TWEAKS_SOURCE_TUBTUBS:
            return VANILLA_TWEAKS_ID
        if disk == TWEAKS_SOURCE_BRNDD:
            from ichalaunch.mods.vanilla_tweaks import leftover_brndd_under_v2, meta_tweaks_source

            chosen_old = meta_tweaks_source(
                settings.installed_mods.get(VANILLA_TWEAKS_OLD_ID)
            ) == TWEAKS_SOURCE_BRNDD
            leftover = leftover_brndd_under_v2() is not None and not chosen_old
            if leftover and desired.get(VANILLA_TWEAKS_OLD_ID) and not desired.get(
                VANILLA_TWEAKS_ID
            ):
                return VANILLA_TWEAKS_OLD_ID
            if leftover:
                return VANILLA_TWEAKS_ID
            return VANILLA_TWEAKS_OLD_ID
        # Missing tweaks_source is not Old.
        if desired.get(VANILLA_TWEAKS_OLD_ID) and not desired.get(VANILLA_TWEAKS_ID):
            return VANILLA_TWEAKS_OLD_ID
        return VANILLA_TWEAKS_ID
    if game_path is not None:
        by_size = _variant_on_disk_by_size(a, b, game_path, mod_catalog_map())
        if by_size is not None:
            return by_size
    installed = settings.installed_mods
    a_rec, b_rec = a in installed, b in installed
    if a_rec and not b_rec:
        return a
    if b_rec and not a_rec:
        return b
    # No install record (manual / pre-migration): fall back to desired-state reconcile.
    want_a, want_b = bool(desired.get(a)), bool(desired.get(b))
    if want_a and not want_b:
        return a
    if want_b and not want_a:
        return b
    if want_a and want_b:
        if _is_alternate_hd_variant(a) and not _is_alternate_hd_variant(b):
            return b
        if _is_alternate_hd_variant(b) and not _is_alternate_hd_variant(a):
            return a
        return a
    if _is_alternate_hd_variant(a) and not _is_alternate_hd_variant(b):
        return b
    if _is_alternate_hd_variant(b) and not _is_alternate_hd_variant(a):
        return a
    return a


def _reconcile_exclusive_variants_detected(
    state: dict[str, bool],
    desired: dict[str, bool] | None = None,
    *,
    game_path: Path | None = None,
) -> dict[str, bool]:
    """Shared install artifacts can mark multiple conflict siblings as present."""
    out = dict(state)
    desired = desired if desired is not None else settings.desired_mods
    catalog = mod_catalog_map()
    seen: set[frozenset[str]] = set()
    for mid, mod in catalog.items():
        for conf in mod.get("conflicts") or []:
            pair = frozenset({mid, conf})
            if pair in seen or conf not in catalog:
                continue
            seen.add(pair)
            if not (out.get(mid) and out.get(conf)):
                continue
            winner = _pick_exclusive_detect_winner(
                mid, conf, desired, game_path=game_path
            )
            loser = conf if winner == mid else mid
            out[loser] = False
    return out


def _desired_conflict_sibling_installed(
    mod_id: str,
    desired: dict[str, bool],
    catalog: dict[str, dict[str, Any]],
) -> bool:
    """True when a desired conflict sibling is the reconciled installed variant."""
    for conf in (catalog.get(mod_id) or {}).get("conflicts") or []:
        if desired.get(conf):
            return True
    return False


def _mpq_exclusive_variant_needs_reinstall(
    mod_id: str,
    desired: dict[str, bool],
    catalog: dict[str, dict[str, Any]],
) -> bool:
    """True when a desired MPQ sibling must replace another variant on disk.

    Shared patch-L/T.mpq detection cannot tell variants apart; installed_mods
    records which sibling the launcher last applied.
    """
    mod = catalog.get(mod_id) or {}
    if mod.get("kind") != "mpq_file" or not desired.get(mod_id):
        return False
    conflicts = [c for c in (mod.get("conflicts") or []) if c in catalog]
    if not conflicts:
        return False
    installed = settings.installed_mods
    if mod_id in installed:
        return False
    return any(conf in installed and not desired.get(conf) for conf in conflicts)


def reconcile_exclusive_desired_mods(
    desired: dict[str, bool],
    *,
    prefer: str | None = None,
    actual: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Ensure at most one mod per catalog conflict pair is desired."""
    catalog = mod_catalog_map()
    out = dict(desired)
    seen: set[frozenset[str]] = set()
    for mid, mod in catalog.items():
        for conf in mod.get("conflicts") or []:
            pair = frozenset({mid, conf})
            if pair in seen or conf not in catalog:
                continue
            seen.add(pair)
            a, b = sorted(pair)
            if not (out.get(a) and out.get(b)):
                continue
            if pair == frozenset({_VANILLAFIXES_ID, _DXVK_ID}):
                if prefer in (a, b):
                    out[b if prefer == a else a] = False
                elif actual:
                    if actual.get(_DXVK_ID):
                        out[_VANILLAFIXES_ID] = False
                    elif actual.get(_VANILLAFIXES_ID):
                        out[_DXVK_ID] = False
                    else:
                        out[_DXVK_ID] = False
                else:
                    out[_DXVK_ID] = False
                continue
            if prefer in (a, b):
                out[b if prefer == a else a] = False
                continue
            if pair == _TWEAKS_IDS:
                from ichalaunch.mods.vanilla_tweaks import preferred_tweaks_variant

                keep = preferred_tweaks_variant(out, prefer=prefer)
                if keep in pair:
                    out[b if keep == a else a] = False
                    continue
            if actual:
                a_have, b_have = bool(actual.get(a)), bool(actual.get(b))
                if a_have and not b_have:
                    out[b] = False
                    continue
                if b_have and not a_have:
                    out[a] = False
                    continue
            winner = _pick_exclusive_detect_winner(a, b, out)
            out[b if winner == a else a] = False
    changed = True
    while changed:
        changed = False
        for mid, mod in catalog.items():
            if not out.get(mid):
                continue
            for req in mod.get("requires") or []:
                if req in catalog and not out.get(req):
                    out[mid] = False
                    changed = True
                    break
    return out


def _persist_reconciled_desired_mods(
    desired: dict[str, bool],
    *,
    actual: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Reconcile conflicting desired mods and persist when settings changed."""
    reconciled = reconcile_exclusive_desired_mods(desired, actual=actual)
    if reconciled != dict(settings.desired_mods):
        settings.set("desired_mods", reconciled)
        sync_vanillafixes_enabled_from_desired(reconciled)
    return reconciled


def reconcile_vanillafixes_dxvk(
    desired: dict[str, bool],
    *,
    prefer: str | None = None,
    actual: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Ensure at most one of VanillaFixes / DXVK is desired.

    The DXVK bundle ships VanillaFixes.exe, so disk detect can mark both present.
    *prefer* must be ``vanillafixes`` or ``dxvk`` when the user picks explicitly.
    """
    if not vanillafixes_dxvk_both_enabled(desired):
        return desired
    return reconcile_exclusive_desired_mods(desired, prefer=prefer, actual=actual)


def apply_vanillafixes_dxvk_choice(keep: str) -> dict[str, bool]:
    """Persist user choice for the VanillaFixes vs DXVK conflict."""
    if keep not in (_VANILLAFIXES_ID, _DXVK_ID):
        return {}
    desired = reconcile_vanillafixes_dxvk(
        dict(settings.desired_mods), prefer=keep
    )
    changes: dict[str, bool] = {}
    for mid in (_VANILLAFIXES_ID, _DXVK_ID):
        want = bool(desired.get(mid))
        if bool(settings.desired_mods.get(mid)) != want:
            changes[mid] = want
        settings.set_desired_mod(mid, want)
    sync_vanillafixes_enabled_from_desired(desired)
    return changes


def _is_hd_patch_id(mod_id: str) -> bool:
    return mod_id.startswith(_HD_PATCH_PREFIX)


def _any_hd_patch_desired(desired: dict[str, bool]) -> bool:
    return any(want and _is_hd_patch_id(mid) for mid, want in desired.items())


def enforce_vanilla_helpers_for_hd_desired(
    desired: dict[str, bool] | None = None,
    *,
    persist: bool = False,
) -> dict[str, bool]:
    """When any HD patch is desired, VanillaHelpers must stay desired too."""
    if desired is None:
        desired = dict(settings.desired_mods)
    if not _any_hd_patch_desired(desired):
        return desired
    if desired.get(_VANILLA_HELPERS_ID):
        return desired
    desired[_VANILLA_HELPERS_ID] = True
    if persist:
        settings.set_desired_mod(_VANILLA_HELPERS_ID, True)
    return desired


def _collect_mod_dependents(
    mod_id: str,
    catalog: dict[str, dict[str, Any]],
    seen: set[str] | None = None,
) -> list[str]:
    """Return mod ids that (transitively) list *mod_id* as a dependency."""
    if seen is None:
        seen = set()
    if mod_id in seen:
        return []
    seen.add(mod_id)
    ordered: list[str] = []
    for oid, mod in catalog.items():
        parents = list(mod.get("dependencies") or []) + list(mod.get("requires") or [])
        if mod_id not in parents:
            continue
        ordered.extend(_collect_mod_dependents(oid, catalog, seen))
        ordered.append(oid)
    return ordered


def _collect_mod_includes(
    mod_id: str,
    catalog: dict[str, dict[str, Any]],
    seen: set[str] | None = None,
) -> list[str]:
    """Return included companion mod ids (and their includes) for *mod_id*."""
    if seen is None:
        seen = set()
    if mod_id in seen or mod_id not in catalog:
        return []
    seen.add(mod_id)
    ordered: list[str] = []
    for inc in catalog[mod_id].get("includes") or []:
        if inc not in catalog or inc in seen:
            continue
        ordered.extend(_collect_mod_includes(inc, catalog, seen))
        ordered.append(inc)
    return ordered


def mod_includes_caption(mod: dict[str, Any] | None, catalog: dict[str, dict[str, Any]] | None = None) -> str:
    """Human-readable 'Includes: …' line for catalog companions listed on *mod*."""
    if not mod:
        return ""
    ids = [str(x) for x in (mod.get("includes") or []) if x]
    if not ids:
        return ""
    cat = catalog if catalog is not None else mod_catalog_map()
    names: list[str] = []
    for mid in ids:
        entry = cat.get(mid) or {}
        names.append(str(entry.get("name") or mid))
    return f"Includes: {', '.join(names)}"


def mod_contains_caption(mod: dict[str, Any] | None, catalog: dict[str, dict[str, Any]] | None = None) -> str:
    """Subtitle beneath a client mod row: pack contents and/or bundled companions."""
    if not mod:
        return ""
    parts: list[str] = []
    mid = str(mod.get("id") or "")
    name = str(mod.get("name") or "")
    list_label = str(mod.get("list_label") or "").strip()
    if list_label:
        parts.append(list_label)
    if mid.startswith(_HD_PATCH_PREFIX) and "(" in name and name.rstrip().endswith(")"):
        inner = name[name.rfind("(") + 1 : name.rfind(")")].strip()
        if inner:
            parts.append(inner)
    includes = mod_includes_caption(mod, catalog)
    if includes:
        parts.append(includes)
    return " · ".join(parts)

def resolve_mod_toggle(mod_id: str, enabled: bool) -> dict[str, bool]:
    """Compute desired-state changes for a user toggle (deps, conflicts, dependents)."""
    catalog = mod_catalog_map()
    if mod_id not in catalog:
        return {mod_id: enabled}

    desired = settings.desired_mods
    changes: dict[str, bool] = {}

    def effective(mid: str) -> bool:
        if mid in changes:
            return changes[mid]
        return bool(desired.get(mid, False))

    def enable_with_deps(mid: str) -> None:
        for dep in _collect_mod_dependencies(mid, catalog):
            # Fog Pushback depends on Tweaks; Old satisfies that slot.
            if dep == "vanilla_tweaks" and effective("vanilla_tweaks_old"):
                continue
            changes[dep] = True
        changes[mid] = True

    def enable_includes_for(mid: str) -> None:
        for inc in _collect_mod_includes(mid, catalog):
            if inc in _BUNDLED_IN_PARENT_MPQ:
                continue
            enable_with_deps(inc)

    def disable_branch(mid: str, seen: set[str]) -> None:
        if mid in seen or mid not in catalog:
            return
        if not effective(mid):
            return
        seen.add(mid)
        for dep_id in _collect_mod_dependents(mid, catalog, set()):
            disable_branch(dep_id, seen)
        changes[mid] = False

    if enabled:
        for req in catalog[mod_id].get("requires") or []:
            if req in catalog and not effective(req):
                return {}
        enable_with_deps(mod_id)
        # Catalog includes that still need a standalone install (not HD-MPQ bundles).
        for mid in list(changes):
            if changes.get(mid):
                enable_includes_for(mid)
        for mid in list(changes):
            if not changes.get(mid):
                continue
            for conf in catalog.get(mid, {}).get("conflicts") or []:
                if conf in catalog and effective(conf):
                    # Switching V2 ↔ Old must not cascade-disable Fog Pushback.
                    if mid in _TWEAKS_IDS and conf in _TWEAKS_IDS:
                        changes[conf] = False
                    else:
                        disable_branch(conf, set())
        for mid in list(changes):
            if not changes.get(mid):
                continue
            for dep in _collect_mod_dependencies(mid, catalog):
                changes[dep] = True
            enable_includes_for(mid)
    else:
        if mod_id == _VANILLA_HELPERS_ID and _any_hd_patch_desired(
            {**desired, **changes}
        ):
            return {}
        disable_branch(mod_id, set())

    return {
        mid: state
        for mid, state in changes.items()
        if bool(desired.get(mid, False)) != state
    }


def apply_mod_toggle(mod_id: str, enabled: bool) -> dict[str, bool]:
    """Apply resolve_mod_toggle and persist each changed desired state."""
    changes = resolve_mod_toggle(mod_id, enabled)
    for mid, state in changes.items():
        settings.set_desired_mod(mid, state)
    if mod_id in (_VANILLAFIXES_ID, _DXVK_ID) or any(
        mid in (_VANILLAFIXES_ID, _DXVK_ID) for mid in changes
    ):
        sync_vanillafixes_enabled_from_desired()
    return changes


def builtin_mod_ids() -> set[str]:
    return {m["id"] for m in json.loads(_data_path().read_text(encoding="utf-8")) if m.get("id")}


def _slug_mod_id(repo_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", repo_name.lower()).strip("_")
    return slug or "custom_dll"


def _pick_dll_asset(assets: list[dict[str, Any]], *, prefer: str | None = None) -> dict[str, Any] | None:
    dlls = [
        a
        for a in assets
        if (a.get("name") or "").lower().endswith(".dll")
        and not (a.get("name") or "").lower().endswith(".pdb")
    ]
    if not dlls:
        return None
    if prefer:
        needle = prefer.lower()
        ranked = [a for a in dlls if needle in (a.get("name") or "").lower()]
        if ranked:
            return ranked[0]
    # Prefer shortest name (base package over variants)
    return min(dlls, key=lambda a: (len(a.get("name") or ""), a.get("name") or ""))


def _pick_zip_asset(assets: list[dict[str, Any]], *, prefer: str | None = None) -> dict[str, Any] | None:
    zips = [a for a in assets if (a.get("name") or "").lower().endswith(".zip")]
    if not zips:
        return None
    if prefer:
        needle = prefer.lower()
        ranked = [a for a in zips if needle in (a.get("name") or "").lower()]
        if ranked:
            return ranked[0]
    return min(zips, key=lambda a: (len(a.get("name") or ""), a.get("name") or ""))


def _companion_addon_source(owner: str, repo_name: str, assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Detect a companion settings/addon zip in the same release or a sibling repo."""
    companion_zips = [
        a
        for a in assets
        if (a.get("name") or "").lower().endswith(".zip")
        and any(tok in (a.get("name") or "").lower() for tok in ("settings", "addon", "config"))
    ]
    if companion_zips:
        asset = companion_zips[0]
        folder_guess = Path(asset["name"]).stem
        folder_guess = re.sub(r"[-_]?v?\d+(?:\.\d+)*$", "", folder_guess, flags=re.IGNORECASE).strip("-_")
        return {
            "type": "github_release_latest",
            "repo": f"{owner}/{repo_name}",
            "asset_contains": asset["name"],
            "folder": folder_guess or "Addon",
        }

    candidates = [
        f"{repo_name}Settings",
        f"{repo_name}_Settings",
        f"{repo_name}Addon",
        f"{repo_name}_Addon",
        f"{repo_name}-Addon",
    ]
    for name in candidates:
        repo = f"{owner}/{name}"
        try:
            r = github_get(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception:
            continue
        sibling_assets = r.json().get("assets") or []
        zip_asset = _pick_zip_asset(sibling_assets)
        if not zip_asset:
            continue
        folder = name
        return {
            "type": "github_release_latest",
            "repo": repo,
            "asset_contains": ".zip",
            "folder": folder,
        }
    return None


def _github_keys_for_mod(mod: dict[str, Any]) -> set[str]:
    """Normalized https://github.com/owner/repo keys for catalog matching."""
    from ichalaunch.ui.widgets.common import github_repo_browse_url

    src = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    addon_src = mod.get("addon_source") if isinstance(mod.get("addon_source"), dict) else {}
    keys: set[str] = set()
    for raw in (
        mod.get("repo_url"),
        mod.get("repo"),
        mod.get("github"),
        mod.get("url"),
        mod.get("info_url"),
        mod.get("repository"),
        (src or {}).get("repo"),
        (src or {}).get("url"),
        (src or {}).get("github"),
        (addon_src or {}).get("repo"),
        (addon_src or {}).get("url"),
    ):
        page = github_repo_browse_url(raw)
        if page:
            keys.add(page.rstrip("/").lower())
    return keys


def find_mod_by_github_url(url: str) -> dict[str, Any] | None:
    """Return an existing catalog/user mod whose repo matches ``url``, if any."""
    from ichalaunch.ui.widgets.common import github_repo_browse_url

    target = github_repo_browse_url(url)
    if not target:
        return None
    target_l = target.rstrip("/").lower()
    for existing in load_mod_catalog():
        if target_l in _github_keys_for_mod(existing):
            return dict(existing)
    return None


def resolve_github_dll_mod(url: str) -> dict[str, Any]:
    """Inspect a GitHub repo's latest release and build a client-mod catalog entry."""
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("Not a valid GitHub repository URL. Example: https://github.com/owner/repo")
    owner, repo_name = parsed.owner, parsed.repo
    repo = f"{owner}/{repo_name}"

    # Reuse a built-in / already-registered entry that points at this repo.
    matched = find_mod_by_github_url(url)
    if matched:
        matched["matched_existing"] = True
        return matched

    try:
        r = github_get(f"https://api.github.com/repos/{repo}/releases/latest")
    except GitHubRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(
            f"No latest release found for {repo}. Publish a release with a .dll or .zip asset."
        ) from exc
    data = r.json()
    assets = data.get("assets") or []
    if not assets:
        raise FileNotFoundError(f"Release for {repo} has no downloadable assets.")

    dll_asset = _pick_dll_asset(assets, prefer=repo_name)
    zip_asset = _pick_zip_asset(assets, prefer=repo_name) if not dll_asset else None
    if not dll_asset and not zip_asset:
        raise FileNotFoundError(
            f"No .dll or .zip asset on the latest release of {repo}."
        )

    if dll_asset:
        dll_name = dll_asset["name"]
        source: dict[str, Any] = {
            "type": "github_release_latest",
            "repo": repo,
            "asset_contains": dll_name,
        }
        kind = "dll_file"
        files = [{"match": dll_name, "destination": dll_name}]
    else:
        assert zip_asset
        source = {
            "type": "github_release_latest",
            "repo": repo,
            "asset_contains": zip_asset["name"],
        }
        kind = "dll_bundle"
        # Placeholder — install_custom_dll_from_github probes the zip for the real name.
        dll_name = f"{repo_name}.dll"
        files = [{"match": dll_name, "destination": dll_name}]

    mid = _slug_mod_id(repo_name)
    if mid in builtin_mod_ids():
        mid = f"custom_{mid}"

    display = repo_name.replace("_", " ").replace("-", " ")
    mod: dict[str, Any] = {
        "id": mid,
        "name": display,
        "category": "Custom",
        "description": f"User-defined DLL from {repo}",
        "detect": {"any_files": [dll_name]},
        "source": source,
        "files": files,
        "dlls_txt": {"add": [dll_name]},
        "kind": kind,
        "dependencies": [],
        "conflicts": [],
        "user_defined": True,
        "matched_existing": False,
        "repo_url": f"https://github.com/{repo}",
    }

    addon_src = _companion_addon_source(owner, repo_name, assets)
    if addon_src:
        mod["addon_source"] = addon_src

    return mod


def preview_github_dll_mod(url: str) -> dict[str, Any]:
    """Resolve a GitHub DLL/client-mod entry for confirmation (no install/persist)."""
    mod = resolve_github_dll_mod(url)
    source = mod.get("source") or {}
    parsed = parse_github_url(str(mod.get("repo_url") or url))
    readme_md = ""
    readme_base = ""
    readme_cache = ""
    if parsed:
        owner, repo = parsed.owner, parsed.repo
        branch = "main"
        try:
            meta = github_get(f"https://api.github.com/repos/{owner}/{repo}").json()
            branch = str(meta.get("default_branch") or "main")
        except Exception:
            pass
        try:
            readme = fetch_repo_readme(owner, repo, branch=branch)
            if readme:
                readme_md = readme.get("markdown") or ""
                readme_base = readme.get("base_url") or ""
                readme_cache = readme.get("cache_dir") or ""
        except GitHubRateLimitError:
            raise
        except Exception as exc:
            log.warning("DLL preview README skipped: %s", exc)
    return {
        "kind": "dll",
        "url": str(mod.get("repo_url") or url).strip(),
        "name": mod.get("name") or mod.get("id") or "DLL",
        "id": mod.get("id"),
        "category": mod.get("category") or "Custom",
        "description": (mod.get("description") or "").strip() or "(no description)",
        "asset": source.get("asset_contains") or "",
        "matched_existing": bool(mod.get("matched_existing")),
        "has_companion_addon": bool(mod.get("addon_source")),
        "mod": mod,
        "readme_markdown": readme_md,
        "readme_base_url": readme_base,
        "readme_cache_dir": readme_cache,
    }


def format_dll_preview(info: dict[str, Any]) -> str:
    """Short summary above the README in the Add DLL confirm dialog."""
    lines = [
        f"{info.get('name')}  ·  {info.get('category')}",
        f"{info.get('url')}",
        f"{info.get('description')}",
    ]
    if info.get("asset"):
        lines.append(f"Release asset: {info['asset']}")
    if info.get("matched_existing"):
        lines.append("Matched an existing catalog entry — will enable that mod.")
    else:
        lines.append("New custom entry — will be added under the Custom tab.")
    if info.get("has_companion_addon"):
        lines.append("Includes a companion addon package from the same release.")
    return "\n".join(lines)


def register_user_mod(mod: dict[str, Any]) -> dict[str, Any]:
    """Persist a user mod entry and enable it in desired_mods."""
    if not mod.get("id"):
        raise ValueError("mod requires id")
    settings.set_user_mod(mod)
    settings.set_desired_mod(mod["id"], True)
    return mod


def install_custom_dll_from_github(url: str, progress: ProgressCb | None = None) -> dict[str, Any]:
    """Resolve, register, and install a DLL (and optional companion addon) from GitHub."""
    mod = resolve_github_dll_mod(url)
    matched_existing = bool(mod.get("matched_existing"))
    source = mod.get("source") or {}
    asset_needle = (source.get("asset_contains") or "").lower()
    # Probe zip-only custom entries for the real DLL name before persisting.
    if (
        mod.get("user_defined")
        and not matched_existing
        and source.get("type") == "github_release_latest"
        and asset_needle.endswith(".zip")
    ):
        with tempfile.TemporaryDirectory(prefix="ichalaunch_probe_") as tmp:
            work = Path(tmp)
            artifact = _download_source(source, work, progress)
            extracted = extract_zip(artifact, work / "extract", progress=progress)
            prefer = (_slug_mod_id((parse_github_url(url) or ("", ""))[1]) or "").replace("_", "")
            dlls = [p for p in extracted.rglob("*.dll") if p.is_file()]
            if not dlls:
                raise FileNotFoundError("Release zip contains no .dll files.")
            chosen = None
            if prefer:
                for p in dlls:
                    if prefer in re.sub(r"[^a-z0-9]+", "", p.stem.lower()):
                        chosen = p
                        break
            chosen = chosen or min(dlls, key=lambda p: (len(p.name), p.name.lower()))
            dll_name = chosen.name
            mod["detect"] = {"any_files": [dll_name]}
            mod["files"] = [{"match": dll_name, "destination": dll_name}]
            mod["dlls_txt"] = {"add": [dll_name]}
            mod["kind"] = "dll_bundle"
    if mod.get("user_defined") and not matched_existing:
        # New custom entry — persist under Custom category via user_mods.
        if not mod.get("category"):
            mod["category"] = "Custom"
        register_user_mod(mod)
    else:
        # Matched built-in (or prior custom): enable desired state, no duplicate.
        settings.set_desired_mod(mod["id"], True)
    install_mod(mod["id"], progress=progress, prefer_latest=True)
    out = dict(mod)
    out["matched_existing"] = matched_existing
    return out


def _game_rel_present(
    game_path: Path,
    rel: str,
    root_names: frozenset[str] | None,
) -> bool:
    """True if *rel* exists under *game_path* (root basename or nested path)."""
    raw = str(rel or "").strip().strip("\"'").replace("\\", "/")
    if not raw:
        return False
    if "/" in raw:
        dest = game_path / raw
        try:
            return dest.is_file()
        except OSError as exc:
            if is_lock_or_av_error(exc):
                return True
            return False
    return name_present(game_path, raw, root_names)


_VANILLA_TWEAKS_BACKUP = "WoW-OriginalBackup.exe"


def _files_content_differ(left: Path, right: Path) -> bool:
    """True when both files hash and the digests differ. Locked/missing → False."""
    digest_left = sha256_file(left)
    digest_right = sha256_file(right)
    if not digest_left or not digest_right:
        return False
    return digest_left != digest_right


def _exe_differs_from_backup(game_path: Path, backup_name: str) -> bool:
    """True when WoW.exe has been patched relative to *backup_name*.

    Turtle/RavenCraft ships a stock ``WoW-OriginalBackup.exe`` that matches
    ``WoW.exe``. Treating backup *presence* as installed made disable forever
    pending (Apply stayed glowing) because remove cannot delete that stock file.
    """
    wow = wow_exe_in(game_path)
    backup = resolve_ci(game_path, backup_name)
    if wow is None or backup is None:
        return False
    try:
        if not wow.is_file() or not backup.is_file():
            return False
    except OSError:
        return False
    try:
        if wow.resolve() == backup.resolve():
            return False
    except OSError:
        pass
    return _files_content_differ(wow, backup)


def _dlls_txt_has(game_path: Path, names: list[str], listing: frozenset[str] | None) -> bool:
    """True if any *names* are uncommented in dlls.txt and present on disk.

    Parses the text list only — never opens/hashes/LoadLibrary the DLLs.
    """
    if not names:
        return False
    want = {Path(n.replace("\\", "/")).name.lower() for n in names if n}
    if not want:
        return False
    listed = {n.lower() for n in read_dlls_txt(game_path)}
    if not (want & listed):
        return False
    return any(name_present(game_path, n, listing) for n in want)


def _dxvk_conf_has_marker(game_path: Path, marker: str) -> bool:
    conf = game_path / "dxvk.conf"
    if not conf.is_file():
        return False
    try:
        return marker in conf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _d3d9_is_hd_dxvk(game_path: Path) -> bool:
    """True when on-disk d3d9.dll is official DXVK 2.7.1, not RetroCro's cursor build.

    Reads bytes only — never LoadLibrary. The 2.7.1 string is embedded in the
    official release; the bigger-cursor DLL does not carry it.
    """
    dll = resolve_ci(game_path, "d3d9.dll")
    if dll is None:
        return False
    try:
        if not dll.is_file():
            return False
        with dll.open("rb") as handle:
            blob = handle.read(4 * 1024 * 1024)
    except OSError:
        return False
    return _HD_DXVK_DLL_MARKER in blob


def _detect_hd_dxvk(
    game_path: Path,
    mod: dict[str, Any],
    root_names: frozenset[str] | None,
) -> bool:
    """HD DXVK is the 2.7.1 DLL, not a leftover comment in dxvk.conf.

    When Bigger Mouse Cursor is also desired it replaces d3d9.dll and keeps the
    2.7.1 conf. Count that layered stack as installed so plan_changes does not
    fight the two writers.
    """
    det = mod.get("detect") or {}
    files_ok = all(
        _game_rel_present(game_path, f, root_names)
        for f in (det.get("all_files") or ["d3d9.dll", "dxvk.conf"])
    )
    if not files_ok or not _dxvk_conf_has_marker(game_path, _HD_DXVK_CONF_MARKER):
        return False
    if _d3d9_is_hd_dxvk(game_path):
        return True
    desired = settings.desired_mods
    if (
        desired.get(_HD_DXVK_ID)
        and desired.get(_DXVK_CURSOR_ID)
        and _dxvk_conf_has_marker(game_path, _HD_DXVK_CONF_MARKER)
        and _dxvk_conf_has_marker(game_path, _DXVK_CURSOR_CONF_MARKER)
    ):
        return True
    return False


def _detect_hd_patch_c(game_path: Path) -> bool:
    """Case-exact: Patch-V.mpq is not proof of Patch-C."""
    data = game_path / "Data"
    names = listed_exact_basenames(data)
    if names is None:
        return False
    return any(name in names for name in _HD_PATCH_C_EXACT_NAMES)


def _vanilla_tweaks_is_ours() -> bool:
    """True when this launcher (or the user via the Client tab) owns V2."""
    from ichalaunch.mods.vanilla_tweaks import vanilla_tweaks_is_ours

    return vanilla_tweaks_is_ours(_VANILLA_TWEAKS_ID)


def _vanilla_tweaks_needs_catalog_repatch(mid: str, actual: dict[str, bool]) -> bool:
    """Desired+installed Tweaks that needs a catalog re-patch.

    Leftover brndd under ``vanilla_tweaks`` force-migrates only when V2 is
    desired. A user who chose Old is not upgraded to tubtubs.
    """
    if mid not in _TWEAKS_IDS:
        return False
    if not _effective_mod_installed(mid, actual):
        return False
    desired = settings.desired_mods
    if mid == _VANILLA_TWEAKS_ID:
        if not desired.get(_VANILLA_TWEAKS_ID) or desired.get(_VANILLA_TWEAKS_OLD_ID):
            return False
        from ichalaunch.mods.vanilla_tweaks import vanilla_tweaks_needs_repatch

        meta = settings.installed_mods.get(mid) or {}
        return vanilla_tweaks_needs_repatch(meta, settings.vanilla_tweaks_options)
    if not desired.get(_VANILLA_TWEAKS_OLD_ID):
        return False
    from ichalaunch.mods.vanilla_tweaks import (
        leftover_brndd_under_v2,
        vanilla_tweaks_old_needs_repatch,
    )

    meta = settings.installed_mods.get(mid) or {}
    if not meta:
        leftover = leftover_brndd_under_v2()
        if leftover is not None:
            meta = leftover
    return vanilla_tweaks_old_needs_repatch(meta, settings.vanilla_tweaks_old_options)


def _order_d3d9_layers(ordered: list[str]) -> list[str]:
    """Stable: base DXVK, then 2.7.1, then the cursor overlay."""
    layers = [mid for mid in ordered if mid in _D3D9_LAYER_RANK]
    if len(layers) < 2:
        return ordered
    rest = [mid for mid in ordered if mid not in _D3D9_LAYER_RANK]
    layers.sort(key=lambda mid: _D3D9_LAYER_RANK[mid])
    return rest + layers


def _order_fog_after_patch_e(ordered: list[str]) -> list[str]:
    """Install standalone Fog Pushback (patch-Y) after Patch-E when both apply."""
    if _HD_PATCH_E_ID not in ordered or _FOG_PUSHBACK_ID not in ordered:
        return ordered
    out = [mid for mid in ordered if mid != _FOG_PUSHBACK_ID]
    out.insert(out.index(_HD_PATCH_E_ID) + 1, _FOG_PUSHBACK_ID)
    return out


def _latest_backup_for(game: Path, label: str) -> Path | None:
    suffix = f"_{label}"
    for root in list_backups(game):
        if root.name.endswith(suffix):
            return root
    return None


def _restore_backup_files(game: Path, backup_root: Path, names: tuple[str, ...]) -> bool:
    restored = False
    for name in names:
        src = backup_root / name
        try:
            if not src.is_file():
                continue
        except OSError:
            continue
        try:
            _install_copy(src, game / name, game_path=game)
            restored = True
        except OSError as exc:
            log.warning("Could not restore %s from %s: %s", name, backup_root.name, exc)
    return restored


def _restore_dxvk_layer(
    game: Path,
    *,
    backup_label: str,
    fallback_id: str | None,
    progress: ProgressCb | None,
) -> None:
    """Put d3d9.dll + dxvk.conf back without requiring the network first."""
    backup = _latest_backup_for(game, backup_label)
    if backup is not None and _restore_backup_files(
        game, backup, ("d3d9.dll", "dxvk.conf")
    ):
        return
    if not fallback_id:
        return
    try:
        install_mod(fallback_id, progress=progress)
    except (
        OSError,
        RuntimeError,
        FileNotFoundError,
        KeyError,
        shutil.Error,
        requests.RequestException,
    ) as exc:
        log.warning(
            "Could not reinstall %s after %s; leaving current DXVK files: %s",
            fallback_id,
            backup_label,
            exc,
        )


def _detect_mod(
    game_path: Path,
    mod: dict[str, Any],
    *,
    root_names: frozenset[str] | None = None,
) -> bool:
    det = mod.get("detect") or {}
    mid = mod.get("id") or ""
    kind = mod.get("kind")
    # HD DXVK shares d3d9.dll / dxvk.conf with VF+Vulkan — require the 2.7.1 DLL.
    if mid == _HD_DXVK_ID or kind == "dxvk_hd":
        return _detect_hd_dxvk(game_path, mod, root_names)
    if mid == "hd_patch_c":
        return _detect_hd_patch_c(game_path)
    if det.get("exe_differs_from"):
        if not _exe_differs_from_backup(game_path, str(det["exe_differs_from"])):
            return False
        if mid in _TWEAKS_IDS:
            from ichalaunch.mods.vanilla_tweaks import vanilla_tweaks_detects_as

            return vanilla_tweaks_detects_as(str(mid), True)
        return True
    if det.get("wdb_file"):
        return name_present(game_path, "WDB", root_names) and (game_path / "WDB").is_file()
    if det.get("any_files"):
        if any(_game_rel_present(game_path, f, root_names) for f in det["any_files"]):
            return True
        dlls = (mod.get("dlls_txt") or {}).get("add") or []
        return _dlls_txt_has(game_path, dlls, root_names)
    if det.get("all_files"):
        return all(_game_rel_present(game_path, f, root_names) for f in det["all_files"])
    if det.get("data_mpq"):
        data = game_path / "Data"
        data_names = listed_basenames(data)
        return any(
            not is_stock_data_mpq(name) and name_present(data, name, data_names)
            for name in det["data_mpq"]
        )
    if det.get("config_contains"):
        cfg = game_path / "WTF" / "Config.wtf"
        if not cfg.is_file():
            return False
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        return det["config_contains"] in text
    if det.get("config_file_contains"):
        spec = det.get("config_file_contains") or []
        if not isinstance(spec, (list, tuple)) or len(spec) < 2:
            return False
        path = game_path / spec[0]
        needle = spec[1]
        if not path.is_file():
            return False
        return needle in path.read_text(encoding="utf-8", errors="ignore")
    # fallback by kind heuristics
    kind = mod.get("kind")
    mid = mod["id"]
    from ichalaunch.mods.vanilla_tweaks import vanilla_tweaks_detects_as

    exe_tweaked = _exe_differs_from_backup(game_path, _VANILLA_TWEAKS_BACKUP)
    legacy = {
        "vanillafixes": name_present(game_path, "VanillaFixes.exe", root_names),
        "dxvk": name_present(game_path, "d3d9.dll", root_names)
        and name_present(game_path, "dxvk.conf", root_names),
        "superwow": name_present(game_path, "SuperWoWhook.dll", root_names),
        "nampower": name_present(game_path, "nampower.dll", root_names),
        "unitxp": name_present(game_path, "UnitXP_SP3.dll", root_names),
        "perfboost": name_present(game_path, "perf_boost.dll", root_names),
        "no1600x1200": name_present(game_path, "no1600x1200.dll", root_names),
        "wdb_block": name_present(game_path, "WDB", root_names) and (game_path / "WDB").is_file(),
        "vanilla_tweaks": vanilla_tweaks_detects_as(_VANILLA_TWEAKS_ID, exe_tweaked),
        "vanilla_tweaks_old": vanilla_tweaks_detects_as(
            _VANILLA_TWEAKS_OLD_ID, exe_tweaked
        ),
    }
    if mid in legacy:
        return legacy[mid]
    if kind == "mpq_file":
        dest = mod.get("destination")
        if not dest:
            return False
        rel = Path(str(dest).replace("\\", "/"))
        if is_stock_data_mpq(rel.name):
            return False
        return name_present(game_path / rel.parent, rel.name)
    dlls = (mod.get("dlls_txt") or {}).get("add") or []
    if dlls:
        return _dlls_txt_has(game_path, dlls, root_names)
    return False


def detect_actual_state(game_path: Path) -> dict[str, bool]:
    """Scan installed client mods. Never LoadLibrary game DLLs; never raise per-mod."""
    try:
        migrate_legacy_pretty_night_sky_y(game_path)
    except OSError as exc:
        log.warning("Pretty Night Sky Y→Z migrate skipped: %s", exc)
    state: dict[str, bool] = {}
    root_names = listed_basenames(game_path)
    for mod in load_mod_catalog():
        mid = mod.get("id") or ""
        if not mid:
            continue
        try:
            state[mid] = bool(_detect_mod(game_path, mod, root_names=root_names))
        except OSError as exc:
            log.warning("detect %s skipped (disk/AV): %s", mid, exc)
            dlls = (mod.get("dlls_txt") or {}).get("add") or []
            state[mid] = _dlls_txt_has(game_path, dlls, root_names) if dlls else False
        except (IndexError, TypeError, ValueError, KeyError, UnicodeError) as exc:
            log.warning("detect %s skipped (bad catalog/parse): %s", mid, exc)
            state[mid] = False
    reconciled = _reconcile_exclusive_variants_detected(
        _reconcile_vf_dxvk_detected(state),
        desired=settings.desired_mods,
        game_path=game_path,
    )
    _backfill_detected_installed_mods(reconciled)
    _refresh_unverified_mod_flags(game_path, reconciled)
    return reconciled


def _apply_frame_cap_if_enabled(
    game: Path, *, raise_on_write_error: bool = True
) -> int | None:
    """Point d3d9.maxFrameRate at the user's display, if a dxvk.conf exists.

    Called from every install path that can leave a dxvk.conf behind (the
    VanillaFixes+DXVK bundle, the optional 2.7.1 upgrade, and the cursor
    preset) and again from prepare_for_launch so an existing install picks
    up a monitor change without a reinstall.
    """
    from ichalaunch.game.display import apply_frame_cap, frame_cap_enabled

    if not frame_cap_enabled():
        return None
    return apply_frame_cap(
        game / "dxvk.conf",
        settings.get("frame_cap_offset", 3),
        raise_on_write_error=raise_on_write_error,
    )


def _pe_artifacts_for_mod(mod: dict[str, Any]) -> list[str]:
    """Game-relative DLL/EXE paths this mod owns (for post-install / detect verify)."""
    return sorted(
        rel
        for rel in _mod_owned_paths(mod)
        if rel.lower().endswith((".dll", ".exe"))
    )


def _refresh_unverified_mod_flags(game: Path, actual: dict[str, bool]) -> None:
    """Clear unverified when PE files become readable; keep flag while still locked."""
    for mid, meta in list(settings.installed_mods.items()):
        if not isinstance(meta, dict) or not meta.get("unverified"):
            continue
        if not actual.get(mid, False):
            # Nothing on disk to verify — drop the flag so Apply can reinstall.
            mark_mod_unverified(mid, unverified=False)
            continue
        mod = get_mod(mid)
        if not mod:
            continue
        rels = _pe_artifacts_for_mod(mod)
        if not rels:
            mark_mod_unverified(mid, unverified=False)
            continue
        outcome = "ok"
        for rel in rels:
            # Comparison key, not a filename -- see _verify_mod_install.
            dest = resolve_ci(game, rel)
            if dest is None or not dest.is_file():
                continue
            try:
                if not validate_pe_binary(dest, min_size=_pe_min_bytes_for_rel(rel)):
                    outcome = "soft"
                    break
            except OSError:
                # Readable but invalid — verification completed; drop soft flag.
                outcome = "corrupt"
                break
        if outcome == "soft":
            log.debug("Keeping unverified flag for %s (lock/AV)", mid)
            continue
        mark_mod_unverified(mid, unverified=False)
        if outcome == "ok":
            log.info("Cleared unverified flag for %s after successful PE check", mid)


def plan_missing_installs(desired: dict[str, bool] | None = None) -> list[dict[str, str]]:
    """Return install actions for desired mods missing on disk (no removals)."""
    return [ch for ch in plan_changes(desired) if ch.get("action") == "install"]


def plan_manual_missing(desired: dict[str, bool] | None = None) -> list[str]:
    """Return manual-install notices for desired mods that cannot auto-install."""
    return [ch["detail"] for ch in plan_changes(desired) if ch.get("action") == "manual"]


def _apply_planned_mod_changes(
    changes: list[dict[str, str]], progress: ProgressCb | None = None
) -> list[str]:
    """Apply install/remove actions from plan_changes. Per-mod failures are logged, not raised.

    Done-list protocol:
    - ``+ id`` / ``- id`` — applied
    - ``~ id`` — installed but PE verify soft-skipped (lock/AV); keep install
    - ``! id …`` — hard failure (friendly text; raw errno only in logs)
    """
    done: list[str] = []
    tweaks_installs = [
        ch.get("id")
        for ch in changes
        if ch.get("action") == "install" and ch.get("id") in _TWEAKS_IDS
    ]
    keep_tweaks = None
    if len(tweaks_installs) > 1:
        from ichalaunch.mods.vanilla_tweaks import preferred_tweaks_variant

        keep_tweaks = preferred_tweaks_variant()
    for ch in changes:
        if ch.get("action") not in ("install", "remove"):
            continue
        mid = ch.get("id") or ""
        action = ch["action"]
        if (
            action == "install"
            and mid in _TWEAKS_IDS
            and keep_tweaks
            and mid != keep_tweaks
        ):
            log.info("Skipping extra Tweaks install %s — keeping %s", mid, keep_tweaks)
            continue
        try:
            vf_label = _vf_sync_action_log_label(mid, action)
            if vf_label:
                log.info("Mod sync: %s", vf_label)
            if action == "install":
                notices = install_mod(mid, progress=progress)
                done.append(f"+ {mid}")
                done.extend(notices)
            else:
                remove_mod(mid, progress=progress)
                done.append(f"- {mid}")
            if not vf_label:
                log.info("Pre-launch mod %s: %s", action, mid)
        except OSError as exc:
            log.warning("Mod %s %s skipped (disk/AV): %s", action, mid, exc)
            done.append(f"! {mid} skipped: {user_facing_os_error(exc)}")
        except (RuntimeError, FileNotFoundError, KeyError, shutil.Error) as exc:
            log.warning("Mod %s %s failed: %s", action, mid, exc)
            done.append(f"! {mid} failed: {user_facing_os_error(exc)}")
        except requests.RequestException as exc:
            log.warning("Mod %s %s failed (download): %s", action, mid, exc)
            done.append(f"! {mid} failed: {exc}")
    return done


def split_mod_apply_results(done: list[str] | None) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split apply/sync done lines into installed, removed, verify-warnings, failures."""
    installed: list[str] = []
    removed: list[str] = []
    warnings: list[str] = []
    failures: list[str] = []
    for ln in done or []:
        if not isinstance(ln, str):
            continue
        if ln.startswith("+ "):
            installed.append(ln[2:].strip())
        elif ln.startswith("- "):
            removed.append(ln[2:].strip())
        elif ln.startswith("~ "):
            warnings.append(ln[2:].strip())
        elif ln.startswith("!"):
            failures.append(ln[1:].strip())
    return installed, removed, warnings, failures


def format_mod_verify_warning(mod_ids: list[str]) -> tuple[str, str]:
    """Title + body for soft-skip PE verify (install kept, unmarked as verified)."""
    names: list[str] = []
    catalog = {m["id"]: m for m in load_mod_catalog()}
    for mid in mod_ids:
        mod = catalog.get(mid) or {}
        names.append(str(mod.get("name") or mid))
    if not names:
        return LOCK_AV_VERIFY_TITLE, LOCK_AV_VERIFY_MESSAGE
    if len(names) == 1:
        lead = f"{names[0]} could not be verified after install."
    else:
        lead = (
            f"{len(names)} client mods could not be verified after install: "
            + ", ".join(names[:6])
            + ("…" if len(names) > 6 else "")
            + "."
        )
    return LOCK_AV_VERIFY_TITLE, f"{lead}\n\n{LOCK_AV_VERIFY_MESSAGE}"


def mod_is_unverified(mod_id: str, meta: dict[str, Any] | None = None) -> bool:
    """True when install was kept but post-install PE verify soft-skipped (lock/AV)."""
    if meta is None:
        meta = settings.installed_mods.get(mod_id)
    return bool(isinstance(meta, dict) and meta.get("unverified"))


def mark_mod_unverified(mod_id: str, *, unverified: bool = True) -> None:
    """Persist or clear the shared unverified flag on installed_mods metadata."""
    if not mod_id:
        return
    settings.set_installed_mod(mod_id, {"unverified": bool(unverified)})


def _effective_mod_installed(mid: str, actual: dict[str, bool]) -> bool:
    """On-disk detect, or soft-verify keep (avoids Apply-flash reinstall loop)."""
    if actual.get(mid, False):
        return True
    return mod_is_unverified(mid)


def plan_sync_changes(desired: dict[str, bool] | None = None) -> list[dict[str, str]]:
    """Return install/remove actions needed to match desired_mods (no manual/error)."""
    return [
        ch
        for ch in plan_changes(desired)
        if ch.get("action") in ("install", "remove")
    ]


def ensure_desired_mods_on_disk(progress: ProgressCb | None = None) -> list[str]:
    """Install enabled client mods that are missing on disk. Install-only; never removes."""
    changes = plan_missing_installs()
    if not changes:
        return []
    done = _apply_planned_mod_changes(changes, progress=progress)
    game = detect_game()
    if game:
        _sync_dlls_txt_for_desired_mods(game)
    log.info("Pre-launch mod repair: %s", done)
    return done


def ensure_desired_mods_synced(progress: ProgressCb | None = None) -> list[str]:
    """Install missing and remove extra client mods to match desired_mods."""
    changes = plan_sync_changes()
    if not changes:
        return []
    done = _apply_planned_mod_changes(changes, progress=progress)
    game = detect_game()
    if game:
        _sync_dlls_txt_for_desired_mods(game)
        _log_vf_on_disk_summary(game, "pre-launch sync")
    log.info("Pre-launch mod sync: %s", done)
    return done


def plan_changes(desired: dict[str, bool] | None = None) -> list[dict[str, str]]:
    game = detect_game()
    if not game:
        return [{"action": "error", "id": "", "detail": "Game path not set"}]
    game_actual = detect_actual_state(game)
    desired = enforce_vanilla_helpers_for_hd_desired(
        dict(desired or settings.desired_mods),
        persist=True,
    )
    desired = _persist_reconciled_desired_mods(desired, actual=game_actual)
    actual = game_actual
    catalog = {m["id"]: m for m in load_mod_catalog()}
    changes: list[dict[str, str]] = []

    to_install = [
        mid
        for mid, want in desired.items()
        if want
        and mid in catalog
        and (
            not _effective_mod_installed(mid, actual)
            or _mpq_exclusive_variant_needs_reinstall(mid, desired, catalog)
            or _vanilla_tweaks_needs_catalog_repatch(mid, actual)
        )
    ]
    ordered: list[str] = []
    seen: set[str] = set()

    def add_with_deps(mid: str) -> None:
        if mid in seen:
            return
        seen.add(mid)
        mod = catalog.get(mid) or {}
        for dep in mod.get("dependencies") or []:
            if dep == _VANILLA_TWEAKS_ID and (
                desired.get(_VANILLA_TWEAKS_OLD_ID)
                or _effective_mod_installed(_VANILLA_TWEAKS_OLD_ID, actual)
            ):
                continue
            if not _effective_mod_installed(dep, actual):
                add_with_deps(dep)
        ordered.append(mid)

    keep_tweaks = None
    tweaks_wanted = [mid for mid in (_VANILLA_TWEAKS_ID, _VANILLA_TWEAKS_OLD_ID) if desired.get(mid)]
    if len(tweaks_wanted) > 1:
        from ichalaunch.mods.vanilla_tweaks import preferred_tweaks_variant

        keep_tweaks = preferred_tweaks_variant(desired)
        desired = dict(desired)
        for mid in tweaks_wanted:
            if mid != keep_tweaks:
                desired[mid] = False
        to_install = [mid for mid in to_install if mid not in _TWEAKS_IDS or mid == keep_tweaks]
    elif tweaks_wanted:
        keep_tweaks = tweaks_wanted[0]

    for mid in to_install:
        add_with_deps(mid)
    if keep_tweaks:
        ordered = [
            mid for mid in ordered if mid not in _TWEAKS_IDS or mid == keep_tweaks
        ]
    ordered = _order_fog_after_patch_e(_order_d3d9_layers(ordered))

    if _any_hd_patch_desired(desired) and not _effective_mod_installed(
        _VANILLA_HELPERS_ID, actual
    ):
        add_with_deps(_VANILLA_HELPERS_ID)

    for mid in ordered:
        mod = catalog.get(mid) or {}
        if mod.get("kind") == "manual_link":
            changes.append(
                {
                    "action": "manual",
                    "id": mid,
                    "detail": f"Manual: {mod.get('name')} — {mod.get('info_url') or ''}",
                }
            )
        else:
            changes.append({"action": "install", "id": mid, "detail": f"Install {mod.get('name', mid)}"})

    for mid, want in desired.items():
        have = _effective_mod_installed(mid, actual)
        if not want and have:
            # DXVK bundle owns VanillaFixes.exe — never remove VF when DXVK is desired.
            if mid == _VANILLAFIXES_ID and desired.get(_DXVK_ID):
                continue
            # VF ↔ DXVK: when switching back to regular VF, remove DXVK layer files.
            if mid == _DXVK_ID and desired.get(_VANILLAFIXES_ID):
                pass
            elif _desired_conflict_sibling_installed(mid, desired, catalog):
                continue
            if mid == _VANILLA_HELPERS_ID and _any_hd_patch_desired(desired):
                continue
            mod = catalog.get(mid) or {}
            if mod.get("kind") == "manual_link":
                continue
            # Official numeric patches are never launcher-owned. Presence of
            # Data/patch-9.mpq must not schedule a Pretty Night Sky delete.
            if _mod_remove_targets_only_stock_mpq(mod):
                continue
            detail = f"Remove {mod.get('name', mid)}"
            if mid == _HD_DXVK_ID and desired.get(_DXVK_ID):
                detail = (
                    f"Revert {mod.get('name', mid)} "
                    "(restore VanillaFixes-bundled DXVK + dxvk.conf)"
                )
            changes.append({"action": "remove", "id": mid, "detail": detail})
    return changes


def _install_addon_folder(src_root: Path, game: Path, preferred_name: str | None = None) -> None:
    # Prefer configured AddOns path; fall back to game/Interface/AddOns.
    addons = resolve_addons_dir(create=True)
    if addons is None:
        addons = game / "Interface" / "AddOns"
        addons.mkdir(parents=True, exist_ok=True)
    pairs = resolve_install_addon_roots(src_root)
    if preferred_name:
        want = preferred_name.lower()
        match = next(
            (
                p
                for p in pairs
                if p[1].lower() == want
                or want in p[1].lower()
                or want in p[0].name.lower()
            ),
            None,
        )
        if match:
            pairs = [match]
    if not pairs:
        try:
            had_toc = any(src_root.rglob("*.toc"))
        except OSError:
            had_toc = False
        if had_toc:
            raise FileNotFoundError(TOC_FOLDER_MISMATCH_MSG)
        return
    # dest_name is the .toc stem from resolve_install_addon_roots, not catalog/extract.
    for root, dest_name in pairs:
        placed, mismatch = place_install_addon_root(root, addons, dest_name)
        if placed:
            continue
        if mismatch is not None:
            note_pending_toc_mismatch(mismatch)
            continue
        log.warning(
            "Installed addon from %s is missing a matching .toc — it will not load",
            root.name,
        )


_VERSION_TOKEN_RE = re.compile(r"[-_]?v?\d+(?:\.\d+)+", re.IGNORECASE)


def _normalize_asset_stem(filename: str) -> str:
    """Strip extension + semver tokens so pinned and latest names compare equal.

    vanillafixes-1.5.2.zip      → vanillafixes
    vanillafixes-1.5.3-dxvk.zip → vanillafixes-dxvk
    """
    stem = Path(filename.split("?")[0]).stem.lower()
    return _VERSION_TOKEN_RE.sub("", stem).strip("-_.")


def _asset_contains_from_filename(filename: str) -> str:
    """Derive a version-stable asset_contains needle from a pinned release filename.

    vanillafixes-1.5.2-dxvk.zip → dxvk
    vanillafixes-1.5.2.zip      → vanillafixes
    """
    name = filename.split("?")[0]
    if not name:
        return ".zip"
    norm = _normalize_asset_stem(name)
    parts = [p for p in re.split(r"[-_]+", norm) if p]
    if len(parts) >= 2:
        # Prefer the trailing qualifier (e.g. dxvk) — unique across variant builds.
        return parts[-1]
    if parts:
        return parts[0]
    suffix = Path(name).suffix.lower()
    return suffix if suffix else ".zip"


def _pick_release_asset(
    assets: list[dict[str, Any]],
    *,
    asset_contains: str | None = None,
    asset_not_contains: str | None = None,
    prefer_filename: str | None = None,
) -> dict[str, Any] | None:
    """Pick a release asset by substring filters, then version-normalized stem."""
    needle = (asset_contains or ".zip").lower()
    exclude = (asset_not_contains or "").lower().strip()
    candidates = [a for a in assets if needle in (a.get("name") or "").lower()]
    if exclude:
        candidates = [a for a in candidates if exclude not in (a.get("name") or "").lower()]
    if not candidates:
        return None
    if prefer_filename:
        target = _normalize_asset_stem(prefer_filename)
        exact = [
            a
            for a in candidates
            if _normalize_asset_stem(a.get("name") or "") == target
        ]
        if exact:
            return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous (.zip matching several builds): prefer the shortest / base package.
    return min(candidates, key=lambda a: (len(a.get("name") or ""), a.get("name") or ""))


def _github_json(api: str) -> Any:
    """GET GitHub JSON; 404 → None so a side tag like SuperWoW ``Patch`` can be skipped."""
    try:
        r = github_get(api)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    try:
        return r.json()
    except ValueError:
        return None


def _asset_from_release(release: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(release, dict):
        return None
    assets = release.get("assets") or []
    if not isinstance(assets, list):
        return None
    return _pick_release_asset(
        assets,
        asset_contains=source.get("asset_contains") or ".zip",
        asset_not_contains=source.get("asset_not_contains"),
        prefer_filename=source.get("prefer_filename"),
    )


def _resolve_github_release_asset(source: dict[str, Any]) -> dict[str, Any]:
    """Pick the DLL/zip asset, walking past MPQ-only tags like SuperWoW ``Patch``."""
    repo = source["repo"]
    needle = source.get("asset_contains") or ".zip"
    tag = None
    if "/" in str(repo):
        owner, name = str(repo).split("/", 1)
        tag = github_latest_version_tag(owner, name)
        if tag and not is_usable_release_tag(tag):
            tag = None

    apis: list[str] = []
    if tag:
        apis.append(f"https://api.github.com/repos/{repo}/releases/tags/{quote(str(tag), safe='')}")
    apis.append(f"https://api.github.com/repos/{repo}/releases/latest")

    seen: set[str] = set()
    for api in apis:
        if api in seen:
            continue
        seen.add(api)
        asset = _asset_from_release(_github_json(api), source)
        if asset:
            return asset

    listing = _github_json(f"https://api.github.com/repos/{repo}/releases")
    if isinstance(listing, list):
        for release in listing:
            asset = _asset_from_release(release, source)
            if asset:
                return asset

    detail = needle
    if source.get("asset_not_contains"):
        detail = f"{needle} (excluding {source['asset_not_contains']})"
    raise FileNotFoundError(f"No release asset matching {detail} for {repo}")


def _looks_like_zip(filename: str, stype: str | None = None) -> bool:
    if stype in ("raw_zip", "github_zip"):
        return True
    return sanitize_filename(filename).lower().endswith(".zip")


def _is_zip_artifact(artifact: Path | bytes, source: dict[str, Any] | None = None) -> bool:
    """True when ``artifact`` should be treated as a zip (path, bytes, or source type)."""
    if isinstance(artifact, (bytes, bytearray)):
        return True
    if source and source.get("type") in ("raw_zip", "github_zip"):
        return True
    return artifact.suffix.lower() == ".zip"


def _pick_dxvk_win32_d3d9(search_root: Path) -> Path:
    """Pick the 32-bit D3D9 shim from a DXVK release tree."""
    candidates = [p for p in search_root.rglob("d3d9.dll") if p.is_file()]
    if not candidates:
        raise FileNotFoundError("d3d9.dll not found in DXVK archive")
    for folder in ("x32", "x86"):
        for path in candidates:
            if folder in {part.lower() for part in path.parts}:
                return path
    return min(candidates, key=lambda p: len(p.parts))


def _download_source(source: dict[str, Any], work: Path, progress: ProgressCb | None) -> Path | bytes:
    """Download a catalog source.

    Zip archives are kept in memory so Windows Defender cannot quarantine the
    tempfile between download and ``ZipFile`` open (VanillaFixes.exe / patcher
    DLLs commonly trip WinError 225 / Errno 22).
    """
    stype = source.get("type")
    status_only(progress, f"Downloading ({stype})...")
    if stype == "local":
        status_only(progress, "Copying local DLL...")
        src_path = resolve_local_source_path(source)
        filename = sanitize_filename(str(source.get("filename") or src_path.name))
        dest = work / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
        return dest
    bytes_cb = download_bytes_cb(progress)
    timeout = int(source.get("timeout") or (300 if stype == "google_drive" else 120))
    if stype == "google_drive":
        file_id = source["id"]
        filename = sanitize_filename(source.get("filename") or f"{file_id}.bin")
        url = google_drive_url(file_id)
        if _looks_like_zip(filename, stype):
            return download_bytes(url, progress=bytes_cb, timeout=timeout)
        dest = work / filename
        download_file(url, dest, progress=bytes_cb, timeout=timeout)
        return dest
    if stype in ("raw", "github_release", "github_zip", "raw_zip"):
        url = source["url"]
        filename = sanitize_filename(
            source.get("filename") or url.split("/")[-1].split("?")[0]
        )
        local = _local_source_override(source)
        if local is not None:
            status_only(progress, "Copying local DLL...")
            dest = work / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, dest)
            return dest
        file_timeout: int | tuple[int, int] = timeout
        if not source.get("timeout") and filename.lower().endswith(".mpq"):
            # HD patches are multi-GB; allow slower links between read chunks.
            file_timeout = (30, 600)
        try:
            known_total = int(source.get("expected_size") or 0)
        except (TypeError, ValueError):
            known_total = 0
        if _looks_like_zip(filename, stype):
            return download_bytes(
                url, progress=bytes_cb, timeout=timeout, known_total=known_total
            )
        dest = work / filename
        download_file(
            url,
            dest,
            progress=bytes_cb,
            timeout=file_timeout,
            known_total=known_total,
        )
        return dest
    if stype == "github_release_latest":
        asset = _resolve_github_release_asset(source)
        filename = sanitize_filename(asset.get("name") or "release.bin")
        url = asset["browser_download_url"]
        try:
            asset_size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            asset_size = 0
        if _looks_like_zip(filename, stype):
            return download_bytes(
                url, progress=bytes_cb, timeout=timeout, known_total=asset_size
            )
        dest = work / filename
        download_file(
            url, dest, progress=bytes_cb, timeout=timeout, known_total=asset_size
        )
        return dest
    raise ValueError(f"Unknown source type: {stype}")


def _repo_from_github_url(url: str) -> str | None:
    parsed = parse_github_url(url)
    if parsed:
        return f"{parsed[0]}/{parsed[1]}"
    # release / archive / raw.githubusercontent.com
    m = re.match(
        r"https?://(?:raw\.)?github(?:usercontent)?\.com/([^/]+)/([^/]+)/",
        url.strip(),
    )
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def _tag_from_release_url(url: str) -> str | None:
    m = re.search(r"/releases/download/([^/]+)/", url)
    return m.group(1) if m else None


_BLANK_VERSION_LABELS = frozenset(
    {
        "detected",
        "catalog",
        "remote",
        "patch",
        "latest",
        "stable",
        "release",
        "assets",
        "mpq",
        "textures",
    }
)


def _displayable_mod_version(raw: str | None) -> str:
    """Keep tags / short SHAs; drop fingerprints, timestamps, and Release aliases."""
    text = str(raw or "").strip().strip('"')
    if not text or text.lower() in _BLANK_VERSION_LABELS:
        return ""
    if text.startswith(("http://", "https://", "W/")) or "\\" in text:
        # Release asset URLs often embed the real semver in the filename.
        extracted = extract_semver_label(text.rsplit("/", 1)[-1])
        return extracted
    if looks_like_timestamp_label(text):
        return ""
    if len(text) > 40:
        extracted = extract_semver_label(text)
        return extracted
    if re.fullmatch(r"[0-9a-f]{40}", text, re.I):
        return text[:7]
    if re.fullmatch(r"[0-9a-f]{7,12}", text, re.I):
        return text[:7]
    if is_preferred_release_alias(text):
        return ""
    extracted = extract_semver_label(text)
    if extracted and (
        is_preferred_release_alias(text)
        or "/" in text
        or "\\" in text
        or text.lower().endswith((".zip", ".rar", ".7z", ".mpq", ".dll", ".exe"))
        or " " in text
    ):
        return extracted
    if is_usable_release_tag(text) or is_version_tag(text):
        return text
    return extracted


def _tips_repo_entry(owner: str, name: str) -> dict[str, Any]:
    from ichalaunch.addons.git_refs import repo_cache_key
    from ichalaunch.addons.tip_index import current_index, ensure_local_index, index_repo_count

    key = repo_cache_key(owner, name)
    index = current_index()
    if index_repo_count(index) == 0:
        index = ensure_local_index()
    repos = index.get("repos")
    if isinstance(repos, dict):
        entry = repos.get(key)
        if isinstance(entry, dict) and entry:
            return entry
    return {}


def mod_version_label(
    mod: dict[str, Any] | None,
    installed: dict[str, Any] | None = None,
) -> str:
    """Grey-row version when we know a real tag, pin, or commit — empty otherwise."""
    if installed:
        for key in ("version_display", "tag", "sha", "url"):
            label = _displayable_mod_version(installed.get(key) if installed else None)
            if label:
                return label
    if not mod:
        return ""
    source = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    if not source:
        return ""
    if str(source.get("type") or "") == "local":
        return _displayable_mod_version(source.get("version"))
    pinned = _tag_from_release_url(str(source.get("url") or ""))
    label = _displayable_mod_version(pinned)
    if label:
        return label
    # Pinned release asset filenames often carry the real semver.
    label = _displayable_mod_version(str(source.get("url") or ""))
    if label:
        return label
    repo = str(source.get("repo") or "").strip() or (_repo_from_github_url(str(source.get("url") or "")) or "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    entry = _tips_repo_entry(owner, name)
    stype = str(source.get("type") or "")
    if stype in ("github_release_latest", "github_release"):
        for key in ("display_version", "latest_tag"):
            label = _displayable_mod_version(entry.get(key))
            if label:
                return label
        from ichalaunch.addons.tip_index import lookup_display_version

        return lookup_display_version(owner, name)
    if stype == "github_zip":
        return _displayable_mod_version(entry.get("sha"))
    return ""


def _branch_from_archive_url(url: str) -> str | None:
    m = re.search(r"/archive/refs/heads/([^/.]+)", url)
    return m.group(1) if m else None


def _head_identity(url: str) -> dict[str, str]:
    """ETag / Last-Modified fingerprint for a static download URL."""
    headers = _download_headers(url)
    r = requests.head(url, timeout=30, headers=headers, allow_redirects=True)
    if r.status_code >= 400:
        # Some hosts reject HEAD — fall back to a ranged GET
        r = requests.get(
            url, timeout=30, headers={**headers, "Range": "bytes=0-0"}, allow_redirects=True
        )
    etag = (r.headers.get("ETag") or "").strip()
    last_mod = (r.headers.get("Last-Modified") or "").strip()
    key = etag or last_mod or url
    # Never surface Last-Modified / ISO dates as the UI "version" — keep them
    # only in the fingerprint key / last_modified field.
    display = (etag.strip('"')[:16] if etag else "") or "remote"
    return {
        "key": key,
        "etag": etag,
        "last_modified": last_mod,
        "display": display,
    }


def _remote_release_tag(repo: str) -> str | None:
    """Latest release/git tag via tip index → Atom → git refs → REST."""
    repo = (repo or "").strip()
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return github_latest_version_tag(owner, name)


def _catalog_release_tag(repo: str) -> str | None:
    """Latest tag from the shared tip index only (no per-repo probes)."""
    repo = (repo or "").strip()
    if "/" not in repo:
        return None
    from ichalaunch.addons.tip_index import lookup_latest_tag

    owner, name = repo.split("/", 1)
    tag = lookup_latest_tag(owner, name)
    if not tag or not is_usable_release_tag(tag):
        return None
    return tag


def _catalog_commit_tip(owner: str, name: str, branch: str | None) -> dict[str, str] | None:
    """Commit SHA from the shared tip index only (no per-repo probes)."""
    from ichalaunch.addons.tip_index import lookup_tip

    hit = lookup_tip(owner, name, branch)
    if not hit:
        return None
    sha, resolved = hit
    if not sha:
        return None
    return {"sha": sha, "branch": resolved or (branch or "")}


def _remote_identity(
    source: dict[str, Any], *, catalog_only: bool = False
) -> dict[str, Any] | None:
    """Return comparable remote identity for a mod source, or None if unsupported.

    *catalog_only* uses the shared tip-SHA JSON and never probes GitHub
    (no git refs, Atom, REST, or HEAD). Bulk update checks must pass True.
    """
    if not source:
        return None
    stype = source.get("type")
    if stype == "github_release_latest":
        repo = source.get("repo")
        if not repo:
            return None
        tag = _catalog_release_tag(str(repo)) if catalog_only else _remote_release_tag(str(repo))
        if not tag:
            return None
        display = tag
        if is_preferred_release_alias(tag) and "/" in str(repo):
            owner, name = str(repo).split("/", 1)
            from ichalaunch.addons.tip_index import lookup_display_version

            nicer = lookup_display_version(owner, name)
            if nicer:
                display = nicer
            elif not catalog_only:
                from ichalaunch.addons.git_refs import fetch_releases_atom_display_version

                nicer = fetch_releases_atom_display_version(owner, name, prefer_tag=tag)
                if nicer:
                    display = nicer
        return {
            "kind": "release",
            "key": tag,
            "display": display,
            "repo": repo,
            "tag": tag,
        }
    if stype == "github_release":
        url = source.get("url") or ""
        repo = _repo_from_github_url(url)
        pinned = _tag_from_release_url(url)
        if repo:
            try:
                tag = _catalog_release_tag(repo) if catalog_only else _remote_release_tag(repo)
                if tag:
                    return {
                        "kind": "release",
                        "key": tag,
                        "display": tag,
                        "repo": repo,
                        "tag": tag,
                        "pinned": pinned,
                    }
            except GitHubRateLimitError:
                if catalog_only:
                    return None
                raise
            except Exception:
                pass
        if catalog_only:
            return None
        if url:
            ident = _head_identity(url)
            return {"kind": "http", "url": url, **ident}
        return None
    if stype == "github_zip":
        url = source.get("url") or ""
        repo = _repo_from_github_url(url)
        branch = _branch_from_archive_url(url) or "main"
        if not repo:
            return None
        owner, name = repo.split("/", 1)
        if catalog_only:
            remote = _catalog_commit_tip(owner, name, branch)
            if not remote:
                return None
        else:
            remote = github_remote_tip(owner, name, branch)
        sha = remote["sha"]
        return {
            "kind": "commit",
            "key": sha,
            "display": sha[:7],
            "repo": repo,
            "branch": remote["branch"],
            "sha": sha,
        }
    if stype in ("raw", "raw_zip"):
        url = source.get("url") or ""
        if not url:
            return None
        # Prefer commit for github raw paths when possible
        repo = _repo_from_github_url(url)
        if repo and "raw.githubusercontent.com" in url:
            parts = urlparse(url).path.strip("/").split("/")
            # owner/repo/refs/heads/branch/... or owner/repo/branch/...
            if len(parts) >= 4:
                owner, name = parts[0], parts[1]
                if parts[2] == "refs" and parts[3] == "heads" and len(parts) >= 5:
                    branch = parts[4]
                else:
                    branch = parts[2]
                if catalog_only:
                    remote = _catalog_commit_tip(owner, name, branch)
                    if not remote:
                        return None
                    sha = remote["sha"]
                    return {
                        "kind": "commit",
                        "key": sha,
                        "display": sha[:7],
                        "repo": f"{owner}/{name}",
                        "branch": remote["branch"],
                        "sha": sha,
                    }
                try:
                    remote = github_remote_tip(owner, name, branch)
                    sha = remote["sha"]
                    return {
                        "kind": "commit",
                        "key": sha,
                        "display": sha[:7],
                        "repo": f"{owner}/{name}",
                        "branch": remote["branch"],
                        "sha": sha,
                    }
                except Exception:
                    pass
        if catalog_only:
            return None
        ident = _head_identity(url)
        return {"kind": "http", "url": url, **ident}
    if stype == "google_drive":
        if catalog_only:
            return None
        file_id = source.get("id") or ""
        if not file_id:
            return None
        url = google_drive_url(file_id)
        try:
            ident = _head_identity(url)
            return {"kind": "http", "url": url, "drive_id": file_id, **ident}
        except Exception:
            return {
                "kind": "http",
                "key": file_id,
                "display": file_id[:12],
                "drive_id": file_id,
                "url": url,
            }
    if stype == "local":
        try:
            path = resolve_local_source_path(source)
        except FileNotFoundError:
            return None
        stat = path.stat()
        return {
            "kind": "local",
            "key": f"{stat.st_mtime_ns}:{stat.st_size}",
            "display": str(source.get("version") or "local"),
            "path": str(path),
        }
    return None


def _clear_exclusive_sibling_install_records(mod_id: str, mod: dict[str, Any]) -> None:
    """Drop install metadata for conflict siblings sharing the same install slot."""
    vf_dxvk_pair = frozenset({_VANILLAFIXES_ID, _DXVK_ID})
    for conf in mod.get("conflicts") or []:
        pair = frozenset({mod_id, conf})
        if (
            mod.get("kind") == "mpq_file"
            or pair == vf_dxvk_pair
            or pair == _TWEAKS_IDS
        ):
            settings.remove_installed_mod(conf)


def _backfill_installed_mod_meta(mod_id: str, mod: dict[str, Any]) -> dict[str, Any]:
    """Build install metadata for a mod detected on disk without a saved record."""
    from ichalaunch.addons.github import iso_date_today

    source = mod.get("source") or {}
    today = iso_date_today()
    meta: dict[str, Any] = {
        "name": mod.get("name"),
        "kind": mod.get("kind"),
        "installed_at": today,
        "updated_at": today,
        "backfilled": True,
    }
    pinned = _tag_from_release_url(source.get("url") or "")
    if pinned:
        meta["version_key"] = pinned
        meta["version_display"] = pinned
        meta["version_kind"] = "release"
        meta["tag"] = pinned
    elif source.get("url"):
        meta["version_key"] = source["url"]
        meta["version_display"] = "detected"
        meta["url"] = source["url"]
    if mod.get("kind") == "mpq_file":
        meta["variant_id"] = mod_id
        src_url = source.get("url")
        if src_url:
            meta["source_url"] = src_url
    return meta


def _backfill_detected_installed_mods(actual: dict[str, bool]) -> None:
    """Persist installed_mods for on-disk mods missing install metadata.

    Uses reconciled detect state so MPQ siblings only get one backfilled record.
    """
    catalog = mod_catalog_map()
    installed = settings.installed_mods
    for mid, present in actual.items():
        if not present or mid not in catalog or mid in installed:
            continue
        if mid == _VANILLA_TWEAKS_OLD_ID:
            # Do not stamp Old from a disk guess — that cleared the V2 record
            # and flipped existing Tweaks users onto brndd.
            if not settings.desired_mods.get(mid) and mid not in settings.user_set_mods:
                continue
        mod = catalog[mid]
        if mod.get("kind") in ("manual_link", "wdb_block", "config_script_memory"):
            continue
        _clear_exclusive_sibling_install_records(mid, mod)
        settings.set_installed_mod(mid, _backfill_installed_mod_meta(mid, mod))


def _addon_remote_identity(
    mod: dict[str, Any], *, catalog_only: bool = False
) -> dict[str, Any] | None:
    """Fingerprint a mod's companion ``addon_source``, or None if it has none.

    Mods like SuperWoW ship as a pair: a DLL pinned to the latest release and a
    companion addon tracked off a branch HEAD. Only the DLL half was ever
    fingerprinted, so the two could drift apart silently.
    """
    addon_src = mod.get("addon_source")
    if not addon_src:
        return None
    try:
        return _remote_identity(addon_src, catalog_only=catalog_only)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fingerprint addon half of %s: %s", mod.get("id"), exc)
        return None


def _record_mod_install(
    mod_id: str,
    mod: dict[str, Any],
    source_override: dict[str, Any] | None = None,
    *,
    unverified: bool = False,
) -> None:
    """Persist installed version fingerprint after a successful install."""
    from ichalaunch.addons.github import iso_date_today

    _clear_exclusive_sibling_install_records(mod_id, mod)
    source = source_override if source_override is not None else (mod.get("source") or {})
    prev = settings.installed_mods.get(mod_id) or {}
    today = iso_date_today()
    meta: dict[str, Any] = {
        "name": mod.get("name"),
        "kind": mod.get("kind"),
        "installed_at": prev.get("installed_at") or today,
        "updated_at": today,
        # Always set so merge clears a prior soft-skip flag on clean reinstall.
        "unverified": bool(unverified),
    }
    # Prefer the catalog-pinned tag when present (accurate for what was downloaded).
    pinned = _tag_from_release_url((source or {}).get("url") or "")
    try:
        remote = _remote_identity(source) if source else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fingerprint mod %s: %s", mod_id, exc)
        remote = None
    if pinned and source.get("type") in ("github_release", "raw", "raw_zip"):
        meta["version_key"] = pinned
        meta["version_display"] = pinned
        meta["version_kind"] = "release"
        meta["tag"] = pinned
        if remote and remote.get("repo"):
            meta["repo"] = remote["repo"]
    elif remote:
        meta["version_key"] = remote.get("key")
        display = str(remote.get("display") or "")
        # Prefer a semver extracted from the release asset / tip over Release aliases
        # or HTTP date fingerprints that older builds stored as version_display.
        nicer = _displayable_mod_version(display)
        if not nicer:
            for candidate in (
                remote.get("tag"),
                source.get("url"),
                remote.get("url"),
            ):
                nicer = _displayable_mod_version(candidate)
                if nicer:
                    break
        if not nicer and remote.get("repo") and "/" in str(remote.get("repo")):
            owner, name = str(remote["repo"]).split("/", 1)
            from ichalaunch.addons.tip_index import lookup_display_version

            nicer = lookup_display_version(owner, name)
        meta["version_display"] = nicer or display
        meta["version_kind"] = remote.get("kind")
        for k in ("etag", "last_modified", "tag", "sha", "repo", "branch", "url"):
            if remote.get(k):
                meta[k] = remote[k]
    elif source.get("url"):
        meta["version_key"] = source["url"]
        meta["version_display"] = "catalog"
        meta["url"] = source["url"]
    addon_remote = _addon_remote_identity(mod)
    if addon_remote and addon_remote.get("key"):
        meta["addon_version_key"] = addon_remote.get("key")
        meta["addon_version_display"] = addon_remote.get("display")
    if mod.get("kind") == "mpq_file":
        meta["variant_id"] = mod_id
        src_url = (source or {}).get("url")
        if src_url:
            meta["source_url"] = src_url
    if mod_id == _VANILLA_TWEAKS_ID:
        from ichalaunch.mods.vanilla_tweaks import tweaks_install_stamp

        meta.update(tweaks_install_stamp(settings.vanilla_tweaks_options))
    elif mod_id == _VANILLA_TWEAKS_OLD_ID:
        from ichalaunch.mods.vanilla_tweaks import tweaks_old_install_stamp

        meta.update(tweaks_old_install_stamp(settings.vanilla_tweaks_old_options))
    settings.set_installed_mod(mod_id, meta)


def recently_checked_mod_updates(cooldown_sec: int | None = None) -> bool:
    """True if an automatic mod scan should skip (hardcoded 15-minute refresh)."""
    if cooldown_sec is None:
        cooldown_sec = settings.auto_scan_cooldown_sec()
    raw = settings.get("last_mod_update_check")
    if not raw:
        return False
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < float(cooldown_sec)


def check_mod_updates(
    *,
    respect_cooldown: bool = False,
    progress: Any = None,
) -> ModUpdateCheckResult:
    """Compare installed client mods to the shared catalog tip-SHA JSON.

    One remote fetch of ``addon_tips.json``, then a local compare. Per-mod
    git/REST/HEAD probes are not used here.
    """
    if respect_cooldown and recently_checked_mod_updates():
        return ModUpdateCheckResult(skipped_recent=True)

    game = detect_game()
    if not game:
        return ModUpdateCheckResult(status_message="Set a game path before checking updates")

    actual = detect_actual_state(game)
    updates: list[dict[str, Any]] = []
    checked = 0
    skipped = 0

    to_check: list[dict[str, Any]] = []
    for mod in load_mod_catalog():
        mid = mod["id"]
        if not actual.get(mid):
            continue
        kind = mod.get("kind")
        source = mod.get("source")
        if kind in ("manual_link", "wdb_block", "config_script_memory") or not source:
            skipped += 1
            continue
        to_check.append(mod)

    on_count = getattr(progress, "on_count", None) if progress is not None else None
    if callable(on_count):
        on_count(0, 1, "Fetching update catalog…")

    from ichalaunch.addons.tip_index import index_repo_count, refresh_tip_index

    try:
        index = refresh_tip_index()
    except Exception as exc:  # noqa: BLE001
        log.debug("Catalog tip index refresh skipped: %s", exc)
        index = None

    if index_repo_count(index) == 0:
        settings.set("last_mod_update_check", time.time())
        if callable(on_count):
            on_count(1, 1, "Checking client mod updates…")
        return ModUpdateCheckResult(
            updates=updates,
            checked=checked,
            skipped=skipped + len(to_check),
            status_message="Update catalog unavailable",
        )

    if callable(on_count):
        on_count(0, 1, "Checking client mod updates…")

    if not to_check:
        settings.set("last_mod_update_check", time.time())
        if callable(on_count):
            on_count(1, 1, "Checking client mod updates…")
        return ModUpdateCheckResult(updates=updates, checked=checked, skipped=skipped)

    log.info(
        "Client mod update check via catalog index (%d repo(s)); comparing %d installed mod(s)",
        index_repo_count(index),
        len(to_check),
    )

    for mod in to_check:
        mid = mod["id"]
        source = mod.get("source")
        remote = _remote_identity(source, catalog_only=True)
        if not remote:
            skipped += 1
            continue
        checked += 1
        local = settings.installed_mods.get(mid) or {}
        local_key = local.get("version_key") or local.get("tag") or local.get("sha") or local.get("etag")
        if not local_key:
            pinned = _tag_from_release_url((source or {}).get("url") or "")
            if pinned and remote.get("key") and pinned != remote.get("key"):
                meta = _backfill_installed_mod_meta(mid, mod)
                meta.pop("backfilled", None)
                meta.update(
                    {
                        "version_key": pinned,
                        "version_display": pinned,
                        "version_kind": "release",
                        "tag": pinned,
                        **{k: remote[k] for k in ("repo",) if remote.get(k)},
                    }
                )
                settings.set_installed_mod(mid, meta)
                updates.append(
                    {
                        "id": mid,
                        "name": mod.get("name") or mid,
                        "local": pinned,
                        "remote": remote.get("display") or str(remote.get("key"))[:12],
                        "kind": remote.get("kind"),
                    },
                )
                continue
            # First check: baseline remote without flagging an update
            meta = _backfill_installed_mod_meta(mid, mod)
            meta.pop("backfilled", None)
            meta.update(
                {
                    "version_key": remote.get("key"),
                    "version_display": remote.get("display"),
                    "version_kind": remote.get("kind"),
                    **{
                        k: remote[k]
                        for k in ("etag", "last_modified", "tag", "sha", "repo", "branch", "url")
                        if remote.get(k)
                    },
                }
            )
            addon_base = _addon_remote_identity(mod, catalog_only=True)
            if addon_base and addon_base.get("key"):
                meta["addon_version_key"] = addon_base.get("key")
                meta["addon_version_display"] = addon_base.get("display")
            settings.set_installed_mod(mid, meta)
            continue
        addon_remote = _addon_remote_identity(mod, catalog_only=True)
        addon_local = local.get("addon_version_key")
        addon_drifted = bool(
            addon_remote
            and addon_remote.get("key")
            and addon_local
            and addon_local != addon_remote.get("key")
        )
        if local_key != remote.get("key") or addon_drifted:
            updates.append(
                {
                    "id": mid,
                    "name": mod.get("name") or mid,
                    "local": (local.get("version_display") or str(local_key)[:12])
                    + (
                        f" (addon {str(addon_local)[:7]})"
                        if addon_drifted and local_key == remote.get("key")
                        else ""
                    ),
                    "remote": (remote.get("display") or str(remote.get("key"))[:12])
                    + (
                        f" (addon {addon_remote.get('display') or str(addon_remote.get('key'))[:7]})"
                        if addon_drifted and local_key == remote.get("key")
                        else ""
                    ),
                    "kind": remote.get("kind"),
                }
            )

    if callable(on_count):
        on_count(1, 1, "Checking client mod updates…")

    settings.set("last_mod_update_check", time.time())
    return ModUpdateCheckResult(updates=updates, checked=checked, skipped=skipped)


def update_mod(mod_id: str, progress: ProgressCb | None = None) -> None:
    """Re-download and re-apply a single client mod (prefer latest GitHub release when pinned)."""
    install_mod(mod_id, progress=progress, prefer_latest=True)


def update_mods(mod_ids: list[str], progress: ProgressCb | None = None) -> list[str]:
    done: list[str] = []
    for mid in mod_ids:
        status_only(progress, f"Updating {mid}…")
        install_mod(mid, progress=progress, prefer_latest=True)
        done.append(mid)
    return done


def install_mod(mod_id: str, progress: ProgressCb | None = None, *, prefer_latest: bool = False) -> list[str]:
    """Install one client mod. Returns soft-verify notices (``~ id``) when PE check soft-skips."""
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game not found")
    mod = get_mod(mod_id)
    if not mod:
        raise KeyError(mod_id)

    if _mod_requires_game_closed(mod, game) and wow_exe_running(game):
        hint = file_in_use_hint(game / "WoW.exe", game / "VanillaFixes.exe", game_path=game)
        log.warning("Refusing %s install — game process still running: %s", mod_id, hint)
        raise OSError(
            32,
            (
                f"Cannot update {mod.get('name') or mod_id} while the game is running. "
                f"{hint}"
            ),
            str(game / "WoW.exe"),
        )

    kind = mod.get("kind")
    backup_root: Path | None = None
    try:
        backup_root = create_backup(
            game,
            f"before_{mod_id}",
            _install_backup_paths(game, mod),
        )
    except OSError as exc:
        log.warning("Pre-install backup for %s skipped: %s", mod_id, exc)

    try:
        with tempfile.TemporaryDirectory(prefix="ichalaunch_") as tmp:
            work = Path(tmp)
            source = dict(mod.get("source") or {}) if mod.get("source") else None
            if (
                prefer_latest
                and source
                and source.get("type") == "github_release"
                and mod_id != _VANILLA_TWEAKS_OLD_ID
            ):
                repo = _repo_from_github_url(source.get("url") or "")
                if repo:
                    fname = (
                        source.get("filename")
                        or (source.get("url") or "").split("/")[-1].split("?")[0]
                    )
                    # Prefer catalog override; else derive a version-stable needle from
                    # the pinned filename (vanillafixes-1.5.2-dxvk.zip → "dxvk"), not
                    # the full versioned name which breaks when the tag bumps.
                    needle = source.get("asset_contains") or _asset_contains_from_filename(fname)
                    converted: dict[str, Any] = {
                        "type": "github_release_latest",
                        "repo": repo,
                        "asset_contains": needle if needle else ".zip",
                        "prefer_filename": fname,
                    }
                    if source.get("asset_not_contains"):
                        converted["asset_not_contains"] = source["asset_not_contains"]
                    source = converted

            if kind == "wdb_block":
                wdb = game / "WDB"
                if wdb.is_dir():
                    shutil.rmtree(wdb)
                elif wdb.exists() and not wdb.is_file():
                    safe_remove(wdb)
                if not wdb.exists():
                    wdb.write_text("", encoding="utf-8")
                status_only(progress, "WDB block applied")
                return _finish_mod_install(mod_id, mod, source)

            if kind == "exe_patch":
                assert source
                z = _download_source(source, work, progress)
                extracted = extract_zip(z, work / "extract", progress=progress)
                vt = next(extracted.rglob("vanilla-tweaks.exe"), None) or next(
                    extracted.rglob("vanilla_tweaks.exe"), None
                )
                if not vt:
                    raise FileNotFoundError("vanilla-tweaks.exe not found in archive")
                wow = wow_exe_in(game) or (game / "WoW.exe")
                if resolve_ci(game, _VANILLA_TWEAKS_BACKUP) is None:
                    _install_copy(wow, game / _VANILLA_TWEAKS_BACKUP, game_path=game)
                from ichalaunch.mods.vanilla_tweaks import (
                    VANILLA_TWEAKS_OLD_ID,
                    tweaks_patch_command,
                    vanilla_tweaks_infile,
                )

                # Always patch the stock backup so option changes do not stack.
                infile = vanilla_tweaks_infile(game, wow, _VANILLA_TWEAKS_BACKUP)
                opts = (
                    settings.vanilla_tweaks_old_options
                    if mod_id == VANILLA_TWEAKS_OLD_ID
                    else settings.vanilla_tweaks_options
                )
                cmd = tweaks_patch_command(mod_id, vt, infile, opts)
                status_only(progress, "Patching WoW.exe with Vanilla Tweaks...")
                before_tweaked = tweaked_exe_snapshot(game)
                # vanilla-tweaks.exe is a Windows PE, so off Windows this has to
                # go through Proton. Running it directly raised OSError at exec
                # time, which meant the headline feature of 1.3.0 could not work
                # on Linux at all.
                run_windows_exe(cmd, game)
                tweaked = patched_exe_from_run(game, infile, wow, before_tweaked)
                if tweaked is None:
                    raise FileNotFoundError(
                        "Vanilla Tweaks exited successfully but wrote no patched "
                        f"executable beside {infile.name}, so the client is "
                        "unchanged."
                    )
                swap_patched_client_exe(tweaked, wow)
                soft: list[str] = []
                try:
                    if not validate_pe_binary(wow, min_size=_DLL_PE_MIN_BYTES):
                        soft = [wow.name]
                except OSError as exc:
                    if is_lock_or_av_error(exc):
                        soft = [wow.name]
                    else:
                        raise
                return _finish_mod_install(mod_id, mod, source, soft_skipped=soft)

            if kind == "zip_root":
                assert source
                z = _download_source(source, work, progress)
                extracted = extract_zip(z, work / "extract", progress=progress)
                dlls_txt = game / "dlls.txt"

                def _zip_root_copy(src: Path, dest: Path) -> None:
                    # VanillaFixes/DXVK zips ship a template dlls.txt — never replace a
                    # user-managed list (IchaLaunch entries + manual DLL lines).
                    if dest.name.lower() == "dlls.txt" and dlls_txt.is_file():
                        log.info("Preserving existing dlls.txt while installing %s", mod_id)
                        return
                    # Hardening: upstream zips do not ship WTF today, but a bundle
                    # must never clobber user configs if a future one does.
                    rel_parts = dest.relative_to(game).parts
                    if (
                        rel_parts and rel_parts[0].lower() == "wtf"
                    ) or dest.name.lower() == "config.wtf":
                        log.info(
                            "Skipping archive config entry %s while installing %s",
                            "/".join(rel_parts),
                            mod_id,
                        )
                        return
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _install_copy(src, dest, game_path=game)

                # copy all files into game root
                for item in extracted.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(extracted)
                        _zip_root_copy(item, game / rel)
                # Flatten: if VanillaFixes.exe is nested, find it
                vf = next(game.rglob("VanillaFixes.exe"), None)
                if vf and vf.parent != game:
                    for f in vf.parent.iterdir():
                        if f.is_file():
                            _zip_root_copy(f, game / f.name)
                _apply_frame_cap_if_enabled(game)
                soft = _verify_mod_install(game, mod)
                return _finish_mod_install(mod_id, mod, source, soft_skipped=soft)

            if kind in ("dll_file", "dll_bundle"):
                assert source
                artifact = _download_source(source, work, progress)
                search_root = work
                if _is_zip_artifact(artifact, source):
                    search_root = extract_zip(artifact, work / "extract", progress=progress)
                else:
                    # single dll
                    assert isinstance(artifact, Path)
                    dest_name = source.get("filename") or artifact.name
                    _install_copy(artifact, game / dest_name, game_path=game)

                for fspec in mod.get("files") or []:
                    match = fspec["match"]
                    found = next(search_root.rglob(match), None)
                    if found:
                        _install_copy(found, game / fspec["destination"], game_path=game)

                if mod.get("addon_folder_match"):
                    folder = next(search_root.rglob(mod["addon_folder_match"]), None)
                    if folder and folder.is_dir():
                        _install_addon_folder(folder, game, preferred_name=mod["addon_folder_match"])

                addon_src = mod.get("addon_source")
                if addon_src:
                    a = _download_source(addon_src, work / "addon", progress)
                    if _is_zip_artifact(a, addon_src):
                        aroot = extract_zip(a, work / "addon_extract", progress=progress)
                    else:
                        assert isinstance(a, Path)
                        aroot = a.parent
                    _install_addon_folder(aroot, game, preferred_name=addon_src.get("folder"))

                dlls = (mod.get("dlls_txt") or {}).get("add") or []
                if dlls:
                    _update_dlls_txt_all(game, add=dlls)
                soft = _verify_mod_install(game, mod)
                return _finish_mod_install(mod_id, mod, source, soft_skipped=soft)

            if kind == "mpq_file":
                assert source
                artifact = _download_source(source, work, progress)
                # Zip sources (e.g. Darker Nights archive) — extract and pick the MPQ
                if _is_zip_artifact(artifact, source):
                    extracted = extract_zip(artifact, work / "extract", progress=progress)
                    needle = (source.get("mpq_match") or Path(mod.get("destination") or "").name or ".mpq").lower()
                    prefer = (source.get("mpq_prefer_path") or "").replace("\\", "/").lower()
                    candidates = [p for p in extracted.rglob("*.mpq") if p.is_file()]
                    if prefer:
                        ranked = [p for p in candidates if prefer in str(p).replace("\\", "/").lower()]
                        candidates = ranked or candidates
                    if needle and needle != ".mpq":
                        matched = [p for p in candidates if needle in p.name.lower()]
                        candidates = matched or candidates
                    if not candidates:
                        raise FileNotFoundError(f"No .mpq found in archive for {mod_id}")
                    artifact = candidates[0]
                assert isinstance(artifact, Path)
                dest_rel = mod.get("destination") or f"Data/{source.get('filename') or artifact.name}"
                if is_stock_data_mpq(dest_rel):
                    stock_name = _mpq_dest_basename(dest_rel)
                    raise RuntimeError(
                        f"{mod.get('name')}: refusing to overwrite official "
                        f"{stock_name}. Numeric patch-*.mpq files are part of "
                        "the stock client."
                    )
                artifact = stage_mpq_before_data(artifact, dest_rel, work)
                dest = game / dest_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if mod_id == "hd_patch_c" and exact_name_present(
                    dest.parent, "Patch-V.mpq"
                ) and not exact_name_present(dest.parent, "patch-v.mpq"):
                    raise RuntimeError(
                        "Data/Patch-V.mpq is already in use (often a WMO crash-fix "
                        "pack). IchaLaunch will not overwrite it with Patch-C. "
                        "Rename or move Patch-V.mpq first."
                    )
                status_only(progress, f"Installing {dest.name} (large file)...")
                _install_copy(artifact, dest, game_path=game)
                if mod_id == "hd_patch_c":
                    for legacy in ("patch-C.mpq", "Patch-C.mpq"):
                        if exact_name_present(dest.parent, legacy):
                            safe_remove(dest.parent / legacy)
                return _finish_mod_install(mod_id, mod, source)

            if kind == "dxvk_hd":
                assert source
                from ichalaunch.core.paths import data_file

                artifact = _download_source(source, work, progress)
                assert isinstance(artifact, Path)
                extracted = extract_tar(artifact, work / "extract", progress=progress)
                dll = _pick_dxvk_win32_d3d9(extracted)
                status_only(progress, "Installing DXVK d3d9.dll...")
                _install_copy(dll, game / "d3d9.dll", game_path=game)
                for spec in mod.get("bundled_files") or []:
                    resource = data_file(str(spec.get("resource") or ""))
                    if not resource.is_file():
                        raise FileNotFoundError(f"Bundled mod file missing: {resource.name}")
                    dest_rel = str(spec.get("destination") or resource.name)
                    _install_copy(resource, game / dest_rel, game_path=game)
                _apply_frame_cap_if_enabled(game)
                soft = _verify_mod_install(game, mod)
                return _finish_mod_install(mod_id, mod, source, soft_skipped=soft)

            if kind == "config_script_memory":
                cfg = game / "WTF" / "Config.wtf"
                cfg.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                if cfg.exists():
                    lines = cfg.read_text(encoding="utf-8", errors="ignore").splitlines()
                    lines = [ln for ln in lines if not ln.strip().upper().startswith("SET SCRIPTMEMORY")]
                lines.insert(0, 'SET scriptMemory "0"')
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return _finish_mod_install(mod_id, mod, source)

            if kind == "glue_autologin":
                assert source
                z = _download_source(source, work, progress)
                extracted = extract_zip(z, work / "extract", progress=progress)
                glue_src = next(extracted.rglob("GlueXML"), None)
                if not glue_src:
                    # repo layout Data/Interface/GlueXML
                    glue_src = next(extracted.rglob("AutoLogin.lua"), None)
                    if glue_src:
                        glue_src = glue_src.parent
                if not glue_src:
                    raise FileNotFoundError("GlueXML / AutoLogin files not found")
                dest = game / "Data" / "Interface" / "GlueXML"
                dest.mkdir(parents=True, exist_ok=True)
                for f in glue_src.iterdir():
                    if f.is_file():
                        _install_copy(f, dest / f.name, game_path=game)
                # Glue signature skip is a one-time WoW.exe write. Updates only
                # replace AutoLogin.lua — rewriting the PE every time is what
                # produced a false "game is running" lock when AV or a
                # read-only bit blocked Path.write_bytes.
                apply_glue_signature_skip(wow_exe_in(game) or (game / "WoW.exe"), game)
                return _finish_mod_install(mod_id, mod, source)

            if kind == "dxvk_cursor":
                assert source
                artifact = _download_source(source, work, progress)
                assert isinstance(artifact, Path)
                _install_copy(artifact, game / "d3d9.dll", game_path=game)
                conf = game / "dxvk.conf"
                text = conf.read_text(encoding="utf-8", errors="ignore") if conf.exists() else ""
                if "enlargeHardwareCursor" not in text:
                    text = (text.rstrip() + "\n\nd3d9.enlargeHardwareCursor = 2\n")
                    conf.write_text(text, encoding="utf-8")
                _apply_frame_cap_if_enabled(game)
                soft = _verify_mod_install(game, mod)
                return _finish_mod_install(mod_id, mod, source, soft_skipped=soft)

            if kind == "manual_link":
                raise RuntimeError(
                    f"{mod.get('name')}: automatic download is not hosted as a direct file. "
                    f"See {mod.get('info_url') or 'the Turtle WoW mods guide'}. "
                    f"{mod.get('note') or ''}"
                )

            raise ValueError(f"Unsupported mod kind: {kind}")
    except (
        OSError,
        RuntimeError,
        FileNotFoundError,
        KeyError,
        shutil.Error,
        ValueError,
        requests.RequestException,
    ) as exc:
        log.warning("Install %s failed, rolling back: %s", mod_id, exc)
        _revert_failed_mod_install(game, mod, backup_root)
        raise


def _norm_rel_path(rel: str | Path) -> str:
    return str(rel).replace("\\", "/").strip("/").lower()


def _mod_owned_paths(mod: dict[str, Any]) -> set[str]:
    """Game-relative files this mod owns on disk (normalized, lowercase)."""
    owned: set[str] = set()

    def add(rel: Any) -> None:
        text = str(rel or "").strip()
        if text and not is_stock_data_mpq(text):
            owned.add(_norm_rel_path(text))

    src = mod.get("source") or {}
    if mod.get("kind") == "mpq_file":
        add(mod.get("destination"))
        if src.get("filename"):
            add(f"Data/{src['filename']}")
    elif src.get("filename"):
        add(src["filename"])
    for fspec in mod.get("files") or []:
        add(fspec.get("destination"))
    for dll in (mod.get("dlls_txt") or {}).get("add") or []:
        add(dll)
    mid = mod.get("id")
    if mid == "vanillafixes":
        add("VanillaFixes.exe")
        add("VfPatcher.dll")
    if mid == "dxvk":
        # Same launcher binaries as regular VF; shared-path keep applies when
        # switching DXVK → VanillaFixes so remove_mod does not delete them.
        add("VanillaFixes.exe")
        add("VfPatcher.dll")
        add("d3d9.dll")
        add("dxvk.conf")
    if mid == "hd_dxvk" or mod.get("kind") == "dxvk_hd":
        add("d3d9.dll")
        add("dxvk.conf")
    return owned


def _mod_remove_targets_only_stock_mpq(mod: dict[str, Any]) -> bool:
    """True when a remove would only touch official numeric Data patches."""
    if is_stock_data_mpq(mod.get("destination") or ""):
        return True
    src = (mod.get("source") or {}).get("filename") or ""
    dest = mod.get("destination") or ""
    candidates = [p for p in (dest, f"Data/{src}" if src else "") if p]
    return bool(candidates) and all(is_stock_data_mpq(p) for p in candidates)


_DLL_PE_MIN_BYTES = 1024
_SUPERWOW_DLL_MIN_BYTES = 200_000

# Vanilla 1.12.1 GlueXML signature skip (Vanilla Auto Login). Offsets are
# absolute in WoW.exe; a client shorter than the last offset is left alone.
_GLUE_SIGNATURE_SKIP = {
    0x2F113A: 0xEB,
    0x2F113B: 0x19,
    0x2F1158: 0x03,
    0x2F11A7: 0x03,
    0x2F11F0: 0xEB,
    0x2F11F1: 0xB2,
}


def glue_signature_skip_applied(data: bytes | bytearray) -> bool:
    """True when *data* already has the Vanilla Auto Login glue skip bytes."""
    if len(data) <= max(_GLUE_SIGNATURE_SKIP):
        return False
    return all(data[off] == val for off, val in _GLUE_SIGNATURE_SKIP.items())


def _glue_signature_skip_needed(wow: Path) -> bool:
    """True when *wow* exists, is large enough, and is not yet patched."""
    if not wow.is_file():
        return False
    try:
        size = wow.stat().st_size
    except OSError:
        return False
    if size <= max(_GLUE_SIGNATURE_SKIP):
        return False
    try:
        with wow.open("rb") as handle:
            for off, val in _GLUE_SIGNATURE_SKIP.items():
                handle.seek(off)
                got = handle.read(1)
                if len(got) != 1 or got[0] != val:
                    return True
        return False
    except OSError:
        return False


def apply_glue_signature_skip(wow: Path, game: Path) -> bool:
    """Patch WoW.exe glue signature checks. Returns True if bytes were written.

    Already-patched clients are left untouched so an Auto Login *update* only
    copies GlueXML lua — it must not rewrite the live PE (AV / read-only /
    exclusive probes then surface as "file in use / game is running").
    """
    if not wow.is_file() or not _glue_signature_skip_needed(wow):
        return False
    backup = game / "WoW-OriginalBackup.exe"
    if not backup.exists():
        _install_copy(wow, backup, game_path=game)
    data = bytearray(wow.read_bytes())
    if glue_signature_skip_applied(data) or len(data) <= max(_GLUE_SIGNATURE_SKIP):
        return False
    for off, val in _GLUE_SIGNATURE_SKIP.items():
        data[off] = val
    ensure_writable(wow)
    staged = wow.with_name(f".{wow.name}.__glue")
    try:
        staged.write_bytes(data)
        # .exe dest uses copy_file_tolerant + file_in_use_hint (not raw write_bytes).
        _install_copy(staged, wow, game_path=game)
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def _mod_requires_game_closed(mod: dict[str, Any], game: Path | None = None) -> bool:
    """True when install must replace PE files that WoW/VanillaFixes typically lock.

    ``glue_autologin`` only writes WoW.exe when the one-time signature skip is
    still missing. Later lua-only updates do not need the client closed.
    """
    kind = str(mod.get("kind") or "")
    if kind in {"dll_file", "dll_bundle", "dxvk_cursor", "dxvk_hd", "exe_patch", "zip_root"}:
        return True
    if kind == "glue_autologin":
        if game is None:
            return False
        return _glue_signature_skip_needed(wow_exe_in(game) or (game / "WoW.exe"))
    if (mod.get("dlls_txt") or {}).get("add"):
        return True
    for rel in _mod_owned_paths(mod):
        if Path(str(rel).replace("\\", "/")).suffix.lower() in {".dll", ".exe"}:
            return True
    return False


def _install_backup_paths(game: Path, mod: dict[str, Any]) -> list[Path]:
    """Files to snapshot before applying a mod (owned paths + core launch files)."""
    paths: list[Path] = [
        wow_exe_in(game) or (game / "WoW.exe"),
        game / "dlls.txt",
        game / "VanillaFixes.exe",
        game / ".ichalaunch" / "dlls.txt",
    ]
    seen: set[str] = set()
    for rel in _mod_owned_paths(mod):
        key = _norm_rel_path(rel)
        if key in seen:
            continue
        seen.add(key)
        # Snapshot the file as it is actually named on disk. Appending the
        # lowercased key instead means the backup silently contains nothing for
        # that entry, and a rollback then has nothing to restore.
        paths.append(resolve_ci(game, rel) or (game / rel))
    return paths


def _pe_min_bytes_for_rel(rel: str) -> int:
    name = Path(str(rel).replace("\\", "/")).name.lower()
    if name == "superwowhook.dll":
        return _SUPERWOW_DLL_MIN_BYTES
    return _DLL_PE_MIN_BYTES


def _verify_mod_install(game: Path, mod: dict[str, Any]) -> list[str]:
    """Ensure downloaded DLL/EXE artifacts are present and look like valid PE files.

    Returns the list of basenames whose PE verify soft-skipped (lock/AV). Callers
    keep the install and mark the mod unverified. Truncated or non-PE content
    still raises (hard failure / rollback).

    Covers dll_file, dll_bundle, dxvk_cursor, and zip_root (VanillaFixes / DXVK).
    """
    kind = mod.get("kind")
    if kind not in ("dll_file", "dll_bundle", "dxvk_cursor", "dxvk_hd", "zip_root"):
        return []
    failures: list[str] = []
    soft_skipped: list[str] = []
    for rel in _pe_artifacts_for_mod(mod):
        # _mod_owned_paths lowercases for comparison, so `rel` is a comparison
        # key and not a real filename. The artifacts genuinely written to disk
        # are VanillaFixes.exe and VfPatcher.dll; game / "vanillafixes.exe"
        # misses both on a case-sensitive filesystem and the install is then
        # reported as having failed.
        dest = resolve_ci(game, rel)
        if dest is None or not dest.is_file():
            failures.append(f"{rel} was not installed")
            continue
        try:
            if not validate_pe_binary(dest, min_size=_pe_min_bytes_for_rel(rel)):
                log.warning(
                    "PE verify skipped for %s (locked or antivirus scan in progress)",
                    dest.name,
                )
                soft_skipped.append(dest.name)
        except OSError as exc:
            # Hard failure — prefer plain detail (no [Errno N] wrapping for UI).
            failures.append(user_facing_os_error(exc))
    if failures:
        raise OSError(22, "; ".join(failures))
    return soft_skipped


def _finish_mod_install(
    mod_id: str,
    mod: dict[str, Any],
    source: dict[str, Any] | None,
    *,
    soft_skipped: list[str] | None = None,
) -> list[str]:
    """Record install metadata; return ``~ id`` notices when PE verify soft-skipped."""
    soft = list(soft_skipped or [])
    _record_mod_install(mod_id, mod, source, unverified=bool(soft))
    if soft:
        log.warning(
            "Install %s kept unverified after soft PE skip: %s",
            mod_id,
            ", ".join(soft),
        )
        return [f"~ {mod_id}"]
    return []


def _revert_failed_mod_install(
    game: Path,
    mod: dict[str, Any],
    backup_root: Path | None,
) -> None:
    """Restore pre-install backup and drop artifacts that were not in the snapshot."""
    manifest_files: set[str] = set()
    if backup_root and (backup_root / "manifest.json").is_file():
        try:
            manifest = json.loads((backup_root / "manifest.json").read_text(encoding="utf-8"))
            manifest_files = {
                str(f).replace("\\", "/").lower() for f in manifest.get("files", [])
            }
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read install backup manifest: %s", exc)

    if backup_root:
        try:
            restore_backup(game, backup_root)
        except OSError as exc:
            log.warning("Install rollback restore failed: %s", exc)

    for rel in _mod_owned_paths(mod):
        norm = _norm_rel_path(rel)
        if norm in manifest_files:
            continue
        target = resolve_ci(game, rel)
        if target is None:
            continue
        try:
            remove_path_strict(target)
        except OSError as exc:
            log.warning("Rollback cleanup skipped %s: %s", rel, exc)

    folder = (mod.get("addon_source") or {}).get("folder") or mod.get("addon_folder_match")
    if folder:
        addons = resolve_addons_dir(create=False)
        if addons is None:
            addons = game / "Interface" / "AddOns"
        try:
            remove_path_strict(addons / folder)
        except OSError as exc:
            log.warning("Rollback cleanup skipped addon %s: %s", folder, exc)


def _update_dlls_txt_all(
    game: Path,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    update_dlls_txt(game, add=add, remove=remove)
    mirror_dlls_txt_updates(game, add=add, remove=remove)


def _paths_shared_with_enabled(mod_id: str) -> dict[str, list[str]]:
    """Map owned file path -> other desired-enabled mods that also own it.

    HD patch letters and d3d9.dll can collide across mods — removal must keep
    files another enabled mod still needs.
    """
    desired = settings.desired_mods
    shared: dict[str, list[str]] = {}
    for other in load_mod_catalog():
        oid = other.get("id") or ""
        if not oid or oid == mod_id or not desired.get(oid, False):
            continue
        for rel in _mod_owned_paths(other):
            shared.setdefault(rel, []).append(oid)
    return shared


def remove_mod(mod_id: str, progress: ProgressCb | None = None) -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game not found")
    mod = get_mod(mod_id)
    if not mod:
        raise KeyError(mod_id)

    status_only(progress, f"Removing {mod_id}...")

    settings.remove_installed_mod(mod_id)

    kind = mod.get("kind")
    shared = _paths_shared_with_enabled(mod_id)
    failures: list[str] = []

    def remove_owned(rel: str | Path) -> None:
        """Strict-delete a file this mod owns, unless another enabled mod shares it."""
        if is_stock_data_mpq(rel):
            log.warning("Refusing to delete official client MPQ %s", rel)
            return
        owners = shared.get(_norm_rel_path(rel))
        if owners:
            log.info("Kept %s — shared with enabled mod(s): %s", rel, ", ".join(owners))
            return
        try:
            remove_path_strict(game / rel)
        except OSError as exc:
            failures.append(str(exc))

    def raise_failures() -> None:
        if failures:
            raise OSError(13, "; ".join(failures))

    if kind == "wdb_block":
        wdb = game / "WDB"
        if wdb.is_file():
            remove_path_strict(wdb)
        return

    if kind == "exe_patch":
        backup = resolve_ci(game, _VANILLA_TWEAKS_BACKUP)
        wow = wow_exe_in(game) or (game / "WoW.exe")
        # Stock clients already ship an identical backup. Skip the copy so a
        # locked WoW.exe (game running) does not fail a no-op revert.
        if backup is not None and backup.is_file() and wow.is_file():
            if _files_content_differ(backup, wow):
                try:
                    create_backup(game, "before_remove_vanilla_tweaks", [wow])
                except OSError as exc:
                    log.warning("Pre-remove WoW.exe snapshot skipped: %s", exc)
                _install_copy(backup, wow, game_path=game)
        return

    if kind == "mpq_file":
        dest = mod.get("destination")
        if dest and mod_id == "hd_patch_c":
            parent = (game / dest).parent
            for name in _HD_PATCH_C_EXACT_NAMES:
                if exact_name_present(parent, name):
                    remove_owned(Path(dest).parent / name)
            src = mod.get("source") or {}
            filename = src.get("filename")
            if filename and not is_stock_data_mpq(filename):
                src_rel = Path("Data") / filename
                if exact_name_present(parent, Path(filename).name):
                    remove_owned(src_rel)
            raise_failures()
            return
        if dest:
            remove_owned(dest)
        src = mod.get("source") or {}
        filename = src.get("filename")
        # Download basename is not ownership. Never delete a stock numeric MPQ
        # just because the host file happened to share that name.
        if filename and not is_stock_data_mpq(filename):
            src_rel = Path("Data") / filename
            if not dest or _norm_rel_path(src_rel) != _norm_rel_path(dest):
                remove_owned(src_rel)
        raise_failures()
        return

    if kind == "config_script_memory":
        return  # leave config alone on uncheck

    if kind == "glue_autologin":
        glue = game / "Data" / "Interface" / "GlueXML"
        for name in ("AutoLogin.lua", "AutoLogin.xml"):
            safe_remove(glue / name)
        return

    if kind == "manual_link":
        return  # detection-only / user-managed files

    if kind == "dxvk_hd":
        # Optional 2.7.1 upgrade sits on top of VF+Vulkan. If the base DXVK
        # bundle remains desired, restore the pre-upgrade snapshot (offline)
        # or reinstall the VF-bundled layer. A failed GitHub fetch must not
        # raise — Play would otherwise abort and re-plan the same remove.
        if settings.desired_mods.get(_DXVK_ID):
            status_only(progress, "Restoring VanillaFixes DXVK (dll + conf)...")
            _restore_dxvk_layer(
                game,
                backup_label=f"before_{_HD_DXVK_ID}",
                fallback_id=_DXVK_ID,
                progress=progress,
            )
            return
        for name in ("d3d9.dll", "dxvk.conf"):
            remove_owned(name)
        raise_failures()
        return

    if kind == "dxvk_cursor":
        conf = game / "dxvk.conf"
        if conf.exists():
            lines = [
                ln
                for ln in conf.read_text(encoding="utf-8", errors="ignore").splitlines()
                if _DXVK_CURSOR_CONF_MARKER not in ln
            ]
            conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        fallback = (
            _HD_DXVK_ID
            if settings.desired_mods.get(_HD_DXVK_ID)
            else (_DXVK_ID if settings.desired_mods.get(_DXVK_ID) else None)
        )
        status_only(progress, "Restoring DXVK d3d9.dll…")
        _restore_dxvk_layer(
            game,
            backup_label=f"before_{_DXVK_CURSOR_ID}",
            fallback_id=fallback,
            progress=progress,
        )
        return

    # Remove known DLLs / files from ownership
    for fspec in mod.get("files") or []:
        remove_owned(fspec["destination"])
    src = mod.get("source") or {}
    if src.get("filename"):
        remove_owned(src["filename"])

    dlls = (mod.get("dlls_txt") or {}).get("add") or []
    removable_dlls = [d for d in dlls if not shared.get(_norm_rel_path(d))]
    kept_dlls = [d for d in dlls if d not in removable_dlls]
    if removable_dlls:
        _update_dlls_txt_all(game, remove=removable_dlls)
    for dll in removable_dlls:
        remove_owned(dll)
    for dll in kept_dlls:
        log.info(
            "Kept %s in dlls.txt — shared with enabled mod(s): %s",
            dll,
            ", ".join(shared.get(_norm_rel_path(dll)) or []),
        )

    # Optional addon folders
    folder = (mod.get("addon_source") or {}).get("folder") or mod.get("addon_folder_match")
    if folder:
        addons = resolve_addons_dir(create=False)
        if addons is None:
            addons = game / "Interface" / "AddOns"
        try:
            remove_path_strict(addons / folder)
        except OSError as exc:
            failures.append(str(exc))

    if mod_id == "vanillafixes":
        for name in ("VanillaFixes.exe", "VfPatcher.dll"):
            remove_owned(name)
    if mod_id == "dxvk":
        # Full disable must clear the VF launcher too. When switching to regular
        # VanillaFixes, remove_owned keeps these via _paths_shared_with_enabled.
        for name in ("d3d9.dll", "dxvk.conf", "VanillaFixes.exe", "VfPatcher.dll"):
            remove_owned(name)
    raise_failures()


def apply_desired_state(progress: ProgressCb | None = None) -> list[str]:
    """One-shot desired-state apply. Per-mod lock/AV failures are skipped, not retried."""
    global _APPLY_IN_PROGRESS
    if _APPLY_IN_PROGRESS:
        log.warning("apply_desired_state already running — refusing nested retry")
        return ["skipped: apply already running"]
    _APPLY_IN_PROGRESS = True
    try:
        return _apply_desired_state_inner(progress)
    finally:
        _APPLY_IN_PROGRESS = False


def _catalog_dll_sets() -> tuple[set[str], set[str]]:
    """Return (desired_dll_names, disabled_catalog_dll_names), all lowercased basenames."""
    desired_dlls: set[str] = set()
    disabled_dlls: set[str] = set()
    desired = settings.desired_mods
    for mod in load_mod_catalog():
        mid = mod.get("id") or ""
        dlls = (mod.get("dlls_txt") or {}).get("add") or []
        names = {
            Path(str(d).replace("\\", "/")).name.lower()
            for d in dlls
            if str(d).strip()
        }
        if desired.get(mid, False):
            desired_dlls |= names
        else:
            disabled_dlls |= names
    return desired_dlls, disabled_dlls


def _sync_dlls_txt_for_desired_mods(game: Path) -> tuple[list[str], list[str]]:
    """Reconcile dlls.txt with desired mod DLL entries. Returns (added, removed)."""
    should_have, disabled_catalog = _catalog_dll_sets()
    to_add_by_lower: dict[str, str] = {}
    for mod in load_mod_catalog():
        mid = mod.get("id") or ""
        if not settings.desired_mods.get(mid, False):
            continue
        for d in (mod.get("dlls_txt") or {}).get("add") or []:
            name = Path(str(d).replace("\\", "/")).name
            if name:
                to_add_by_lower[name.lower()] = name

    current = {n.lower(): n for n in read_dlls_txt(game)}
    to_add = [to_add_by_lower[k] for k in should_have if k not in current]
    to_remove = [current[k] for k in current if k not in should_have and k in disabled_catalog]
    if to_add or to_remove:
        _update_dlls_txt_all(game, add=to_add, remove=to_remove)
    return to_add, to_remove


@dataclass
class PreLaunchResult:
    fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    permission_scan: PermissionScanResult | None = None

    @property
    def status_line(self) -> str | None:
        if not self.fixes:
            return None
        n = len(self.fixes)
        return f"Pre-launch: {n} fix{'es' if n != 1 else ''} applied"


def _ensure_enabled_data_writable(game: Path) -> tuple[list[str], list[str]]:
    """Clear read-only on Data/ files owned by desired-enabled mods."""
    fixes: list[str] = []
    warnings: list[str] = []
    desired = settings.desired_mods

    def check_path(p: Path) -> None:
        if not p.is_file():
            return
        try:
            was_ro = not (p.stat().st_mode & stat.S_IWRITE)
        except OSError as exc:
            warnings.append(f"Cannot access {p.name}: {exc}")
            return
        ensure_data_writable(p, game)
        try:
            still_ro = not (p.stat().st_mode & stat.S_IWRITE)
        except OSError:
            still_ro = True
        rel = p.relative_to(game)
        if was_ro and not still_ro:
            fixes.append(f"Cleared read-only on {rel}")
        elif still_ro:
            warnings.append(f"Still read-only: {rel}")

    for mod in load_mod_catalog():
        mid = mod.get("id") or ""
        if not desired.get(mid, False):
            continue
        for rel in _mod_owned_paths(mod):
            if rel.startswith("data/"):
                # _mod_owned_paths lowercases for comparison, so `rel` is a
                # comparison key, not a real filename. resolve_ci finds the
                # on-disk casing (Data/patch-A.MPQ) that game / rel would miss.
                resolved = resolve_ci(game, rel)
                if resolved is not None:
                    check_path(resolved)
        if mod.get("kind") == "glue_autologin":
            glue = game / "Data" / "Interface" / "GlueXML"
            for name in ("AutoLogin.lua", "AutoLogin.xml"):
                check_path(glue / name)
    return fixes, warnings


def prepare_for_launch(game: Path | None = None) -> PreLaunchResult:
    """Quick pre-boot checks: dlls.txt sync and Data/ read-only fixes."""
    result = PreLaunchResult()
    game = game or detect_game()
    if not game:
        result.warnings.append("Game path not set")
        return result

    added, removed = _sync_dlls_txt_for_desired_mods(game)
    if added:
        result.fixes.append(f"Added to dlls.txt: {', '.join(added)}")
        listed = {n.lower() for n in read_dlls_txt(game)}
        still_missing = [d for d in added if d.lower() not in listed]
        if still_missing:
            result.warnings.append(
                f"Could not update dlls.txt for: {', '.join(still_missing)}"
            )
    if removed:
        result.fixes.append(f"Removed from dlls.txt: {', '.join(removed)}")
        listed = {n.lower() for n in read_dlls_txt(game)}
        still_present = [d for d in removed if d.lower() in listed]
        if still_present:
            result.warnings.append(
                f"Could not remove from dlls.txt: {', '.join(still_present)}"
            )

    data_fixes, data_warnings = _ensure_enabled_data_writable(game)
    result.fixes.extend(data_fixes)
    result.warnings.extend(data_warnings)

    # Existing installs and monitor changes: rewrite dxvk.conf if needed.
    # A locked file must not block PLAY.
    _apply_frame_cap_if_enabled(game, raise_on_write_error=False)

    result.permission_scan = scan_game_permissions(game)

    if result.fixes:
        log.info("Pre-launch preparation: %s", "; ".join(result.fixes))
    if result.warnings:
        log.warning("Pre-launch warnings: %s", "; ".join(result.warnings))
    return result


def _apply_desired_state_inner(progress: ProgressCb | None) -> list[str]:
    changes = plan_changes()
    for ch in changes:
        if ch["action"] == "error":
            raise RuntimeError(ch["detail"])
    manuals = [ch["detail"] for ch in changes if ch["action"] == "manual"]
    actionable = [ch for ch in changes if ch["action"] in ("install", "remove")]
    done = _apply_planned_mod_changes(actionable, progress=progress)
    game = detect_game()
    if game:
        _sync_dlls_txt_for_desired_mods(game)
        actual = detect_actual_state(game)
        _persist_reconciled_desired_mods(dict(settings.desired_mods), actual=actual)
        _log_vf_on_disk_summary(game, "apply")
    log.info("Applied mod changes: %s manuals=%s", done, manuals)
    if manuals:
        done.append("Manual downloads needed:")
        done.extend(manuals)
    return done
