"""Filesystem helpers."""

from __future__ import annotations

import getpass
import hashlib
import io
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("ichalaunch")


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


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str | None:
    """Content hash. Reads bytes only — never LoadLibrary. None if locked/missing.

    Opening injector-style game DLLs (VanillaHelpers, nampower, …) can trip
    Defender (WinError 5/32/225). Callers must treat None as skip, not crash.
    """
    if should_skip_locked_path(path):
        _log.warning("Skipping hash of locked file %s", path)
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(path)
            _log.warning("Could not hash %s: %s", path, exc)
        return None
    return h.hexdigest()


def _zip_member_is_safe(dest: Path, filename: str) -> bool:
    """Reject zip entries that would extract outside *dest* (zip-slip)."""
    rel = (filename or "").replace("\\", "/")
    if not rel or rel.startswith("/") or rel.startswith("../") or "/../" in f"/{rel}":
        dest_res = dest.resolve()
        target = (dest / rel.lstrip("/")).resolve()
        try:
            target.relative_to(dest_res)
        except ValueError:
            return False
        return True
    dest_res = dest.resolve()
    target = (dest / rel).resolve()
    try:
        target.relative_to(dest_res)
        return True
    except ValueError:
        return False


def _report_extract_progress(progress: Any | None, done: int, total: int) -> None:
    """Determinate extract percent. Never call progress() — that resets to bounce."""
    if progress is None or total <= 0:
        return
    on_count = getattr(progress, "on_count", None)
    pct = max(0, min(100, int(done * 100 / total)))
    msg = f"Extracting… {pct}%"
    if callable(on_count):
        on_count(done, total, msg)
        return
    on_bytes = getattr(progress, "on_bytes", None)
    if callable(on_bytes):
        set_status = getattr(progress, "set_status", None)
        if callable(set_status):
            set_status("Extracting…")
        on_bytes(done, total)


