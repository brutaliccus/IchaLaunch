"""Discord Rich Presence: tell the player's own Discord what they are playing.

Deliberately the smallest possible thing that works, because this is a launcher
that patches WoW.exe and replaces its own executable, and a social feature has no
business widening that blast radius.

What this is
------------
An outbound connection to a local socket that the player's own Discord client
already listens on. The launcher sends one JSON frame saying "playing
RavenCraft"; Discord's servers do the distribution.

What this is NOT, and why that matters
--------------------------------------
No listening port. No inbound connection. No account, no credential, no token.
No remote content is fetched, and nothing from the network is rendered. There is
no user-generated text anywhere in this path, so none of the injection surface
that a chat or friends-list feature would bring exists here at all.

Written without a dependency on purpose. The protocol is a handshake and one
command, and adding a package to this particular process to avoid writing a
hundred lines is a poor trade when unpinned dependencies are themselves a
documented attack path.

Rules this module keeps
-----------------------
- Opt in. Off unless the player turns it on: presence tells their friends what
  they are doing, and that is theirs to choose.
- Never blocks the launch. Every socket call is short-timeout and every failure
  is swallowed. Discord not running is the normal case, not an error.
- Never raises at the caller. A social nicety must not be able to stop the game
  starting.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time
import uuid
from dataclasses import dataclass

from ichalaunch.core.logging_setup import log

SETTING_ENABLED = "discord_rich_presence"

# Discord application id. Owns the name and art shown on the player's profile,
# so it should belong to the project or the server, not to an individual.
# Presence is silently inert until this is set, which is the right default for
# a build that has not been given one.
APPLICATION_ID = ""

_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2

# Short on purpose. This runs near launch, and a stalled socket must never be
# something the player notices.
_TIMEOUT_SEC = 1.5
_MAX_FRAME = 64 * 1024


@dataclass(frozen=True)
class Activity:
    """What to show. Plain strings only; Discord renders no markup here."""

    details: str = ""
    state: str = ""
    large_image: str = ""
    large_text: str = ""
    start_ts: int | None = None

    def payload(self) -> dict:
        act: dict = {}
        if self.details:
            act["details"] = self.details[:128]
        if self.state:
            act["state"] = self.state[:128]
        assets = {}
        if self.large_image:
            assets["large_image"] = self.large_image[:32]
        if self.large_text:
            assets["large_text"] = self.large_text[:128]
        if assets:
            act["assets"] = assets
        if self.start_ts:
            act["timestamps"] = {"start": int(self.start_ts)}
        return act


def _candidate_paths() -> list[str]:
    """Where Discord's IPC socket lives, in the order worth trying.

    Discord numbers its sockets 0 to 9 and uses the first free one, so several
    clients (stable, PTB, canary) can coexist. On Linux, Flatpak and Snap
    installs put it under their own runtime directories.
    """
    if os.name == "nt":
        return [rf"\\.\pipe\discord-ipc-{i}" for i in range(10)]
    base = (
        os.environ.get("XDG_RUNTIME_DIR")
        or os.environ.get("TMPDIR")
        or "/tmp"  # noqa: S108 - Discord's own documented fallback
    ).rstrip("/")
    roots = [base, f"{base}/app/com.discordapp.Discord", f"{base}/snap.discord"]
    return [f"{root}/discord-ipc-{i}" for root in roots for i in range(10)]


class _Connection:
    """One short-lived connection. Not kept open; presence is set and released."""

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._pipe = None

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> bool:
        for path in _candidate_paths():
            try:
                if os.name == "nt":
                    self._pipe = open(path, "r+b", buffering=0)  # noqa: SIM115
                else:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(_TIMEOUT_SEC)
                    s.connect(path)
                    self._sock = s
                return True
            except (OSError, ValueError):
                continue
        return False

    def _write(self, data: bytes) -> None:
        if self._pipe is not None:
            self._pipe.write(data)
            self._pipe.flush()
        elif self._sock is not None:
            self._sock.sendall(data)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = (
                self._pipe.read(n - len(buf))
                if self._pipe is not None
                else self._sock.recv(n - len(buf))  # type: ignore[union-attr]
            )
            if not chunk:
                raise OSError("Discord closed the connection")
            buf += chunk
        return buf

    def send(self, op: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        if len(body) > _MAX_FRAME:
            raise ValueError("presence frame too large")
        self._write(struct.pack("<II", op, len(body)) + body)

    def recv(self) -> dict:
        op, length = struct.unpack("<II", self._read_exact(8))
        if length > _MAX_FRAME:
            # Refuse to allocate whatever a misbehaving peer asks for.
            raise ValueError(f"Discord sent an oversized frame ({length} bytes)")
        data = self._read_exact(length) if length else b"{}"
        if op == _OP_CLOSE:
            raise OSError("Discord refused the connection")
        return json.loads(data.decode("utf-8", "replace"))

    def close(self) -> None:
        for closer in (self._pipe, self._sock):
            try:
                if closer is not None:
                    closer.close()
            except OSError:
                pass
        self._pipe = None
        self._sock = None


def is_enabled() -> bool:
    """True only when the player opted in and the build has an application id."""
    from ichalaunch.config.settings import settings

    return bool(settings.get(SETTING_ENABLED, False)) and bool(APPLICATION_ID)


def discord_is_running() -> bool:
    """Cheap check with no connection, for showing UI state."""
    if os.name == "nt":
        return True  # Named pipes cannot be probed without opening them.
    return any(os.path.exists(p) for p in _candidate_paths())


def set_activity(activity: Activity) -> bool:
    """Publish *activity*. Returns True when Discord accepted it.

    Never raises. Every failure path here (Discord closed, socket refused, a
    malformed reply, no application id) means the player simply does not get a
    presence, which is not worth interrupting anyone over.
    """
    if not is_enabled():
        return False
    try:
        with _Connection() as conn:
            if not conn.connect():
                return False
            conn.send(_OP_HANDSHAKE, {"v": 1, "client_id": str(APPLICATION_ID)})
            conn.recv()
            conn.send(
                _OP_FRAME,
                {
                    "cmd": "SET_ACTIVITY",
                    "args": {"pid": os.getpid(), "activity": activity.payload()},
                    "nonce": str(uuid.uuid4()),
                },
            )
            reply = conn.recv()
            if reply.get("evt") == "ERROR":
                log.debug("Discord rejected presence: %s", reply.get("data"))
                return False
            return True
    except Exception as exc:  # noqa: BLE001 - a nicety must never break launch
        log.debug("Discord presence unavailable: %s", exc)
        return False


def clear_activity() -> bool:
    """Remove the presence, e.g. when the player disables it mid-session."""
    if not APPLICATION_ID:
        return False
    try:
        with _Connection() as conn:
            if not conn.connect():
                return False
            conn.send(_OP_HANDSHAKE, {"v": 1, "client_id": str(APPLICATION_ID)})
            conn.recv()
            conn.send(
                _OP_FRAME,
                {
                    "cmd": "SET_ACTIVITY",
                    "args": {"pid": os.getpid(), "activity": None},
                    "nonce": str(uuid.uuid4()),
                },
            )
            conn.recv()
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("Discord presence clear failed: %s", exc)
        return False


def playing_activity(realm: str = "RavenCraft") -> Activity:
    """The presence shown while the client is running."""
    return Activity(
        details=f"Playing on {realm}",
        state="Launched with IchaLaunch",
        large_image="ravencraft",
        large_text=realm,
        start_ts=int(time.time()),
    )


if __name__ == "__main__":  # manual check: python -m ichalaunch.core.discord_presence
    print("discord running:", discord_is_running())
    print("application id set:", bool(APPLICATION_ID))
    print("sockets found:", [p for p in _candidate_paths() if os.path.exists(p)][:3])
    sys.exit(0)
