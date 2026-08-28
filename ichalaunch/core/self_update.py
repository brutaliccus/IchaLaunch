"""IchaLaunch self-update from GitHub Releases."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

import requests

from ichalaunch import __version__
from ichalaunch.addons.github import (
    GitHubRateLimitError,
    GITHUB_TOKEN_REJECTED_MSG,
    RATE_LIMIT_STATUS,
    github_get,
    github_open,
    rate_limit_exhausted,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import download_bytes_cb, resolve_download_total, status_only

ProgressCb = Callable[[str], None]

LAUNCHER_REPO = "brutaliccus/IchaLaunch"
PREFERRED_ASSET = "IchaLaunch.exe"
# Reuse silent startup/periodic check results for the 15-minute refresh window.
LAUNCHER_RELEASE_CACHE_SEC = 15 * 60


@dataclass
class LauncherReleaseInfo:
    tag: str
    version: str
    name: str
    asset_name: str
    download_url: str
    update_available: bool
    html_url: str = ""
    asset_id: int | None = None
    asset_size: int = 0


def normalize_version(raw: str) -> str:
    """Strip leading 'v' and whitespace for semver-ish comparison."""
    return (raw or "").strip().lstrip("vV").strip()


def parse_version_tuple(raw: str) -> tuple[int, ...]:
    cleaned = normalize_version(raw)
    if not cleaned:
        return (0,)
    parts: list[int] = []
    for piece in cleaned.split("."):
        m = re.match(r"(\d+)", piece)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version_tuple(remote) > parse_version_tuple(local)


def _ps_single_quote(value: str) -> str:
    """Single-quoted PowerShell string literal (safe for paths with spaces)."""
    return "'" + (value or "").replace("'", "''") + "'"


def resolve_install_exe() -> Path:
    """Path of the real on-disk EXE to replace (never PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False):
        # One-file and one-dir: sys.executable is the real launcher EXE.
        # Extracted payload lives under sys._MEIPASS — never write there.
        exe = Path(sys.executable).resolve()
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_path = Path(meipass).resolve()
            try:
                exe.relative_to(meipass_path)
            except ValueError:
                pass
            else:
                raise RuntimeError(
                    "Refusing to self-update inside the PyInstaller extract folder "
                    f"({meipass_path}). sys.executable must be the installed EXE."
                )
        if not exe.is_file() or exe.suffix.lower() != ".exe":
            raise RuntimeError(f"Frozen executable path is not an .exe: {exe}")
        return exe
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "dist" / PREFERRED_ASSET,
        Path.cwd() / "dist" / PREFERRED_ASSET,
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise RuntimeError(
        "Self-update requires the packaged IchaLaunch.exe "
        "(run the frozen build, or place it under dist/)."
    )


def _validate_pe_exe(path: Path) -> None:
    """Reject non-EXE / truncated downloads before we try to replace ourselves."""
    size = path.stat().st_size
    if size < 1024 * 64:
        raise RuntimeError(f"Downloaded launcher looks too small ({size} bytes)")
    with path.open("rb") as f:
        magic = f.read(2)
    if magic != b"MZ":
        raise RuntimeError("Downloaded launcher is not a Windows EXE (missing MZ header)")


def _pick_exe_asset(assets: list[dict]) -> dict | None:
    preferred = next(
        (a for a in assets if str(a.get("name") or "") == PREFERRED_ASSET),
        None,
    )
    if preferred:
        return preferred
    return next(
        (a for a in assets if str(a.get("name") or "").lower().endswith(".exe")),
        None,
    )


def launcher_release_info_to_dict(info: LauncherReleaseInfo) -> dict[str, Any]:
    return {
        "tag": info.tag,
        "version": info.version,
        "name": info.name,
        "asset_name": info.asset_name,
        "download_url": info.download_url,
        "update_available": info.update_available,
        "html_url": info.html_url,
        "asset_id": info.asset_id,
        "asset_size": info.asset_size,
    }


