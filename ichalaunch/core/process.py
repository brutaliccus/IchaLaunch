"""Process / download helpers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError, HTTPError, Timeout

BytesProgressCb = Callable[[int, int], None]  # downloaded, total
# Back-compat alias used by download_file callers.
ProgressCb = BytesProgressCb

# HTTPError subclasses OSError, so download retries must not treat every
# 4xx as "try again". These statuses are the usual CDN / gateway blips.
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def http_error_status(exc: BaseException) -> int | None:
    """Status code from a ``requests.HTTPError``, or None."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            return int(resp.status_code)
        except (TypeError, ValueError):
            return None
    if isinstance(exc, HTTPError):
        match = re.match(r"^(\d{3})\b", str(exc))
        if match:
            return int(match.group(1))
    return None


def is_transient_http_error(exc: BaseException) -> bool:
    """True for gateway / rate-limit HTTP failures that are worth retrying."""
    status = http_error_status(exc)
    return status in TRANSIENT_HTTP_STATUSES


def is_retryable_download_error(exc: BaseException) -> bool:
    """True when ``download_file`` should retry *exc*."""
    if isinstance(exc, HTTPError):
        return is_transient_http_error(exc)
    return isinstance(exc, (ConnectionError, ChunkedEncodingError, Timeout, OSError))


class StatusProgress:
    """Status-string reporter that can also emit determinate download percents.

    Compatible with ``Callable[[str], None]`` progress hooks used by install/update
    workers. Pass ``.on_bytes`` into ``download_file`` for byte-level progress.
    """

    def __init__(
        self,
        on_status: Callable[[str], None],
        on_pct: Callable[[int], None],
    ) -> None:
        self._on_status = on_status
        self._on_pct = on_pct
        self._label = ""

    def __call__(self, msg: str) -> None:
        self._label = (msg or "").strip()
        self._on_status(self._label)
        # Status-only updates (install/extract) fall back to indeterminate.
        # Download tracking must use on_count / on_bytes — those keep a determinate %.
        self._on_pct(-1)

    def set_status(self, msg: str) -> None:
        """Update the status label without changing determinate/indeterminate percent."""
        self._label = (msg or "").strip()
        if self._label:
            self._on_status(self._label)

    def on_count(self, done: int, total: int, msg: str | None = None) -> None:
        """Report determinate item progress (update checks, multi-step jobs)."""
        if msg is not None:
            self._label = (msg or "").strip()
            if self._label:
                self._on_status(self._label)
        if total and total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            self._on_pct(pct)
        else:
            self._on_pct(0)

    def on_bytes(self, done: int, total: int) -> None:
        if total and total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            self._on_pct(pct)
            base = self._label.rstrip(".…") or "Downloading"
            self._on_status(f"{base}… {pct}%")
        else:
            # Unknown size: indeterminate is OK, but do not re-emit -1 every chunk
            # (that restarts ThemeLoadingBar's pulse).
            self._on_pct(-1)


def status_only(progress: Any, msg: str) -> None:
    """Update the status label without forcing indeterminate (unlike ``progress()``)."""
    if progress is None:
        return
    setter = getattr(progress, "set_status", None)
    if callable(setter):
        setter(msg)
        return
    if callable(progress):
        progress(msg)


def download_bytes_cb(progress: Any) -> BytesProgressCb | None:
    """Adapt a status progress object to ``download_file``'s (done, total) callback."""
    if progress is None:
        return None
    cb = getattr(progress, "on_bytes", None)
    return cb if callable(cb) else None


def resolve_download_total(headers: Any, known_total: int = 0) -> int:
    """Prefer Content-Length; fall back to an API/catalog size when the header is missing."""
    try:
        n = int((headers or {}).get("Content-Length") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    try:
        k = int(known_total or 0)
    except (TypeError, ValueError):
        k = 0
    return k if k > 0 else 0


def _download_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }


