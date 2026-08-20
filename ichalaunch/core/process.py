"""Process / download helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import requests

ProgressCb = Callable[[int, int], None]  # downloaded, total


def download_file(url: str, dest: Path, progress: ProgressCb | None = None, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "IchaLaunch/0.1"}) as r:
        r.raise_for_status()
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
    return dest


def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return subprocess.Popen(
        [str(path)],
        cwd=str(cwd or path.parent),
        shell=False,
    )