def extract_zip(
    zip_source: Path | bytes | bytearray,
    dest: Path,
    progress: Any | None = None,
) -> Path:
    """Extract a zip from a path or in-memory bytes.

    Prefer ``bytes`` for archives that Windows Defender may quarantine on disk
    (e.g. VanillaFixes.zip containing injector-style DLLs) — writing the zip
    then reopening it can fail with ``[Errno 22]`` / WinError 225.

    When *progress* is a StatusProgress-like object, members are extracted one
    by one and ``on_count`` reports uncompressed-byte percent (determinate).
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
        if progress is None:
            zf.extractall(dest)
        else:
            dest_res = dest.resolve()
            members = zf.infolist()
            total_bytes = sum(max(0, int(info.file_size or 0)) for info in members)
            total_members = len(members)
            use_bytes = total_bytes > 0
            total = total_bytes if use_bytes else total_members
            done = 0
            last_pct = -1
            _report_extract_progress(progress, 0, total or 1)
            for i, info in enumerate(members, start=1):
                name = info.filename or ""
                if not _zip_member_is_safe(dest_res, name):
                    done += max(0, int(info.file_size or 0)) if use_bytes else 1
                    continue
                zf.extract(info, dest)
                done += max(0, int(info.file_size or 0)) if use_bytes else 1
                pct = max(0, min(100, int(done * 100 / total))) if total else 100
                if pct != last_pct or i == total_members:
                    last_pct = pct
                    _report_extract_progress(progress, done if use_bytes else i, total or 1)
    children = [c for c in dest.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def ensure_writable(path: Path | str) -> None:
    """Clear the Windows read-only bit so overwrites can succeed."""
    try:
        p = Path(path)
        mode = p.stat().st_mode
        if not (mode & stat.S_IWRITE):
            os.chmod(p, mode | stat.S_IWRITE)
    except OSError:
        pass


def _is_under_data(path: Path, game_path: Path) -> bool:
    try:
        path.resolve().relative_to((game_path / "Data").resolve())
        return True
    except ValueError:
        return False


def ensure_data_writable(path: Path | str, game_path: Path | str) -> None:
    """Clear read-only only for paths under ``{game}/Data/`` (MPQs, GlueXML, etc.)."""
    p = Path(path)
    game = Path(game_path)
    if _is_under_data(p, game):
        ensure_writable(p)


def _make_writable(path: Path) -> None:
    """Alias for internal callers — prefer :func:`ensure_writable` in new code."""
    ensure_writable(path)


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


def is_access_denied(exc: BaseException) -> bool:
    """True for Windows ERROR_ACCESS_DENIED (5) / POSIX EACCES/EPERM."""
    if getattr(exc, "winerror", None) == 5:
        return True
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "errno", None) in (5, 13)


# ERROR_ACCESS_DENIED=5, SHARING_VIOLATION=32, LOCK_VIOLATION=33,
# ERROR_VIRUS_INFECTED=225 (Python often maps this to errno 22 / EINVAL).
_WIN_LOCK_OR_AV = frozenset({5, 32, 33, 225})
_POSIX_LOCK_OR_AV = frozenset({5, 11, 13, 16, 22})  # EACCES, EAGAIN, EACCES, EBUSY, EINVAL
_SKIP_UNTIL: dict[str, float] = {}
_DLL_BACKOFF_SEC = 90.0
_DIR_LIST_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
_DIR_LIST_TTL = 4.0


def is_lock_or_av_error(exc: BaseException) -> bool:
    """True for Defender / sharing locks that must not abort the Qt event loop."""
    if getattr(exc, "winerror", None) in _WIN_LOCK_OR_AV:
        return True
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "errno", None) in _POSIX_LOCK_OR_AV


def _norm_path_key(path: Path | str) -> str:
    return str(path).replace("/", "\\").lower()


def should_skip_locked_path(path: Path | str) -> bool:
    """True while a prior lock/AV error on *path* is still backing off."""
    return time.monotonic() < _SKIP_UNTIL.get(_norm_path_key(path), 0.0)


def mark_path_locked(path: Path | str, seconds: float = _DLL_BACKOFF_SEC) -> None:
    """Do not retry this file for *seconds* (avoids a 2s crash/retry loop)."""
    _SKIP_UNTIL[_norm_path_key(path)] = time.monotonic() + max(1.0, float(seconds))


def clear_path_locked(path: Path | str) -> None:
    """Drop the lock backoff for *path* (call after the file is confirmed gone)."""
    _SKIP_UNTIL.pop(_norm_path_key(path), None)


def invalidate_dir_listing(directory: Path | str) -> None:
    """Drop the cached listdir for *directory* after files are added/removed.

    Without this, detect_actual_state can see a just-deleted patch MPQ for up
    to _DIR_LIST_TTL seconds and re-flag the mod as installed (nag loop).
    """
    _DIR_LIST_CACHE.pop(_norm_path_key(directory), None)


def clear_fs_caches() -> None:
    """Test helper — drop listdir + lock-backoff caches."""
    _DIR_LIST_CACHE.clear()
    _SKIP_UNTIL.clear()


def listed_basenames(directory: Path) -> frozenset[str] | None:
    """Lowercase names in *directory* via listdir — does not open files.

    Prefer this over per-file ``exists()``/``stat()`` on injector-style DLLs
    (VanillaHelpers.dll): first open can trip Defender and kill the launcher.
    Returns None if the folder cannot be listed.
    """
    key = _norm_path_key(directory)
    now = time.monotonic()
    cached = _DIR_LIST_CACHE.get(key)
    if cached and now < cached[0]:
        return cached[1]
    try:
        names = frozenset(n.lower() for n in os.listdir(directory))
    except FileNotFoundError:
        empty: frozenset[str] = frozenset()
        _DIR_LIST_CACHE[key] = (now + _DIR_LIST_TTL, empty)
        return empty
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(directory)
            _log.warning("Could not list %s: %s", directory, exc)
        return None
    _DIR_LIST_CACHE[key] = (now + _DIR_LIST_TTL, names)
    return names


def name_present(
    directory: Path,
    name: str,
    listing: frozenset[str] | None = None,
) -> bool:
    """Case-insensitive presence check. Never LoadLibrary. Never raises.

    Locked/AV-blocked files are treated as **present** so we do not reinstall
    or re-hash them every UI refresh.
    """
    raw = (name or "").strip().strip("\"'")
    if not raw:
        return False
    needle = Path(raw.replace("\\", "/")).name.lower()
    if not needle or needle in {".", ".."}:
        return False
    dest = directory / needle
    if should_skip_locked_path(dest):
        return True
    names = listing if listing is not None else listed_basenames(directory)
    if names is not None:
        return needle in names
    try:
        return (directory / raw).exists()
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(dest)
            return True
        return False


def copy_file_tolerant(src: Path, dest: Path) -> bool:
    """``copy2`` without mapping *dest* as a DLL. False on lock/AV (does not raise)."""
    if should_skip_locked_path(src) or should_skip_locked_path(dest):
        _log.warning("Skipping copy (backoff) %s → %s", src, dest)
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(src)
            mark_path_locked(dest)
            _log.warning("Lock/AV skipped copy %s → %s: %s", src, dest, exc)
            return False
        raise


def _park_or_remove(dest: Path) -> None:
    """Clear *dest* so a move can use that name. Parks to a unique sibling if delete fails."""
    if not dest.exists():
        return
    try:
        robust_rmtree(dest)
        return
    except OSError:
        parked = dest.with_name(f".{dest.name}.__old_{os.getpid()}_{time.time_ns()}")
        shutil.move(str(dest), str(parked))
        try:
            robust_rmtree(parked)
        except OSError:
            pass


def _copy_then_remove(src: Path, dest: Path) -> None:
    if dest.exists():
        _park_or_remove(dest)
    shutil.copytree(src, dest)
    robust_rmtree(src)


def robust_move_tree(
    src: Path,
    dest: Path,
    *,
    retries: int = 3,
    delay: float = 0.15,
) -> str:
    """Move a directory tree. Returns the strategy that succeeded.

    Fallback chain (each attempt): ``rename`` → ``shutil.move`` → copytree+rmtree.
    Retries the chain on access-denied (WinError 5) after clearing read-only bits.
    """
    import gc

    src = Path(src)
    dest = Path(dest)
    if not src.is_dir():
        raise FileNotFoundError(f"Source folder not found: {src}")
    try:
        if src.resolve() == dest.resolve():
            return "already"
    except OSError:
        pass

    dest.parent.mkdir(parents=True, exist_ok=True)
    gc.collect()

    last_exc: BaseException | None = None
    for attempt in range(max(1, retries)):
        _make_tree_writable(src)
        try:
            _park_or_remove(dest)
        except OSError as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay)
                continue
            raise

        strategies = (
            ("rename", lambda: src.rename(dest)),
            ("shutil.move", lambda: shutil.move(str(src), str(dest))),
            ("copytree", lambda: _copy_then_remove(src, dest)),
        )
        for strategy, fn in strategies:
            if not src.is_dir():
                break
            if dest.exists() and strategy != "copytree":
                try:
                    _park_or_remove(dest)
                except OSError as exc:
                    last_exc = exc
                    continue
            try:
                fn()
            except OSError as exc:
                last_exc = exc
                continue
            if dest.exists() and not src.exists():
                return strategy

        if attempt + 1 < retries:
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise OSError("Could not move folder")


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        robust_rmtree(dest)
    shutil.copytree(src, dest)


def safe_remove(path: Path) -> None:
    try:
        if not path.exists():
            clear_path_locked(path)
            invalidate_dir_listing(path.parent)
            return
        if path.is_dir():
            robust_rmtree(path)
        else:
            _make_writable(path)
            path.unlink()
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(path)
            _log.warning("Could not remove locked %s: %s", path, exc)
            return
        raise
    clear_path_locked(path)
    invalidate_dir_listing(path.parent)


def remove_path_strict(path: Path) -> None:
    """Delete *path*; raise a clear OSError naming the file when it is locked.

    Unlike ``safe_remove``, lock/AV errors (WinError 5/32/225) are NOT swallowed —
    mod removal must either delete the file or tell the user which file is stuck,
    otherwise detect keeps seeing the mod as installed and the UI loops.
    """
    try:
        if not path.exists():
            clear_path_locked(path)
            invalidate_dir_listing(path.parent)
            return
        if path.is_dir():
            robust_rmtree(path)
        else:
            _make_writable(path)
            path.unlink()
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(path)
            winerr = getattr(exc, "winerror", None)
            code = f"WinError {winerr}" if winerr else f"errno {getattr(exc, 'errno', '?')}"
            raise OSError(
                getattr(exc, "errno", None) or 13,
                (
                    f"Could not remove {path.name} — the file is locked or blocked ({code}). "
                    "Close the game, file previews, or antivirus scans using it, then Apply again."
                ),
                str(path),
            ) from exc
        raise
    clear_path_locked(path)
    invalidate_dir_listing(path.parent)


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


def dlls_txt_paths(game_path: Path) -> list[Path]:
    """VanillaFixes reads ``{game}/dlls.txt``; some installs keep a copy under ``.ichalaunch``."""
    game_path = Path(game_path)
    return [game_path / "dlls.txt", game_path / ".ichalaunch" / "dlls.txt"]


def _dll_basename(entry: str) -> str:
    text = (entry or "").strip()
    if not text or text.startswith("#"):
        return ""
    if "#" in text:
        text = text.split("#", 1)[0].strip()
    text = text.strip("\"'").strip()
    if not text:
        return ""
    return Path(text.replace("\\", "/")).name


def parse_dlls_txt_text(text: str) -> list[str]:
    """Active (uncommented, non-blank) DLL names. Never opens the DLLs themselves."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        s = (line or "").strip()
        if not s or s.startswith("#"):
            continue
        name = _dll_basename(s)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def read_dlls_txt(game_path: Path) -> list[str]:
    """Parse game ``dlls.txt`` (and ``.ichalaunch/dlls.txt``). Skips comments/blanks.

    Missing or locked list files return ``[]`` — never crash.
    """
    names: list[str] = []
    seen: set[str] = set()
    for path in dlls_txt_paths(game_path):
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            _log.warning("Could not read %s: %s", path, exc)
            continue
        for name in parse_dlls_txt_text(text):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def validate_pe_binary(path: Path, *, min_size: int = 1024) -> None:
    """Reject truncated or non-PE downloads before they land in the game folder."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise OSError(
            getattr(exc, "errno", None) or 13,
            f"Could not verify {p.name}: {exc}",
            str(p),
        ) from exc
    if size < min_size:
        raise OSError(
            22,
            f"{p.name} looks truncated ({size} bytes; expected at least {min_size})",
            str(p),
        )
    try:
        with p.open("rb") as f:
            magic = f.read(2)
    except OSError as exc:
        raise OSError(
            getattr(exc, "errno", None) or 13,
            f"Could not read {p.name} for verification: {exc}",
            str(p),
        ) from exc
    if magic != b"MZ":
        raise OSError(
            22,
            f"{p.name} is not a valid Windows PE file (missing MZ header)",
            str(p),
        )


def write_dlls_txt(game_path: Path, dlls: list[str]) -> None:
    path = Path(game_path) / "dlls.txt"
    content = "# Managed by IchaLaunch\n" + "\n".join(dlls) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        _log.warning("Could not write %s: %s", path, exc)


def update_dlls_txt(
    game_path: Path,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    *,
    dlls_path: Path | None = None,
) -> None:
    """Add/remove active entries while preserving comments and blank lines."""
    path = dlls_path if dlls_path is not None else Path(game_path) / "dlls.txt"
    had_file = path.is_file()
    read_ok = False
    raw_lines: list[str] = []
    if had_file:
        try:
            raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            read_ok = True
        except OSError as exc:
            _log.warning("Could not read %s: %s", path, exc)
            raw_lines = []

    remove_l = {_dll_basename(x).lower() for x in (remove or []) if _dll_basename(x)}
    add_list = [_dll_basename(x) for x in (add or []) if _dll_basename(x)]

    if remove_l and had_file and not read_ok:
        _log.warning("Skipping dlls.txt update — could not read existing file")
        return
    if remove_l and not had_file and not add_list:
        return
    present_l: set[str] = set()
    kept: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        name = _dll_basename(stripped)
        key = name.lower()
        if not name or key in remove_l:
            continue
        present_l.add(key)
        kept.append(line)
    for d in add_list:
        key = d.lower()
        if key in present_l or key in remove_l:
            continue
        kept.append(d)
        present_l.add(key)
    if not any(x.strip().startswith("#") for x in kept):
        kept.insert(0, "# Managed by IchaLaunch")
    try:
        path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        _log.warning("Could not write %s: %s", path, exc)


def mirror_dlls_txt_updates(
    game_path: Path,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    """Apply the same dlls.txt add/remove to ``.ichalaunch/dlls.txt`` when present."""
    mirror = Path(game_path) / ".ichalaunch" / "dlls.txt"
    if not mirror.is_file():
        return
    if not (add or remove):
        return
    update_dlls_txt(game_path, add=add, remove=remove, dlls_path=mirror)


# --- Game folder permissions (Windows) ---------------------------------------

GAME_PERMISSION_SUBDIRS = ("Data", "WTF", "Interface")
_SUBPROC_FLAGS = (
    subprocess.CREATE_NO_WINDOW
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
    else 0
)
_ICACLS_WRITE_RIGHTS = frozenset({"M", "F", "W", "WD", "WDEX", "GW", "GE", "GM", "FULL", "MODIFY", "WRITE", "CHANGE"})


@dataclass
class PermissionIssue:
    rel: str
    kind: str
    detail: str


def protected_location_guidance(game: Path | str) -> str:
    """Plain-language advice when the game folder is in a restricted Windows path."""
    return (
        f"Your game folder is in a restricted Windows location:\n{game}\n\n"
        "Folders like Downloads, Desktop, Documents, and Program Files often "
        "block the client from saving configs, mods, and patches — a common "
        "cause of access-denied crashes.\n\n"
        "Move the entire game folder to a location you own, for example:\n"
        "  C:\\Games\\TurtleWoW\n"
        "  D:\\Games\\RavenCraft\n\n"
        "Then use Browse on the Home or Settings page to select the new folder, "
        "and run Check Game Permissions again."
    )


@dataclass
class PermissionScanResult:
    game: Path
    issues: list[PermissionIssue] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    needs_elevation: bool = False
    protected_path: bool = False

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    @property
    def can_auto_fix(self) -> bool:
        return self.has_issues and not self.protected_path

    def user_message(self, *, max_issues: int = 6) -> str:
        if self.protected_path:
            lines = [
                "Your game folder is in a restricted Windows location "
                "(Downloads, Desktop, Documents, or Program Files).",
                "",
                "Games copied or extracted here often keep restrictive permissions "
                "that cause access-denied crashes. IchaLaunch cannot fully fix "
                "permissions in these folders.",
                "",
                "Move the entire game folder to a normal location you own, for example:",
                "• C:\\Games\\TurtleWoW",
                "• D:\\Games\\RavenCraft",
                "",
                "Then browse to the new folder in Settings and run "
                "Check Game Permissions again.",
                "",
                f"Current folder:\n{self.game}",
            ]
            if self.issues:
                lines.extend(["", "Problems found:"])
                for issue in self.issues[:max_issues]:
                    lines.append(f"• {issue.rel}: {issue.detail}")
                if len(self.issues) > max_issues:
                    lines.append(f"• …and {len(self.issues) - max_issues} more")
            return "\n".join(lines)

        lines = [
            "Some game files or folders may block the client from writing saves, "
            "config, or patches — a common cause of access-denied crashes.",
            "",
        ]
        if self.hints:
            lines.extend(self.hints)
            lines.append("")
        lines.append("Problems found:")
        for issue in self.issues[:max_issues]:
            lines.append(f"• {issue.rel}: {issue.detail}")
        if len(self.issues) > max_issues:
            lines.append(f"• …and {len(self.issues) - max_issues} more")
        lines.append("")
        lines.append(
            "This often happens when the game was copied from Downloads or extracted "
            "with restrictive permissions."
        )
        lines.append(
            "IchaLaunch can grant your Windows user Modify access and clear read-only flags."
        )
        if self.needs_elevation:
            lines.append("")
            lines.append(
                "Some items may still need Administrator approval to repair fully."
            )
        return "\n".join(lines)


@dataclass
class PermissionFixResult:
    game: Path
    fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_elevation: bool = False


def iter_game_permission_targets(game: Path | str) -> list[Path]:
    """Key game paths checked for permission problems (WoW.exe is intentionally skipped)."""
    root = Path(game)
    targets: list[Path] = []
    if root.is_dir():
        targets.append(root)
    for name in GAME_PERMISSION_SUBDIRS:
        sub = root / name
        if sub.exists():
            targets.append(sub)
    return targets


def _rel_to_game(game: Path, path: Path) -> str:
    try:
        return str(path.relative_to(game)).replace("\\", "/")
    except ValueError:
        return path.name


def _path_is_readonly(path: Path) -> bool:
    try:
        return not (path.stat().st_mode & stat.S_IWRITE)
    except OSError:
        return False


def _dir_write_probe(path: Path) -> bool:
    probe = path / f".ichalaunch_write_probe_{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _file_write_probe(path: Path) -> bool:
    if _path_is_readonly(path):
        return False
    try:
        with path.open("ab"):
            pass
        return True
    except OSError:
        return False


def _whoami_principal() -> str:
    if sys.platform != "win32":
        return ""
    try:
        proc = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=_SUBPROC_FLAGS,
        )
        text = (proc.stdout or "").strip()
        if text:
            return text
    except OSError as exc:
        _log.debug("whoami failed: %s", exc)
    user = os.environ.get("USERNAME", "").strip()
    if user:
        return user
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _principal_aliases() -> set[str]:
    aliases: set[str] = set()
    principal = _whoami_principal()
    if principal:
        aliases.add(principal.lower())
        if "\\" in principal:
            aliases.add(principal.split("\\", 1)[1].lower())
    user = os.environ.get("USERNAME", "").strip()
    if user:
        aliases.add(user.lower())
    try:
        aliases.add(getpass.getuser().lower())
    except Exception:
        pass
    return {a for a in aliases if a}


def _icacls_text(path: Path) -> str:
    if sys.platform != "win32" or not path.exists():
        return ""
    try:
        proc = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_SUBPROC_FLAGS,
        )
    except OSError as exc:
        _log.debug("icacls read failed for %s: %s", path, exc)
        return ""
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "").strip()
    return proc.stdout or ""


def _run_icacls(path: Path, *args: str) -> tuple[int, str]:
    if sys.platform != "win32":
        return 1, "not Windows"
    cmd = ["icacls", str(path), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=_SUBPROC_FLAGS,
        )
    except OSError as exc:
        return 1, str(exc)
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, text.strip()


def _ace_matches_user(ace_principal: str, aliases: set[str]) -> bool:
    principal = ace_principal.strip().lower()
    if not principal:
        return False
    if principal in aliases:
        return True
    if "\\" in principal:
        return principal.split("\\", 1)[1] in aliases
    return False


def _parse_icacls_issues(path: Path, aliases: set[str]) -> list[tuple[str, str]]:
    """Return (kind, detail) tuples for ACL problems on *path*."""
    text = _icacls_text(path)
    if not text:
        return []
    issues: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        principal, rights = line.split(":", 1)
        principal = principal.strip()
        rights_u = rights.upper()
        if not _ace_matches_user(principal, aliases):
            continue
        if "DENY" in rights_u:
            issues.append(("deny_acl", "Windows security denies access for your user"))
            continue
        tokens = {tok.strip("()") for tok in rights_u.replace(",", " ").split()}
        if tokens & _ICACLS_WRITE_RIGHTS:
            continue
        if tokens & {"R", "RX", "READ", "READANDEXECUTE"}:
            issues.append(("no_modify", "Your user can read but not modify this folder"))
    return issues


def _permission_hints(game: Path) -> list[str]:
    hints: list[str] = []
    lowered = str(game).lower().replace("/", "\\")
    if "\\downloads\\" in lowered:
        hints.append(
            "Hint: the game folder is inside Downloads — copying or extracting here "
            "often leaves restrictive permissions."
        )
    elif is_protected_path(game):
        hints.append(
            "Hint: this location (Desktop, Documents, Program Files, or Downloads) "
            "often causes permission problems with mods and saves."
        )
    return hints


def scan_game_permissions(game: Path | str) -> PermissionScanResult:
    """Scan key game paths for read-only attributes and Windows ACL/write problems."""
    root = Path(game)
    result = PermissionScanResult(game=root)
    if sys.platform != "win32":
        return result
    if not root.is_dir():
        result.issues.append(
            PermissionIssue(".", "missing", "Game folder does not exist")
        )
        return result

    result.protected_path = is_protected_path(root)
    result.hints.extend(_permission_hints(root))
    aliases = _principal_aliases()
    seen: set[tuple[str, str]] = set()

    def add_issue(path: Path, kind: str, detail: str) -> None:
        rel = _rel_to_game(root, path)
        key = (rel, kind)
        if key in seen:
            return
        seen.add(key)
        result.issues.append(PermissionIssue(rel=rel, kind=kind, detail=detail))

    for target in iter_game_permission_targets(root):
        if not target.exists():
            continue
        if _path_is_readonly(target):
            label = "folder" if target.is_dir() else "file"
            add_issue(target, "readonly", f"Read-only {label} attribute is set")

        writable = _dir_write_probe(target) if target.is_dir() else _file_write_probe(target)
        if not writable:
            add_issue(target, "not_writable", "Your user cannot write here")
            for kind, detail in _parse_icacls_issues(target, aliases):
                add_issue(target, kind, detail)
            if not result.protected_path:
                result.needs_elevation = True

    if result.issues:
        _log.info(
            "Game permission scan found %d issue(s) under %s",
            len(result.issues),
            root,
        )
    return result


def fix_game_permissions(game: Path | str) -> PermissionFixResult:
    """Grant the current user Modify access and clear read-only flags under *game*."""
    root = Path(game)
    result = PermissionFixResult(game=root)
    if sys.platform != "win32":
        result.warnings.append("Permission repair is only supported on Windows.")
        return result
    if not root.is_dir():
        result.warnings.append("Game folder does not exist.")
        return result
    if is_protected_path(root):
        result.warnings.append(
            "This game folder is in a restricted location (Downloads, Desktop, "
            "Documents, or Program Files). Move the entire folder to a location "
            "you own (e.g. C:\\Games\\RavenCraft), update the game path in "
            "Settings, then run Check Game Permissions again."
        )
        return result

    _log.info("Repairing game folder permissions: %s", root)

    try:
        _make_tree_writable(root)
        result.fixes.append("Cleared read-only attributes")
    except OSError as exc:
        result.warnings.append(f"Could not clear all read-only flags: {exc}")
        _log.warning("Read-only cleanup incomplete for %s: %s", root, exc)

    principal = _whoami_principal()
    if principal:
        code, out = _run_icacls(root, "/inheritance:e")
        if code != 0 and out:
            _log.debug("icacls inheritance for %s: %s", root, out)
        grant_code, grant_out = _run_icacls(
            root,
            "/grant",
            f"{principal}:(OI)(CI)M",
            "/T",
        )
        if grant_code == 0:
            result.fixes.append(f"Granted Modify access to {principal}")
            _log.info("Granted Modify on %s to %s", root, principal)
        else:
            if is_access_denied(OSError(5, grant_out or "access denied")):
                result.needs_elevation = True
                result.warnings.append(
                    "Could not update all security permissions — move the game to a "
                    "folder you own (e.g. C:\\Games\\RavenCraft) and run "
                    "Check Game Permissions again."
                )
            elif grant_out:
                result.warnings.append(f"Permission grant incomplete: {grant_out[:240]}")
            _log.warning("icacls grant failed for %s (%s): %s", root, grant_code, grant_out)

        deny_code, deny_out = _run_icacls(root, "/remove:d", principal, "/T")
        if deny_code == 0:
            result.fixes.append("Removed explicit deny rules for your user")
            _log.info("Removed deny ACEs for %s under %s", principal, root)
        elif deny_out and "No mappings" not in deny_out:
            _log.debug("icacls remove:d for %s: %s", root, deny_out)
    else:
        result.warnings.append("Could not determine the current Windows user for ACL repair.")

    remaining = scan_game_permissions(root)
    if remaining.has_issues:
        result.warnings.append(
            f"{len(remaining.issues)} permission issue(s) remain after repair."
        )
        result.needs_elevation = result.needs_elevation or remaining.needs_elevation
    return result
