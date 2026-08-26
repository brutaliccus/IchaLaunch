"""Finding the cache-rich CCD on AMD X3D parts, so the client can be pinned to it.

WHY. Vanilla WoW is a 2006 engine: effectively single-threaded and extremely
cache-sensitive. On a dual-CCD X3D part -- 7950X3D, 7900X3D, 9900X3D, 9950X3D --
one CCD carries 3D V-Cache and the other does not, and the scheduler is free to
put the game on the wrong one. Pinning it to the cache-rich CCD is a real and
well-documented win for exactly this class of workload.

⭐ WHY TOPOLOGY AND NOT A MODEL NAME. Matching "X3D" in the CPU string would be
wrong for the single-CCD parts -- 7800X3D, 9800X3D -- where every core already
has the cache and there is nothing to choose between. Deriving the answer from
the L3 cache layout makes those parts fall out naturally: they report one L3
domain, so this module returns None and callers change nothing. It also means a
future part works without a catalog update.

Returns None generously. None means "do not pin", never "pin to everything".
"""

from __future__ import annotations

import glob
import os
import re
import sys
from dataclasses import dataclass

from ichalaunch.core.logging_setup import log

# A ratio alone does not identify 3D V-Cache, and getting this wrong pins the
# whole Proton tree onto a subset of cores for no reason. AMD's Strix Point parts
# (Ryzen AI 9 HX 370 / AI 9 365) pair a 16 MB Zen 5 CCX with an 8 MB Zen 5c CCX:
# a ratio of 2.0, and no V-Cache anywhere on the package. Every dual-CCD X3D part
# shipped so far -- 7900X3D, 7950X3D, 9900X3D, 9950X3D -- carries 96 MB on the
# stacked die against 32 MB on the other, so an absolute floor separates the two
# cases cleanly where a ratio cannot. Both tests must pass.
_MIN_CACHE_RATIO = 1.5
_MIN_VCACHE_BYTES = 64 * 1024 * 1024


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


def _parse_windows_cache_buffer(buf, length: int) -> list["CacheDomain"]:
    """Walk a GetLogicalProcessorInformationEx(RelationCache) buffer.

    Split out from the API call on purpose: the call itself cannot run anywhere
    but Windows, but the parsing is where the offsets can be wrong, and wrong
    offsets here read zeroes out of a reserved field rather than failing. Keeping
    this pure lets it be driven from any platform against a synthetic buffer laid
    out to the documented structure.

    Offsets are from the start of each SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX
    record and were taken from offsetof() on the real headers, not counted by
    hand::

        Relationship  +0    (LOGICAL_PROCESSOR_RELATIONSHIP)
        Size          +4    (DWORD)
        CACHE_RELATIONSHIP begins at +8:
            Level       +8   Associativity +9   LineSize +10
            CacheSize   +12  Type          +16
            Reserved[18] +20..37          GroupCount +38
            GROUP_AFFINITY +40: Mask +40 (KAFFINITY, 8 bytes), Group +48 (WORD)

    ⚠️ Reserved[18] and GroupCount sit between Type and the GROUP_AFFINITY.
    Omitting them puts Mask at +24, which is inside Reserved -- Windows zero-fills
    it, so every cache record is silently discarded and nothing ever pins.
    """
    import ctypes

    RelationCache = 2
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
        if rel == RelationCache and size >= 56:
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
        # A plain affinity mask addresses one processor group. Rather than pin
        # against a truncated mask, decline entirely.
        log.info("More than one processor group present; declining to pin.")
        return []
    return list(domains.values())


def _domains_windows() -> list["CacheDomain"]:
    """L3 domains via GetLogicalProcessorInformationEx(RelationCache)."""
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return []
    if not hasattr(ctypes, "windll"):
        return []

    RelationCache = 2
    kernel32 = ctypes.windll.kernel32
    kernel32.GetLogicalProcessorInformationEx.restype = wintypes.BOOL
    length = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(RelationCache, None,
                                              ctypes.byref(length))
    if not length.value:
        return []
    buf = (ctypes.c_byte * length.value)()
    if not kernel32.GetLogicalProcessorInformationEx(RelationCache, buf,
                                                     ctypes.byref(length)):
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
    """The 3D V-Cache CCD, or None when there is no meaningful choice to make.

    None covers every case where pinning would be wrong or pointless: a
    single-CCD part (including 7800X3D / 9800X3D), a symmetric multi-die part
    with no cache asymmetry, an unreadable topology, or a machine large enough
    to need processor groups.
    """
    domains = cache_domains()
    if len(domains) < 2:
        return None
    biggest, second = domains[0], domains[1]
    if biggest.l3_bytes < second.l3_bytes * _MIN_CACHE_RATIO:
        return None
    if biggest.l3_bytes < _MIN_VCACHE_BYTES:
        # Asymmetric, but too small to be a stacked cache die. Heterogeneous
        # core designs land here and must not be pinned.
        log.info("L3 domains differ (%.0f MB vs %.0f MB) but neither is large "
                 "enough to be 3D V-Cache; not pinning.",
                 biggest.l3_bytes / 1024 ** 2, second.l3_bytes / 1024 ** 2)
        return None
    log.info("3D V-Cache CCD detected: L3 %.0f MB on CPUs %s (other CCD %.0f MB)",
             biggest.l3_bytes / 1024 ** 2, biggest.cpu_list,
             second.l3_bytes / 1024 ** 2)
    return biggest


