"""Launching a Windows game executable on Linux, via umu-launcher.

Windows runs the client directly; everywhere else it needs Proton, and the
supported way to drive Proton outside Steam is umu-launcher, which ships
Valve's own Steam Linux Runtime. This module locates umu-run and a Proton
build; it does not download or bundle either.

PINNING IS THE DEFAULT. ``PROTONPATH=GE-Proton`` asks umu for the *latest*
build, which would silently move a working install onto an untested runtime.
The first successful resolution is therefore written back to settings as an
absolute path, and honoured from then on. Tracking latest is opt-in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ichalaunch.config.settings import appdata_root, settings
from ichalaunch.core.logging_setup import log

# Where Steam-family installs keep third-party compatibility tools. Probed
# blind rather than by distro: no launcher branches on distro, and several of
# these alias the same directory (~/.steam/root and ~/.steam/steam commonly
# resolve to ~/.local/share/Steam), which is why results are deduplicated by
# device+inode rather than by string.
_COMPAT_DIR = "compatibilitytools.d"
_STEAM_ROOTS = (
    "~/.steam/root",
    "~/.steam/steam",
    "~/.steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
    "~/snap/steam/common/.local/share/Steam",
    "/usr/share/steam",
)

UMU_MISSING_MSG = (
    "umu-run was not found. Install umu-launcher (package 'umu-launcher' on "
    "Arch; a COPR on Fedora; a release from Open-Wine-Components on GitHub "
    "elsewhere), or set a full path in Settings."
)
PROTON_MISSING_MSG = (
    "No Proton build was found. Install one with ProtonUp-Qt, or via Steam, "
    "so that it appears under a compatibilitytools.d folder."
)


def find_umu_run() -> Path | None:
    """umu-run on PATH, then the usual per-user install location."""
    configured = (settings.get("linux_umu_path") or "").strip()
    if configured:
        p = Path(configured).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise FileNotFoundError(
            f"The umu-run path set in Settings is not a runnable file:\n{p}"
        )
    found = shutil.which("umu-run")
    if found:
        return Path(found)
    fallback = Path.home() / ".local/bin/umu-run"
    return fallback if fallback.is_file() else None


def _is_proton_build(d: Path) -> bool:
    """A compatibility tool umu will actually accept.

    umu refuses any PROTONPATH without a toolmanifest.vdf, so accepting a
    directory on the strength of its 'proton' script alone gets that build
    pinned and then rejected on every launch from then on.
    """
    return (d / "toolmanifest.vdf").is_file()


def proton_supports_wow64(d: Path) -> bool:
    """Whether *d* ships the 64-bit host binaries that new WoW64 needs.

    Proton's own launch script swaps its bin directory to files/bin-wow64 when
    PROTON_USE_WOW64 is set, so a build that lacks that directory cannot honour
    the flag and the launch fails outright. Builds genuinely differ --
    GE-Proton10-34 ships it, GE-Proton11-5 does not -- so this is probed per
    build rather than assumed.
    """
    return (d / "files" / "bin-wow64").is_dir()


def discover_proton_builds() -> list[Path]:
    """Every Proton build on disk, newest-looking first, deduplicated."""
    roots: list[Path] = []
    for raw in _STEAM_ROOTS:
        roots.append(Path(raw).expanduser() / _COMPAT_DIR)
    for extra in (os.environ.get("STEAM_EXTRA_COMPAT_TOOLS_PATHS") or "").split(":"):
        if extra.strip():
            roots.append(Path(extra.strip()).expanduser())

    seen: set[tuple[int, int]] = set()
    builds: list[Path] = []
    for root in roots:
        try:
            st = root.stat()
        except OSError:
            continue
        if (st.st_dev, st.st_ino) in seen:
            continue
        seen.add((st.st_dev, st.st_ino))
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for d in entries:
            if d.is_dir() and _is_proton_build(d):
                builds.append(d)
    builds.sort(key=_version_key, reverse=True)
    return builds


def _version_key(path: Path) -> tuple[int, list[int], str]:
    """Sort newest-first by the numbers in the name.

    A build whose name carries no digits -- 'Proton-GE Latest', which some
    launchers materialise as a real directory -- sorts last, so automatic
    selection never lands on a moving target.
    """
    nums = [int(n) for n in re.findall(r"\d+", path.name)]
    # Shorter name wins a numeric tie, so 'GE-Proton10-34 (copy)' never
    # outranks 'GE-Proton10-34'.
    return (1 if nums else 0, nums, -len(path.name), path.name.lower())


def resolve_proton_path() -> Path | None:
    """The Proton build to use, pinning the choice the first time it is made."""
    pinned = (settings.get("linux_proton_path") or "").strip()
    if pinned and not settings.get("linux_use_latest_proton", False):
        p = Path(pinned).expanduser()
        if _is_proton_build(p):
            return p
        # Say which build vanished rather than silently substituting another.
        log.warning("Pinned Proton build is missing: %s", p)

    builds = discover_proton_builds()
    if not builds:
        return None
    chosen = builds[0]
    if not settings.get("linux_use_latest_proton", False):
        try:
            settings.set("linux_proton_path", str(chosen))
        except OSError as exc:
            # The pin is a convenience; a settings write failure must not
            # stop a launch that would otherwise work.
            log.warning("Could not pin Proton build: %s", exc)
        else:
            log.info("Pinned Proton build: %s", chosen)
    return chosen


def wineprefix_for(game_dir: Path) -> Path:
    """Launcher-owned prefix, so no assumption is made about the user's layout."""
    configured = (settings.get("linux_wineprefix") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return appdata_root() / "prefixes" / game_dir.name


def build_launch_command(exe: Path, cwd: Path) -> tuple[list[str], dict[str, str]]:
    """argv and environment for running *exe* under umu. Raises if unusable."""
    umu = find_umu_run()
    if umu is None:
        raise FileNotFoundError(UMU_MISSING_MSG)
    proton = resolve_proton_path()
    if proton is None:
        raise FileNotFoundError(PROTON_MISSING_MSG)

    prefix = wineprefix_for(cwd)
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Could not create the Wine prefix at:\n{prefix}\n\n{exc}\n\n"
            "Pick a writable location in Settings."
        ) from exc

    env = dict(os.environ)
    # A launch has to be reproducible, so drop the variables that would
    # silently choose a different runtime or a different set of DLLs than the
    # one being pinned. UMU_NO_PROTON replaces Proton outright, PROTON_VERB
    # can turn the launch into a no-wait shim, and WINEDLLOVERRIDES would
    # fight the DLL chain the client ships (DXVK's d3d9 among them).
    for name in (
        "LD_PRELOAD",
        "PROTON_NO_ESYNC",
        "PROTON_NO_FSYNC",
        "PROTON_USE_WINED3D",
        "PROTON_USE_WOW64",
        "PROTON_VERB",
        "SDL_VIDEODRIVER",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH",
        "STEAM_COMPAT_DATA_PATH",
        "UMU_NO_PROTON",
        "UMU_RUNTIME_UPDATE",
        "WINEDLLOVERRIDES",
    ):
        env.pop(name, None)

    env.update({
        "WINEPREFIX": str(prefix),
        "PROTONPATH": str(proton),
        "GAMEID": "umu-default",
        "STORE": "none",
    })

    # New WoW64 runs the 32-bit client inside a 64-bit host process, which moves
    # Wine's own libraries, the Vulkan loader and DXVK's host-side allocations
    # out of the application's 4 GB of address space. The client stays 32-bit and
    # its own ceiling is unchanged; what this buys is that the ceiling stops
    # being shared with the translation layer. Measured on a vanilla WoW client
    # with ~11 GB of texture packs: peak use of the low 4 GB was 48%.
    if settings.get("linux_use_wow64", False):
        if proton_supports_wow64(proton):
            env["PROTON_USE_WOW64"] = "1"
            log.info("New WoW64 requested and supported by %s", proton.name)
        else:
            # Setting it anyway would break the launch rather than degrade it.
            log.warning(
                "New WoW64 is enabled in Settings but %s has no "
                "files/bin-wow64; launching in the default mode instead.",
                proton.name,
            )
    return [str(umu), str(exe)], env