def zip_url_from_html(html: str, base_url: str) -> str | None:
    """Best-effort zip / download href from a file-host landing page."""
    text = html or ""
    for pat in (
        r'href=["\']([^"\']+\.zip[^"\']*)["\']',
        r'https?://[^\s"\'<>]+?\.zip(?:\?[^\s"\'<>]*)?',
        r'href=["\']([^"\']+/download[^"\']*)["\']',
        r'data-url=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, text, re.I)
        if not m:
            continue
        href = (m.group(1) if m.lastindex else m.group(0)).strip()
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
        if href.startswith("/") and base_url:
            return urljoin(base_url, href)
    return None


def download_bytes(
    url: str,
    progress: ProgressCb | None = None,
    timeout: int = 120,
    known_total: int = 0,
) -> bytes:
    """Download into memory (avoids Windows AV locking certain zip names on disk)."""
    headers = _download_headers()
    chunks: list[bytes] = []
    with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype and "drive.google" in url:
            raise RuntimeError(
                "Google Drive returned an HTML page instead of the file. "
                "Try again later or download manually."
            )
        total = resolve_download_total(r.headers, known_total)
        done = 0
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            chunks.append(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return b"".join(chunks)


def download_file(
    url: str,
    dest: Path,
    progress: ProgressCb | None = None,
    timeout: int | tuple[int, int] = 120,
    extra_headers: dict[str, str] | None = None,
    source_url: str | None = None,
    known_total: int = 0,
    *,
    retries: int = 3,
) -> Path:
    """Download *url* to *dest*, retrying transient connection / HTTP 5xx failures."""
    last_exc: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            return _download_file_once(
                url,
                dest,
                progress=progress,
                timeout=timeout,
                extra_headers=extra_headers,
                source_url=source_url,
                known_total=known_total,
            )
        except Exception as exc:
            if not is_retryable_download_error(exc):
                raise
            last_exc = exc
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            if attempt + 1 >= attempts:
                break
            time.sleep(min(8, 2**attempt))
    assert last_exc is not None
    raise last_exc


def _download_file_once(
    url: str,
    dest: Path,
    progress: ProgressCb | None = None,
    timeout: int | tuple[int, int] = 120,
    extra_headers: dict[str, str] | None = None,
    source_url: str | None = None,
    known_total: int = 0,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _download_headers()
    if extra_headers:
        headers.update(extra_headers)
    origin = source_url or url
    with requests.get(
        url, stream=True, timeout=timeout, headers=headers, allow_redirects=True
    ) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        disp = (r.headers.get("Content-Disposition") or "").lower()
        header_len = 0
        try:
            header_len = int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            header_len = 0
        total = resolve_download_total(r.headers, known_total)
        html_type = "text/html" in ctype and "attachment" not in disp
        if html_type and (not header_len or header_len < 2_000_000):
            body = b"".join(r.iter_content(chunk_size=1024 * 64))
            if "drive.google" in url:
                raise RuntimeError(
                    "Google Drive returned an HTML page instead of the file. "
                    "Try again later or download manually."
                )
            nxt = zip_url_from_html(body.decode("utf-8", "replace"), str(r.url))
            if nxt and nxt != url:
                return _download_file_once(
                    nxt,
                    dest,
                    progress=progress,
                    timeout=timeout,
                    extra_headers=extra_headers,
                    source_url=origin,
                    known_total=known_total,
                )
            raise RuntimeError(
                "Download returned a web page instead of a zip.\n" + origin
            )
        done = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    # Sanity-check MPQ magic when expecting an archive asset
    if dest.suffix.lower() == ".mpq" and dest.stat().st_size >= 4:
        with dest.open("rb") as f:
            magic = f.read(3)
        if magic != b"MPQ":
            raise RuntimeError(f"Downloaded file is not a valid MPQ: {dest.name}")
    return dest


def google_drive_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )

_GAME_IMAGE_NAMES = frozenset({"wow.exe", "vanillafixes.exe"})
CLIENT_PROCESS_NAMES = ("wow.exe", "vanillafixes.exe")


def _norm_cmdline(text: str) -> str:
    """Lowercase, forward slashes, NULs as spaces.

    Wine hands the client its own path as ``Z:\\home\\you\\Games\\...\\WoW.exe``.
    Normalising both sides lets the unix game directory be found in that string
    as a plain substring, with no drive-letter or separator special-casing.
    """
    return (text or "").replace("\x00", " ").replace("\\", "/").lower()


def _arg_names_client(arg: str, needle: str) -> bool:
    """True when one normalised argv entry names a client exe we care about.

    The exe name has to sit behind a separator, so an argument that merely
    mentions WoW.exe in prose (a shell command, an editor buffer, a log line)
    cannot trip the guard. With a known game directory the same argument must
    also carry that directory, which is the scoping guarantee.
    """
    if not any(arg == name or arg.endswith(f"/{name}") for name in CLIENT_PROCESS_NAMES):
        # The argument has to END with the exe name. Requiring only that it
        # appears leaves "/games/wow/wow.exe.bak" and a log line quoting the
        # path matching just as well as the client itself.
        return False
    return needle in arg if needle else True


def _configured_game_dir() -> Path | None:
    """Configured game folder, or None. Imported here, not at module scope.

    ``ichalaunch.game.launcher`` imports from this module, so a top-level import
    would be a cycle. Every other ichalaunch import in this file is deferred the
    same way.
    """
    try:
        from ichalaunch.game.launcher import detect_game

        return detect_game()
    except Exception:  # noqa: BLE001
        return None


def _proc_client_running(game_dir: Path | str | None) -> bool:
    """True when some ``/proc/<pid>/cmdline`` names a client exe under *game_dir*.

    Matching is on the client PATH rather than the process name. That scopes the
    answer to the install this launcher is tied to, still catches a client the
    user started outside the launcher, and sidesteps ``/proc/<pid>/comm``, which
    truncates at 15 characters and so never holds "VanillaFixes.exe".

    When the game directory is unknown this returns False rather than guessing,
    because an unscoped match would block work on one install because a client
    for a different one is running.
    """
    needle = _norm_cmdline(str(game_dir or "")).rstrip("/")
    if not needle:
        return False
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except (OSError, ValueError):
            # A pid can exit between the listing and the read, and processes
            # owned by other users can be unreadable. Both are normal, not errors.
            continue
        if not raw:
            continue
        text = raw.decode("utf-8", "replace")
        for arg in text.split("\x00"):
            if arg and _arg_names_client(_norm_cmdline(arg), needle):
                return True
    return False


def _path_is_under(image: Path | str, root: Path | str) -> bool:
    """True when *image* is *root* or a file inside it (Windows-case-safe)."""
    img_s = os.path.normcase(os.path.normpath(str(image)))
    base_s = os.path.normcase(os.path.normpath(str(root)))
    if not img_s or not base_s:
        return False
    try:
        img_s = os.path.normcase(str(Path(img_s).expanduser().resolve()))
        base_s = os.path.normcase(str(Path(base_s).expanduser().resolve()))
    except OSError:
        pass
    if img_s == base_s:
        return True
    prefix = base_s.rstrip("\\/") + os.sep
    return img_s.startswith(prefix)


def _query_process_image(pid: int) -> Path | None:
    """Full image path for *pid*, or None. No admin; same-user processes only."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            text = (buf.value or "").strip()
            return Path(text) if text else None
    except (OSError, AttributeError, ValueError, TypeError):
        return None
    finally:
        kernel32.CloseHandle(handle)
    return None


def _wow_process_images_toolhelp() -> tuple[bool, list[Path]] | None:
    """Enumerate WoW.exe / VanillaFixes.exe via Toolhelp. None if the snapshot fails."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    invalid = wintypes.HANDLE(-1).value
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == invalid:
        return None
    name_seen = False
    images: list[Path] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return False, []
        while True:
            exe = (entry.szExeFile or "").strip().lower()
            if exe in _GAME_IMAGE_NAMES:
                name_seen = True
                image = _query_process_image(int(entry.th32ProcessID))
                if image is not None:
                    images.append(image)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    except (OSError, AttributeError, ValueError, TypeError):
        return None
    finally:
        kernel32.CloseHandle(snap)
    return name_seen, images


def _wow_exe_running_tasklist() -> bool:
    """Name-only fallback when Toolhelp is unavailable."""
    names = ("WoW.exe", "VanillaFixes.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for name in names:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=flags,
            )
            out = (proc.stdout or "").lower()
            if "no tasks" in out:
                continue
            if name.lower() in out:
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _wow_process_images() -> tuple[bool, list[Path]]:
    """``(name_seen, image_paths)`` for WoW.exe / VanillaFixes.exe. Windows-only."""
    if sys.platform != "win32":
        return False, []
    try:
        result = _wow_process_images_toolhelp()
        if result is not None:
            return result
    except (OSError, AttributeError, ValueError, TypeError):
        pass
    return _wow_exe_running_tasklist(), []


def _locker_is_game_exe(name: str) -> bool:
    n = (name or "").strip().lower()
    if n in _GAME_IMAGE_NAMES:
        return True
    if n.endswith("wow.exe") or n.endswith("vanillafixes.exe"):
        return True
    if n in {"wow", "world of warcraft"}:
        return True
    return "vanillafixes" in n


def _game_dir_exe_paths(game_dir: Path | str) -> list[Path]:
    """This folder's WoW.exe / VanillaFixes.exe only — no tree walk, no RM."""
    root = Path(game_dir)
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None:
            return
        key = os.path.normcase(os.fspath(path))
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    try:
        from ichalaunch.game.launcher import wow_exe_in

        _add(wow_exe_in(root))
    except Exception:  # noqa: BLE001
        pass
    for name in ("WoW.exe", "VanillaFixes.exe"):
        _add(root / name)
    return found


def _win_exe_in_use(path: Path | str) -> bool:
    """True when *path* is mapped/locked (exclusive CreateFileW fails).

    Does not use Restart Manager. OpenProcess ACCESS_DENIED is irrelevant —
    this opens the file, not the process. Missing files are not in use.
    """
    if sys.platform != "win32":
        return False
    text = os.fspath(path)
    if not text:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    generic_read_write = 0x80000000 | 0x40000000
    open_existing = 3
    file_attribute_normal = 0x80
    error_sharing_violation = 32
    error_lock_violation = 33
    invalid_handle = wintypes.HANDLE(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        text,
        generic_read_write,
        0,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle or not handle:
        err = int(ctypes.get_last_error() or 0)
        return err in (error_sharing_violation, error_lock_violation)
    kernel32.CloseHandle(handle)
    return False


def _wow_exe_locked_in_dir(game_dir: Path | str) -> bool:
    """True when this folder's WoW.exe or VanillaFixes.exe is mapped/locked."""
    if sys.platform != "win32":
        return False
    root = str(game_dir or "").strip()
    if not root:
        return False
    return any(_win_exe_in_use(path) for path in _game_dir_exe_paths(root))


def _wow_process_match(game_dir: Path | str | None) -> bool | None:
    """Toolhelp + image path only. Never Restart Manager.

    ``True`` / ``False`` when we can tell. ``None`` when a client name is
    running but the image path is unreadable (OpenProcess ACCESS_DENIED) —
    scoping that case to *game_dir* would need Restart Manager.
    """
    name_seen, images = _wow_process_images()
    if not name_seen:
        return False
    root = str(game_dir or "").strip()
    if not root:
        # No folder to scope to — a locker check is not needed.
        return True
    if images:
        return any(_path_is_under(image, root) for image in images)
    return None


def wow_exe_running(
    game_dir: Path | str | None = None,
    *,
    allow_restart_manager: bool = True,
) -> bool:
    """True when WoW.exe or VanillaFixes.exe is running for this install.

    When *game_dir* is set, only that folder's ``WoW.exe`` / ``VanillaFixes.exe``
    count — exclusive ``CreateFileW`` (sharing violation = mapped/locked).
    OpenProcess ACCESS_DENIED does not matter; this never uses Restart Manager
    (*allow_restart_manager* is ignored). If *game_dir* is omitted, Linux uses
    ``detect_game()`` and ``/proc``; Windows uses Toolhelp for any client name.

    Linux reads ``/proc/<pid>/cmdline`` because Wine loads WoW.exe as data, so
    the kernel never sets ETXTBSY on a live swap.
    """
    del allow_restart_manager  # never Restart Manager — ctypes RM AVs in-process
    if sys.platform != "win32":
        root = game_dir
        if root is None or not str(root).strip():
            root = _configured_game_dir()
        return _proc_client_running(root)
    root = str(game_dir or "").strip()
    if root:
        return _wow_exe_locked_in_dir(root)
    match = _wow_process_match(None)
    return bool(match)


def wow_exe_may_be_running(game_dir: Path | str | None = None) -> bool:
    """True when this install's client exe is locked. Never Restart Manager.

    Use on the UI thread before walking AddOns or Config.wtf (farclip / TOC).
    """
    return wow_exe_running(game_dir)


def processes_locking_paths(paths: list[Path | str], *, limit: int = 6) -> list[str]:
    """Best-effort image names holding *paths* open (Windows Restart Manager).

    Returns ``[]`` when unavailable (non-Windows, API failure, or no lockers found).
    Never raises. Does not require admin.

    Deliberately Windows-only. The Linux equivalent would walk
    ``/proc/<pid>/maps`` and ``/proc/<pid>/fd``; ``wow_exe_running`` already
    covers the case that matters.
    """
    found = _restart_manager_lockers(paths, limit=limit)
    return found if found is not None else []


def _restart_manager_lockers(paths: list[Path | str], *, limit: int = 6) -> list[str] | None:
    """Locker image names, or None when Restart Manager cannot be queried."""
    if sys.platform != "win32" or not paths:
        return None if sys.platform != "win32" else []
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    rstrtmgr = ctypes.windll.rstrtmgr
    kernel32 = ctypes.windll.kernel32

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", wintypes.DWORD),
            ("ProcessStartTime", wintypes.FILETIME),
        ]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", ctypes.c_uint),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(32)
    if int(rstrtmgr.RmStartSession(ctypes.byref(session), 0, key)) != 0:
        return None

    found: list[str] = []
    failed = False
    try:
        existing = [str(Path(p)) for p in paths if p and Path(p).exists()]
        if not existing:
            return []
        arr = (wintypes.LPCWSTR * len(existing))(*existing)
        if int(rstrtmgr.RmRegisterResources(session, len(existing), arr, 0, None, 0, None)) != 0:
            return None

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot = wintypes.DWORD(0)
        # First call often returns ERROR_MORE_DATA (234) with the required size.
        rc = int(
            rstrtmgr.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reboot)
            )
        )
        if needed.value == 0:
            return []
        infos = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        rc = int(
            rstrtmgr.RmGetList(
                session,
                ctypes.byref(needed),
                ctypes.byref(count),
                infos,
                ctypes.byref(reboot),
            )
        )
        if rc not in (0, 234):
            return None

        seen: set[str] = set()
        for i in range(int(count.value)):
            pid = int(infos[i].Process.dwProcessId)
            name = ""
            # Prefer the live image name when QueryFullProcessImageName succeeds.
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(len(buf))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        name = Path(buf.value).name
                finally:
                    kernel32.CloseHandle(handle)
            if not name:
                name = (infos[i].strAppName or "").strip() or f"PID {pid}"
            key_name = name.lower()
            if key_name in seen:
                continue
            seen.add(key_name)
            found.append(name)
            if len(found) >= max(1, int(limit)):
                break
    except (OSError, AttributeError, ValueError, TypeError):
        failed = True
        return None
    finally:
        try:
            rstrtmgr.RmEndSession(session)
        except Exception:  # noqa: BLE001
            pass
    return None if failed else found


