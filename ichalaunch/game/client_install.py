"""Download + extract the RavenCraft client, then apply bundled realmlist.wtf."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from requests.adapters import HTTPAdapter

from ichalaunch.core.filesystem import (
    extract_zip,
    is_protected_path,
    robust_move_tree,
    robust_rmtree,
    scan_game_permissions,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import data_file
from ichalaunch.core.process import download_bytes_cb, download_file, status_only, zip_url_from_html
from ichalaunch.game.launcher import (
    CLIENT_ZIP_MIRRORS,
    has_wow_exe,
    GAME_DOWNLOAD_URL,
    GOFILE_EXPECTED_SIZE,
    GOFILE_FILE_ID,
    GOFILE_FILE_NAME,
    GOFILE_MD5,
    GOFILE_STORE,
    VIKINGFILE_ZIP_URL,
    commit_game_home,
    find_wow_exe_dir,
    gofile_content_id,
    gofile_file_link_from_payload,
    validate_install_location,
)

Progress = Callable[[str], None]

GAME_HOME_NAME = "RavenCraft"
_SKIP_UNWRAP = {".ichalaunch"}
_WRAPPER_EXACT = frozenset({"twmoa", "twmoa_1181"})
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

GOFILE_CONTENT_ID = gofile_content_id(GAME_DOWNLOAD_URL) or "zrTbjjv1"
GOFILE_PAGE = GAME_DOWNLOAD_URL
VIKINGFILE_URL = VIKINGFILE_ZIP_URL
BROWSER_ZIP_WAIT_SEC = 12 * 60
_PARTIAL_SUFFIXES = (".crdownload", ".tmp", ".part", ".partial", ".download")
_WIN_DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"
_WIN_DESKTOP_GUID = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_JSON_HDRS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://gofile.io",
    "Referer": "https://gofile.io/",
    "X-BL": "en-US",
}
_GOFILE_API = "https://api.gofile.io"
# gallery-dl / gofile-dl salts; JS scrape may add the current one.
_GOFILE_WT_SALTS = ("5d4f7g8sd45fsd", "12af056dacea0b")
_GOFILE_STATIC_WT = "4fd6sg89d7s6"
# api.gofile.io is often firewalled; fail fast (no urllib3 retry doubling).
_GOFILE_CONNECT_SEC = 4
_GOFILE_READ_SEC = 12
_GOFILE_TIMEOUT = (_GOFILE_CONNECT_SEC, _GOFILE_READ_SEC)
_GOFILE_STORE_HOSTS = (
    f"{GOFILE_STORE}.gofile.io",
    "store-na-phx-4.gofile.io",
    "store4.gofile.io",
)


def bundled_realmlist() -> Path:
    return data_file("realmlist.wtf")


def _preserve_existing_realmlist(dest: Path, payload: bytes) -> bool:
    """Keep an existing realmlist.wtf as .bak / .bak2 / .bak3 before replacing it.

    A hand-edited realmlist is the user's own work and used to be destroyed
    without warning. A numbered copy beside the original costs a few bytes and
    leaves the old contents somewhere they can be found.

    Returns True when the caller may go ahead and write.
    """
    try:
        if not dest.is_file():
            return True
        if dest.read_bytes() == payload:
            # Already what we would write -- do not manufacture a backup of it.
            return False
    except OSError as exc:
        log.warning("Could not read %s: %s", dest, exc)
        return False

    for n in range(1, 100):
        spare = dest.with_suffix(".bak" if n == 1 else f".bak{n}")
        if spare.exists():
            continue
        try:
            shutil.copy2(dest, spare)
        except OSError as exc:
            # Never trade the user's realmlist for a failed backup.
            log.warning("Could not preserve %s (%s); leaving it alone", dest, exc)
            return False
        log.info("Preserved existing realmlist -> %s", spare)
        return True

    log.warning("Too many realmlist backups beside %s; leaving it alone", dest)
    return False


def apply_bundled_realmlist(game_root: Path) -> None:
    """Write the bundled realmlist.wtf, keeping any existing one as .bak."""
    src = bundled_realmlist()
    if not src.is_file():
        log.warning("Bundled realmlist.wtf missing at %s", src)
        return
    payload = src.read_bytes()
    targets = {game_root / "realmlist.wtf"}
    try:
        # Matched case-insensitively: clients ship both realmlist.wtf and
        # RealmList.wtf, and only Windows treats those as the same file.
        for found in game_root.rglob("*.wtf"):
            if found.name.lower() == "realmlist.wtf" and found.is_file():
                targets.add(found)
    except OSError:
        pass
    for dest in targets:
        if not _preserve_existing_realmlist(dest, payload):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        log.info("Wrote realmlist.wtf -> %s", dest)


def find_wow_root(start: Path) -> Path | None:
    """Directory that contains WoW.exe under *start* (itself or a child)."""
    return find_wow_exe_dir(start)


def wow_exe_here(picked: Path) -> Path | None:
    """Return *picked* or ``picked/RavenCraft`` if that folder contains WoW.exe.

    Two ``is_file`` checks only — safe on the GUI thread. Does not ``rglob``.
    """
    picked = Path(picked)
    try:
        if has_wow_exe(picked):
            return picked
        home = ravencraft_home_for(picked)
        if home != picked and has_wow_exe(home):
            return home
    except OSError:
        return None
    return None


def _gofile_wt(user_agent: str, account_token: str, salt: str, lang: str = "en-US") -> str:
    slot = int(time.time()) // 14400
    raw = f"{user_agent}::{lang}::{account_token}::{slot}::{salt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _gofile_salts_from_js(js: str) -> list[str]:
    found: list[str] = []

    def add(value: str) -> None:
        text = (value or "").strip()
        if 8 <= len(text) <= 24 and re.fullmatch(r"[A-Za-z0-9]+", text) and text not in found:
            found.append(text)

    for enc in re.findall(r"(?:\\x[0-9a-fA-F]{2}){6,24}", js or ""):
        try:
            raw = bytes(int(enc[i + 2 : i + 4], 16) for i in range(0, len(enc), 4))
            add(raw.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            continue
    for m in re.finditer(r"websiteToken['\"]?\s*[:=]\s*['\"]([^'\"]+)", js or ""):
        add(m.group(1))
    return found


def _tcp_open(host: str, port: int = 443, timeout: float = _GOFILE_CONNECT_SEC) -> bool:
    """True if TCP connect succeeds. Does not wait on a second urllib3 retry."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _no_retry_session(headers: dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.headers.update(headers)
    adapter = HTTPAdapter(max_retries=0, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _gofile_guest_token(session: requests.Session) -> str:
    r = session.post(f"{_GOFILE_API}/accounts", json={}, timeout=_GOFILE_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    token = (data.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("Gofile did not return a guest token")
    return str(token)


def _gofile_salt_candidates(session: requests.Session) -> list[str]:
    salts: list[str] = []
    try:
        jr = session.get("https://gofile.io/js/wt.obf.js", timeout=_GOFILE_TIMEOUT)
        if jr.status_code == 200 and jr.text:
            for s in _gofile_salts_from_js(jr.text):
                if s not in salts:
                    salts.append(s)
    except requests.RequestException:
        pass
    for s in _GOFILE_WT_SALTS:
        if s not in salts:
            salts.append(s)
    return salts or list(_GOFILE_WT_SALTS)


def _gofile_auth_headers(token: str | None) -> dict[str, str]:
    extra = {"Referer": GOFILE_PAGE, "Origin": "https://gofile.io"}
    if token:
        extra["Authorization"] = f"Bearer {token}"
        extra["Cookie"] = f"accountToken={token}"
    return extra


def _gofile_contents(
    session: requests.Session,
    content_id: str,
    token: str,
    salts: list[str],
) -> dict[str, Any] | None:
    """GET /contents/{id} — file id first (folder listing is premium-gated)."""
    attempts: list[tuple[str, dict[str, str]]] = []
    for salt in salts[:2]:
        wt = _gofile_wt(_UA, token, salt)
        attempts.append((wt, {"X-Website-Token": wt}))
    attempts.append((_GOFILE_STATIC_WT, {"X-Website-Token": _GOFILE_STATIC_WT}))
    last_status = ""
    for wt, extra in attempts:
        params = {
            "wt": wt,
            "contentFilter": "",
            "page": "1",
            "pageSize": "1000",
            "sortField": "name",
            "sortDirection": "1",
            "cache": "true",
        }
        try:
            cr = session.get(
                f"{_GOFILE_API}/contents/{content_id}",
                params=params,
                headers=extra,
                timeout=_GOFILE_TIMEOUT,
            )
            js = cr.json()
        except (requests.RequestException, ValueError) as exc:
            last_status = str(exc)
            continue
        status = str(js.get("status") or "")
        last_status = status or f"HTTP {cr.status_code}"
        if status == "ok" and isinstance(js.get("data"), dict):
            return js["data"]
        if status in ("error-notFound", "error-passwordRequired", "error-notPremium"):
            log.info("Gofile /contents/%s -> %s", content_id, status)
            return None
        if status == "error-rateLimit":
            break
    log.info("Gofile /contents/%s failed (%s)", content_id, last_status)
    return None


def _probe_store_zip(progress: Progress | None = None) -> str | None:
    """If the store host serves the zip without a signed query, return that URL.

    CDN query-strings expire — we only accept a live, unsigned store path that
    already looks like a zip (magic / type / advertised size). Do not follow
    redirects onto gofile.io (blocked on some networks).
    """
    session = _no_retry_session({**_JSON_HDRS, "Accept": "*/*"})
    name = GOFILE_FILE_NAME
    fid = GOFILE_FILE_ID
    paths = (
        f"/download/direct/{fid}/{name}",
        f"/download/web/{fid}/{name}",
        f"/download/{fid}/{name}",
    )
    seen: set[str] = set()
    for host in _GOFILE_STORE_HOSTS:
        if host in seen:
            continue
        seen.add(host)
        if not _tcp_open(host, timeout=3):
            continue
        if progress:
            status_only(progress, f"Trying Gofile store {host}…")
        for path in paths:
            url = f"https://{host}{path}"
            try:
                r = session.get(
                    url,
                    headers={"Range": "bytes=0-3"},
                    timeout=_GOFILE_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                continue
            try:
                loc = (r.headers.get("Location") or "").lower()
                if loc.startswith("https://gofile.io/") or loc.startswith("https://www.gofile.io/"):
                    continue
                ctype = (r.headers.get("Content-Type") or "").lower()
                clen = int(r.headers.get("Content-Length") or 0)
                if r.status_code >= 400:
                    continue
                peek = next(r.iter_content(8), b"")
                zip_like = (
                    peek.startswith(b"PK")
                    or "zip" in ctype
                    or "octet-stream" in ctype
                    or clen == GOFILE_EXPECTED_SIZE
                )
                if zip_like:
                    log.info("Gofile store zip reachable: %s", url)
                    return url
            finally:
                r.close()
    return None


def resolve_gofile_direct(progress: Progress | None = None) -> tuple[str, str, dict[str, str]]:
    """Resolve twmoa_1181.zip via file id (not the premium-gated folder listing)."""
    if _tcp_open("api.gofile.io"):
        status_only(progress, "Resolving Gofile file…")
        session = _no_retry_session(_JSON_HDRS)
        token = _gofile_guest_token(session)
        session.headers["Authorization"] = f"Bearer {token}"
        session.cookies.set("accountToken", token, domain=".gofile.io")
        salts = _gofile_salt_candidates(session)
        payload = _gofile_contents(session, GOFILE_FILE_ID, token, salts)
        if payload is None:
            payload = _gofile_contents(session, GOFILE_CONTENT_ID, token, salts)
        if payload is None:
            try:
                cr = session.get(
                    f"{_GOFILE_API}/getContent",
                    params={
                        "contentId": GOFILE_FILE_ID,
                        "token": token,
                        "websiteToken": salts[0] if salts else _GOFILE_STATIC_WT,
                    },
                    timeout=_GOFILE_TIMEOUT,
                )
                js = cr.json()
                if js.get("status") == "ok" and isinstance(js.get("data"), dict):
                    payload = js["data"]
            except (requests.RequestException, ValueError):
                payload = None
        if payload is not None:
            url, name = gofile_file_link_from_payload(payload)
            log.info("Resolved Gofile client zip %s: %s", name, url.split("?", 1)[0])
            return url, name, _gofile_auth_headers(token)
        raise RuntimeError(
            "Gofile API reachable but /contents did not return a download link "
            f"for file {GOFILE_FILE_ID}."
        )

    status_only(progress, "Gofile API blocked — trying store host…")
    log.warning("api.gofile.io is unreachable; probing store hosts")
    store_url = _probe_store_zip(progress)
    if store_url:
        return store_url, GOFILE_FILE_NAME, _gofile_auth_headers(None)
    raise RuntimeError(
        "Gofile website/API (gofile.io / api.gofile.io) is blocked on this network. "
        f"{GOFILE_STORE}.gofile.io is reachable, but the zip is not served without "
        "a live API token (unsigned /download/direct and /download/web 404 or "
        "redirect to the blocked site). Cannot bake a CDN URL — those expire."
    )


def resolve_vikingfile_direct() -> tuple[str, dict[str, str]]:
    """Follow Vikingfile landing page to a real zip URL when possible."""
    extra = {"Referer": VIKINGFILE_URL, "Accept": "*/*"}
    session = _no_retry_session({"User-Agent": _UA, "Accept": "*/*", "Referer": VIKINGFILE_URL})
    r = session.get(
        VIKINGFILE_URL,
        timeout=(15, 45),
        allow_redirects=True,
        stream=True,
    )
    try:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "zip" in ctype or "octet-stream" in ctype:
            log.info("Vikingfile direct zip: %s", str(r.url).split("?", 1)[0])
            return str(r.url), extra
        peek = next(r.iter_content(8), b"")
        if peek.startswith(b"PK"):
            return str(r.url), extra
        if "html" in ctype or peek[:1] == b"<" or peek[:15].lower().startswith(b"<!doctype"):
            r.close()
            page = session.get(
                VIKINGFILE_URL,
                headers={"Accept": "text/html"},
                timeout=(15, 45),
            )
            page.raise_for_status()
            href = zip_url_from_html(page.text, str(page.url))
            if href:
                log.info("Resolved Vikingfile zip href: %s", href.split("?", 1)[0])
                return href, extra
            raise RuntimeError(
                "Vikingfile returned a web page instead of the zip.\n" + VIKINGFILE_URL
            )
    finally:
        r.close()
    return VIKINGFILE_URL, extra


def _zip_magic_ok(path: Path) -> bool:
    if path.stat().st_size < 64:
        return False
    with path.open("rb") as f:
        return f.read(2) == b"PK"


def _win_user_shell_folder(name: str) -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    keys = (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )
    for key_path in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                raw, _typ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        text = os.path.expandvars(str(raw or "")).strip()
        if not text:
            continue
        path = Path(text)
        if path.is_dir():
            return path
    return None


def _win_known_folder(folder_id: str) -> Path | None:
    """Resolve a Windows Known Folder (FOLDERID_Downloads / Desktop) via shell32."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
    except ImportError:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    try:
        u = uuid.UUID(folder_id)
        guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (wintypes.BYTE * 8).from_buffer_copy(u.bytes[8:]))
        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        SHGetKnownFolderPath.restype = ctypes.HRESULT
        SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        ppath = ctypes.c_wchar_p()
        hr = SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ppath))
        if hr != 0 or not ppath.value:
            return None
        text = ppath.value
        ctypes.windll.ole32.CoTaskMemFree(ppath)
        path = Path(os.path.expandvars(text))
        if path.is_dir():
            return path
    except OSError:
        return None
    return None


def client_watch_dirs(extra: Iterable[Path] | None = None) -> list[Path]:
    """Downloads, Desktop, and optional extra folders to watch for the client zip."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            return
        seen.add(key)
        found.append(resolved)

    add(_win_known_folder(_WIN_DOWNLOADS_GUID))
    add(_win_user_shell_folder(_WIN_DOWNLOADS_GUID))
    add(_win_user_shell_folder("Downloads"))
    add(_win_known_folder(_WIN_DESKTOP_GUID))
    add(_win_user_shell_folder("Desktop"))
    home = Path.home()
    add(home / "Downloads")
    add(home / "OneDrive" / "Downloads")
    add(home / "Desktop")
    add(home / "OneDrive" / "Desktop")
    if extra:
        for item in extra:
            add(Path(item))
            add(Path(item) / ".ichalaunch")
    return found


def _is_partial_name(name: str, zip_name: str) -> bool:
    lower = name.lower()
    zip_l = zip_name.lower()
    zip_stem = Path(zip_name).stem.lower()
    # Chrome / Edge: "Unconfirmed 809132.crdownload" (final name not in the temp file).
    if "unconfirmed" in lower and "crdownload" in lower:
        return True
    if lower.startswith("unconfirmed") and any(lower.endswith(s) for s in _PARTIAL_SUFFIXES):
        return True
    for suf in _PARTIAL_SUFFIXES:
        if lower == zip_l + suf:
            return True
        if lower.endswith(suf) and (zip_l in lower or zip_stem in lower):
            return True
    return False


def _named_zip_candidates(directory: Path, zip_name: str) -> list[Path]:
    stem = Path(zip_name).stem.lower()
    suffix = Path(zip_name).suffix.lower()
    out: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return out
    for path in entries:
        if not path.is_file():
            continue
        n = path.name
        nl = n.lower()
        if nl == zip_name.lower():
            out.append(path)
        elif nl.startswith(stem) and nl.endswith(suffix) and "(" in n:
            out.append(path)
    out.sort(key=lambda p: (p.name.lower() != zip_name.lower(), p.name.lower()))
    return out


def _partial_downloads(directory: Path, zip_name: str, expected_size: int) -> list[Path]:
    found: list[Path] = []
    unnamed: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return found
    for path in entries:
        if not path.is_file():
            continue
        name = path.name
        if _is_partial_name(name, zip_name):
            found.append(path)
            continue
        lower = name.lower()
        if any(lower.endswith(suf) for suf in _PARTIAL_SUFFIXES):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if expected_size and size > 10_000_000:
                unnamed.append(path)
            elif not expected_size and size > 64:
                unnamed.append(path)
    if found:
        return found
    return unnamed


def _size_matches_expected(size: int, expected_size: int) -> bool:
    if not expected_size:
        return True
    if size == expected_size:
        return True
    return size >= int(expected_size * 0.999)


def zip_looks_complete(
    path: Path,
    *,
    expected_size: int = GOFILE_EXPECTED_SIZE,
    require_stable: bool = False,
) -> bool:
    """True if *path* is a finished client zip (not a browser temp file)."""
    try:
        if not path.is_file():
            return False
        name = path.name.lower()
        if any(name.endswith(suf) for suf in _PARTIAL_SUFFIXES):
            return False
        if path.suffix.lower() != ".zip":
            return False
        size = path.stat().st_size
        if not _zip_magic_ok(path):
            return False
        if not expected_size:
            return True
        if size == expected_size:
            return True
        if require_stable and _size_matches_expected(size, expected_size):
            return True
    except OSError:
        return False
    return False


def find_complete_client_zip(
    dirs: list[Path] | None = None,
    *,
    extra_dirs: Iterable[Path] | None = None,
    expected_size: int = GOFILE_EXPECTED_SIZE,
    name: str = GOFILE_FILE_NAME,
) -> Path | None:
    """Return a finished ``twmoa_1181.zip`` in Downloads/Desktop if present."""
    search = list(dirs) if dirs is not None else client_watch_dirs(extra_dirs)
    for folder in search:
        for cand in _named_zip_candidates(folder, name):
            if zip_looks_complete(cand, expected_size=expected_size):
                return cand
    return None


def _pick_progress_file(
    paths: Iterable[Path],
    zip_name: str,
    expected_size: int,
) -> Path | None:
    """Prefer a named in-progress zip; then the largest plausible temp file."""
    best: Path | None = None
    best_key: tuple[int, int, int] | None = None
    zip_l = zip_name.lower()
    zip_stem = Path(zip_name).stem.lower()
    seen: set[str] = set()
    for path in paths:
        try:
            ident = str(path.resolve()).lower()
        except OSError:
            ident = str(path).lower()
        if ident in seen:
            continue
        seen.add(ident)
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        name = path.name.lower()
        named = zip_l in name or zip_stem in name
        over = 1 if expected_size and size > int(expected_size * 1.02) else 0
        rank = (0 if named else 1, over, -size)
        if best_key is None or rank < best_key:
            best_key = rank
            best = path
    return best


def _report_partial_progress(
    progress: Progress | None,
    path: Path,
    expected_size: int,
) -> None:
    """Push determinate browser-download percent. Never call progress() (indeterminate)."""
    if progress is None:
        return
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    on_count = getattr(progress, "on_count", None)
    on_bytes = getattr(progress, "on_bytes", None)
    set_status = getattr(progress, "set_status", None)
    if expected_size > 0:
        pct = max(0, min(99, int(size * 100 / expected_size)))
        msg = f"Downloading in browser… {pct}%"
        if callable(on_count):
            on_count(size, expected_size, msg)
            return
        if callable(on_bytes):
            if callable(set_status):
                set_status("Downloading in browser…")
            on_bytes(size, expected_size)
            return
        progress(msg)
        return
    if callable(set_status):
        set_status("Downloading in browser…")
        return
    progress("Downloading in browser…")


def wait_for_browser_zip(
    progress: Progress | None = None,
    *,
    timeout_sec: int = BROWSER_ZIP_WAIT_SEC,
    poll_sec: float = 1.0,
    dirs: list[Path] | None = None,
    extra_dirs: Iterable[Path] | None = None,
    expected_size: int = GOFILE_EXPECTED_SIZE,
    name: str = GOFILE_FILE_NAME,
    stable_polls: int = 3,
) -> Path | None:
    """Poll Downloads (and extras) until the client zip is complete, or time out.

    Treats Chrome ``*.crdownload`` / Edge ``*.partial`` / Firefox ``*.part`` /
    ``Unconfirmed *crdownload*`` as in-progress. A ``.zip`` whose size matches
    *expected_size* is accepted immediately; otherwise the size must stay
    unchanged for *stable_polls*.
    """
    search = list(dirs) if dirs is not None else client_watch_dirs(extra_dirs)
    deadline = time.monotonic() + max(1, int(timeout_sec))
    last_sizes: dict[str, tuple[int, int]] = {}
    if progress:
        # Indeterminate until an in-progress file appears — do not repeat this
        # each poll (StatusProgress.__call__ would wipe determinate %).
        progress("Waiting for download…")
    while time.monotonic() < deadline:
        in_progress: list[Path] = []
        for folder in search:
            for cand in _named_zip_candidates(folder, name):
                if zip_looks_complete(cand, expected_size=expected_size):
                    status_only(progress, "Download found…")
                    return cand
                try:
                    size = cand.stat().st_size
                except OSError:
                    continue
                key = str(cand)
                prev_size, streak = last_sizes.get(key, (size, 0))
                streak = streak + 1 if size == prev_size else 1
                last_sizes[key] = (size, streak)
                if streak >= stable_polls and zip_looks_complete(
                    cand, expected_size=expected_size, require_stable=True
                ):
                    status_only(progress, "Download found…")
                    return cand
                in_progress.append(cand)
            in_progress.extend(_partial_downloads(folder, name, expected_size))
        best = _pick_progress_file(in_progress, name, expected_size)
        if best is not None:
            _report_partial_progress(progress, best, expected_size)
        slept = 0.0
        step = min(0.25, poll_sec) if poll_sec > 0 else 0.0
        while slept < poll_sec and time.monotonic() < deadline:
            time.sleep(step or poll_sec)
            slept += step or poll_sec
        if poll_sec <= 0:
            break
    return None


def _verify_client_zip(path: Path, progress: Progress | None = None) -> None:
    """Confirm zip magic, advertised size, and MD5 from the Gofile Properties dialog."""
    if not _zip_magic_ok(path):
        raise RuntimeError("Downloaded file is not a zip archive")
    size = path.stat().st_size
    if GOFILE_EXPECTED_SIZE and size != GOFILE_EXPECTED_SIZE:
        raise RuntimeError(
            f"Zip size {size} bytes does not match Gofile listing "
            f"({GOFILE_EXPECTED_SIZE} bytes)."
        )
    if not GOFILE_MD5:
        return
    status_only(progress, "Verifying download…")
    digest = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    got = digest.hexdigest()
    if got.lower() != GOFILE_MD5.lower():
        raise RuntimeError(f"Zip MD5 {got} does not match Gofile listing ({GOFILE_MD5}).")


def _download_zip(dest: Path, progress: Any) -> None:
    """Stream the client zip to *dest*; Gofile first, Vikingfile last resort."""
    byte_cb = download_bytes_cb(progress)
    errors: list[str] = []
    try:
        url, _name, extra = resolve_gofile_direct(progress)
        status_only(progress, "Downloading from Gofile…")
        download_file(
            url,
            dest,
            progress=byte_cb,
            timeout=(30, 600),
            extra_headers=extra,
            source_url=GOFILE_PAGE,
        )
        _verify_client_zip(dest, progress)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("Gofile client download failed: %s", exc)
        errors.append(f"{GOFILE_PAGE}\n{exc}")
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
    try:
        status_only(progress, "Gofile blocked on this network — falling back to Vikingfile…")
        url, extra = resolve_vikingfile_direct()
        status_only(progress, "Downloading from Vikingfile…")
        download_file(
            url,
            dest,
            progress=byte_cb,
            timeout=(30, 600),
            extra_headers=extra,
            source_url=VIKINGFILE_URL,
        )
        _verify_client_zip(dest, progress)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("Vikingfile client download failed: %s", exc)
        errors.append(f"{VIKINGFILE_URL}\n{exc}")
    raise RuntimeError(
        "Could not download the WoW client zip.\nTried:\n"
        + "\n".join(CLIENT_ZIP_MIRRORS)
        + "\n\n"
        + "\n\n".join(errors)
    )


def ravencraft_home_for(picked: Path) -> Path:
    """Game root: *picked* if already named RavenCraft, else ``picked/RavenCraft``."""
    picked = Path(picked)
    if picked.name.lower() == GAME_HOME_NAME.lower():
        return picked
    return picked / GAME_HOME_NAME


def _is_wrapper_name(name: str) -> bool:
    """True for Gofile/UUID/twmoa wrapper folders — not a real game-home name."""
    n = (name or "").strip()
    low = n.lower()
    if not n or low == GAME_HOME_NAME.lower() or low in _SKIP_UNWRAP:
        return False
    if low in _WRAPPER_EXACT or low.startswith("twmoa_"):
        return True
    if _UUID_RE.fullmatch(n):
        return True
    if re.fullmatch(r"[0-9a-f]{16,}", n, re.I):
        return True
    if re.fullmatch(r"[A-Za-z0-9]{12,64}", n):
        return True
    return False


def _prune_empty_ancestors(start: Path, stop: Path) -> None:
    """Remove empty dirs from *start* up to but not including *stop*."""
    try:
        stop_res = stop.resolve()
    except OSError:
        stop_res = stop
    current = start
    for _ in range(24):
        if not current.exists() or not current.is_dir():
            current = current.parent
            continue
        try:
            cur_res = current.resolve()
            if cur_res == stop_res or cur_res == current.parent.resolve():
                return
        except OSError:
            return
        kids = [c for c in current.iterdir() if c.name not in _SKIP_UNWRAP]
        if kids:
            return
        parent = current.parent
        try:
            current.rmdir()
        except OSError:
            return
        current = parent


def _move_tree_children(src: Path, dest: Path) -> None:
    """Hoist every child of *src* into *dest* (merge dirs; overwrite files)."""
    dest.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for child in list(src.iterdir()):
        if child.name in _SKIP_UNWRAP:
            continue
        target = dest / child.name
        try:
            if child.resolve() == dest.resolve() or child.resolve() == target.resolve():
                continue
        except OSError:
            pass
        if child.is_dir():
            if target.exists() and target.is_dir():
                _move_tree_children(child, target)
                try:
                    child.rmdir()
                except OSError:
                    robust_rmtree(child)
            else:
                robust_move_tree(child, target)
        else:
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    robust_rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target))


def unwrap_to_ravencraft(target: Path) -> Path:
    """Hoist WoW.exe (and siblings) to *target*, deleting leftover wrapper dirs."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    wow = find_wow_exe_dir(target)
    if wow is None:
        return target
    try:
        if wow.resolve() == target.resolve():
            return target
    except OSError:
        if wow == target:
            return target
    _move_tree_children(wow, target)
    _prune_empty_ancestors(wow, target)
    if wow.exists() and wow.resolve() != target.resolve():
        leftovers = [c for c in wow.iterdir()] if wow.is_dir() else []
        if not leftovers:
            try:
                wow.rmdir()
            except OSError:
                robust_rmtree(wow)
        else:
            robust_rmtree(wow)
        _prune_empty_ancestors(wow.parent, target)
    return target


def should_settle_existing(picked: Path, wow_dir: Path) -> bool:
    """True when an existing client sits under jumble/UUID wrappers we should rename."""
    picked = Path(picked)
    wow_dir = Path(wow_dir)
    target = ravencraft_home_for(picked)
    try:
        if wow_dir.resolve() == target.resolve():
            return False
        rel = wow_dir.resolve().relative_to(picked.resolve())
    except ValueError:
        return False
    except OSError:
        return False
    parts = rel.parts
    if not parts:
        # User picked the game folder directly — never treat it as disposable packaging.
        return False
    return all(_is_wrapper_name(p) for p in parts)


def settle_ravencraft_home(picked: Path, wow_dir: Path | None = None) -> Path:
    """Move the extracted/found WoW tree to ``picked/RavenCraft`` (or *picked*)."""
    picked = Path(picked)
    target = ravencraft_home_for(picked)
    wow = wow_dir or find_wow_exe_dir(target) or find_wow_exe_dir(picked)
    if wow is None:
        return target
    try:
        wow_res = wow.resolve()
        target_res = target.resolve()
        picked_res = picked.resolve()
    except OSError:
        wow_res, target_res, picked_res = wow, target, picked
    if wow_res == target_res:
        return target
    try:
        wow_res.relative_to(target_res)
        inside_target = True
    except ValueError:
        inside_target = False
    if inside_target:
        unwrap_to_ravencraft(target)
        return target
    try:
        target_res.relative_to(wow_res)
    except ValueError:
        pass
    else:
        log.warning(
            "Refusing to settle %s into its own subfolder %s",
            wow_res,
            target_res,
        )
        return wow
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_after_move = wow.parent
    if not target.exists():
        robust_move_tree(wow, target)
    else:
        _move_tree_children(wow, target)
        if wow.exists() and wow_res != target_res:
            robust_rmtree(wow)
    if parent_after_move.exists() and parent_after_move.resolve() != picked_res:
        _prune_empty_ancestors(parent_after_move, picked)
    unwrap_to_ravencraft(target)
    return target


def _named_partial_leftover(name: str, zip_name: str) -> bool:
    """True for ``twmoa_1181.zip.crdownload``-style leftovers we created/watched."""
    lower = (name or "").lower()
    zip_l = zip_name.lower()
    zip_stem = Path(zip_name).stem.lower()
    for suf in _PARTIAL_SUFFIXES:
        if lower == zip_l + suf:
            return True
        if lower.endswith(suf) and (zip_l in lower or zip_stem in lower):
            return True
    return False


def _unlink_client_zip(path: Path) -> None:
    """Delete one leftover file. Never rmtree a directory (RavenCraft must stay)."""
    try:
        if not path.is_file():
            return
        path.unlink()
        log.info("Removed client zip leftover: %s", path)
    except OSError as exc:
        log.warning("Could not remove client zip %s: %s", path, exc)


def cleanup_client_zip(
    dest: Path,
    zip_path: Path | None = None,
    *,
    name: str = GOFILE_FILE_NAME,
    watch_dirs: Iterable[Path] | None = None,
) -> None:
    """Delete ``twmoa_1181.zip`` and owned in-progress leftovers after a successful install.

    Removes the extracted archive (Downloads/Desktop/browse path), copies under
    ``{dest}/.ichalaunch/``, and matching ``*.crdownload`` / ``*.partial`` leftovers
    in the watch folders. Does not delete the RavenCraft game tree or unrelated zips.
    """
    dest = Path(dest)
    staging = dest / ".ichalaunch"
    targets: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        targets.append(Path(path))

    if zip_path is not None:
        add(Path(zip_path))
    add(staging / name)

    scan: list[Path] = [staging]
    if watch_dirs is None:
        scan.extend(client_watch_dirs(extra=(dest, staging)))
    else:
        scan.extend(Path(p) for p in watch_dirs)
        scan.append(dest)
        scan.append(staging)

    for folder in scan:
        try:
            if not folder.is_dir():
                continue
            add(folder / name)
            for child in folder.iterdir():
                if child.is_file() and _named_partial_leftover(child.name, name):
                    add(child)
        except OSError:
            continue

    for path in targets:
        _unlink_client_zip(path)


def _extract_client(
    dest: Path,
    zip_path: Path,
    progress: Progress | None,
    *,
    watch_dirs: Iterable[Path] | None = None,
) -> str:
    if not _zip_magic_ok(zip_path):
        raise RuntimeError(f"File is not a zip archive:\n{zip_path}")
    size = zip_path.stat().st_size
    if GOFILE_EXPECTED_SIZE and size != GOFILE_EXPECTED_SIZE:
        log.warning(
            "Client zip size %s does not match Gofile listing %s (%s)",
            size,
            GOFILE_EXPECTED_SIZE,
            zip_path,
        )
    target = ravencraft_home_for(dest)
    target.mkdir(parents=True, exist_ok=True)
    extract_zip(zip_path, target, progress=progress)
    unwrap_to_ravencraft(target)
    game = target if has_wow_exe(target) else find_wow_exe_dir(target)
    if game is None:
        raise RuntimeError(
            f"WoW.exe was not found after extracting into {target}. "
            "Pick a different folder or extract the zip manually."
        )
    if game.resolve() != target.resolve():
        settle_ravencraft_home(dest, game)
        game = target if has_wow_exe(target) else find_wow_exe_dir(target)
        if game is None:
            raise RuntimeError(
                f"WoW.exe was not found after extracting into {target}. "
                "Pick a different folder or extract the zip manually."
            )
    set_status = getattr(progress, "set_status", None) if progress is not None else None
    if callable(set_status):
        set_status("Writing realmlist.wtf…")
    elif progress:
        progress("Writing realmlist.wtf…")
    apply_bundled_realmlist(target if has_wow_exe(target) else game)
    home = target if has_wow_exe(target) else game
    commit_game_home(home)
    _log_post_install_permissions(home)
    cleanup_client_zip(dest, zip_path, watch_dirs=watch_dirs)
    if callable(set_status):
        set_status("Install complete")
    elif progress:
        progress("Install complete")
    return str(home)


def _log_post_install_permissions(home: Path) -> None:
    scan = scan_game_permissions(home)
    if scan.has_issues:
        log.warning(
            "Post-install permission scan found %d issue(s) under %s",
            len(scan.issues),
            home,
        )
    else:
        log.info("Post-install permission scan OK for %s", home)


def install_client(
    dest: Path,
    progress: Progress | None = None,
    *,
    zip_path: Path | str | None = None,
    wait_sec: int = BROWSER_ZIP_WAIT_SEC,
    auto_download: bool = False,
    cleanup_watch_dirs: Iterable[Path] | None = None,
) -> str | None:
    """Install the client into *dest*.

    Primary path: wait for the user to download ``twmoa_1181.zip`` in a browser
    (Gofile), then extract. Returns ``None`` if that wait times out so the UI
    can offer browse-for-zip or auto-download.

    *zip_path* extracts a zip the user already has. *auto_download* uses the
    Gofile API / Vikingfile as a last-resort fallback.
    """
    dest = Path(dest)
    if is_protected_path(dest):
        ok, msg = validate_install_location(dest)
        if not ok:
            raise ValueError(msg)
    ok, msg = validate_install_location(dest)
    if not ok:
        raise ValueError(msg)
    dest.mkdir(parents=True, exist_ok=True)

    existing = find_wow_exe_dir(dest)
    if existing is not None:
        if should_settle_existing(dest, existing):
            existing = settle_ravencraft_home(dest, existing)
        apply_bundled_realmlist(existing)
        commit_game_home(existing)
        _log_post_install_permissions(existing)
        return str(existing)

    chosen: Path | None = Path(zip_path) if zip_path else None
    if chosen is not None:
        if not chosen.is_file():
            raise FileNotFoundError(f"Zip not found:\n{chosen}")
    elif auto_download:
        staging = dest / ".ichalaunch"
        staging.mkdir(parents=True, exist_ok=True)
        chosen = staging / GOFILE_FILE_NAME
        status_only(progress, "Downloading…")
        _download_zip(chosen, progress)
    else:
        if progress:
            progress("Waiting for download…")
        chosen = wait_for_browser_zip(
            progress,
            timeout_sec=wait_sec,
            extra_dirs=(dest, dest / ".ichalaunch"),
        )
        if chosen is None:
            return None

    return _extract_client(dest, chosen, progress, watch_dirs=cleanup_watch_dirs)
