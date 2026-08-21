"""Process / download helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import requests

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
        self._on_pct(-1)

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
            self._on_pct(-1)


def download_bytes_cb(progress: Any) -> BytesProgressCb | None:
    """Adapt a status progress object to ``download_file``'s (done, total) callback."""
    if progress is None:
        return None
    cb = getattr(progress, "on_bytes", None)
    return cb if callable(cb) else None


def _download_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }


def download_bytes(url: str, progress: ProgressCb | None = None, timeout: int = 120) -> bytes:
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
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            chunks.append(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return b"".join(chunks)


def download_file(url: str, dest: Path, progress: ProgressCb | None = None, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _download_headers()
    with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
        r.raise_for_status()
        # Google Drive sometimes returns an HTML interstitial; reject clearly
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype and "drive.google" in url:
            raise RuntimeError(
                "Google Drive returned an HTML page instead of the file. "
                "Try again later or download manually."
            )
        total = int(r.headers.get("Content-Length") or 0)
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
        magic = dest.read_bytes()[:3]
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
    import sys

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


def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return subprocess.Popen(
        [str(path)],
        cwd=str(cwd or path.parent),
        shell=False,
    )
