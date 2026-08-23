"""Process / download helpers."""

from __future__ import annotations

import os
import re
import subprocess
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

def _wow_running_linux() -> bool:
    """Detect WoW.exe running under wine/Proton by scanning /proc.

    The Windows path below shells out to ``tasklist``, which does not exist
    here, so the check returned False unconditionally on Linux and callers
    happily moved addon folders out from under a live game. Wine keeps the
    Windows-style path in the process cmdline, so this needs no privileges.
    """
    # Match only a WINDOWS-style path (backslash separator), which is what the
    # real wine process carries: "X:\\Games\\RavenCraft\\WoW.exe". The Linux-side
    # wrappers (umu-run, proton) use forward slashes.
    names = ("\\wow.exe", "\\vanillafixes.exe", "\\superwowlauncher.exe")
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if not raw:
            continue
        # argv[0] ONLY -- the executable itself. Matching the whole command line
        # would count any process that merely MENTIONS the path (a shell, an
        # editor with the file open) as "the game is running".
        argv0 = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").lower()
        if argv0.endswith(names):
            return True
    return False


def wow_exe_running() -> bool:
    """True when WoW.exe (or VanillaFixes.exe) is running. No admin required."""
    import sys

    if sys.platform != "win32":
        return _wow_running_linux()
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


# Game client DLLs (VanillaHelpers.dll, nampower.dll, VfPatcher.dll, d3d9.dll, …)
# must never be loaded into the IchaLaunch process via ctypes.WinDLL / CDLL /
# QLibrary / kernel32.LoadLibrary. Mapping them runs DllMain here and can crash
# the Qt event loop (or trip Defender on first access). Hash/stat/copy them as
# plain files only, and treat WinError 5/32/225 as skip + backoff.


def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return subprocess.Popen(
        [str(path)],
        cwd=str(cwd or path.parent),
        shell=False,
    )