def launcher_release_info_from_dict(data: dict[str, Any] | None) -> LauncherReleaseInfo | None:
    if not isinstance(data, dict) or not data.get("version"):
        return None
    asset_id = data.get("asset_id")
    try:
        asset_id = int(asset_id) if asset_id is not None else None
    except (TypeError, ValueError):
        asset_id = None
    try:
        asset_size = int(data.get("asset_size") or 0)
    except (TypeError, ValueError):
        asset_size = 0
    return LauncherReleaseInfo(
        tag=str(data.get("tag") or ""),
        version=str(data.get("version") or ""),
        name=str(data.get("name") or ""),
        asset_name=str(data.get("asset_name") or PREFERRED_ASSET),
        download_url=str(data.get("download_url") or ""),
        update_available=bool(data.get("update_available")),
        html_url=str(data.get("html_url") or ""),
        asset_id=asset_id,
        asset_size=asset_size,
    )


def read_cached_launcher_release(
    *,
    max_age_sec: int = LAUNCHER_RELEASE_CACHE_SEC,
    local_version: str | None = None,
) -> LauncherReleaseInfo | None:
    """Return persisted launcher release check if still fresh enough."""
    from ichalaunch.config.settings import settings

    raw_ts = settings.get("last_launcher_release_check")
    try:
        checked_at = float(raw_ts)
    except (TypeError, ValueError):
        return None
    if time.time() - checked_at > max(60, int(max_age_sec)):
        return None
    info = launcher_release_info_from_dict(settings.get("cached_launcher_release"))
    if info is None:
        return None
    local = local_version if local_version is not None else __version__
    info.update_available = bool(info.version) and is_newer(info.version, local)
    return info


def store_cached_launcher_release(info: LauncherReleaseInfo | None) -> None:
    from ichalaunch.config.settings import settings

    settings.set("last_launcher_release_check", time.time())
    if info is None:
        settings.set("cached_launcher_release", None)
    else:
        settings.set("cached_launcher_release", launcher_release_info_to_dict(info))


def check_latest_launcher_release(
    *,
    repo: str = LAUNCHER_REPO,
    local_version: str | None = None,
    progress: ProgressCb | None = None,
) -> LauncherReleaseInfo | None:
    """Fetch latest GitHub release; return info (update_available set by semver compare).

    Returns None only when the latest release has no usable .exe asset.
    Raises GitHubRateLimitError on rate limit; other errors propagate.
    """
    del progress  # release metadata fetch has no byte progress; accepted for Worker compat

    if rate_limit_exhausted():
        raise GitHubRateLimitError(RATE_LIMIT_STATUS)

    r = github_get(f"https://api.github.com/repos/{repo}/releases/latest")
    data = r.json()
    tag = str(data.get("tag_name") or data.get("name") or "")
    version = normalize_version(tag)
    assets = data.get("assets") or []
    asset = _pick_exe_asset(assets)
    if not asset:
        log.warning("Latest %s release has no .exe asset", repo)
        return None

    local = local_version if local_version is not None else __version__
    download_url = str(asset.get("browser_download_url") or "")
    asset_id = asset.get("id")
    if not download_url and asset_id is not None:
        download_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    try:
        asset_size = int(asset.get("size") or 0)
    except (TypeError, ValueError):
        asset_size = 0

    info = LauncherReleaseInfo(
        tag=tag,
        version=version or tag,
        name=str(data.get("name") or tag),
        asset_name=str(asset.get("name") or PREFERRED_ASSET),
        download_url=download_url,
        update_available=bool(version) and is_newer(version, local),
        html_url=str(data.get("html_url") or ""),
        asset_id=int(asset_id) if asset_id is not None else None,
        asset_size=asset_size,
    )
    store_cached_launcher_release(info)
    return info


