"""Client mod catalog + desired-state applicator."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from ichalaunch.addons.github import (
    GitHubRateLimitError,
    RATE_LIMIT_STATUS,
    fetch_repo_readme,
    github_get,
    github_latest_commit,
    parse_github_url,
    rate_limit_exhausted,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.backup import create_backup, restore_backup
from ichalaunch.core.filesystem import (
    copy_file_tolerant,
    copy_tree,
    ensure_data_writable,
    extract_zip,
    find_toc_roots,
    invalidate_dir_listing,
    is_lock_or_av_error,
    listed_basenames,
    mirror_dlls_txt_updates,
    name_present,
    PermissionScanResult,
    read_dlls_txt,
    scan_game_permissions,
    remove_path_strict,
    safe_remove,
    sanitize_filename,
    update_dlls_txt,
    validate_pe_binary,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import download_bytes, download_bytes_cb, download_file, google_drive_url, status_only
from ichalaunch.game.launcher import (
    detect_game,
    detect_vf_disk_mode,
    ensure_addons_dir,
    resolve_addons_dir,
    vf_mode_display,
)

ProgressCb = Callable[[str], None]
UA = {"User-Agent": "IchaLaunch/0.1"}
# Re-entrancy guard: apply is one-shot. A timer must never stack retries.
_APPLY_IN_PROGRESS = False


def _install_copy(src: Path, dest: Path, game_path: Path | None = None) -> None:
    """Copy into the game tree. DLLs/EXEs are never LoadLibrary'd; lock/AV → OSError skip."""
    if dest.suffix.lower() in {".dll", ".exe"}:
        if not copy_file_tolerant(src, dest):
            raise OSError(
                13,
                f"Skipped locked or antivirus-blocked file {dest.name}",
                str(dest),
            )
        if game_path is not None:
            ensure_data_writable(dest, game_path)
        invalidate_dir_listing(dest.parent)
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if game_path is not None and dest.exists():
            ensure_data_writable(dest, game_path)
        shutil.copy2(src, dest)
        if game_path is not None:
            ensure_data_writable(dest, game_path)
    except OSError as exc:
        if is_lock_or_av_error(exc):
            raise OSError(
                getattr(exc, "errno", None) or 13,
                f"Skipped locked file {dest.name}: {exc}",
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


def load_mod_catalog() -> list[dict[str, Any]]:
    catalog = json.loads(_data_path().read_text(encoding="utf-8"))
    seen = {m["id"] for m in catalog if m.get("id")}
    for mod in settings.user_mods:
        mid = mod.get("id")
        if not mid or mid in seen:
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
_VANILLA_HELPERS_ID = "vanilla_helpers"
_VANILLAFIXES_ID = "vanillafixes"
_DXVK_ID = "dxvk"

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


def _pick_exclusive_detect_winner(
    a: str,
    b: str,
    desired: dict[str, bool],
) -> str:
    """Pick which conflicting mod id should read as installed when both match disk."""
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
            winner = _pick_exclusive_detect_winner(mid, conf, desired)
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
        if reconciled.get(_VANILLAFIXES_ID) or reconciled.get(_DXVK_ID):
            settings.set(
                "vanillafixes_enabled",
                bool(reconciled.get(_VANILLAFIXES_ID) or reconciled.get(_DXVK_ID)),
            )
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
    settings.set(
        "vanillafixes_enabled",
        bool(desired.get(_VANILLAFIXES_ID) or desired.get(_DXVK_ID)),
    )
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
        if mod_id not in (mod.get("dependencies") or []):
            continue
        ordered.extend(_collect_mod_dependents(oid, catalog, seen))
        ordered.append(oid)
    return ordered


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
        for dep in _collect_mod_dependencies(mod_id, catalog):
            changes[dep] = True
        changes[mod_id] = True
        for mid in list(changes):
            if not changes.get(mid):
                continue
            for conf in catalog.get(mid, {}).get("conflicts") or []:
                if conf in catalog and effective(conf):
                    disable_branch(conf, set())
        for mid in list(changes):
            if not changes.get(mid):
                continue
            for dep in _collect_mod_dependencies(mid, catalog):
                changes[dep] = True
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


def _detect_mod(
    game_path: Path,
    mod: dict[str, Any],
    *,
    root_names: frozenset[str] | None = None,
) -> bool:
    det = mod.get("detect") or {}
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
        return any(name_present(data, name, data_names) for name in det["data_mpq"])
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
        "vanilla_tweaks": name_present(game_path, "WoW-OriginalBackup.exe", root_names),
    }
    if mid in legacy:
        return legacy[mid]
    if kind == "mpq_file":
        dest = mod.get("destination")
        if not dest:
            return False
        rel = Path(str(dest).replace("\\", "/"))
        return name_present(game_path / rel.parent, rel.name)
    dlls = (mod.get("dlls_txt") or {}).get("add") or []
    if dlls:
        return _dlls_txt_has(game_path, dlls, root_names)
    return False


