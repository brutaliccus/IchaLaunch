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
import tarfile
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


def extract_tar(
    tar_source: Path,
    dest: Path,
    progress: Any | None = None,
) -> Path:
    """Extract a ``.tar.gz`` / ``.tar`` archive into *dest*."""
    ensure_dir(dest)
    with tarfile.open(tar_source, "r:*") as tf:
        members = [m for m in tf.getmembers() if m.isfile() or m.isdir()]
        total = len(members) or 1
        done = 0
        _report_extract_progress(progress, 0, total)
        for member in members:
            tf.extract(member, dest, filter="data")
            done += 1
            _report_extract_progress(progress, done, total)
    children = [c for c in dest.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def resolve_ci(base: Path, rel: str | Path) -> Path | None:
    """Resolve *rel* under *base*, falling back to a case-insensitive match.

    Windows and macOS resolve paths case-insensitively, so code written there
    can name ``Data/patch-A.MPQ`` and reach a file stored as ``DATA/patch-a.mpq``.
    On Linux that lookup simply misses, and callers that read the miss as "not
    present" silently skip their work. The exact path is tried first, so this
    costs nothing whenever the case already agrees -- which is always, on the
    platforms that do not need it.

    Returns None when no component matches, in any case.
    """
    exact = base / rel
    if exact.exists():
        return exact
    parts = [part for part in Path(rel).parts if part not in ("", ".")]
    return _resolve_ci_parts(base, parts)


def _resolve_ci_parts(current: Path, parts: list[str]) -> Path | None:
    """Walk *parts* under *current*, trying every casing that could match.

    Taking the first folded match per component and committing to it is not
    enough: a tree holding both Data/ and data/ can keep the wanted file in
    either, so a component that matches must still be abandoned when the rest
    of the path is not under it. Exact case is tried first at every level, so
    a directory that matches outright is never passed over for one that
    merely folds to the same name.
    """
    if not parts:
        return current
    head, rest = parts[0], parts[1:]
    try:
        entries = sorted(current.iterdir(), key=lambda c: c.name)
    except OSError:
        return None
    folded = head.lower()
    candidates = [c for c in entries if c.name == head]
    candidates += [c for c in entries if c.name != head and c.name.lower() == folded]
    for candidate in candidates:
        found = _resolve_ci_parts(candidate, rest)
        if found is not None:
            return found
    return None


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
    # Compared a component at a time rather than against a literal "Data",
    # because the path being tested has usually come back from resolve_ci and
    # so carries the casing the disk actually uses -- data/, DATA/ and Data/
    # all name the game's own folder.
    try:
        rel = Path(path).resolve().relative_to(Path(game_path).resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0].lower() == "data"


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


# Plain-English copy for UI — never surface raw Errno / WinError to users.
# WinError 32 is a sharing lock ("in use by another process"), not antivirus by
# itself; we used to lead with AV and misled users who had Defender disabled.
_WIN_END_GAME_PROCESS_HINT = (
    "Open Task Manager (Ctrl+Shift+Esc) and End task on WoW.exe and "
    "VanillaFixes.exe if either is listed."
)
# Task Manager, Explorer and Controlled Folder Access do not exist here, and a
# hint a user cannot act on is worse than none. Name the tools they do have.
_POSIX_END_GAME_PROCESS_HINT = (
    "Close the game, then list any leftover client process with "
    "'pgrep -fai WoW.exe' and end it with 'pkill -fi WoW.exe'."
)
END_GAME_PROCESS_HINT = (
    _WIN_END_GAME_PROCESS_HINT if sys.platform == "win32" else _POSIX_END_GAME_PROCESS_HINT
)
_WIN_OTHER_HOLDERS = (
    "Overlays, Explorer previews, backup/sync, Controlled Folder Access, or "
    "antivirus (not only Windows Defender) can also hold the file."
)
_POSIX_OTHER_HOLDERS = (
    "A second Wine process, an overlay, a file manager preview, or backup/sync "
    "can also hold the file."
)
_OTHER_HOLDERS_HINT = (
    _WIN_OTHER_HOLDERS if sys.platform == "win32" else _POSIX_OTHER_HOLDERS
)
LOCK_AV_VERIFY_TITLE = "Could not verify install"
LOCK_AV_VERIFY_MESSAGE = (
    "Could not verify the install because the file is in use by another process. "
    "The mod was left installed. "
    f"{END_GAME_PROCESS_HINT} Then retry. "
    f"{_OTHER_HOLDERS_HINT}"
)
LOCK_AV_APPLY_MESSAGE = (
    "Could not apply this change because the file is in use by another process. "
    f"{END_GAME_PROCESS_HINT} Then retry Apply. "
    f"{_OTHER_HOLDERS_HINT}"
)


def has_end_game_guidance(text: str, *, strict: bool = True) -> bool:
    """True when *text* already carries this platform's end-the-game steps.

    ``strict`` also requires VanillaFixes to be named, which is what the
    ``user_facing_os_error`` path checked inline before this was shared.
    """
    low = (text or "").strip().lower()
    if sys.platform == "win32":
        if "task manager" not in low or "wow.exe" not in low:
            return False
        return ("vanillafixes" in low) if strict else True
    if "wow.exe" not in low:
        return False
    return "pgrep" in low or "pkill" in low

_RAW_OS_DETAIL = frozenset(
    {
        "invalid argument",
        "access is denied",
        "permission denied",
        "the process cannot access the file because it is being used by another process",
    }
)


def _looks_like_raw_os_detail(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    if low in _RAW_OS_DETAIL:
        return True
    return low.startswith(("[errno", "[winerror")) or "winerror" in low


def _ensure_end_game_guidance(text: str, *, retry_apply: bool = True) -> str:
    """Append the end-the-game steps when *text* lacks them."""
    body = (text or "").strip()
    if has_end_game_guidance(body):
        return body
    suffix = (
        f"{END_GAME_PROCESS_HINT} Then retry Apply."
        if retry_apply
        else f"{END_GAME_PROCESS_HINT} Then retry."
    )
    return f"{body}\n\n{suffix}" if body else suffix


def user_facing_os_error(exc: BaseException, *, kept_install: bool = False) -> str:
    """User-visible OSError text — lock/AV → plain English; never raw errno jargon.

    Launcher-authored ``OSError`` detail strings (missing file, truncated PE, …)
    are preserved even when errno happens to be 22. Lock/sharing failures always
    include Task Manager guidance to end WoW.exe / VanillaFixes.exe.
    """
    detail = ""
    filename = ""
    if isinstance(exc, OSError):
        if len(exc.args) > 1:
            detail = str(exc.args[1] or "").strip()
        filename = str(getattr(exc, "filename", None) or "")
    if is_lock_or_av_error(exc) and _looks_like_raw_os_detail(detail):
        base = LOCK_AV_VERIFY_MESSAGE if kept_install else LOCK_AV_APPLY_MESSAGE
        if filename:
            try:
                from ichalaunch.core.process import file_in_use_hint

                hint = file_in_use_hint(filename)
                # Only append a *specific* diagnosis (game running / named lockers).
                if hint.startswith("WoW.exe") or hint.startswith("In use by:"):
                    return _ensure_end_game_guidance(
                        f"{base}\n\n{hint}",
                        retry_apply=not kept_install,
                    )
            except Exception:  # noqa: BLE001
                pass
        return base
    if detail:
        if is_lock_or_av_error(exc):
            return _ensure_end_game_guidance(detail, retry_apply=not kept_install)
        return detail
    text = str(exc).strip()
    if is_lock_or_av_error(exc):
        return LOCK_AV_VERIFY_MESSAGE if kept_install else LOCK_AV_APPLY_MESSAGE
    return text or "An unexpected file error occurred."


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


def listed_exact_basenames(directory: Path) -> frozenset[str] | None:
    """Original-case names in *directory* via listdir — does not open files.

    ``listed_basenames`` folds case, so ``patch-v.mpq`` and ``Patch-V.mpq``
    look the same. Callers that must not treat a hand-placed ``Patch-V`` as
    the launcher's lowercase ``patch-v`` need the on-disk spelling.
    """
    try:
        return frozenset(os.listdir(directory))
    except FileNotFoundError:
        return frozenset()
    except OSError as exc:
        if is_lock_or_av_error(exc):
            mark_path_locked(directory)
            _log.warning("Could not list %s: %s", directory, exc)
        return None


def exact_name_present(directory: Path, name: str) -> bool:
    """True only when *name* appears in the directory with this exact casing."""
    raw = (name or "").strip()
    if not raw:
        return False
    names = listed_exact_basenames(directory)
    if names is None:
        return False
    return raw in names


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
            _log.warning("File-in-use skipped copy %s → %s: %s", src, dest, exc)
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
            try:
                from ichalaunch.core.process import file_in_use_hint

                hint = file_in_use_hint(path)
            except Exception:  # noqa: BLE001
                hint = (
                    f"{END_GAME_PROCESS_HINT} Then retry Apply."
                )
            raise OSError(
                getattr(exc, "errno", None) or 13,
                (
                    f"Could not remove {path.name} — the file is in use by another process "
                    f"({code}). {hint}"
                ),
                str(path),
            ) from exc
        raise
    clear_path_locked(path)
    invalidate_dir_listing(path.parent)


TOC_FOLDER_MISMATCH_MSG = (
    "Addon folder name must match the .toc file name "
    r"(example: Atlas-CFM\Atlas-CFM.toc)."
)


@dataclass(frozen=True)
class AddonTocMismatch:
    """A folder whose ``.toc`` stem does not match the folder name."""

    folder: Path
    current_name: str
    toc_stem: str
    toc_name: str

    @property
    def can_rename(self) -> bool:
        """True when there is a unique ``.toc`` stem to rename the folder to."""
        stem = (self.toc_stem or "").strip()
        current = (self.current_name or "").strip()
        return bool(stem) and stem.lower() != current.lower()


@dataclass
class RenameAddonFolderResult:
    """Outcome of :func:`rename_addon_folder_to_toc`."""

    status: str
    old_name: str
    new_name: str
    dest: Path | None = None
    detail: str = ""

    @property
    def renamed(self) -> bool:
        return self.status == "renamed"


_pending_toc_mismatches: list[AddonTocMismatch] = []


def note_pending_toc_mismatch(item: AddonTocMismatch) -> None:
    """Record a mismatch found on a worker thread for a later UI prompt."""
    _pending_toc_mismatches.append(item)


def take_pending_toc_mismatches() -> list[AddonTocMismatch]:
    """Drain mismatches collected during the last install worker."""
    items = list(_pending_toc_mismatches)
    _pending_toc_mismatches.clear()
    return items


def clear_pending_toc_mismatches() -> None:
    """Drop any leftover worker-collected mismatches (tests / failed jobs)."""
    _pending_toc_mismatches.clear()


def toc_mismatch_prompt_text(current_name: str, toc_name: str) -> str:
    """Yes/No dialog body: the .toc is the source of truth; rename the folder."""
    folder = (current_name or "").strip() or "(unknown folder)"
    toc = (toc_name or "").strip() or "(missing .toc)"
    dest = Path(toc).stem if toc.endswith(".toc") or toc.endswith(".TOC") else toc
    if not dest:
        dest = toc
    return (
        f"Folder is {folder} but the .toc is {toc}. "
        f"WoW requires the folder name to match the .toc. "
        f"Rename folder to {dest}?"
    )


def describe_toc_mismatch(folder: Path) -> AddonTocMismatch | None:
    """Return mismatch info when *folder* has a clear non-matching primary ``.toc``.

    Handles a single ``.toc``, or multi-TOC primary/variant sets (e.g.
    ``pfQuest.toc`` + ``pfQuest-tbc.toc`` under a leftover ``pfQuest-main``
    GitHub unwrap). Case-only differences are not mismatches —
    :func:`matching_toc_path` already accepts those on Windows/Wine.
    Unrelated sibling ``.toc`` files (no clear primary) return ``None``.
    """
    folder = Path(folder)
    if matching_toc_path(folder) is not None:
        return None
    tocs = folder_toc_files(folder)
    if not tocs:
        return None
    if len(tocs) == 1:
        toc = tocs[0]
        if toc.stem.lower() == folder.name.lower():
            return None
        return AddonTocMismatch(
            folder=folder,
            current_name=folder.name,
            toc_stem=toc.stem,
            toc_name=toc.name,
        )
    primary = _primary_toc_stem(tocs)
    if not primary or primary.lower() == folder.name.lower():
        return None
    return AddonTocMismatch(
        folder=folder,
        current_name=folder.name,
        toc_stem=primary,
        toc_name=f"{primary}.toc",
    )


def rename_addon_folder_to_toc(
    folder: Path,
    toc_stem: str | None = None,
    *,
    update_settings: bool = True,
) -> RenameAddonFolderResult:
    """Rename *folder* to the ``.toc`` stem. Never clobbers an existing dest.

    Destination name is the ``.toc`` stem (the file is already named correctly).
    Case-only differences are treated as already matching.
    """
    folder = Path(folder)
    old_name = folder.name
    if not folder.is_dir():
        return RenameAddonFolderResult(
            status="missing",
            old_name=old_name,
            new_name=(toc_stem or "").strip(),
            detail=f"Addon folder not found: {folder}",
        )
    if matching_toc_path(folder) is not None:
        return RenameAddonFolderResult(
            status="already_match",
            old_name=old_name,
            new_name=old_name,
            dest=folder,
        )

    tocs = folder_toc_files(folder)
    wanted = (toc_stem or "").strip()
    toc_stems = [t.stem for t in tocs]
    if wanted and any(s.lower() == wanted.lower() for s in toc_stems):
        stem = next(s for s in toc_stems if s.lower() == wanted.lower())
    elif len(tocs) == 1:
        stem = tocs[0].stem
    else:
        return RenameAddonFolderResult(
            status="error",
            old_name=old_name,
            new_name="",
            detail="No unique .toc file to rename this folder to.",
        )

    dest = folder.with_name(stem)
    try:
        if dest.resolve() == folder.resolve():
            return RenameAddonFolderResult(
                status="already_match",
                old_name=old_name,
                new_name=stem,
                dest=folder,
            )
    except OSError:
        pass

    if dest.exists():
        return RenameAddonFolderResult(
            status="collision",
            old_name=old_name,
            new_name=stem,
            dest=dest,
            detail=(
                f'Cannot rename "{old_name}" to "{stem}" — '
                f'a folder named "{stem}" already exists.'
            ),
        )

    try:
        folder.rename(dest)
    except OSError as exc:
        return RenameAddonFolderResult(
            status="error",
            old_name=old_name,
            new_name=stem,
            dest=dest,
            detail=f'Could not rename "{old_name}" to "{stem}": {exc}',
        )

    invalidate_dir_listing(dest.parent)
    if update_settings:
        try:
            from ichalaunch.config.settings import settings

            settings.retarget_installed_addon(old_name, stem)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Renamed %s → %s but could not retarget settings: %s",
                old_name,
                stem,
                exc,
            )
    return RenameAddonFolderResult(
        status="renamed",
        old_name=old_name,
        new_name=stem,
        dest=dest,
    )


def place_install_addon_root(
    root: Path,
    addons_dir: Path,
    dest_name: str,
) -> tuple[str | None, AddonTocMismatch | None]:
    """Copy an extracted addon root into AddOns under the ``.toc`` stem.

    The ``.toc`` filename is the source of truth. Catalog names and extract
    folder names (for example ``Atlas-TW`` when the file is ``Atlas-CFM.toc``)
    are ignored when they disagree. Existing dest folders are replaced (update).
    """
    root = Path(root)
    addons_dir = Path(addons_dir)
    ensure_dir(addons_dir)

    canonical = canonical_addon_folder_name(root)
    dest_name = (canonical or dest_name or root.name).strip() or root.name

    dest = addons_dir / dest_name
    if dest.exists():
        safe_remove(dest)
    copy_tree(root, dest)
    if matching_toc_path(dest) is not None:
        return dest.name, None
    leftover = describe_toc_mismatch(dest)
    if leftover is None:
        _log.warning(
            "Installed %s is missing a folder-matching .toc — removing incomplete copy",
            dest.name,
        )
        safe_remove(dest)
        return None, None
    return None, leftover


def matching_toc_path(folder: Path) -> Path | None:
    """Return ``{folder}/{folder.name}.toc`` when that file exists.

    Comparison is case-insensitive so Windows/Wine folders match mixed-case TOCs.
    WoW only loads an addon when the primary folder and ``.toc`` share a name.
    """
    if not folder.is_dir():
        return None
    wanted = f"{folder.name}.toc".lower()
    try:
        for child in folder.iterdir():
            if child.is_file() and child.name.lower() == wanted:
                return child
    except OSError:
        return None
    return None


def folder_toc_files(folder: Path) -> list[Path]:
    """Immediate ``.toc`` files in *folder* (not recursive)."""
    if not folder.is_dir():
        return []
    try:
        return [
            child
            for child in folder.iterdir()
            if child.is_file() and child.suffix.lower() == ".toc"
        ]
    except OSError:
        return []


def _primary_toc_stem(tocs: list[Path]) -> str | None:
    """Return the primary ``.toc`` stem when others are expansion-style variants.

    Example: ``Foo.toc`` plus only ``Foo-*.toc`` / ``Foo_*.toc`` → ``Foo``.
    Unrelated siblings (``Foo.toc`` + ``Bar.toc``) → ``None``.
    """
    if len(tocs) < 2:
        return None
    stems = [t.stem for t in tocs]
    stems_l = [s.lower() for s in stems]
    # Prefer shorter stems so Foo wins over Foo-tbc when both could qualify.
    candidates = sorted(set(stems), key=lambda s: (len(s), s.lower()))
    for candidate in candidates:
        c_l = candidate.lower()
        prefix_dash = c_l + "-"
        prefix_us = c_l + "_"
        if all(
            s_l == c_l or s_l.startswith(prefix_dash) or s_l.startswith(prefix_us)
            for s_l in stems_l
        ):
            return candidate
    return None


def canonical_addon_folder_name(root: Path) -> str | None:
    """Destination folder name WoW requires: the stem of the primary ``.toc``.

    If ``{root.name}.toc`` exists, return ``root.name``. If the folder has
    exactly one ``.toc``, return that stem so the installer can place the
    addon under the matching name. If multiple ``.toc`` files share a clear
    primary stem with only ``Primary-*`` / ``Primary_*`` variants (e.g.
    pfQuest under a ``pfQuest-main`` GitHub unwrap), return that primary.
    Otherwise ``None`` (not a valid root).
    """
    if matching_toc_path(root) is not None:
        return root.name
    tocs = folder_toc_files(root)
    if len(tocs) == 1:
        return tocs[0].stem
    if len(tocs) > 1:
        return _primary_toc_stem(tocs)
    return None


def resolve_install_addon_roots(extracted: Path) -> list[tuple[Path, str]]:
    """Return ``(source_root, dest_folder_name)`` for installable TOC roots."""
    roots = find_toc_roots(extracted)
    if not roots:
        name = canonical_addon_folder_name(extracted)
        if name:
            roots = [extracted]
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for root in roots:
        name = canonical_addon_folder_name(root)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((root, name))
    return out


def find_toc_roots(root: Path) -> list[Path]:
    """Find addon root folders that have a usable ``.toc``.

    A folder is usable when :func:`canonical_addon_folder_name` accepts it —
    matching ``{folder}.toc``, a single ``.toc``, or multi-TOC primary/variant
    sets. Nested library TOCs are ignored when a shallower addon root exists.
    """
    roots: list[Path] = []
    seen: set[Path] = set()
    try:
        tocs = list(root.rglob("*.toc"))
    except OSError:
        return []
    for toc in tocs:
        parent = toc.parent
        if parent in seen:
            continue
        if canonical_addon_folder_name(parent) is None:
            continue
        seen.add(parent)
        roots.append(parent)
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


def validate_pe_binary(
    path: Path,
    *,
    min_size: int = 1024,
    retries: int = 3,
    delay: float = 0.12,
) -> bool:
    """Reject truncated or non-PE binaries (MZ header + minimum size).

    Returns True when the file looks like a valid PE.

    Returns False when Windows Defender / sharing locks block the read after
    brief retries (same class of errors as ``sha256_file`` → None). Callers
    must treat False as "could not verify", not as corruption — newly written
    injector DLLs commonly trip WinError 225 / Errno 22 right after copy.

    Raises OSError for missing, truncated, or non-PE content failures.
    """
    p = Path(path)
    if should_skip_locked_path(p):
        _log.warning("Skipping PE verify of locked file %s", p)
        return False

    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            size = p.stat().st_size
        except OSError as exc:
            if is_lock_or_av_error(exc):
                if attempt + 1 < attempts:
                    time.sleep(delay * (attempt + 1))
                    continue
                mark_path_locked(p)
                _log.warning("Could not verify %s (lock/AV): %s", p, exc)
                return False
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
            if is_lock_or_av_error(exc):
                if attempt + 1 < attempts:
                    time.sleep(delay * (attempt + 1))
                    continue
                mark_path_locked(p)
                _log.warning(
                    "Could not read %s for verification (lock/AV): %s", p, exc
                )
                return False
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
        return True
    return False


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