def _download_asset(
    url: str,
    dest: Path,
    *,
    asset_id: int | None = None,
    repo: str = LAUNCHER_REPO,
    progress: Callable[[int, int], None] | None = None,
    known_total: int = 0,
    min_size: int = 1024,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Prefer API asset URL when we have an id (works with token for private assets).
    fetch_url = url
    extra_headers: dict[str, str] = {}
    if asset_id is not None:
        fetch_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
        extra_headers["Accept"] = "application/octet-stream"

    def _write_stream(resp: requests.Response) -> None:
        total = resolve_download_total(resp.headers, known_total)
        done = 0
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    with github_open(
        fetch_url,
        headers=extra_headers,
        stream=True,
        timeout=180,
        allow_redirects=True,
    ) as r:
        if r.status_code == 404 and fetch_url != url and url:
            # Fall back to browser_download_url for public releases.
            r.close()
            with github_open(
                url,
                stream=True,
                timeout=180,
                allow_redirects=True,
            ) as r2:
                r2.raise_for_status()
                _write_stream(r2)
            if dest.stat().st_size < min_size:
                raise RuntimeError(
                    f"Downloaded update asset looks too small ({dest.stat().st_size} bytes) — aborting"
                )
            return dest
        r.raise_for_status()
        _write_stream(r)
    if dest.stat().st_size < min_size:
        raise RuntimeError(
            f"Downloaded update asset looks too small ({dest.stat().st_size} bytes) — aborting"
        )
    return dest


def _write_windows_replace_script(*, pid: int, src: Path, dest: Path) -> Path:
    """PowerShell helper: wait for PID, replace EXE with retries, relaunch, clean up.

    Relaunch uses explorer.exe (not cmd ``start``) so the new process's parent is
    explorer — the same as a normal double-click. Relaunching via ``cmd /c start``
    leaves cmd.exe as parent and triggers Windows/runtime
    "Security validation failure: parent process has different executable!".
    """
    script = Path(tempfile.gettempdir()) / f"ichalaunch_update_{pid}.ps1"
    src_s = _ps_single_quote(str(src))
    dest_s = _ps_single_quote(str(dest))
    dest_dir_s = _ps_single_quote(str(dest.parent))
    old_s = _ps_single_quote(str(dest) + ".old")
    # Retries handle AV / file locks after the old process exits.
    lines = [
        "$ErrorActionPreference = 'Continue'",
        f"$pidToWait = {int(pid)}",
        f"$src = {src_s}",
        f"$dst = {dest_s}",
        f"$dstDir = {dest_dir_s}",
        f"$old = {old_s}",
        "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {",
        "  Start-Sleep -Seconds 1",
        "}",
        "$ok = $false",
        "for ($i = 0; $i -lt 40; $i++) {",
        "  try {",
        "    if (Test-Path -LiteralPath $old) {",
        "      Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue",
        "    }",
        "    if (Test-Path -LiteralPath $dst) {",
        "      Move-Item -LiteralPath $dst -Destination $old -Force -ErrorAction Stop",
        "    }",
        "    Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop",
        "    Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue",
        "    if (Test-Path -LiteralPath $old) {",
        "      Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue",
        "    }",
        "    $ok = $true",
        "    break",
        "  } catch {",
        "    Start-Sleep -Seconds 1",
        "  }",
        "}",
        "if (-not $ok) { exit 1 }",
        "# Relaunch via explorer.exe so the new process parent matches a normal",
        "# double-click. cmd/powershell Start-Process leaves a non-explorer parent and",
        "# triggers: Security validation failure: parent process has different executable!",
        'Start-Process -FilePath "explorer.exe" -ArgumentList ([string]::Format(\'"{0}"\', $dst)) | Out-Null',
        "Start-Sleep -Seconds 1",
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
        "exit 0",
    ]
    script.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return script


SIGNATURE_ASSET_SUFFIX = ".sig"
# Detached Ed25519 sidecars are a few hundred bytes of JSON, not an EXE.
_SIGNATURE_MIN_SIZE = 32


def _signature_url_for(info: "LauncherReleaseInfo") -> str:
    """Where the detached signature for this release asset lives.

    By convention the release carries ``IchaLaunch.exe`` and
    ``IchaLaunch.exe.sig`` side by side, so the signature URL is the asset URL
    plus a suffix. Publishing both is one extra upload in the release step.
    """
    return f"{info.download_url}{SIGNATURE_ASSET_SUFFIX}" if info.download_url else ""


def _verify_staged_update(staged: Path, info: "LauncherReleaseInfo") -> None:
    """Refuse to install a launcher build we cannot prove came from us.

    This is the control that makes every other failure in the update path
    survivable. Without it the only thing standing between a compromised
    release credential and code execution on every player's machine is TLS to
    GitHub, and TLS authenticates the server rather than the artefact.

    Fails closed in every direction: no pinned keys, no signature asset, an
    unreadable signature, or a signature that verifies under no trusted key all
    raise. There is deliberately no override.
    """
    from ichalaunch.core.signing import (
        Signature,
        SignatureError,
        signing_is_configured,
        verify_bytes,
    )

    if not signing_is_configured():
        raise SignatureError(
            "This build pins no update-signing keys, so it cannot verify a "
            "launcher update. Download the new version manually instead."
        )

    sig_url = _signature_url_for(info)
    if not sig_url:
        raise SignatureError("Release has no signature URL to check against")

    sig_path = staged.with_name(staged.name + SIGNATURE_ASSET_SUFFIX)
    try:
        _download_asset(sig_url, sig_path, min_size=_SIGNATURE_MIN_SIZE)
        signature = Signature.parse(sig_path.read_bytes())
        key_id = verify_bytes(staged.read_bytes(), signature)
    except SignatureError:
        raise
    except Exception as exc:  # noqa: BLE001 - a missing/unreadable sig is a failure
        raise SignatureError(
            f"Could not fetch or read the signature for {info.asset_name}: {exc}"
        ) from exc
    finally:
        try:
            sig_path.unlink(missing_ok=True)
        except OSError:
            pass
    log.info("Launcher update %s verified against pinned key %s…", info.tag, key_id[:12])


def download_and_stage_update(
    info: LauncherReleaseInfo,
    progress: ProgressCb | None = None,
) -> Path:
    """Download release EXE to a temp file and return its path."""
    if not info.download_url and info.asset_id is None:
        raise ValueError("Release has no download URL")
    status_only(progress, f"Downloading {info.asset_name} ({info.tag})…")
    tmp = Path(tempfile.gettempdir()) / f"IchaLaunch_update_{info.version}.exe"
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    _download_asset(
        info.download_url,
        tmp,
        asset_id=info.asset_id,
        progress=download_bytes_cb(progress),
        known_total=info.asset_size,
    )
    _validate_pe_exe(tmp)
    status_only(progress, "Verifying signature…")
    try:
        _verify_staged_update(tmp, info)
    except Exception:
        # Never leave an unverified executable lying in temp where a later step,
        # or a user, could mistake it for a good build.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    status_only(progress, "Preparing installer…")
    return tmp


def apply_windows_self_replace(staged_exe: Path, *, target: Path | None = None) -> Path:
    """Spawn helper to replace this process's EXE after exit; caller must quit.

    Returns the path of the helper .ps1 that was started.
    """
    if os.name != "nt":
        raise RuntimeError("Automatic launcher replace is only supported on Windows")
    dest = (target or resolve_install_exe()).resolve()
    staged = staged_exe.resolve()
    if not staged.is_file():
        raise FileNotFoundError(str(staged))
    _validate_pe_exe(staged)
    if dest.resolve() == staged.resolve():
        raise RuntimeError("Staged update path must differ from the installed EXE")
    script = _write_windows_replace_script(pid=os.getpid(), src=staged, dest=dest)
    log.info("Launching self-update helper %s -> %s (script %s)", staged, dest, script)
    # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP: helper outlives us with no console flash.
    # (DETACHED_PROCESS is mutually exclusive with CREATE_NO_WINDOW on some Windows builds.)
    create_no_window = 0x08000000
    create_new_process_group = 0x00000200
    flags = create_no_window | create_new_process_group
    # List argv quotes paths with spaces correctly for CreateProcess.
    proc = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        cwd=str(dest.parent),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the helper a moment to enter its wait loop before we return to quit.
    try:
        proc.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass  # still running — expected
    else:
        if proc.returncode not in (0, None):
            raise RuntimeError(
                f"Update helper exited immediately with code {proc.returncode}. "
                f"Script: {script}"
            )
    return script


def perform_launcher_update(
    info: LauncherReleaseInfo,
    progress: ProgressCb | None = None,
) -> Path:
    """Download + stage update; return staged path (caller applies replace + quits)."""
    return download_and_stage_update(info, progress=progress)
