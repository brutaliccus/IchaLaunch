"""Lightweight GPU / Vulkan suitability checks for DXVK.

Windows asks WMI for adapter names and matches them against known-bad
hardware. Linux cannot be answered that way, because a DRM driver name
says nothing about whether DXVK will load, so it checks what actually
decides the question there.

WOW 1.12 IS A 32-BIT EXECUTABLE, so DXVK's ``d3d9.dll`` is 32-bit and
loads a 32-bit Vulkan driver. A machine with flawless 64-bit Vulkan and no
lib32 drivers is the common failure, and no amount of name matching can
see it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

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


# --- Linux: the 32-bit Vulkan pre-flight ---------------------------------

# The loader honours an explicit manifest list in the environment, and when
# one is set it replaces the search path rather than adding to it.
_ICD_ENV_VARS = ("VK_DRIVER_FILES", "VK_ICD_FILENAMES")

# Where a 32-bit driver lands. Distros disagree and several of these alias
# one another, so hits are deduplicated by resolved path. /usr/lib is listed
# because it is the 32-bit directory on Fedora-family layouts (/usr/lib64
# holds the 64-bit ones); elsewhere it is 64-bit and the ELF class check
# rejects it, which costs one five-byte read.
_LIB32_DIRS = (
    "/usr/lib32",
    "/usr/lib/i386-linux-gnu",
    "/lib32",
    "/usr/lib",
)

_LIB32_HINT = (
    "Install the 32-bit Vulkan packages for your card:\n"
    "  Arch     lib32-vulkan-icd-loader, plus lib32-vulkan-radeon,\n"
    "           lib32-vulkan-intel or lib32-nvidia-utils\n"
    "  Debian   libvulkan1:i386, plus mesa-vulkan-drivers:i386\n"
    "  Fedora   vulkan-loader.i686, plus mesa-vulkan-drivers.i686"
)


def _is_elf32(path: Path) -> bool:
    """True when *path* is an ELF object built for 32-bit (ELFCLASS32)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
    except OSError:
        return False
    return head[:4] == b"\x7fELF" and head[4:5] == b"\x01"


def _icd_manifest_dirs() -> tuple[Path, ...]:
    """Vulkan ICD manifest directories, in the loader's own search order."""
    home = Path.home()

    def _env_dir(var: str, fallback: str) -> str:
        return os.environ.get(var) or str(home / fallback)

    roots = [
        _env_dir("XDG_CONFIG_HOME", ".config"),
        "/etc/xdg",
        "/etc",
        _env_dir("XDG_DATA_HOME", ".local/share"),
    ]
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots.extend(d for d in data_dirs.split(os.pathsep) if d)

    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        d = Path(root).expanduser() / "vulkan" / "icd.d"
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return tuple(out)


def _icd_manifest_files() -> tuple[Path, ...]:
    """Every ICD manifest the loader would read."""
    for var in _ICD_ENV_VARS:
        raw = os.environ.get(var, "")
        explicit = [Path(p) for p in raw.split(os.pathsep) if p]
        if explicit:
            return tuple(p for p in explicit if p.is_file())

    out: list[Path] = []
    seen: set[str] = set()
    for d in _icd_manifest_dirs():
        try:
            entries = sorted(d.glob("*.json"))
        except OSError:
            continue
        for entry in entries:
            key = str(entry)
            if key not in seen:
                seen.add(key)
                out.append(entry)
    return tuple(out)


def _icd_library_candidates(manifest: Path) -> tuple[Path, ...]:
    """Where the driver library named by *manifest* might live, 32-bit first.

    A manifest usually names a bare soname and is shared between both
    architectures, so the 32-bit copy is the same file name under a lib32
    directory. Absolute and manifest-relative forms are also honoured, for
    the distros that ship a separate 32-bit manifest.
    """
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    icd = data.get("ICD")
    lib = ((icd or {}).get("library_path") or "").strip() if isinstance(icd, dict) else ""
    if not lib:
        return ()

    out: list[Path] = [Path(d) / Path(lib).name for d in _LIB32_DIRS]
    if os.path.isabs(lib):
        out.append(Path(lib))
    elif "/" in lib:
        out.append(manifest.parent / lib)
    return tuple(out)


@lru_cache(maxsize=1)
def find_vulkan_loader_32bit() -> str | None:
    """The 32-bit Vulkan loader, if this machine has one."""
    for d in _LIB32_DIRS:
        cand = Path(d) / "libvulkan.so.1"
        if _is_elf32(cand):
            return str(cand)
    return None