def launch_log_path() -> Path:
    """Where umu's own output is kept, so a failed launch can be explained."""
    return appdata_root() / "logs" / "umu-launch.log"


def launch_log_tail(limit: int = 1200) -> str:
    """The end of the last launch log, for showing the user what went wrong."""
    try:
        data = launch_log_path().read_bytes()[-limit:]
    except OSError:
        return ""
    return data.decode("utf-8", "replace").strip()


def launch_windows_exe(exe: Path, cwd: Path) -> subprocess.Popen:
    argv, env = build_launch_command(exe, cwd)
    log.info("Launching via umu: %s (proton=%s)", exe, env.get("PROTONPATH"))

    # umu reports a bad Proton build, an unusable prefix or a failed runtime
    # download on stderr, which goes nowhere a windowed launcher can show. Send
    # it to a file rather than a pipe: nothing here drains a pipe, and umu is
    # chatty enough on first run to fill one and stall the child behind it.
    sink = None
    try:
        path = launch_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = path.open("wb")
    except OSError as exc:
        log.warning("Could not open the umu launch log (%s); output is lost", exc)

    try:
        if sink is None:
            return subprocess.Popen(argv, cwd=str(cwd), env=env, shell=False)
        return subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            shell=False,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    finally:
        if sink is not None:
            # The child holds its own descriptor; this one has done its job.
            sink.close()


def is_linux() -> bool:
    return sys.platform != "win32"