def detect_actual_state(game_path: Path) -> dict[str, bool]:
    """Scan installed client mods. Never LoadLibrary game DLLs; never raise per-mod."""
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
    )
    _backfill_detected_installed_mods(reconciled)
    return reconciled


def plan_missing_installs(desired: dict[str, bool] | None = None) -> list[dict[str, str]]:
    """Return install actions for desired mods missing on disk (no removals)."""
    return [ch for ch in plan_changes(desired) if ch.get("action") == "install"]


def plan_manual_missing(desired: dict[str, bool] | None = None) -> list[str]:
    """Return manual-install notices for desired mods that cannot auto-install."""
    return [ch["detail"] for ch in plan_changes(desired) if ch.get("action") == "manual"]


def _apply_planned_mod_changes(
    changes: list[dict[str, str]], progress: ProgressCb | None = None
) -> list[str]:
    """Apply install/remove actions from plan_changes. Per-mod failures are logged, not raised."""
    done: list[str] = []
    for ch in changes:
        if ch.get("action") not in ("install", "remove"):
            continue
        mid = ch.get("id") or ""
        action = ch["action"]
        try:
            vf_label = _vf_sync_action_log_label(mid, action)
            if vf_label:
                log.info("Mod sync: %s", vf_label)
            if action == "install":
                install_mod(mid, progress=progress)
                done.append(f"+ {mid}")
            else:
                remove_mod(mid, progress=progress)
                done.append(f"- {mid}")
            if not vf_label:
                log.info("Pre-launch mod %s: %s", action, mid)
        except OSError as exc:
            log.warning("Mod %s %s skipped (disk/AV): %s", action, mid, exc)
            done.append(f"! {mid} skipped: {exc}")
        except (RuntimeError, FileNotFoundError, KeyError, shutil.Error) as exc:
            log.warning("Mod %s %s failed: %s", action, mid, exc)
            done.append(f"! {mid} failed: {exc}")
        except requests.RequestException as exc:
            log.warning("Mod %s %s failed (download): %s", action, mid, exc)
            done.append(f"! {mid} failed: {exc}")
    return done


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
        and (
            not actual.get(mid, False)
            or _mpq_exclusive_variant_needs_reinstall(mid, desired, catalog)
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
            if not actual.get(dep, False):
                add_with_deps(dep)
        ordered.append(mid)

    for mid in to_install:
        add_with_deps(mid)

    if _any_hd_patch_desired(desired) and not actual.get(_VANILLA_HELPERS_ID, False):
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
        have = actual.get(mid, False)
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
            changes.append({"action": "remove", "id": mid, "detail": f"Remove {mod.get('name', mid)}"})
    return changes


def _install_addon_folder(src_root: Path, game: Path, preferred_name: str | None = None) -> None:
    # Prefer configured AddOns path; fall back to game/Interface/AddOns.
    addons = resolve_addons_dir(create=True)
    if addons is None:
        addons = game / "Interface" / "AddOns"
        addons.mkdir(parents=True, exist_ok=True)
    roots = find_toc_roots(src_root)
    if preferred_name:
        match = next((r for r in roots if r.name == preferred_name or preferred_name in r.name), None)
        if match:
            roots = [match]
    if not roots:
        # whole folder might already be the addon
        if any(src_root.glob("*.toc")):
            roots = [src_root]
    for root in roots:
        name = preferred_name or root.name
        # strip -master / -main
        for suffix in ("-master", "-main"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        dest = addons / name
        copy_tree(root, dest)


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


def _download_source(source: dict[str, Any], work: Path, progress: ProgressCb | None) -> Path | bytes:
    """Download a catalog source.

    Zip archives are kept in memory so Windows Defender cannot quarantine the
    tempfile between download and ``ZipFile`` open (VanillaFixes.exe / patcher
    DLLs commonly trip WinError 225 / Errno 22).
    """
    stype = source.get("type")
    status_only(progress, f"Downloading ({stype})...")
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
        file_timeout: int | tuple[int, int] = timeout
        if not source.get("timeout") and filename.lower().endswith(".mpq"):
            # HD patches are multi-GB; allow slower links between read chunks.
            file_timeout = (30, 600)
        if _looks_like_zip(filename, stype):
            return download_bytes(url, progress=bytes_cb, timeout=timeout)
        dest = work / filename
        download_file(url, dest, progress=bytes_cb, timeout=file_timeout)
        return dest
    if stype == "github_release_latest":
        repo = source["repo"]
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        r = github_get(api)
        assets = r.json().get("assets") or []
        needle = source.get("asset_contains") or ".zip"
        asset = _pick_release_asset(
            assets,
            asset_contains=needle,
            asset_not_contains=source.get("asset_not_contains"),
            prefer_filename=source.get("prefer_filename"),
        )
        if not asset:
            detail = needle
            if source.get("asset_not_contains"):
                detail = f"{needle} (excluding {source['asset_not_contains']})"
            raise FileNotFoundError(f"No release asset matching {detail} for {repo}")
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


def _branch_from_archive_url(url: str) -> str | None:
    m = re.search(r"/archive/refs/heads/([^/.]+)", url)
    return m.group(1) if m else None


def _head_identity(url: str) -> dict[str, str]:
    """ETag / Last-Modified fingerprint for a static download URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    r = requests.head(url, timeout=30, headers=headers, allow_redirects=True)
    if r.status_code >= 400:
        # Some hosts reject HEAD — fall back to a ranged GET
        r = requests.get(
            url, timeout=30, headers={**headers, "Range": "bytes=0-0"}, allow_redirects=True
        )
    etag = (r.headers.get("ETag") or "").strip()
    last_mod = (r.headers.get("Last-Modified") or "").strip()
    key = etag or last_mod or url
    return {
        "key": key,
        "etag": etag,
        "last_modified": last_mod,
        "display": (etag.strip('"')[:16] if etag else last_mod[:24]) or "remote",
    }


def _remote_identity(source: dict[str, Any]) -> dict[str, Any] | None:
    """Return comparable remote identity for a mod source, or None if unsupported."""
    if not source:
        return None
    stype = source.get("type")
    if stype == "github_release_latest":
        repo = source.get("repo")
        if not repo:
            return None
        r = github_get(f"https://api.github.com/repos/{repo}/releases/latest")
        data = r.json()
        tag = data.get("tag_name") or data.get("name") or ""
        return {
            "kind": "release",
            "key": tag,
            "display": tag,
            "repo": repo,
            "tag": tag,
        }
    if stype == "github_release":
        url = source.get("url") or ""
        repo = _repo_from_github_url(url)
        pinned = _tag_from_release_url(url)
        if repo:
            try:
                r = github_get(f"https://api.github.com/repos/{repo}/releases/latest")
                data = r.json()
                tag = data.get("tag_name") or data.get("name") or ""
                return {
                    "kind": "release",
                    "key": tag,
                    "display": tag,
                    "repo": repo,
                    "tag": tag,
                    "pinned": pinned,
                }
            except Exception:
                pass
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
        remote = github_latest_commit(owner, name, branch)
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
                try:
                    remote = github_latest_commit(owner, name, branch)
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
        ident = _head_identity(url)
        return {"kind": "http", "url": url, **ident}
    if stype == "google_drive":
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
    return None


def _clear_exclusive_sibling_install_records(mod_id: str, mod: dict[str, Any]) -> None:
    """Drop install metadata for conflict siblings sharing the same install slot."""
    vf_dxvk_pair = frozenset({_VANILLAFIXES_ID, _DXVK_ID})
    for conf in mod.get("conflicts") or []:
        if mod.get("kind") == "mpq_file" or frozenset({mod_id, conf}) == vf_dxvk_pair:
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
        mod = catalog[mid]
        if mod.get("kind") in ("manual_link", "wdb_block", "config_script_memory"):
            continue
        _clear_exclusive_sibling_install_records(mid, mod)
        settings.set_installed_mod(mid, _backfill_installed_mod_meta(mid, mod))


def _record_mod_install(
    mod_id: str, mod: dict[str, Any], source_override: dict[str, Any] | None = None
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
    }
    # Prefer the catalog-pinned tag when present (accurate for what was downloaded).
    pinned = _tag_from_release_url((source or {}).get("url") or "")
    try:
        remote = _remote_identity(source) if source else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fingerprint mod %s: %s", mod_id, exc)
        remote = None
    if pinned and source.get("type") == "github_release":
        meta["version_key"] = pinned
        meta["version_display"] = pinned
        meta["version_kind"] = "release"
        meta["tag"] = pinned
        if remote and remote.get("repo"):
            meta["repo"] = remote["repo"]
    elif remote:
        meta["version_key"] = remote.get("key")
        meta["version_display"] = remote.get("display")
        meta["version_kind"] = remote.get("kind")
        for k in ("etag", "last_modified", "tag", "sha", "repo", "branch", "url"):
            if remote.get(k):
                meta[k] = remote[k]
    elif source.get("url"):
        meta["version_key"] = source["url"]
        meta["version_display"] = "catalog"
        meta["url"] = source["url"]
    if mod.get("kind") == "mpq_file":
        meta["variant_id"] = mod_id
        src_url = (source or {}).get("url")
        if src_url:
            meta["source_url"] = src_url
    settings.set_installed_mod(mod_id, meta)


def recently_checked_mod_updates(cooldown_sec: int | None = None) -> bool:
    """True if an automatic mod scan should skip (uses Settings auto-scan cooldown)."""
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
    """Compare installed client mods against upstream where sources support it."""
    if respect_cooldown and recently_checked_mod_updates():
        return ModUpdateCheckResult(skipped_recent=True)

    game = detect_game()
    if not game:
        return ModUpdateCheckResult(status_message="Set a game path before checking updates")

    actual = detect_actual_state(game)
    updates: list[dict[str, Any]] = []
    checked = 0
    skipped = 0
    rate_limited = False

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

    total = max(1, len(to_check))
    on_count = getattr(progress, "on_count", None) if progress is not None else None
    if callable(on_count):
        on_count(0, total, "Checking client mod updates…")

    if not to_check:
        settings.set("last_mod_update_check", time.time())
        if callable(on_count):
            on_count(1, 1, "Checking client mod updates…")
        return ModUpdateCheckResult(updates=updates, checked=checked, skipped=skipped)

    for i, mod in enumerate(to_check):
        mid = mod["id"]
        kind = mod.get("kind")
        source = mod.get("source")
        label = str(mod.get("name") or mid)

        if rate_limit_exhausted():
            rate_limited = True
            break
        try:
            remote = _remote_identity(source)
        except GitHubRateLimitError:
            rate_limited = True
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Mod update check failed for %s: %s", mid, exc)
            skipped += 1
            if callable(on_count):
                on_count(i + 1, total, f"Checking {label}…")
            continue
        if not remote:
            skipped += 1
            if callable(on_count):
                on_count(i + 1, total, f"Checking {label}…")
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
                if callable(on_count):
                    on_count(i + 1, total, f"Checking {label}…")
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
            settings.set_installed_mod(mid, meta)
            if callable(on_count):
                on_count(i + 1, total, f"Checking {label}…")
            continue
        if local_key != remote.get("key"):
            updates.append(
                {
                    "id": mid,
                    "name": mod.get("name") or mid,
                    "local": local.get("version_display") or str(local_key)[:12],
                    "remote": remote.get("display") or str(remote.get("key"))[:12],
                    "kind": remote.get("kind"),
                }
            )
        if callable(on_count):
            on_count(i + 1, total, f"Checking {label}…")
        if rate_limit_exhausted():
            rate_limited = True
            break

    settings.set("last_mod_update_check", time.time())

    if rate_limited:
        return ModUpdateCheckResult(
            updates=updates,
            rate_limited=True,
            checked=checked,
            skipped=skipped,
            status_message=RATE_LIMIT_STATUS,
        )
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


def install_mod(mod_id: str, progress: ProgressCb | None = None, *, prefer_latest: bool = False) -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game not found")
    mod = get_mod(mod_id)
    if not mod:
        raise KeyError(mod_id)

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
            if prefer_latest and source and source.get("type") == "github_release":
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
                _record_mod_install(mod_id, mod, source)
                return

            if kind == "exe_patch":
                assert source
                z = _download_source(source, work, progress)
                extracted = extract_zip(z, work / "extract", progress=progress)
                vt = next(extracted.rglob("vanilla-tweaks.exe"), None) or next(
                    extracted.rglob("vanilla_tweaks.exe"), None
                )
                if not vt:
                    raise FileNotFoundError("vanilla-tweaks.exe not found in archive")
                wow = game / "WoW.exe"
                if not (game / "WoW-OriginalBackup.exe").exists():
                    _install_copy(wow, game / "WoW-OriginalBackup.exe", game_path=game)
                # Run patcher; creates WoW_tweaked.exe next to WoW.exe
                status_only(progress, "Patching WoW.exe with Vanilla Tweaks...")
                subprocess.run([str(vt), str(wow)], cwd=str(game), check=True)
                tweaked = game / "WoW_tweaked.exe"
                if tweaked.exists():
                    wow.unlink(missing_ok=True)
                    tweaked.rename(wow)
                _record_mod_install(mod_id, mod, source)
                return

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
                _record_mod_install(mod_id, mod, source)
                return

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
                _verify_mod_install(game, mod)
                _record_mod_install(mod_id, mod, source)
                return

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
                dest = game / dest_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                status_only(progress, f"Installing {dest.name} (large file)...")
                _install_copy(artifact, dest, game_path=game)
                _record_mod_install(mod_id, mod, source)
                return

            if kind == "config_script_memory":
                cfg = game / "WTF" / "Config.wtf"
                cfg.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                if cfg.exists():
                    lines = cfg.read_text(encoding="utf-8", errors="ignore").splitlines()
                    lines = [ln for ln in lines if not ln.strip().upper().startswith("SET SCRIPTMEMORY")]
                lines.insert(0, 'SET scriptMemory "0"')
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                _record_mod_install(mod_id, mod, source)
                return

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
                # apply glue signature skip patch (vanilla 1.12.1)
                wow = game / "WoW.exe"
                if wow.exists():
                    if not (game / "WoW-OriginalBackup.exe").exists():
                        _install_copy(wow, game / "WoW-OriginalBackup.exe", game_path=game)
                    data = bytearray(wow.read_bytes())
                    patches = {
                        0x2F113A: 0xEB,
                        0x2F113B: 0x19,
                        0x2F1158: 0x03,
                        0x2F11A7: 0x03,
                        0x2F11F0: 0xEB,
                        0x2F11F1: 0xB2,
                    }
                    if len(data) > max(patches):
                        for off, val in patches.items():
                            data[off] = val
                        wow.write_bytes(data)
                _record_mod_install(mod_id, mod, source)
                return

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
                _verify_mod_install(game, mod)
                _record_mod_install(mod_id, mod, source)
                return

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
        if text:
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
        # DXVK bundle zip ships VanillaFixes.exe — keep it when swapping off regular VF.
        add("VanillaFixes.exe")
        add("VfPatcher.dll")
        add("d3d9.dll")
        add("dxvk.conf")
    return owned


_DLL_PE_MIN_BYTES = 1024
_SUPERWOW_DLL_MIN_BYTES = 200_000


def _install_backup_paths(game: Path, mod: dict[str, Any]) -> list[Path]:
    """Files to snapshot before applying a mod (owned paths + core launch files)."""
    paths: list[Path] = [
        game / "WoW.exe",
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
        paths.append(game / rel)
    return paths


def _pe_min_bytes_for_rel(rel: str) -> int:
    name = Path(str(rel).replace("\\", "/")).name.lower()
    if name == "superwowhook.dll":
        return _SUPERWOW_DLL_MIN_BYTES
    return _DLL_PE_MIN_BYTES


def _verify_mod_install(game: Path, mod: dict[str, Any]) -> None:
    """Ensure downloaded DLL/EXE artifacts are present and look like valid PE files."""
    kind = mod.get("kind")
    if kind not in ("dll_file", "dll_bundle", "dxvk_cursor"):
        return
    failures: list[str] = []
    for rel in sorted(_mod_owned_paths(mod)):
        low = rel.lower()
        if not (low.endswith(".dll") or low.endswith(".exe")):
            continue
        dest = game / rel
        if not dest.is_file():
            failures.append(f"{rel} was not installed")
            continue
        try:
            validate_pe_binary(dest, min_size=_pe_min_bytes_for_rel(rel))
        except OSError as exc:
            failures.append(str(exc.args[1] if len(exc.args) > 1 else exc))
    if failures:
        raise OSError(22, "; ".join(failures))


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
        try:
            remove_path_strict(game / rel)
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
        backup = game / "WoW-OriginalBackup.exe"
        if backup.exists():
            _install_copy(backup, game / "WoW.exe", game_path=game)
        return

    if kind == "mpq_file":
        dest = mod.get("destination")
        if dest:
            remove_owned(dest)
        src = mod.get("source") or {}
        if src.get("filename"):
            remove_owned(Path("Data") / src["filename"])
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

    if kind == "dxvk_cursor":
        conf = game / "dxvk.conf"
        if conf.exists():
            lines = [ln for ln in conf.read_text(encoding="utf-8", errors="ignore").splitlines() if "enlargeHardwareCursor" not in ln]
            conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
        for name in ("d3d9.dll", "dxvk.conf"):
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
                check_path(game / rel)
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