@lru_cache(maxsize=1)
def find_vulkan_icds_32bit() -> tuple[str, ...]:
    """Resolved 32-bit Vulkan driver libraries, at most one per manifest."""
    found: list[str] = []
    seen: set[str] = set()
    for manifest in _icd_manifest_files():
        for ref in _icd_library_candidates(manifest):
            try:
                real = str(ref.resolve())
            except OSError:
                continue
            if real in seen or not _is_elf32(Path(real)):
                continue
            seen.add(real)
            found.append(real)
            break
    return tuple(found)


def _drm_adapter_names() -> tuple[str, ...]:
    """Display adapters from sysfs: driver name and PCI id, no external tools."""
    try:
        cards = sorted(Path("/sys/class/drm").glob("card[0-9]*"))
    except OSError:
        return ()
    names: list[str] = []
    for card in cards:
        if "-" in card.name:
            continue  # card0-DP-1 and friends are connectors, not adapters
        try:
            text = (card / "device" / "uevent").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        driver = pci = ""
        for line in text.splitlines():
            key, _sep, value = line.partition("=")
            if key == "DRIVER":
                driver = value.strip()
            elif key == "PCI_ID":
                pci = value.strip()
        if not driver:
            continue
        label = f"{driver} [{pci}]" if pci else driver
        if label not in names:
            names.append(label)
    return tuple(names)


def _assess_dxvk_linux() -> tuple[str, tuple[str, ...], str]:
    """DXVK suitability on Linux: can a 32-bit d3d9.dll reach a Vulkan driver?"""
    gpus = query_gpu_names()
    joined = " · ".join(gpus) if gpus else "no display adapter reported"
    icds = find_vulkan_icds_32bit()
    loader = find_vulkan_loader_32bit()

    if icds and loader:
        return (
            "ok",
            gpus,
            f"Detected: {joined}\n\n32-bit Vulkan is installed, so VanillaFixes + DXVK "
            "should work.",
        )

    if icds and not loader:
        # Proton's Steam Linux Runtime carries a loader of its own, so this is
        # worth mentioning and not worth blocking on.
        return (
            "warn",
            gpus,
            f"Detected: {joined}\n\nA 32-bit Vulkan driver is installed but the 32-bit "
            "Vulkan loader (libvulkan.so.1) is not. Proton usually supplies one, so "
            "DXVK will probably still run.\n\n" + _LIB32_HINT,
        )

    if not _icd_manifest_files():
        return (
            "warn",
            gpus,
            f"Detected: {joined}\n\nNo Vulkan drivers were found on this system. "
            "VanillaFixes + DXVK needs Vulkan; regular VanillaFixes does not.",
        )

    return (
        "bad",
        gpus,
        f"Detected: {joined}\n\nVulkan is installed, but only for 64-bit programs. "
        "WoW 1.12 is a 32-bit executable, so DXVK needs a 32-bit Vulkan driver and "
        "will fail to start without one. Use regular VanillaFixes, or install the "
        "32-bit packages.\n\n" + _LIB32_HINT,
    )


@lru_cache(maxsize=1)
def query_gpu_names() -> tuple[str, ...]:
    """Display adapter names: WMI on Windows, sysfs elsewhere. Empty on failure."""
    if sys.platform != "win32":
        return _drm_adapter_names()
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


def _assess_by_name(gpus: tuple[str, ...]) -> tuple[str, tuple[str, ...], str] | None:
    """Verdict from the adapter name alone, or None when no pattern matches."""
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
    return None


def assess_dxvk_gpu() -> tuple[str, tuple[str, ...], str]:
    """Assess DXVK suitability.

    Returns ``(level, gpu_names, message)`` where *level* is ``ok``, ``warn``, or ``bad``.

    The two platforms ask different questions. Windows matches the adapter
    name against hardware known to do Vulkan badly. Linux checks whether a
    32-bit Vulkan driver exists at all, which is what decides it there; the
    name table cannot help, because a DRM driver is called "amdgpu" or
    "i915" and never carries the marketing string those patterns match.
    """
    if sys.platform != "win32":
        return _assess_dxvk_linux()

    gpus = query_gpu_names()
    if not gpus:
        return (
            "warn",
            gpus,
            "Could not detect your graphics card. VanillaFixes + DXVK needs a GPU with "
            "working Vulkan drivers. If unsure, try regular VanillaFixes first.",
        )

    by_name = _assess_by_name(gpus)
    if by_name is not None:
        return by_name

    joined = " · ".join(gpus)
    return (
        "ok",
        gpus,
        f"Detected: {joined}\n\nYour GPU should support Vulkan/DXVK. If you see issues, "
        "switch back to regular VanillaFixes.",
    )
