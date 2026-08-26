"""Process / download helpers."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

BytesProgressCb = Callable[[int, int], None]  # downloaded, total
# Back-compat alias used by download_file callers.
ProgressCb = BytesProgressCb


class StatusProgress:
    """Status-string reporter that can also emit determinate download percents.

    Compatible with ``Callable[[str], None]`` progress hooks used by install/update
    workers. Pass ``.on_bytes`` into ``download_file`` for byte-level progress.
    """

    def __init__(
        self,
        on_status: Callable[[str], None],
        on_pct: Callable[[int], None],
    ) -> None:
        self._on_status = on_status
        self._on_pct = on_pct
        self._label = ""

    def __call__(self, msg: str) -> None:
        self._label = (msg or "").strip()
        self._on_status(self._label)
        # Status-only updates (install/extract) fall back to indeterminate.
        # Download tracking must use on_count / on_bytes — those keep a determinate %.
        self._on_pct(-1)

    def set_status(self, msg: str) -> None:
        """Update the status label without changing determinate/indeterminate percent."""
        self._label = (msg or "").strip()
        if self._label:
            self._on_status(self._label)

    def on_count(self, done: int, total: int, msg: str | None = None) -> None:
        """Report determinate item progress (update checks, multi-step jobs)."""
        if msg is not None:
            self._label = (msg or "").strip()
            if self._label:
                self._on_status(self._label)
        if total and total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            self._on_pct(pct)
        else:
            self._on_pct(0)

    def on_bytes(self, done: int, total: int) -> None:
        if total and total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            self._on_pct(pct)
            base = self._label.rstrip(".…") or "Downloading"
            self._on_status(f"{base}… {pct}%")
        else:
            # Unknown size: indeterminate is OK, but do not re-emit -1 every chunk
            # (that restarts ThemeLoadingBar's pulse).
            self._on_pct(-1)


def status_only(progress: Any, msg: str) -> None:
    """Update the status label without forcing indeterminate (unlike ``progress()``)."""
    if progress is None:
        return
    setter = getattr(progress, "set_status", None)
    if callable(setter):
        setter(msg)
        return
    if callable(progress):
        progress(msg)


def download_bytes_cb(progress: Any) -> BytesProgressCb | None:
    """Adapt a status progress object to ``download_file``'s (done, total) callback."""
    if progress is None:
        return None
    cb = getattr(progress, "on_bytes", None)
    return cb if callable(cb) else None


def resolve_download_total(headers: Any, known_total: int = 0) -> int:
    """Prefer Content-Length; fall back to an API/catalog size when the header is missing."""
    try:
        n = int((headers or {}).get("Content-Length") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    try:
        k = int(known_total or 0)
    except (TypeError, ValueError):
        k = 0
    return k if k > 0 else 0


def _download_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }


def zip_url_from_html(html: str, base_url: str) -> str | None:
    """Best-effort zip / download href from a file-host landing page."""
    text = html or ""
    for pat in (
        r'href=["\']([^"\']+\.zip[^"\']*)["\']',
        r'https?://[^\s"\'<>]+?\.zip(?:\?[^\s"\'<>]*)?',
        r'href=["\']([^"\']+/download[^"\']*)["\']',
        r'data-url=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, text, re.I)
        if not m:
            continue
        href = (m.group(1) if m.lastindex else m.group(0)).strip()
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
        if href.startswith("/") and base_url:
            return urljoin(base_url, href)
    return None


def download_bytes(
    url: str,
    progress: ProgressCb | None = None,
    timeout: int = 120,
    known_total: int = 0,
) -> bytes:
    """Download into memory (avoids Windows AV locking certain zip names on disk)."""
    headers = _download_headers()
    chunks: list[bytes] = []
    with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype and "drive.google" in url:
            raise RuntimeError(
                "Google Drive returned an HTML page instead of the file. "
                "Try again later or download manually."
            )
        total = resolve_download_total(r.headers, known_total)
        done = 0
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            chunks.append(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return b"".join(chunks)


def download_file(
    url: str,
    dest: Path,
    progress: ProgressCb | None = None,
    timeout: int | tuple[int, int] = 120,
    extra_headers: dict[str, str] | None = None,
    source_url: str | None = None,
    known_total: int = 0,
    *,
    retries: int = 3,
) -> Path:
    """Download *url* to *dest*, retrying transient connection failures."""
    last_exc: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            return _download_file_once(
                url,
                dest,
                progress=progress,
                timeout=timeout,
                extra_headers=extra_headers,
                source_url=source_url,
                known_total=known_total,
            )
        except (ConnectionError, ChunkedEncodingError, Timeout, OSError) as exc:
            last_exc = exc
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            if attempt + 1 >= attempts:
                break
            time.sleep(min(8, 2**attempt))
    assert last_exc is not None
    raise last_exc


def _download_file_once(
    url: str,
    dest: Path,
    progress: ProgressCb | None = None,
    timeout: int | tuple[int, int] = 120,
    extra_headers: dict[str, str] | None = None,
    source_url: str | None = None,
    known_total: int = 0,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _download_headers()
    if extra_headers:
        headers.update(extra_headers)
    origin = source_url or url
    with requests.get(
        url, stream=True, timeout=timeout, headers=headers, allow_redirects=True
    ) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        disp = (r.headers.get("Content-Disposition") or "").lower()
        header_len = 0
        try:
            header_len = int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            header_len = 0
        total = resolve_download_total(r.headers, known_total)
        html_type = "text/html" in ctype and "attachment" not in disp
        if html_type and (not header_len or header_len < 2_000_000):
            body = b"".join(r.iter_content(chunk_size=1024 * 64))
            if "drive.google" in url:
                raise RuntimeError(
                    "Google Drive returned an HTML page instead of the file. "
                    "Try again later or download manually."
                )
            nxt = zip_url_from_html(body.decode("utf-8", "replace"), str(r.url))
            if nxt and nxt != url:
                return _download_file_once(
                    nxt,
                    dest,
                    progress=progress,
                    timeout=timeout,
                    extra_headers=extra_headers,
                    source_url=origin,
                    known_total=known_total,
                )
            raise RuntimeError(
                "Download returned a web page instead of a zip.\n" + origin
            )
        done = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    # Sanity-check MPQ magic when expecting an archive asset
    if dest.suffix.lower() == ".mpq" and dest.stat().st_size >= 4:
        with dest.open("rb") as f:
            magic = f.read(3)
        if magic != b"MPQ":
            raise RuntimeError(f"Downloaded file is not a valid MPQ: {dest.name}")
    return dest


def google_drive_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )

def wow_exe_running() -> bool:
    """True when WoW.exe (or VanillaFixes.exe) is running. Windows-only; no admin."""
    if sys.platform != "win32":
        return False
    names = ("WoW.exe", "VanillaFixes.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for name in names:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=flags,
            )
            out = (proc.stdout or "").lower()
            if "no tasks" in out:
                continue
            if name.lower() in out:
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def processes_locking_paths(paths: list[Path | str], *, limit: int = 6) -> list[str]:
    """Best-effort image names holding *paths* open (Windows Restart Manager).

    Returns ``[]`` when unavailable (non-Windows, API failure, or no lockers found).
    Never raises. Does not require admin.
    """
    if sys.platform != "win32" or not paths:
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    rstrtmgr = ctypes.windll.rstrtmgr
    kernel32 = ctypes.windll.kernel32

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", wintypes.DWORD),
            ("ProcessStartTime", wintypes.FILETIME),
        ]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", ctypes.c_uint),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(32)
    if int(rstrtmgr.RmStartSession(ctypes.byref(session), 0, key)) != 0:
        return []

    found: list[str] = []
    try:
        existing = [str(Path(p)) for p in paths if p and Path(p).exists()]
        if not existing:
            return []
        arr = (wintypes.LPCWSTR * len(existing))(*existing)
        if int(rstrtmgr.RmRegisterResources(session, len(existing), arr, 0, None, 0, None)) != 0:
            return []

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot = wintypes.DWORD(0)
        # First call often returns ERROR_MORE_DATA (234) with the required size.
        rc = int(
            rstrtmgr.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reboot)
            )
        )
        if needed.value == 0:
            return []
        infos = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        rc = int(
            rstrtmgr.RmGetList(
                session,
                ctypes.byref(needed),
                ctypes.byref(count),
                infos,
                ctypes.byref(reboot),
            )
        )
        if rc not in (0, 234):
            return []

        seen: set[str] = set()
        for i in range(int(count.value)):
            pid = int(infos[i].Process.dwProcessId)
            name = ""
            # Prefer the live image name when QueryFullProcessImageName succeeds.
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(len(buf))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        name = Path(buf.value).name
                finally:
                    kernel32.CloseHandle(handle)
            if not name:
                name = (infos[i].strAppName or "").strip() or f"PID {pid}"
            key_name = name.lower()
            if key_name in seen:
                continue
            seen.add(key_name)
            found.append(name)
            if len(found) >= max(1, int(limit)):
                break
    except (OSError, AttributeError, ValueError, TypeError):
        return []
    finally:
        try:
            rstrtmgr.RmEndSession(session)
        except Exception:  # noqa: BLE001
            pass
    return found


