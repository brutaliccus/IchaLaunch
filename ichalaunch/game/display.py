"""Reading the user's real display mode, so the frame cap can follow it.

WHY THIS EXISTS. Project Reforged's setup guide prescribes a DXVK frame cap a
few frames below the monitor's refresh rate -- their own wording for a 165 Hz
panel is "162-163". That is the right rule, but shipping the literal number
would hand 163 to somebody on a 60 Hz laptop and to somebody on a 240 Hz panel
alike. Computing it from the display reproduces PR's intent on every machine
instead of on exactly one.

⛔ WHY NOT READ Config.wtf. The obvious shortcut -- take gxRefresh out of the
user's own Config.wtf -- is actively wrong. The vanilla client forces gxRefresh
to 60 whenever it runs windowed, because a windowed surface reports refresh 0.
On a verified 165.058 Hz panel the file says "60", which would compute a cap of
57 and quietly throttle the game to a third of the display. The client's crash
logs carry no display information at all either. The mode has to come from the
compositor or the OS, live.

Detection degrades to None rather than guessing. A None result means callers
leave whatever cap is already configured completely alone.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys

from ichalaunch.core.logging_setup import log

# PR's guide says 162-163 for a 165 Hz panel, i.e. two or three frames of
# headroom. Three is the safer end of their own range and is the default.
DEFAULT_CAP_OFFSET = 3

# Below this refresh rate, subtracting an offset does more harm than good -- a
# 30 Hz panel capped at 27 is a worse experience than an uncapped one.
_MIN_SENSIBLE_REFRESH = 48.0

# The offset is a couple of frames of headroom, not a free-form number, and the
# setting behind it is hand-edited. Read as "the cap" rather than "frames below
# refresh", a value like 165 would compute a 1 fps lock and persist it. Bounding
# the input is what makes that unreachable: clamping only the result would still
# let an offset of 100 produce 44 on a 144 Hz panel, which is just as wrong and
# far harder to notice.
_MAX_CAP_OFFSET = 10

_PROBE_TIMEOUT = 5


def _run(cmd: list[str]) -> str | None:
    """Run a probe command, returning stdout, or None on any failure."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _refresh_windows() -> float | None:
    """Current refresh of the primary display, via EnumDisplaySettingsW.

    Windows reports this as a whole number and commonly rounds a 165.058 Hz
    mode down to 164, which is harmless here: the cap is derived by subtracting
    an offset, so a slightly low reading stays on the safe side of the panel.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return None

    class _DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmPositionX", ctypes.c_long),
            ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", wintypes.DWORD),
            ("dmDisplayFixedOutput", wintypes.DWORD),
            ("dmColor", ctypes.c_short),
            ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    ENUM_CURRENT_SETTINGS = -1
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(_DEVMODEW)
    try:
        ok = ctypes.windll.user32.EnumDisplaySettingsW(
            None, ENUM_CURRENT_SETTINGS, ctypes.byref(mode))
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    hz = float(mode.dmDisplayFrequency)
    # 0 and 1 are documented placeholders meaning "hardware default".
    return hz if hz > 1 else None


def _refresh_kscreen() -> float | None:
    """Current refresh under KDE (Wayland or X11), via kscreen-doctor -j."""
    if not shutil.which("kscreen-doctor"):
        return None
    raw = _run(["kscreen-doctor", "-j"])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    best: float | None = None
    for out in data.get("outputs") or []:
        if not out.get("enabled"):
            continue
        current = out.get("currentModeId")
        for mode in out.get("modes") or []:
            if mode.get("id") != current:
                continue
            try:
                hz = float(mode.get("refreshRate"))
            except (TypeError, ValueError):
                continue
            # Always the fastest enabled output, never a "primary" preference.
            # libkscreen dropped the primary key after 5.24 in favour of
            # priority, so that branch is dead on all of Plasma 6 -- and where it
            # is alive it is harmful: a 60 Hz primary beside a 165 Hz gaming
            # panel would return 60 and cap the game at 57, which is the exact
            # failure this module exists to avoid. A maximum can only ever
            # produce a cap at or above what some attached panel can present, so
            # the worst case is an inert cap. _refresh_xrandr already does this.
            if best is None or hz > best:
                best = hz
    return best


def _refresh_xrandr() -> float | None:
    """Current refresh under plain X11, via xrandr's starred mode."""
    if not shutil.which("xrandr"):
        return None
    raw = _run(["xrandr", "--query"])
    if not raw:
        return None
    best: float | None = None
    for line in raw.splitlines():
        # A current mode is marked with '*', e.g. "  1920x1080  164.91*+"
        for match in re.finditer(r"(\d+\.\d+)\*", line):
            try:
                hz = float(match.group(1))
            except ValueError:
                continue
            if best is None or hz > best:
                best = hz
    return best


