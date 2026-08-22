"""Backup and rollback helpers."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from ichalaunch.core.filesystem import copy_file_tolerant, ensure_dir, is_lock_or_av_error
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
            rel = Path(p.name)
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
    (backup_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return backup_root


def restore_backup(game_path: Path, backup_root: Path) -> None:
    manifest_path = backup_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Backup manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel in manifest.get("files", []):
        src = backup_root / rel
        dest = game_path / rel
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
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