def file_in_use_hint(*paths: Path | str) -> str:
    """Short user-facing diagnosis when a game-tree file cannot be replaced.

    Prefers detecting WoW/VanillaFixes, then Restart Manager lockers, then a
    generic "another process" note (not antivirus-first). Always includes
    Task Manager End-task steps for WoW.exe / VanillaFixes.exe.
    """
    from ichalaunch.core.filesystem import TASK_MANAGER_END_GAME_HINT

    end_tasks = f"{TASK_MANAGER_END_GAME_HINT} Then retry Apply."
    if wow_exe_running():
        return (
            "WoW.exe or VanillaFixes.exe is still running "
            "(the game window can be closed while the process stays in Task Manager). "
            + end_tasks
        )
    lockers = processes_locking_paths([p for p in paths if p])
    if lockers:
        return f"In use by: {', '.join(lockers)}. {end_tasks}"
    return (
        "Another process still has the file open "
        "(overlays, Explorer preview, backup/sync, or antivirus — "
        "including non-Defender products). "
        + end_tasks
    )


# Game client DLLs (VanillaHelpers.dll, nampower.dll, VfPatcher.dll, d3d9.dll, …)
# must never be loaded into the IchaLaunch process via ctypes.WinDLL / CDLL /
# QLibrary / kernel32.LoadLibrary. Mapping them runs DllMain here and can crash
# the Qt event loop (or trip Defender on first access). Hash/stat/copy them as
# plain files only, and treat WinError 5/32/225 as skip + backoff.


def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    workdir = cwd or path.parent
    if sys.platform != "win32":
        # A Windows PE cannot be exec'd here: it needs Proton, and the
        # supported way to drive Proton outside Steam is umu-launcher.
        from ichalaunch.game.proton import launch_windows_exe

        return launch_windows_exe(path, workdir)
    # Vanilla WoW is single-threaded and cache-bound, so on a dual-CCD X3D part
    # it wants the CCD carrying the 3D V-Cache. The mask goes on this process for
    # the duration of the spawn so the child -- and, when VanillaFixes is doing
    # the launching, its own child -- inherits it at creation. No-ops on every
    # other CPU and on every other platform.
    from ichalaunch.config.settings import settings as _settings
    from ichalaunch.game.cpu_topology import launch_affinity

    if not _settings.get("pin_to_vcache_ccd", True):
        return subprocess.Popen([str(path)], cwd=str(workdir), shell=False)
    with launch_affinity():
        return subprocess.Popen([str(path)], cwd=str(workdir), shell=False)
