"""Find the cache-rich CCD on dual-CCD AMD X3D parts so the client can be pinned.

Vanilla WoW is effectively single-threaded and cache-sensitive. On a 7950X3D /
9950X3D class part one CCD has 3D V-Cache and the other does not; the scheduler
is free to park the game on the wrong one. Pinning is derived from L3 layout,
not the CPU name -- a 7800X3D / 9800X3D reports one L3 domain, so nothing pins.

None means "do not pin". A ratio alone is not a V-Cache signature: Strix Point
(16 MB vs 8 MB) has a 2.0 ratio and no stacked cache. Dual-CCD X3D parts ship
96 MB vs 32 MB, so both a ratio floor and an absolute 64 MB floor must pass.
"""

from __future__ import annotations

import contextlib
import glob
import os
import re
import sys
from dataclasses import dataclass

from ichalaunch.core.logging_setup import log

_MIN_CACHE_RATIO = 1.5
_MIN_VCACHE_BYTES = 64 * 1024 * 1024

# Untouched installs resolve to this. Kept out of DEFAULTS so save() does not
# bake the default into every settings.json (see proton.WOW64_DEFAULT_ON).
VCACHE_PIN_DEFAULT_ON = True


@dataclass(frozen=True)
class CacheDomain:
    """One L3 cache and the logical CPUs that share it -- in practice, one CCD."""

    l3_bytes: int
    cpus: tuple[int, ...]

    @property
    def affinity_mask(self) -> int:
        mask = 0
        for c in self.cpus:
            mask |= 1 << c
        return mask

    @property
    def cpu_list(self) -> str:
        """The domain as taskset(1) would want it, e.g. '0-7,16-23'."""
        out: list[str] = []
        run: list[int] = []
        for c in sorted(self.cpus):
            if run and c == run[-1] + 1:
                run.append(c)
                continue
            if run:
                out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
            run = [c]
        if run:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        return ",".join(out)


def vcache_pin_enabled() -> bool:
    """Whether to pin, resolving an unset setting to the default."""
    from ichalaunch.config.settings import settings

    stored = settings.get("pin_to_vcache_ccd", None)
    return VCACHE_PIN_DEFAULT_ON if stored is None else bool(stored)


