"""Read / rewrite vanilla ``WTF/Config.wtf`` CVars.

The launcher does not invent farclip values. This module only inspects the
file the client already wrote, and can clamp a stored farclip that is above
the stock 1.12 maximum (777).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ichalaunch.core.filesystem import resolve_ci

FARCLIP_STOCK_MAX = 777.0
FARCLIP_FIX_VALUE = 777

_FARCLIP_LINE = re.compile(
    r"(?i)^(?P<prefix>\s*SET\s+farclip\s+)"
    r"(?:\"(?P<quoted>[^\"]*)\"|(?P<bare>\S+))"
    r"(?P<suffix>.*)$"
)


@dataclass(frozen=True)
class FarclipConfig:
    path: Path
    value: float
    raw: str

    @property
    def display(self) -> str:
        return format_cvar_number(self.value)


def format_cvar_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6g}"


def config_wtf_path(game: Path | str) -> Path | None:
    """Existing ``WTF/Config.wtf`` under *game*, or None."""
    root = Path(game)
    wtf_dir = resolve_ci(root, "WTF") or (root / "WTF")
    found = resolve_ci(wtf_dir, "Config.wtf")
    if found is not None and found.is_file():
        return found
    candidate = wtf_dir / "Config.wtf"
    return candidate if candidate.is_file() else None


def parse_farclip_value(raw: str | None) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value:  # NaN
        return None
    return value


def _farclip_values_in_text(text: str) -> list[tuple[int, float, str]]:
    found: list[tuple[int, float, str]] = []
    for index, line in enumerate(text.splitlines()):
        match = _FARCLIP_LINE.match(line)
        if match is None:
            continue
        raw = match.group("quoted")
        if raw is None:
            raw = match.group("bare") or ""
        value = parse_farclip_value(raw)
        if value is None:
            continue
        found.append((index, value, raw))
    return found


def read_farclip(game: Path | str) -> FarclipConfig | None:
    """First parseable ``SET farclip`` in Config.wtf, or None."""
    path = config_wtf_path(game)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    values = _farclip_values_in_text(text)
    if not values:
        return None
    _index, value, raw = values[0]
    return FarclipConfig(path=path, value=value, raw=raw)


def farclip_too_high(
    game: Path | str,
    *,
    limit: float = FARCLIP_STOCK_MAX,
) -> FarclipConfig | None:
    """Stored farclip when it is above *limit*; otherwise None."""
    found = read_farclip(game)
    if found is None or found.value <= limit:
        return None
    return found


def set_farclip(
    game: Path | str,
    value: float = FARCLIP_FIX_VALUE,
    *,
    only_if_above: float | None = FARCLIP_STOCK_MAX,
) -> bool:
    """Rewrite high ``SET farclip`` lines. True when the file changed."""
    path = config_wtf_path(game)
    if path is None:
        return False
    data = path.read_bytes()
    newline = "\r\n" if b"\r\n" in data else "\n"
    text = data.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    changed = False
    replacement = format_cvar_number(float(value))
    out: list[str] = []
    for line in lines:
        match = _FARCLIP_LINE.match(line)
        if match is None:
            out.append(line)
            continue
        raw = match.group("quoted")
        if raw is None:
            raw = match.group("bare") or ""
        current = parse_farclip_value(raw)
        if current is None:
            out.append(line)
            continue
        if only_if_above is not None and current <= only_if_above:
            out.append(line)
            continue
        out.append(f'{match.group("prefix")}"{replacement}"{match.group("suffix")}')
        changed = True
    if not changed:
        return False
    body = newline.join(out)
    if text.endswith("\n") or text.endswith("\r\n"):
        body += newline
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(body.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return True
