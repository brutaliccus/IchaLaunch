"""Backup and rollback helpers."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from ichalaunch.core.filesystem import (
    copy_file_tolerant,
    ensure_dir,
    is_lock_or_av_error,
    robust_rmtree,
)
from ichalaunch.core.logging_setup import log


def ichalaunch_meta_dir(game_path: Path) -> Path:
    return ensure_dir(game_path / ".ichalaunch")


def backups_dir(game_path: Path) -> Path:
    return ensure_dir(ichalaunch_meta_dir(game_path) / "backups")


def create_backup(game_path: Path, label: str, paths: list[Path]) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_root = ensure_dir(backups_dir(game_path) / f"{stamp}_{label}")
    manifest = {"label": label, "stamp": stamp, "files": []}
    for p in paths:
        try:
            if not p.exists():
                continue
        except OSError as exc:
            log.warning("Backup skipped %s: %s", p, exc)
            continue
        try:
            rel = p.relative_to(game_path)
        except ValueError:
            # A path outside game_path used to collapse to its bare NAME, so
            # restore_backup would later write to game_path/<name> and rmtree
            # whatever legitimately lived there -- backing up any folder called
            # "Interface" from elsewhere would destroy the real Interface/ on
            # restore. Refuse rather than guess.
            log.error(
                "Backup REFUSED for %s: outside the game folder %s. "
                "Restoring it would overwrite an unrelated path.", p, game_path,
            )
            continue
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p.is_dir():
                shutil.copytree(p, dest, dirs_exist_ok=True)
            elif not copy_file_tolerant(p, dest):
                log.warning("Backup skipped locked file %s", p)
                continue
        except OSError as exc:
            log.warning("Backup skipped %s: %s", p, exc)
            continue
        manifest["files"].append(str(rel).replace("\\", "/"))
    # Every failure path above is a `continue`, so a backup where nothing could
    # be copied used to return normally and look successful -- and the rollback
    # it promised would silently restore nothing. Record it.
    manifest["requested"] = len(paths)
    manifest["empty"] = not manifest["files"]
    if manifest["empty"] and paths:
        log.error(
            "Backup '%s' captured NOTHING despite %d requested path(s) -- "
            "rollback from this backup will not restore anything.", label, len(paths),
        )
    (backup_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return backup_root


def restore_backup(game_path: Path, backup_root: Path) -> None:
    manifest_path = backup_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Backup manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if not files:
        # Silently doing nothing is the worst outcome here: the caller believes
        # it rolled back. Fail loudly instead.
        raise RuntimeError(
            f"Backup at {backup_root} contains no files; there is nothing to "
            "restore. The original backup captured nothing."
        )
    for rel in files:
        src = backup_root / rel
        dest = game_path / rel
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.is_dir():
                if dest.exists():
                    robust_rmtree(dest)
                shutil.copytree(src, dest)
            elif not copy_file_tolerant(src, dest):
                log.warning("Restore skipped locked file %s", dest)
                continue
        except OSError as exc:
            if is_lock_or_av_error(exc):
                log.warning("Restore skipped %s: %s", dest, exc)
                continue
            raise


def list_backups(game_path: Path) -> list[Path]:
    root = backups_dir(game_path)
    return sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