# --- applying the pin --------------------------------------------------------

def taskset_prefix() -> list[str]:
    """A ``taskset -c <list>`` prefix for the launch command, or [] to not pin.

    Affinity is inherited across fork/exec, so prefixing the umu-launcher
    invocation pins the client that umu eventually starts, without this module
    needing to know anything about umu's process tree.
    """
    import shutil

    domain = vcache_domain()
    if domain is None:
        return []
    if not shutil.which("taskset"):
        log.info("A V-Cache CCD was found but taskset is not installed; not pinning.")
        return []

    # ⚠️ taskset OWNS THE EXIT STATUS. It sets affinity first and only then
    # exec's the command, so if sched_setaffinity is refused the command never
    # runs and the caller sees exit 1 -- which the launcher surfaces as "Launch
    # failed". A pin that cannot be applied must degrade to an unpinned launch,
    # never to no launch, so the CPUs are checked against what this process is
    # actually permitted before taskset is involved. This mirrors the
    # intersection the Windows path already performs against the system mask.
    try:
        allowed = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        allowed = None
    if allowed is not None:
        usable = set(domain.cpus) & allowed
        if not usable:
            log.warning("The V-Cache CCD (CPUs %s) is outside this process's "
                        "permitted affinity; launching unpinned.", domain.cpu_list)
            return []
        if usable != set(domain.cpus):
            # A cpuset/cgroup restriction, or offline CPUs. Pin to what remains
            # rather than handing taskset a list it will reject outright.
            domain = CacheDomain(domain.l3_bytes, tuple(sorted(usable)))
            log.info("Some V-Cache CPUs are unavailable; pinning to %s instead.",
                     domain.cpu_list)
    return ["taskset", "-c", domain.cpu_list]


import contextlib


@contextlib.contextmanager
def launch_affinity():
    """Hold this process on the V-Cache CCD while a child is being created.

    ⭐ WHY THE PARENT AND NOT THE CHILD. Windows propagates an affinity mask to
    children created *after* it is set, and never retroactively. The process
    started here is usually not the game: vanillafixes_enabled defaults to True,
    so what gets launched is the VanillaFixes loader, and WoW.exe is its child.
    Masking the loader after Popen returns therefore races -- the loader may
    already have spawned the game -- and even when it wins it says nothing about
    the grandchild. Setting the mask on the launcher itself before Popen means
    the child inherits at creation and every descendant follows, which is exactly
    what the taskset prefix achieves on Linux.

    The launcher's own mask is restored on the way out, including on exception,
    so the UI does not spend the rest of the session on half the machine.

    A no-op on Linux (taskset already covers it), on non-X3D hardware, and
    whenever the mask cannot be read back to restore it.
    """
    domain = None if sys.platform != "win32" else vcache_domain()
    if domain is None:
        yield None
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentProcess()
        proc_mask, sys_mask = ctypes.c_size_t(0), ctypes.c_size_t(0)
        if not kernel32.GetProcessAffinityMask(handle, ctypes.byref(proc_mask),
                                               ctypes.byref(sys_mask)):
            # Without the original there is no safe way back; do not pin at all.
            log.warning("Could not read this process's affinity; not pinning.")
            yield None
            return
        original = proc_mask.value
        wanted = domain.affinity_mask & sys_mask.value
        if not wanted:
            log.warning("V-Cache CCD mask %#x is outside this process's allowed "
                        "affinity %#x; not pinning.",
                        domain.affinity_mask, sys_mask.value)
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

    log.info("Launching pinned to the V-Cache CCD (CPUs %s); descendants inherit.",
             domain.cpu_list)
    try:
        yield domain
    finally:
        try:
            kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(original))
        except Exception:
            log.warning("Could not restore this process's affinity mask.")