def file_in_use_hint(*paths: Path | str) -> str:
    """Short user-facing diagnosis when a game-tree file cannot be replaced.

    Prefers detecting WoW/VanillaFixes, then Restart Manager lockers, then a
    generic "another process" note (not antivirus-first). Always includes the
    steps for ending WoW.exe / VanillaFixes.exe on this platform.
    """
    from ichalaunch.core.filesystem import END_GAME_PROCESS_HINT

    end_tasks = f"{END_GAME_PROCESS_HINT} Then retry Apply."
    game_dir = None
    try:
        from ichalaunch.game.launcher import detect_game

        game_dir = detect_game()
    except Exception:  # noqa: BLE001
        game_dir = None
    if wow_exe_running(game_dir):
        linger = (
            "the game window can be closed while the process stays in Task Manager"
            if sys.platform == "win32"
            else "closing the game window does not always end the Wine process"
        )
        return (
            "WoW.exe or VanillaFixes.exe is still running from this client folder "
            f"({linger}). "
            + end_tasks
        )
    lockers = processes_locking_paths([p for p in paths if p])
    if lockers:
        return f"In use by: {', '.join(lockers)}. {end_tasks}"
    if sys.platform == "win32":
        return (
            "Another process still has the file open "
            "(overlays, Explorer preview, backup/sync, or antivirus — "
            "including non-Defender products). "
            + end_tasks
        )
    return (
        "Another process still has the file open "
        "(a Wine process, an overlay, a file manager preview, or backup/sync). "
        + end_tasks
    )


