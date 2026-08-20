"""IchaLaunch self-update from GitHub Releases."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from ichalaunch import __version__
from ichalaunch.addons.github import (
    GitHubRateLimitError,
    RATE_LIMIT_STATUS,
    github_get,
    github_headers,
    rate_limit_exhausted,
)
from ichalaunch.core.logging_setup import log

ProgressCb = Callable[[str], None]

LAUNCHER_REPO = "brutaliccus/IchaLaunch"
PREFERRED_ASSET = "IchaLaunch.exe"


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


def _bat_escape(value: str) -> str:
    """Escape % so cmd does not expand env vars inside set "VAR=..."."""
    return value.replace("%", "%%")


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


def check_latest_launcher_release(
    *,
    repo: str = LAUNCHER_REPO,
    local_version: str | None = None,
) -> LauncherReleaseInfo | None:
    """Fetch latest GitHub release; return info (update_available set by semver compare).

    Returns None only when the latest release has no usable .exe asset.
    Raises GitHubRateLimitError on rate limit; other errors propagate.
    """
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

    return LauncherReleaseInfo(
        tag=tag,
        version=version or tag,
        name=str(data.get("name") or tag),
        asset_name=str(asset.get("name") or PREFERRED_ASSET),
        download_url=download_url,
        update_available=bool(version) and is_newer(version, local),
        html_url=str(data.get("html_url") or ""),
        asset_id=int(asset_id) if asset_id is not None else None,
    )


def _download_asset(url: str, dest: Path, *, asset_id: int | None = None, repo: str = LAUNCHER_REPO) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = github_headers()
    # Prefer API asset URL when we have an id (works with token for private assets).
    fetch_url = url
    if asset_id is not None:
        fetch_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
        headers["Accept"] = "application/octet-stream"
    with requests.get(fetch_url, headers=headers, stream=True, timeout=180, allow_redirects=True) as r:
        if r.status_code == 404 and fetch_url != url and url:
            # Fall back to browser_download_url for public releases.
            r.close()
            with requests.get(url, headers=github_headers(), stream=True, timeout=180, allow_redirects=True) as r2:
                r2.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r2.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
            return dest
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    if dest.stat().st_size < 1024:
        raise RuntimeError("Downloaded launcher update looks too small — aborting")
    return dest


def _write_windows_replace_script(*, pid: int, src: Path, dest: Path) -> Path:
    """Batch helper: wait for PID, replace EXE with retries, relaunch, clean up.

    Paths are quoted; % is escaped. Working directory for the new process is
    the EXE's folder so relative assets resolve after restart.
    """
    script = Path(tempfile.gettempdir()) / f"ichalaunch_update_{pid}.bat"
    src_s = _bat_escape(str(src))
    dest_s = _bat_escape(str(dest))
    dest_dir_s = _bat_escape(str(dest.parent))
    old_s = _bat_escape(str(dest) + ".old")
    # ping delay avoids needing timeout.exe; retries handle AV file locks.
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        f"set \"PID={pid}\"",
        f"set \"SRC={src_s}\"",
        f"set \"DST={dest_s}\"",
        f"set \"DSTDIR={dest_dir_s}\"",
        f"set \"OLD={old_s}\"",
        "set /A ATTEMPTS=0",
        ":wait",
        'tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL',
        "if not errorlevel 1 (",
        "  ping -n 2 127.0.0.1 >NUL",
        "  goto wait",
        ")",
        ":replace",
        "set /A ATTEMPTS+=1",
        'if exist "%OLD%" del /F /Q "%OLD%" >NUL 2>&1',
        'if exist "%DST%" (',
        '  move /Y "%DST%" "%OLD%" >NUL 2>&1',
        "  if errorlevel 1 (",
        "    if %ATTEMPTS% LSS 40 (",
        "      ping -n 2 127.0.0.1 >NUL",
        "      goto replace",
        "    )",
        "    exit /B 1",
        "  )",
        ")",
        'copy /Y "%SRC%" "%DST%" >NUL',
        "if errorlevel 1 (",
        '  if exist "%OLD%" move /Y "%OLD%" "%DST%" >NUL 2>&1',
        "  if %ATTEMPTS% LSS 40 (",
        "    ping -n 2 127.0.0.1 >NUL",
        "    goto replace",
        "  )",
        "  exit /B 1",
        ")",
        'del /F /Q "%SRC%" >NUL 2>&1',
        'del /F /Q "%OLD%" >NUL 2>&1',
        'start "" /D "%DSTDIR%" "%DST%"',
        "ping -n 2 127.0.0.1 >NUL",
        'del /F /Q "%~f0" >NUL 2>&1',
        "exit /B 0",
    ]
    script.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return script


def download_and_stage_update(
    info: LauncherReleaseInfo,
    progress: ProgressCb | None = None,
) -> Path:
    """Download release EXE to a temp file and return its path."""
    if not info.download_url and info.asset_id is None:
        raise ValueError("Release has no download URL")
    if progress:
        progress(f"Downloading {info.asset_name} ({info.tag})…")
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
    )
    _validate_pe_exe(tmp)
    if progress:
        progress("Preparing installer…")
    return tmp


def apply_windows_self_replace(staged_exe: Path, *, target: Path | None = None) -> Path:
    """Spawn helper to replace this process's EXE after exit; caller must quit.

    Returns the path of the helper .bat that was started.
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
        ["cmd.exe", "/C", "call", str(script)],
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
