"""Reachability of the RavenCraft logon host (the address in realmlist.wtf).

ICMP ping is the wrong check here: many hosts drop it, and it is not the
path the client uses. The 1.12 auth daemon listens on TCP 3724, so a timed
connect to that port is both the online/offline signal and the latency we
show next to PLAY.
"""

from __future__ import annotations

import os
import random
import re
import socket
import time
from dataclasses import dataclass

from ichalaunch.core.paths import data_file

LOGON_HOST = "logon.ravencraft.io"
LOGON_PORT = 3724
REALM_NAME = "Medivh"
CONNECT_TIMEOUT_S = 3.0
# One address per check; a second A/AAAA only if that connect fails. No
# extra retries or tight loops — this is a liveness probe, not a flood.
_MAX_ADDRESSES = 2

# Playable-quality bands for the PLAY-bar dot (TCP connect RTT).
GREEN_MAX_MS = 150
YELLOW_MAX_MS = 300

# Gentle polling: 60s + a few seconds of jitter so launchers do not sync.
PROBE_INTERVAL_MS = 60_000
PROBE_FIRST_DELAY_MS = 600
PROBE_JITTER_MS = 3_000
PROBE_BACKOFF_CAP_MS = 5 * 60 * 1000

_DISABLE_ENV = "ICHALAUNCH_NO_REALM_PING"
_REALMLIST_HOST = re.compile(
    r'(?i)^\s*SET\s+realmList\s+"([^"]+)"\s*$'
)

_cached_host: str | None = None


@dataclass(frozen=True)
class RealmProbe:
    online: bool
    latency_ms: int | None
    quality: str
    host: str
    port: int
    error: str | None = None


def realm_ping_disabled() -> bool:
    """True in smoke tests so MainWindow construction never opens a socket."""
    return (os.environ.get(_DISABLE_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def logon_host() -> str:
    """Host from the bundled realmlist, falling back to ``LOGON_HOST``."""
    global _cached_host
    if _cached_host:
        return _cached_host
    host = LOGON_HOST
    path = data_file("realmlist.wtf")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _cached_host = host
        return host
    for line in text.splitlines():
        match = _REALMLIST_HOST.match(line)
        if match:
            found = (match.group(1) or "").strip()
            if found:
                host = found
                break
    _cached_host = host
    return host


def quality_for(latency_ms: int | None, *, online: bool = True) -> str:
    """``green`` / ``yellow`` / ``red`` while reachable; ``grey`` if not."""
    if not online or latency_ms is None:
        return "grey"
    if latency_ms < GREEN_MAX_MS:
        return "green"
    if latency_ms < YELLOW_MAX_MS:
        return "yellow"
    return "red"


def next_probe_backoff_ms(*, success: bool, previous_ms: int | None = None) -> int:
    """Wait after a probe. Failures double (capped); a success resets to 60s."""
    if success:
        return PROBE_INTERVAL_MS
    prev = int(previous_ms) if previous_ms else PROBE_INTERVAL_MS
    if prev < PROBE_INTERVAL_MS:
        prev = PROBE_INTERVAL_MS
    return min(PROBE_BACKOFF_CAP_MS, prev * 2)


def jittered_probe_delay_ms(delay_ms: int, jitter_ms: int | None = None) -> int:
    """Add 0..jitter ms so many clients do not poll in lockstep."""
    span = PROBE_JITTER_MS if jitter_ms is None else max(0, int(jitter_ms))
    extra = random.randint(0, span) if span else 0
    return max(1, int(delay_ms) + extra)


def tooltip_for(probe: RealmProbe | None, *, checking: bool = False) -> str:
    """Hover copy: latency when the logon port answered, otherwise Offline."""
    if checking or probe is None:
        return "Checking…"
    if not probe.online or probe.latency_ms is None:
        return "Offline"
    return f"{probe.latency_ms} ms"


def offline_probe(
    *,
    host: str | None = None,
    port: int = LOGON_PORT,
    error: str | None = None,
) -> RealmProbe:
    target = (host or logon_host()).strip() or LOGON_HOST
    return RealmProbe(
        online=False,
        latency_ms=None,
        quality="grey",
        host=target,
        port=port,
        error=error,
    )


def probe_logon(
    host: str | None = None,
    port: int = LOGON_PORT,
    timeout: float = CONNECT_TIMEOUT_S,
    progress=None,
) -> RealmProbe:
    """Time a TCP connect to the auth port, then close. No WoW auth bytes."""
    del progress
    target = (host or logon_host()).strip() or LOGON_HOST
    try:
        infos = socket.getaddrinfo(target, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return offline_probe(host=target, port=port, error=str(exc))

    last_error: str | None = None
    tried = 0
    for family, socktype, proto, _canon, sockaddr in infos:
        if tried >= _MAX_ADDRESSES:
            break
        tried += 1
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        started = time.perf_counter()
        try:
            sock.connect(sockaddr)
            ms = max(1, int(round((time.perf_counter() - started) * 1000)))
            return RealmProbe(
                online=True,
                latency_ms=ms,
                quality=quality_for(ms),
                host=target,
                port=port,
            )
        except OSError as exc:
            last_error = str(exc) or type(exc).__name__
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return offline_probe(host=target, port=port, error=last_error)