# Game client DLLs (VanillaHelpers.dll, nampower.dll, VfPatcher.dll, d3d9.dll, …)
# must never be loaded into the IchaLaunch process via ctypes.WinDLL / CDLL /
# QLibrary / kernel32.LoadLibrary. Mapping them runs DllMain here and can crash
# the Qt event loop (or trip Defender on first access). Hash/stat/copy them as
# plain files only, and treat WinError 5/32/225 as skip + backoff.


def child_launch_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the game child: inherit, then apply launcher-owned vars."""
    from ichalaunch.core.logging_setup import log
    from ichalaunch.core.tls import strip_launcher_ca_env
    from ichalaunch.game.nampower_encrypt import apply_wow_encryption_env

    env = dict(os.environ if base is None else base)
    # This process's own CA bundle sits inside the PyInstaller extraction
    # directory, which is removed when the launcher exits. The child outlives
    # it, so the path must not be inherited.
    dropped_ca = strip_launcher_ca_env(env)
    if dropped_ca:
        log.info(
            "Dropped launcher-owned CA variables from the launch environment: %s",
            ", ".join(dropped_ca),
        )
    apply_wow_encryption_env(env)
    return env


def run_windows_exe(argv: list[str], cwd: Path) -> None:
    """Run a Windows command line to completion, raising unless it exits 0.

    The blocking sibling of launch_exe, and it dispatches the same way. On
    Windows the executable runs directly. Everywhere else argv[0] is a PE that
    the kernel refuses to exec (ENOEXEC unless the user happens to have a
    binfmt_misc wine registration), so it goes through Proton, which is the
    same route the game itself takes.

    Used for the tools the launcher drives rather than hands to the player, the
    Vanilla Tweaks patcher among them. Those have to be waited for: the caller
    reads their output the moment they return.
    """
    if not argv:
        raise ValueError("run_windows_exe needs a command")
    if sys.platform == "win32":
        subprocess.run(argv, cwd=str(cwd), check=True)
        return
    from ichalaunch.game.proton import run_windows_exe as _run_under_proton

    _run_under_proton(Path(argv[0]), cwd, argv[1:])


def launch_exe(path: Path, cwd: Path | None = None) -> subprocess.Popen:
    if not path.exists():
        raise FileNotFoundError(str(path))
    workdir = cwd or path.parent
    if sys.platform != "win32":
        # A Windows PE cannot be exec'd here: it needs Proton, and the
        # supported way to drive Proton outside Steam is umu-launcher.
        from ichalaunch.game.proton import launch_windows_exe

        return launch_windows_exe(path, workdir)
    # Vanilla WoW is single-threaded and cache-bound, so on a dual-CCD X3D
    # part it wants the CCD carrying the 3D V-Cache. The mask goes on this
    # process for the duration of the spawn so the child -- and, when
    # VanillaFixes is doing the launching, its own child -- inherits it at
    # creation. No-op on every other CPU.
    from ichalaunch.game.cpu_topology import launch_affinity, vcache_pin_enabled

    env = child_launch_env()
    if not vcache_pin_enabled():
        return subprocess.Popen([str(path)], cwd=str(workdir), env=env, shell=False)
    with launch_affinity():
        return subprocess.Popen([str(path)], cwd=str(workdir), env=env, shell=False)