def _parse_size(text: str) -> int | None:
    """'98304K' / '96M' / '32768K' -> bytes."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMG]?)B?\s*", text, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2).upper()]


def _parse_cpu_list(text: str) -> tuple[int, ...]:
    """'0-7,16-23' -> (0,1,...,7,16,...,23)."""
    cpus: set[int] = set()
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            try:
                cpus.update(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                cpus.add(int(part))
            except ValueError:
                continue
    return tuple(sorted(cpus))


def _domains_linux() -> list[CacheDomain]:
    """L3 domains from sysfs. Deduplicated -- every CPU reports its own copy."""
    seen: dict[tuple[int, tuple[int, ...]], CacheDomain] = {}
    for d in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cache/index*"):
        try:
            with open(os.path.join(d, "level")) as f:
                if f.read().strip() != "3":
                    continue
            with open(os.path.join(d, "size")) as f:
                size = _parse_size(f.read())
            with open(os.path.join(d, "shared_cpu_list")) as f:
                cpus = _parse_cpu_list(f.read())
        except (OSError, ValueError):
            continue
        if not size or not cpus:
            continue
        seen[(size, cpus)] = CacheDomain(size, cpus)
    return list(seen.values())


def _parse_windows_cache_buffer(buf, length: int) -> list[CacheDomain]:
    """Walk a GetLogicalProcessorInformationEx(RelationCache) buffer.

    Split from the API call so offsets can be tested on any platform. Offsets
    are from offsetof() on the real headers, not counted by hand:

        Relationship  +0    Size +4
        CACHE_RELATIONSHIP at +8:
            Level +8  CacheSize +12  Type +16
            Reserved[18] +20  GroupCount +38
            GROUP_AFFINITY.Mask +40  Group +48

    Reading Mask at +24 lands inside Reserved (zero-filled) and every record
    is silently discarded.
    """
    import ctypes

    relation_cache = 2
    domains: dict[tuple[int, tuple[int, ...]], CacheDomain] = {}
    groups_seen: set[int] = set()
    offset = 0
    base_addr = ctypes.addressof(buf)
    while offset + 8 <= length:
        base = base_addr + offset
        rel = ctypes.c_uint32.from_address(base).value
        size = ctypes.c_uint32.from_address(base + 4).value
        if size == 0 or offset + size > length:
            break
        if rel == relation_cache and size >= 56:
            level = ctypes.c_uint8.from_address(base + 8).value
            cache_size = ctypes.c_uint32.from_address(base + 12).value
            if level == 3:
                mask = ctypes.c_uint64.from_address(base + 40).value
                group = ctypes.c_uint16.from_address(base + 48).value
                groups_seen.add(group)
                cpus = tuple(i for i in range(64) if mask & (1 << i))
                if cpus and cache_size:
                    domains[(cache_size, cpus)] = CacheDomain(cache_size, cpus)
        offset += size

    if len(groups_seen) > 1:
        # A plain affinity mask addresses one processor group.
        log.info("More than one processor group present; declining to pin.")
        return []
    return list(domains.values())


def _domains_windows() -> list[CacheDomain]:
    """L3 domains via GetLogicalProcessorInformationEx(RelationCache)."""
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return []
    if not hasattr(ctypes, "windll"):
        return []

    relation_cache = 2
    kernel32 = ctypes.windll.kernel32
    kernel32.GetLogicalProcessorInformationEx.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetLogicalProcessorInformationEx.restype = wintypes.BOOL
    length = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(
        relation_cache, None, ctypes.byref(length)
    )
    if not length.value:
        return []
    buf = (ctypes.c_byte * length.value)()
    if not kernel32.GetLogicalProcessorInformationEx(
        relation_cache, buf, ctypes.byref(length)
    ):
        return []
    return _parse_windows_cache_buffer(buf, length.value)


def cache_domains() -> list[CacheDomain]:
    """Every L3 domain on this machine, largest cache first."""
    try:
        found = _domains_windows() if sys.platform == "win32" else _domains_linux()
    except Exception:
        log.debug("CPU topology probe failed", exc_info=True)
        return []
    return sorted(found, key=lambda d: -d.l3_bytes)


def vcache_domain() -> CacheDomain | None:
    """The 3D V-Cache CCD, or None when pinning would be wrong or pointless."""
    domains = cache_domains()
    if len(domains) < 2:
        return None
    biggest, second = domains[0], domains[1]
    if biggest.l3_bytes < second.l3_bytes * _MIN_CACHE_RATIO:
        return None
    if biggest.l3_bytes < _MIN_VCACHE_BYTES:
        log.info(
            "L3 domains differ (%.0f MB vs %.0f MB) but neither is large "
            "enough to be 3D V-Cache; not pinning.",
            biggest.l3_bytes / 1024 ** 2,
            second.l3_bytes / 1024 ** 2,
        )
        return None
    log.info(
        "3D V-Cache CCD detected: L3 %.0f MB on CPUs %s (other CCD %.0f MB)",
        biggest.l3_bytes / 1024 ** 2,
        biggest.cpu_list,
        second.l3_bytes / 1024 ** 2,
    )
    return biggest


def taskset_prefix() -> list[str]:
    """A ``taskset -c`` prefix for the launch command, or [] to not pin.

    taskset sets affinity and only then execs, so a refused CPU list means the
    game never starts (exit 1). Intersect with this process's allowed affinity
    first: empty overlap means no prefix; a partial overlap pins to what remains.
    """
    import shutil

    domain = vcache_domain()
    if domain is None:
        return []
    if not shutil.which("taskset"):
        log.info("A V-Cache CCD was found but taskset is not installed; not pinning.")
        return []

    try:
        allowed = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        allowed = None
    if allowed is not None:
        usable = set(domain.cpus) & allowed
        if not usable:
            log.warning(
                "The V-Cache CCD (CPUs %s) is outside this process's "
                "permitted affinity; launching unpinned.",
                domain.cpu_list,
            )
            return []
        if usable != set(domain.cpus):
            domain = CacheDomain(domain.l3_bytes, tuple(sorted(usable)))
            log.info(
                "Some V-Cache CPUs are unavailable; pinning to %s instead.",
                domain.cpu_list,
            )
    return ["taskset", "-c", domain.cpu_list]


@contextlib.contextmanager
def launch_affinity():
    """Hold this process on the V-Cache CCD while a child is being created.

    Windows propagates affinity only to children created *after* the mask is
    set. The process started here is usually VanillaFixes, and WoW.exe is its
    child -- masking the loader after Popen races and says nothing about the
    grandchild. Pin the launcher first so inheritance is deterministic.

    No-op on Linux (taskset already covers it), on non-X3D hardware, and when
    the original mask cannot be read back to restore.
    """
    domain = None if sys.platform != "win32" else vcache_domain()
    if domain is None:
        yield None
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetProcessAffinityMask.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.GetProcessAffinityMask.restype = ctypes.c_int
        kernel32.SetProcessAffinityMask.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        kernel32.SetProcessAffinityMask.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        proc_mask, sys_mask = ctypes.c_size_t(0), ctypes.c_size_t(0)
        if not kernel32.GetProcessAffinityMask(
            handle, ctypes.byref(proc_mask), ctypes.byref(sys_mask)
        ):
            log.warning("Could not read this process's affinity; not pinning.")
            yield None
            return
        original = proc_mask.value
        wanted = domain.affinity_mask & sys_mask.value
        if not wanted:
            log.warning(
                "V-Cache CCD mask %#x is outside this process's allowed "
                "affinity %#x; not pinning.",
                domain.affinity_mask,
                sys_mask.value,
            )
            yield None
            return
        if not kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(wanted)):
            log.warning("Could not set affinity; launching unpinned.")
            yield None
            return
    except Exception:
        log.debug("Affinity setup failed", exc_info=True)
        yield None
        return

    log.info(
        "Launching pinned to the V-Cache CCD (CPUs %s); descendants inherit.",
        domain.cpu_list,
    )
    try:
        yield domain
    finally:
        try:
            kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(original))
        except Exception:
            log.warning("Could not restore this process's affinity mask.")
