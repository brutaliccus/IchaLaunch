"""Read the live display mode so the DXVK frame cap can follow it.

Project Reforged's setup guide wants a cap a few frames under refresh
("162-163" for a 165 Hz panel). Shipping 1000 (or a hardcoded 163) is that
rule applied to one machine. Config.wtf is the wrong source: windowed vanilla
forces gxRefresh to 60, so a 165 Hz panel would compute 57.

Detection degrades to None rather than guessing. None means callers leave
whatever cap is already configured alone. Where several outputs are attached
the fastest wins -- a 60 Hz "primary" beside a 165 Hz game panel must not
lock the game at 57.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys

from ichalaunch.core.logging_setup import log

DEFAULT_CAP_OFFSET = 3
_MIN_SENSIBLE_REFRESH = 48.0
_MAX_CAP_OFFSET = 10
_PROBE_TIMEOUT = 5
_CAP_KEY = "d3d9.maxFrameRate"

# Untouched installs resolve to this. Kept out of DEFAULTS so save() does not
# bake the default into every settings.json (see proton.WOW64_DEFAULT_ON).
FRAME_CAP_DEFAULT_ON = True


def frame_cap_enabled() -> bool:
    """Whether to compute the cap from the display, resolving unset to the default."""
    from ichalaunch.config.settings import settings

    stored = settings.get("frame_cap_from_refresh", None)
    return FRAME_CAP_DEFAULT_ON if stored is None else bool(stored)


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _devmode_w():
    import ctypes
    from ctypes import wintypes

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

    return _DEVMODEW


def _hz_or_none(raw: float) -> float | None:
    # 0 and 1 are documented placeholders meaning "hardware default".
    return raw if raw > 1 else None


def _refresh_from_devmode(user32, device_name) -> float | None:
    import ctypes

    enum_current_settings = -1
    mode = _devmode_w()()
    mode.dmSize = ctypes.sizeof(mode)
    try:
        ok = user32.EnumDisplaySettingsW(
            device_name, enum_current_settings, ctypes.byref(mode)
        )
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return _hz_or_none(float(mode.dmDisplayFrequency))


def _best_refresh(candidates: list[float]) -> float | None:
    usable = [hz for hz in candidates if hz >= _MIN_SENSIBLE_REFRESH]
    return max(usable) if usable else None


def _refresh_windows() -> float | None:
    """Fastest attached display via EnumDisplayDevicesW + EnumDisplaySettingsW.

    EnumDisplaySettingsW(None) only sees the primary. A 60 Hz primary beside a
    165 Hz game panel is the exact failure this module exists to avoid, so every
    attached adapter (and its monitors) is walked and the maximum wins.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return None
    if not hasattr(ctypes, "windll"):
        return None

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    attached = 0x00000001
    mirroring = 0x00000008
    user32 = ctypes.windll.user32
    found: list[float] = []

    adapter_index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(adapter)
        if not user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        if adapter.StateFlags & mirroring:
            continue
        if not (adapter.StateFlags & attached):
            continue
        hz = _refresh_from_devmode(user32, adapter.DeviceName)
        if hz:
            found.append(hz)
        monitor_index = 0
        while True:
            monitor = _DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(monitor)
            if not user32.EnumDisplayDevicesW(
                adapter.DeviceName, monitor_index, ctypes.byref(monitor), 0
            ):
                break
            monitor_index += 1
            if not (monitor.StateFlags & attached):
                continue
            hz = _refresh_from_devmode(user32, monitor.DeviceName)
            if hz:
                found.append(hz)

    best = _best_refresh(found)
    if best is not None:
        return best
    return _refresh_from_devmode(user32, None)


def parse_kscreen_refresh(raw: str) -> float | None:
    """Fastest enabled output in a kscreen-doctor -j payload."""
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
            if best is None or hz > best:
                best = hz
    return best


