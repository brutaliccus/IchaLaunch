"""Client mod catalog + desired-state applicator."""

from __future__ import annotations

import json
import re
import shutil
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
    STARTUP_CHECK_COOLDOWN_SEC,
    github_get,
    github_headers,
    github_latest_commit,
    parse_github_url,
    rate_limit_exhausted,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.backup import create_backup
from ichalaunch.core.filesystem import (
    copy_tree,
    extract_zip,
    find_toc_roots,
    safe_remove,
    update_dlls_txt,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import download_file, google_drive_url
from ichalaunch.game.launcher import detect_game

ProgressCb = Callable[[str], None]
UA = {"User-Agent": "IchaLaunch/0.1"}


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
    return json.loads(_data_path().read_text(encoding="utf-8"))


def get_mod(mod_id: str) -> dict[str, Any] | None:
    for m in load_mod_catalog():
        if m["id"] == mod_id:
            return m
    return None


def _detect_mod(game_path: Path, mod: dict[str, Any]) -> bool:
    det = mod.get("detect") or {}
    if det.get("wdb_file"):
        return (game_path / "WDB").is_file()
    if det.get("any_files"):
        return any((game_path / f).exists() for f in det["any_files"])
    if det.get("all_files"):
        return all((game_path / f).exists() for f in det["all_files"])
    if det.get("data_mpq"):
        data = game_path / "Data"
        return any((data / name).exists() for name in det["data_mpq"])
    if det.get("config_contains"):
        cfg = game_path / "WTF" / "Config.wtf"
        if not cfg.exists():
            return False
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        return det["config_contains"] in text
    if det.get("config_file_contains"):
        path = game_path / det["config_file_contains"][0]
        needle = det["config_file_contains"][1]
        if not path.exists():
            return False
        return needle in path.read_text(encoding="utf-8", errors="ignore")
    # fallback by kind heuristics
    kind = mod.get("kind")
    mid = mod["id"]
    legacy = {
        "vanillafixes": (game_path / "VanillaFixes.exe").exists(),
        "dxvk": (game_path / "d3d9.dll").exists() and (game_path / "dxvk.conf").exists(),
        "superwow": (game_path / "SuperWoWhook.dll").exists(),
        "nampower": (game_path / "nampower.dll").exists(),
        "unitxp": (game_path / "UnitXP_SP3.dll").exists(),
        "perfboost": (game_path / "perf_boost.dll").exists(),
        "no1600x1200": (game_path / "no1600x1200.dll").exists(),
        "wdb_block": (game_path / "WDB").is_file(),
        "vanilla_tweaks": (game_path / "WoW-OriginalBackup.exe").exists(),
    }
    if mid in legacy:
        return legacy[mid]
    if kind == "mpq_file":
        dest = mod.get("destination")
        return bool(dest and (game_path / dest).exists())
    return False


def detect_actual_state(game_path: Path) -> dict[str, bool]:
    state: dict[str, bool] = {}
    for mod in load_mod_catalog():
        state[mod["id"]] = _detect_mod(game_path, mod)
    return state


def plan_changes(desired: dict[str, bool] | None = None) -> list[dict[str, str]]:
    game = detect_game()
    if not game:
        return [{"action": "error", "id": "", "detail": "Game path not set"}]
    desired = desired or settings.desired_mods
    actual = detect_actual_state(game)
    catalog = {m["id"]: m for m in load_mod_catalog()}
    changes: list[dict[str, str]] = []

    to_install = [mid for mid, want in desired.items() if want and not actual.get(mid, False)]
    ordered: list[str] = []
    seen: set[str] = set()

    def add_with_deps(mid: str) -> None:
        if mid in seen:
            return
        mod = catalog.get(mid) or {}
        for dep in mod.get("dependencies") or []:
            if not actual.get(dep, False):
                add_with_deps(dep)
        if mid not in seen:
            ordered.append(mid)
            seen.add(mid)

    for mid in to_install:
        add_with_deps(mid)

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
            mod = catalog.get(mid) or {}
            if mod.get("kind") == "manual_link":
                continue
            changes.append({"action": "remove", "id": mid, "detail": f"Remove {mod.get('name', mid)}"})
    return changes


def _install_addon_folder(src_root: Path, game: Path, preferred_name: str | None = None) -> None:
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


def _download_source(source: dict[str, Any], work: Path, progress: ProgressCb | None) -> Path:
    stype = source.get("type")
    if progress:
        progress(f"Downloading ({stype})...")
    if stype == "google_drive":
        file_id = source["id"]
        filename = source.get("filename") or f"{file_id}.bin"
        dest = work / filename
        download_file(google_drive_url(file_id), dest, timeout=int(source.get("timeout") or 300))
        return dest
    if stype in ("raw", "github_release", "github_zip", "raw_zip"):
        url = source["url"]
        filename = source.get("filename") or url.split("/")[-1].split("?")[0]
        dest = work / filename
        download_file(url, dest, timeout=int(source.get("timeout") or 120))
        return dest
    if stype == "github_release_latest":
        repo = source["repo"]
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        r = github_get(api)
        assets = r.json().get("assets") or []
        needle = (source.get("asset_contains") or ".zip").lower()
        asset = next((a for a in assets if needle in a["name"].lower()), None)
        if not asset:
            raise FileNotFoundError(f"No release asset matching {needle} for {repo}")
        dest = work / asset["name"]
        download_file(asset["browser_download_url"], dest)
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


def _record_mod_install(
    mod_id: str, mod: dict[str, Any], source_override: dict[str, Any] | None = None
) -> None:
    """Persist installed version fingerprint after a successful install."""
    source = source_override if source_override is not None else (mod.get("source") or {})
    meta: dict[str, Any] = {
        "name": mod.get("name"),
        "kind": mod.get("kind"),
        "installed_at": time.time(),
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
    settings.set_installed_mod(mod_id, meta)


def recently_checked_mod_updates(cooldown_sec: int = STARTUP_CHECK_COOLDOWN_SEC) -> bool:
    raw = settings.get("last_mod_update_check")
    if not raw:
        return False
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < cooldown_sec


def check_mod_updates(*, respect_cooldown: bool = False) -> ModUpdateCheckResult:
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

    for mod in load_mod_catalog():
        mid = mod["id"]
        if not actual.get(mid):
            continue
        kind = mod.get("kind")
        source = mod.get("source")
        if kind in ("manual_link", "wdb_block", "config_script_memory") or not source:
            skipped += 1
            continue
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
            continue
        if not remote:
            skipped += 1
            continue
        checked += 1
        local = settings.installed_mods.get(mid) or {}
        local_key = local.get("version_key") or local.get("tag") or local.get("sha") or local.get("etag")
        if not local_key:
            pinned = _tag_from_release_url((source or {}).get("url") or "")
            if pinned and remote.get("key") and pinned != remote.get("key"):
                settings.set_installed_mod(
                    mid,
                    {
                        "name": mod.get("name"),
                        "kind": kind,
                        "version_key": pinned,
                        "version_display": pinned,
                        "version_kind": "release",
                        "tag": pinned,
                        **{k: remote[k] for k in ("repo",) if remote.get(k)},
                    },
                )
                updates.append(
                    {
                        "id": mid,
                        "name": mod.get("name") or mid,
                        "local": pinned,
                        "remote": remote.get("display") or str(remote.get("key"))[:12],
                        "kind": remote.get("kind"),
                    }
                )
                continue
            # First check: baseline remote without flagging an update
            settings.set_installed_mod(
                mid,
                {
                    "name": mod.get("name"),
                    "kind": kind,
                    "version_key": remote.get("key"),
                    "version_display": remote.get("display"),
                    "version_kind": remote.get("kind"),
                    **{
                        k: remote[k]
                        for k in ("etag", "last_modified", "tag", "sha", "repo", "branch", "url")
                        if remote.get(k)
                    },
                },
            )
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
        if progress:
            progress(f"Updating {mid}…")
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
    create_backup(game, f"before_{mod_id}", [game / "WoW.exe", game / "dlls.txt", game / "VanillaFixes.exe"])

    with tempfile.TemporaryDirectory(prefix="ichalaunch_") as tmp:
        work = Path(tmp)
        source = dict(mod.get("source") or {}) if mod.get("source") else None
        if prefer_latest and source and source.get("type") == "github_release":
            repo = _repo_from_github_url(source.get("url") or "")
            if repo:
                fname = source.get("filename") or (source.get("url") or "").split("/")[-1]
                source = {
                    "type": "github_release_latest",
                    "repo": repo,
                    "asset_contains": fname if fname else ".zip",
                }

        if kind == "wdb_block":
            wdb = game / "WDB"
            if wdb.is_dir():
                shutil.rmtree(wdb)
            elif wdb.exists() and not wdb.is_file():
                safe_remove(wdb)
            if not wdb.exists():
                wdb.write_text("", encoding="utf-8")
            if progress:
                progress("WDB block applied")
            _record_mod_install(mod_id, mod, source)
            return

        if kind == "exe_patch":
            assert source
            z = _download_source(source, work, progress)
            extracted = extract_zip(z, work / "extract")
            vt = next(extracted.rglob("vanilla-tweaks.exe"), None) or next(
                extracted.rglob("vanilla_tweaks.exe"), None
            )
            if not vt:
                raise FileNotFoundError("vanilla-tweaks.exe not found in archive")
            wow = game / "WoW.exe"
            if not (game / "WoW-OriginalBackup.exe").exists():
                shutil.copy2(wow, game / "WoW-OriginalBackup.exe")
            # Run patcher; creates WoW_tweaked.exe next to WoW.exe
            if progress:
                progress("Patching WoW.exe with Vanilla Tweaks...")
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
            extracted = extract_zip(z, work / "extract")
            # copy all files into game root
            for item in extracted.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(extracted)
                    # if nested single folder, flatten one level if needed
                    dest = game / rel
                    # Prefer files that live at shallowest level matching known names
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
            # Flatten: if VanillaFixes.exe is nested, find it
            vf = next(game.rglob("VanillaFixes.exe"), None)
            if vf and vf.parent != game:
                for f in vf.parent.iterdir():
                    if f.is_file():
                        shutil.copy2(f, game / f.name)
            _record_mod_install(mod_id, mod, source)
            return

        if kind in ("dll_file", "dll_bundle"):
            assert source
            artifact = _download_source(source, work, progress)
            search_root = work
            if artifact.suffix.lower() == ".zip" or source.get("type") in ("raw_zip", "github_zip"):
                search_root = extract_zip(artifact, work / "extract")
            else:
                # single dll
                dest_name = source.get("filename") or artifact.name
                shutil.copy2(artifact, game / dest_name)

            for fspec in mod.get("files") or []:
                match = fspec["match"]
                found = next(search_root.rglob(match), None)
                if found:
                    shutil.copy2(found, game / fspec["destination"])

            if mod.get("addon_folder_match"):
                folder = next(search_root.rglob(mod["addon_folder_match"]), None)
                if folder and folder.is_dir():
                    _install_addon_folder(folder, game, preferred_name=mod["addon_folder_match"])

            addon_src = mod.get("addon_source")
            if addon_src:
                a = _download_source(addon_src, work / "addon", progress)
                if a.suffix.lower() == ".zip" or addon_src.get("type") in ("raw_zip", "github_zip"):
                    aroot = extract_zip(a, work / "addon_extract")
                else:
                    aroot = a.parent
                _install_addon_folder(aroot, game, preferred_name=addon_src.get("folder"))

            dlls = (mod.get("dlls_txt") or {}).get("add") or []
            if dlls:
                update_dlls_txt(game, add=dlls)
            _record_mod_install(mod_id, mod, source)
            return

        if kind == "mpq_file":
            assert source
            artifact = _download_source(source, work, progress)
            # Zip sources (e.g. Darker Nights archive) — extract and pick the MPQ
            if artifact.suffix.lower() == ".zip" or source.get("type") in ("raw_zip", "github_zip"):
                extracted = extract_zip(artifact, work / "extract")
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
            dest_rel = mod.get("destination") or f"Data/{source.get('filename') or artifact.name}"
            dest = game / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if progress:
                progress(f"Installing {dest.name} (large file)...")
            shutil.copy2(artifact, dest)
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
            extracted = extract_zip(z, work / "extract")
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
                    shutil.copy2(f, dest / f.name)
            # apply glue signature skip patch (vanilla 1.12.1)
            wow = game / "WoW.exe"
            if wow.exists():
                if not (game / "WoW-OriginalBackup.exe").exists():
                    shutil.copy2(wow, game / "WoW-OriginalBackup.exe")
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
            shutil.copy2(artifact, game / "d3d9.dll")
            conf = game / "dxvk.conf"
            text = conf.read_text(encoding="utf-8", errors="ignore") if conf.exists() else ""
            if "enlargeHardwareCursor" not in text:
                text = (text.rstrip() + "\n\nd3d9.enlargeHardwareCursor = 2\n")
                conf.write_text(text, encoding="utf-8")
            _record_mod_install(mod_id, mod, source)
            return

        if kind == "manual_link":
            raise RuntimeError(
                f"{mod.get('name')}: automatic download is not hosted as a direct file. "
                f"See {mod.get('info_url') or 'the Turtle WoW mods guide'}. "
                f"{mod.get('note') or ''}"
            )

        raise ValueError(f"Unsupported mod kind: {kind}")


def remove_mod(mod_id: str, progress: ProgressCb | None = None) -> None:
    game = detect_game()
    if not game:
        raise FileNotFoundError("Game not found")
    mod = get_mod(mod_id)
    if not mod:
        raise KeyError(mod_id)

    if progress:
        progress(f"Removing {mod_id}...")

    settings.remove_installed_mod(mod_id)

    kind = mod.get("kind")

    if kind == "wdb_block":
        wdb = game / "WDB"
        if wdb.is_file():
            wdb.unlink()
        return

    if kind == "exe_patch":
        backup = game / "WoW-OriginalBackup.exe"
        if backup.exists():
            shutil.copy2(backup, game / "WoW.exe")
        return

    if kind == "mpq_file":
        dest = mod.get("destination")
        if dest:
            safe_remove(game / dest)
        src = mod.get("source") or {}
        if src.get("filename"):
            safe_remove(game / "Data" / src["filename"])
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
        safe_remove(game / fspec["destination"])
    src = mod.get("source") or {}
    if src.get("filename"):
        safe_remove(game / src["filename"])

    dlls = (mod.get("dlls_txt") or {}).get("add") or []
    if dlls:
        update_dlls_txt(game, remove=dlls)

    # Optional addon folders
    folder = (mod.get("addon_source") or {}).get("folder") or mod.get("addon_folder_match")
    if folder:
        safe_remove(game / "Interface" / "AddOns" / folder)

    if mod_id == "vanillafixes":
        for name in ("VanillaFixes.exe", "VfPatcher.dll"):
            safe_remove(game / name)
    if mod_id == "dxvk":
        for name in ("d3d9.dll", "dxvk.conf"):
            safe_remove(game / name)


def apply_desired_state(progress: ProgressCb | None = None) -> list[str]:
    changes = plan_changes()
    done: list[str] = []
    manuals: list[str] = []
    for ch in changes:
        if ch["action"] == "error":
            raise RuntimeError(ch["detail"])
        if ch["action"] == "manual":
            manuals.append(ch["detail"])
            continue
        if ch["action"] == "install":
            install_mod(ch["id"], progress=progress)
            done.append(f"+ {ch['id']}")
        elif ch["action"] == "remove":
            remove_mod(ch["id"], progress=progress)
            done.append(f"- {ch['id']}")
    log.info("Applied mod changes: %s manuals=%s", done, manuals)
    if manuals:
        done.append("Manual downloads needed:")
        done.extend(manuals)
    return done
