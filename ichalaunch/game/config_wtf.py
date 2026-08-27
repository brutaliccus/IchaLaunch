"""Read / rewrite vanilla ``WTF/Config.wtf`` CVars.

The launcher does not invent farclip values. This module only inspects the
file the client already wrote, and can clamp a stored farclip that is above
the stock 1.12 maximum (777). It can also move the whole file aside (into
``WTF/Backup``) so the client regenerates defaults on the next launch, and
restore any of those timestamped backups back into place.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
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


_BACKUP_NAME = re.compile(
    r"(?i)^Config-(?P<stamp>\d{8}-\d{6})(?:-(?P<n>\d+))?\.wtf\.bak$"
)


@dataclass(frozen=True)
class ConfigBackup:
    path: Path
    stamp: datetime
    suffix: int

    @property
    def label(self) -> str:
        text = self.stamp.strftime("%Y-%m-%d %H:%M:%S")
        return f"{text} ({self.suffix})" if self.suffix else text


def _wtf_dir(game: Path | str) -> Path:
    root = Path(game)
    return resolve_ci(root, "WTF") or (root / "WTF")


def _config_backup_dir(game: Path | str) -> Path:
    wtf_dir = _wtf_dir(game)
    return resolve_ci(wtf_dir, "Backup") or (wtf_dir / "Backup")


def list_config_backups(game: Path | str) -> list[ConfigBackup]:
    """Timestamped Config.wtf backups under ``WTF/Backup``, newest first."""
    backup_dir = _config_backup_dir(game)
    try:
        entries = list(backup_dir.iterdir())
    except OSError:
        return []
    found: list[ConfigBackup] = []
    for entry in entries:
        match = _BACKUP_NAME.match(entry.name)
        if match is None or not entry.is_file():
            continue
        try:
            stamp = datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        found.append(
            ConfigBackup(path=entry, stamp=stamp, suffix=int(match.group("n") or 0))
        )
    found.sort(key=lambda b: (b.stamp, b.suffix), reverse=True)
    return found


def restore_config_backup(game: Path | str, backup: Path | str) -> Path | None:
    """Replace ``WTF/Config.wtf`` with *backup*; the live file is saved first.

    *backup* is copied, not moved, so it stays available in ``WTF/Backup``.
    The live Config.wtf (when present) goes through
    :func:`backup_and_remove_config`, making the restore itself undoable.
    Returns the path the live file was saved to, or None when there was no
    live file. Raises FileNotFoundError when *backup* is gone.
    """
    src = Path(backup)
    if not src.is_file():
        raise FileNotFoundError(2, "Backup no longer exists", str(src))
    data = src.read_bytes()
    wtf_dir = _wtf_dir(game)
    wtf_dir.mkdir(parents=True, exist_ok=True)
    target = wtf_dir / "Config.wtf"
    # Stage inside WTF so the final os.replace is a same-volume swap; only
    # move the live file aside once the staged copy is safely on disk.
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        prior = backup_and_remove_config(game)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return prior


def backup_and_remove_config(game: Path | str) -> Path | None:
    """Move ``WTF/Config.wtf`` into ``WTF/Backup`` so the client regenerates it.

    Backups are timestamped (like ``core.backup``), so repeated regenerations
    never destroy an earlier backup and users can undo by renaming one back.
    Returns the backup path, or None when there is no Config.wtf to move.
    """
    path = config_wtf_path(game)
    if path is None:
        return None
    backup_dir = path.parent / "Backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"Config-{stamp}.wtf.bak"
    counter = 1
    # Same-second regenerations get a numeric suffix instead of overwriting.
    while backup.exists():
        backup = backup_dir / f"Config-{stamp}-{counter}.wtf.bak"
        counter += 1
    os.replace(path, backup)
    return backup


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
