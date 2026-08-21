"""Filesystem helpers."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path


PROTECTED_HINTS = (
    "program files",
    "program files (x86)",
    "desktop",
    "downloads",
    "documents",
)

# Windows CreateFile rejects these in a file name component.
_WIN_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, fallback: str = "download.bin") -> str:
    """Make a download basename safe for Windows paths.

    Strips Content-Disposition quoting, whitespace/newlines, path segments,
    and characters that trigger ``[Errno 22] Invalid argument``.
    """
    raw = (name or "").strip().strip("\"'")
    # RFC 5987 / path leftovers
    if "filename*" in raw.lower() or "filename=" in raw.lower():
        m = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", raw, re.I)
        if m:
            raw = m.group(1).strip().strip("\"'")
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = raw.split("?")[0].split("#")[0]
    raw = raw.strip().strip("\"'")
    raw = _WIN_INVALID_CHARS.sub("_", raw)
    raw = raw.rstrip(" .")
    if not raw or raw in (".", ".."):
        return fallback
    stem = Path(raw).stem
    if stem.lower() in _WIN_RESERVED:
        raw = f"_{raw}"
    return raw


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


def extract_zip(zip_source: Path | bytes | bytearray, dest: Path) -> Path:
    """Extract a zip from a path or in-memory bytes.

    Prefer ``bytes`` for archives that Windows Defender may quarantine on disk
    (e.g. VanillaFixes.zip containing injector-style DLLs) — writing the zip
    then reopening it can fail with ``[Errno 22]`` / WinError 225.
    """
    ensure_dir(dest)
    if isinstance(zip_source, (bytes, bytearray)):
        opener = zipfile.ZipFile(io.BytesIO(zip_source), "r")
    else:
        try:
            opener = zipfile.ZipFile(zip_source, "r")
        except OSError as e:
            winerr = getattr(e, "winerror", None)
            if e.errno == 22 or winerr == 225:
                raise OSError(
                    e.errno,
                    (
                        f"Windows blocked reading {getattr(zip_source, 'name', zip_source)} "
                        "(often Defender quarantining the archive). "
                        "IchaLaunch now extracts sensitive zips from memory; "
                        "update the launcher if you still see this."
                    ),
                    str(zip_source),
                ) from e
            raise
    with opener as zf:
        zf.extractall(dest)
    children = [c for c in dest.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def _make_writable(path: Path) -> None:
    """Clear the Windows read-only bit so deletes can succeed (common under .git)."""
    try:
        mode = path.stat().st_mode
        if not (mode & stat.S_IWRITE):
            os.chmod(path, mode | stat.S_IWRITE)
    except OSError:
        pass


def _make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    if root.is_file():
        _make_writable(root)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            _make_writable(Path(dirpath) / name)
        for name in dirnames:
            _make_writable(Path(dirpath) / name)
    _make_writable(root)


def _rmtree_clear_readonly(func, path, _exc=None) -> None:  # noqa: ANN001
    """``shutil.rmtree`` onerror/onexc: clear read-only and retry once."""
    _make_writable(Path(path))
    func(path)

def _remove_error_message(path: Path, exc: BaseException) -> str:
    text = str(path).replace("/", "\\").lower()
    if "\\.git\\" in text or text.endswith("\\.git"):
        tip = (
            " That folder contains a .git directory (git clone or leftover repo). "
            "Close Git/IDE tools using it, then retry — or delete the addon folder manually."
        )
    else:
        tip = (
            " Another program may be locking files (antivirus, indexer, or the game). "
            "Close them and retry, or delete the folder manually."
        )
    return f"Could not remove {path}: {exc}.{tip}"


def robust_rmtree(path: Path, *, retries: int = 4, delay: float = 0.2) -> None:
    """Remove a directory tree, clearing read-only bits and retrying brief locks.

    Windows often denies delete of ``.git/objects/pack/*.idx`` when files are
    read-only or briefly locked (Explorer, Defender, Git, IDE).
    """
    if not path.exists():
        return
    if not path.is_dir():
        _make_writable(path)
        path.unlink()
        return

    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            _make_tree_writable(path)
            kwargs: dict = {}
            if sys.version_info >= (3, 12):
                kwargs["onexc"] = _rmtree_clear_readonly
            else:
                kwargs["onerror"] = _rmtree_clear_readonly
            shutil.rmtree(path, **kwargs)
            return
        except OSError as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
                continue
            raise OSError(
                getattr(exc, "errno", None) or 13,
                _remove_error_message(path, exc),
                str(path),
            ) from exc
    if last_exc is not None:
        raise OSError(
            getattr(last_exc, "errno", None) or 13,
            _remove_error_message(path, last_exc),
            str(path),
        ) from last_exc


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        robust_rmtree(dest)
    shutil.copytree(src, dest)


def safe_remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        robust_rmtree(path)
    else:
        _make_writable(path)
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
