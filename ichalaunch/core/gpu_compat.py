"""Lightweight GPU / Vulkan suitability checks for DXVK on Windows."""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache

# Patterns that almost certainly cannot run DXVK/Vulkan usefully.
_BAD_GPU_RE = re.compile(
    r"(?:"
    r"microsoft basic (?:display|render) driver|"
    r"virtualbox|vmware|"
    r"intel.*\bhd graphics\s*(?:2\d{3}|3\d{3}|4[0-4]\d{0,2})\b|"
    r"intel.*\bhd\s*(?:2\d{3}|3\d{3}|4[0-4]\d{0,2})\b|"
    r"intel.*\bgma\s*\d+|"
    r"mobile intel.*945|"
    r"ati radeon.*(?:x1\d{3}|hd\s*2\d{3}|hd\s*3[0-4]\d{0,2})\b"
    r")",
    re.IGNORECASE,
)

# Older / weak iGPUs — DXVK may work but is often slower than native D3D9.
_WARN_GPU_RE = re.compile(
    r"(?:"
    r"intel.*\b(uhd|iris)\b|"
    r"intel.*\bhd graphics\s*5\d{2}\b|"
    r"intel.*\bhd\s*5\d{2}\b|"
    r"radeon.*\br[357]\s|"
    r"geforce.*\bgt\s*\d{3}\b"
    r")",
    re.IGNORECASE,
)


def _creationflags() -> int:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


@lru_cache(maxsize=1)
def query_gpu_names() -> tuple[str, ...]:
    """Return display adapter names from WMI (Windows). Empty on failure."""
    try:
        proc = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=_creationflags(),
        )
        if proc.returncode != 0:
            return ()
        names: list[str] = []
        for line in (proc.stdout or "").splitlines():
            text = line.strip()
            if not text or text.lower() == "name":
                continue
            names.append(text)
        return tuple(names)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ()


def assess_dxvk_gpu() -> tuple[str, tuple[str, ...], str]:
    """Assess DXVK suitability.

    Returns ``(level, gpu_names, message)`` where *level* is ``ok``, ``warn``, or ``bad``.
    """
    gpus = query_gpu_names()
    if not gpus:
        return (
            "warn",
            gpus,
            "Could not detect your graphics card. VanillaFixes + DXVK needs a GPU with "
            "working Vulkan drivers. If unsure, try regular VanillaFixes first.",
        )

    joined = " · ".join(gpus)
    for name in gpus:
        if _BAD_GPU_RE.search(name):
            return (
                "bad",
                gpus,
                f"Detected: {joined}\n\nThis GPU is very unlikely to work well with "
                "VanillaFixes + DXVK (Vulkan). Regular VanillaFixes is strongly recommended.",
            )

    for name in gpus:
        if _WARN_GPU_RE.search(name):
            return (
                "warn",
                gpus,
                f"Detected: {joined}\n\nThis GPU may have limited or older Vulkan support. "
                "DXVK can work but regular VanillaFixes is often more reliable on integrated "
                "or low-end hardware.",
            )

    return (
        "ok",
        gpus,
        f"Detected: {joined}\n\nYour GPU should support Vulkan/DXVK. If you see issues, "
        "switch back to regular VanillaFixes.",
    )