def detect_refresh_hz() -> float | None:
    """The live refresh rate of the user's display, or None if unknowable.

    None is a legitimate, common answer -- a headless session, an unusual
    compositor, a locked-down box. Callers must treat it as "change nothing".
    """
    probes = ((_refresh_windows,) if sys.platform == "win32"
              else (_refresh_kscreen, _refresh_xrandr))
    for probe in probes:
        try:
            hz = probe()
        except Exception:  # a probe must never take the launcher down
            log.debug("Refresh probe %s failed", probe.__name__, exc_info=True)
            continue
        if hz and hz >= _MIN_SENSIBLE_REFRESH:
            log.info("Detected display refresh %.3f Hz via %s", hz, probe.__name__)
            return hz
    log.info("Could not detect a display refresh rate; leaving any frame cap as-is")
    return None


def frame_cap_for(refresh_hz: float, offset: int = DEFAULT_CAP_OFFSET) -> int:
    """The DXVK frame cap for a panel running at *refresh_hz*.

    Floors before subtracting, deliberately. Real modes are fractional --
    165.058, 59.94 -- and rounding 59.94 up to 60 would place the cap one frame
    higher than the panel can actually present.

    The offset is coerced and bounded here as well as at the call site, because
    this is the function a future caller reaches for and the setting behind it is
    hand-editable. A non-numeric value falls back to the default rather than
    raising: this runs after DXVK's files are already on disk, and an exception
    here would roll back a completed install over a typo in a config file.
    """
    try:
        off = int(offset)
    except (TypeError, ValueError):
        off = DEFAULT_CAP_OFFSET
    return max(1, math.floor(refresh_hz) - min(max(off, 0), _MAX_CAP_OFFSET))


# --- applying the cap --------------------------------------------------------

_CAP_KEY = "d3d9.maxFrameRate"


def apply_frame_cap(conf_path, offset: int = DEFAULT_CAP_OFFSET) -> int | None:
    """Rewrite ``d3d9.maxFrameRate`` in *conf_path* to suit the live display.

    Returns the cap written, or None when nothing was changed -- either the
    refresh rate could not be read or the file is absent. A None return must
    leave the file exactly as it was: an undetectable display is not a reason
    to impose a guess on somebody's frame pacing.

    Only the one key is touched. Every other line, comment and blank survives
    unchanged, line terminators included, because this file is shared three
    ways: the bundled 2.7.1 preset, whatever the user tuned by hand, and the
    marker comment the launcher detects the installed DXVK from.
    """
    import pathlib

    path = pathlib.Path(conf_path)
    if not path.is_file():
        return None
    hz = detect_refresh_hz()
    if hz is None:
        return None
    cap = frame_cap_for(hz, offset)

    try:
        # newline="" keeps whatever terminators the file already uses. The
        # default rewrites every line to os.linesep on Windows, which DXVK
        # parses happily but which breaks the promise this function makes about
        # leaving the rest of the file alone -- and that file carries the
        # "DXVK 2.7.1" marker the launcher detects the installed DXVK from.
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            original = fh.read()
    except OSError as exc:
        log.warning("Could not read %s to set the frame cap: %s", path, exc)
        return None

    newline = "\r\n" if "\r\n" in original else "\n"
    out, replaced = [], False
    for line in original.splitlines():
        stripped = line.lstrip()
        # Rewrite the live setting; leave commented examples as documentation.
        if not stripped.startswith("#") and stripped.split("=")[0].strip() == _CAP_KEY:
            indent = line[:len(line) - len(stripped)]
            out.append(f"{indent}{_CAP_KEY} = {cap}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{_CAP_KEY} = {cap}")

    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(newline.join(out) + newline)
    except OSError as exc:
        # install_mod already wraps this call and reverts the mod on OSError, and
        # _mod_owned_paths snapshots dxvk.conf for dxvk_hd -- the rollback for
        # exactly this file exists. Swallowing the error would opt out of it and
        # leave a half-configured install with nothing in the log.
        log.warning("Could not write the frame cap to %s: %s", path, exc)
        raise
    log.info("Frame cap set to %d (display %.3f Hz, offset %d)", cap, hz, offset)
    return cap