def _refresh_kscreen() -> float | None:
    if not shutil.which("kscreen-doctor"):
        return None
    raw = _run(["kscreen-doctor", "-j"])
    return parse_kscreen_refresh(raw) if raw else None


def parse_xrandr_refresh(raw: str) -> float | None:
    """Fastest starred current mode in ``xrandr --query`` output.

    Accepts both ``164.91*`` and integer ``144*`` -- some drivers omit the
    decimal. The preferred-mode ``+`` marker is ignored; only ``*`` is current.
    """
    best: float | None = None
    for match in re.finditer(r"(\d+(?:\.\d+)?)\*", raw):
        try:
            hz = float(match.group(1))
        except ValueError:
            continue
        if best is None or hz > best:
            best = hz
    return best


def _refresh_xrandr() -> float | None:
    if not shutil.which("xrandr"):
        return None
    raw = _run(["xrandr", "--query"])
    return parse_xrandr_refresh(raw) if raw else None


def detect_refresh_hz() -> float | None:
    """Live refresh of the user's fastest attached display, or None."""
    probes = (
        (_refresh_windows,)
        if sys.platform == "win32"
        else (_refresh_kscreen, _refresh_xrandr)
    )
    for probe in probes:
        try:
            hz = probe()
        except Exception:
            log.debug("Refresh probe %s failed", probe.__name__, exc_info=True)
            continue
        if hz and hz >= _MIN_SENSIBLE_REFRESH:
            log.info("Detected display refresh %.3f Hz via %s", hz, probe.__name__)
            return hz
    log.info("Could not detect a display refresh rate; leaving any frame cap as-is")
    return None


def frame_cap_for(refresh_hz: float, offset=DEFAULT_CAP_OFFSET) -> int:
    """DXVK frame cap for a panel running at *refresh_hz*.

    Floors before subtracting so 59.94 does not become a 60 Hz cap. The offset
    is coerced and bounded here: a hand-edited 165 must not compute a 1 fps lock.
    A non-numeric value falls back to the default instead of raising -- this
    runs after DXVK is already on disk, and an exception would roll back a
    successful install.
    """
    try:
        off = int(offset)
    except (TypeError, ValueError):
        off = DEFAULT_CAP_OFFSET
    return max(1, math.floor(refresh_hz) - min(max(off, 0), _MAX_CAP_OFFSET))


def apply_frame_cap(
    conf_path,
    offset: int = DEFAULT_CAP_OFFSET,
    *,
    raise_on_write_error: bool = True,
) -> int | None:
    """Rewrite ``d3d9.maxFrameRate`` in *conf_path* to suit the live display.

    Returns the cap, or None when nothing was changed (missing file, unknown
    refresh). Only the live key is touched; comments stay comments; the file's
    own line terminators are kept so the DXVK 2.7.1 marker survives.

    A write that would not change the file is skipped. Write failures re-raise
    by default so install_mod can roll back; pass raise_on_write_error=False
    from the launch path so a locked conf cannot block PLAY.
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
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            original = fh.read()
    except OSError as exc:
        log.warning("Could not read %s to set the frame cap: %s", path, exc)
        return None

    newline = "\r\n" if "\r\n" in original else "\n"
    out: list[str] = []
    replaced = False
    already = False
    for line in original.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#") and stripped.split("=")[0].strip() == _CAP_KEY:
            indent = line[: len(line) - len(stripped)]
            current = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            if current == str(cap):
                already = True
            out.append(f"{indent}{_CAP_KEY} = {cap}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{_CAP_KEY} = {cap}")
    new_body = newline.join(out) + newline
    if already and new_body == original:
        return cap

    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_body)
    except OSError as exc:
        log.warning("Could not write the frame cap to %s: %s", path, exc)
        if raise_on_write_error:
            raise
        return None
    log.info("Frame cap set to %d (display %.3f Hz, offset %d)", cap, hz, offset)
    return cap
