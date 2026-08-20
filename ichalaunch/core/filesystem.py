"""Filesystem helpers."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path


PROTECTED_HINTS = (
    "program files",
    "program files (x86)",
    "desktop",
    "downloads",
    "documents",
)


def is_protected_path(path: str | Path) -> bool:
    p = str(path).lower().replace("/", "\\")
    return any(h in p for h in PROTECTED_HINTS)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def extract_zip(zip_path: Path, dest: Path) -> Path:
    ensure_dir(dest)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)
    children = [c for c in dest.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def safe_remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def find_toc_roots(root: Path) -> list[Path]:
    """Find addon root folders (directories containing a .toc)."""
    roots: list[Path] = []
    for toc in root.rglob("*.toc"):
        # skip libs nested deep unless they're top-level packages
        parent = toc.parent
        # prefer folders whose name matches toc stem roughly
        if parent not in roots:
            roots.append(parent)
    # Prefer shallowest roots
    roots.sort(key=lambda p: len(p.parts))
    if not roots:
        return []
    min_depth = len(roots[0].parts)
    return [r for r in roots if len(r.parts) == min_depth]


def read_dlls_txt(game_path: Path) -> list[str]:
    path = game_path / "dlls.txt"
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def write_dlls_txt(game_path: Path, dlls: list[str]) -> None:
    path = game_path / "dlls.txt"
    content = "# Managed by IchaLaunch\n" + "\n".join(dlls) + "\n"
    path.write_text(content, encoding="utf-8")


def update_dlls_txt(game_path: Path, add: list[str] | None = None, remove: list[str] | None = None) -> None:
    current = read_dlls_txt(game_path)
    add = add or []
    remove = set(remove or [])
    result = [d for d in current if d not in remove]
    for d in add:
        if d not in result:
            result.append(d)
    write_dlls_txt(game_path, result)
