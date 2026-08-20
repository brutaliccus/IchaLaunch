"""Process / download helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import requests

ProgressCb = Callable[[int, int], None]  # downloaded, total


def download_file(url: str, dest: Path, progress: ProgressCb | None = None, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
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

def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return subprocess.Popen(
        [str(path)],
        cwd=str(cwd or path.parent),
        shell=False,
    )
