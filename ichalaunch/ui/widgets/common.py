"""Reusable UI widgets."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen
from PySide6.QtCore import QObject, QPoint, QProcess, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import (
    GLUE_ROW_H,
    GLUE_ROW_MENU_W,
    GLUE_ROW_W,
    GluePanelButton,
    glue_row_square_chrome,
)
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox

_OPTIONS_COG = "UI-OptionsButton.PNG"
_OPTIONS_COG_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / _OPTIONS_COG
_OPTIONS_COG_PX = 20
_OPTIONS_COG_CACHE: QPixmap | None = None

_PASS_UP = "UI-GroupLoot-Pass-Up.PNG"
_PASS_DOWN = "UI-GroupLoot-Pass-Down.PNG"
_PASS_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons")
_PASS_ICON_PX = 18
_PASS_CACHE: dict[str, QPixmap] = {}


def _options_cog_pixmap() -> QPixmap:
    """Bundled WoW UI-OptionsButton, scaled for the addons row cog."""
    global _OPTIONS_COG_CACHE
    if _OPTIONS_COG_CACHE is not None:
        return _OPTIONS_COG_CACHE
    path = theme_file(_OPTIONS_COG)
    if not path.is_file():
        path = _OPTIONS_COG_EXTERNAL
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _OPTIONS_COG_PX,
                _OPTIONS_COG_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _OPTIONS_COG_CACHE = pm
    return pm


class OptionsCogButton(QPushButton):
    """Addons repository-settings control painted with UI-OptionsButton art."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("OptionsCogButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#OptionsCogButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._icon = _options_cog_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        icon = self._icon
        if icon.isNull():
            painter.setPen(Qt.GlobalColor.yellow)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "⚙")
            painter.end()
            return
        if self.isDown():
            painter.setOpacity(0.75)
        elif self.underMouse():
            painter.setOpacity(1.0)
        else:
            painter.setOpacity(0.92)
        x = rect.center().x() - icon.width() // 2
        y = rect.center().y() - icon.height() // 2 + (1 if self.isDown() else 0)
        painter.drawPixmap(x, y, icon)
        painter.end()


def _pass_icon_pixmap(pressed: bool) -> QPixmap:
    """Bundled WoW GroupLoot Pass art for the addon-row Remove control."""
    key = "down" if pressed else "up"
    hit = _PASS_CACHE.get(key)
    if hit is not None:
        return hit
    name = _PASS_DOWN if pressed else _PASS_UP
    path = theme_file(name)
    if not path.is_file():
        path = _PASS_EXTERNAL / name
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _PASS_ICON_PX,
                _PASS_ICON_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _PASS_CACHE[key] = pm
    return pm


class PassRemoveButton(QPushButton):
    """Compact square Remove control: glue-panel chrome + GroupLoot Pass icon."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PassRemoveButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#PassRemoveButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        glue_row_square_chrome(pressed=False, side=GLUE_ROW_H)
        glue_row_square_chrome(pressed=True, side=GLUE_ROW_H)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        chrome = glue_row_square_chrome(
            pressed=self.isDown(),
            disabled=not self.isEnabled(),
            side=GLUE_ROW_H,
        )
        if chrome.isNull():
            painter.setPen(Qt.GlobalColor.darkGray)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
        else:
            painter.drawPixmap(rect, chrome)
        icon = _pass_icon_pixmap(self.isDown())
        if icon.isNull():
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "×")
        else:
            x = rect.center().x() - icon.width() // 2
            y = rect.center().y() - icon.height() // 2 + (1 if self.isDown() else 0)
            painter.drawPixmap(x, y, icon)
        painter.end()


# Turtle WoW custom-addon badge (splash raven / ichalaunch icon).
_TURTLE_BADGE_PX = 18
_TURTLE_BADGE_TIP = "Turtle WoW custom addon"
_TURTLE_CUSTOM_FLAGS = frozenset(
    {"turtle_custom", "turtle_wow_custom", "custom_turtle"}
)
# Name/folder: Turtle WoW, TWoW, word-boundary TW, TW-prefixed compounds, "Turtle…".
# Avoid bare "tw" inside words (e.g. Between / Network).
_TURTLE_CUSTOM_NAME_RE = re.compile(
    r"(?:"
    r"Turtle\s*WoW|"
    r"TurtleWoW|"
    r"TWoW|"
    r"\(TW\)|"
    r"\[TW\]|"
    r"(?<![A-Za-z0-9])TW(?![A-Za-z])|"
    r"(?:^|[\-_/\s])TW(?=[A-Z0-9_\-]|$)|"
    r"Turtle"
    r")",
    re.IGNORECASE,
)
# Description: strong custom phrases only (not “Turtle WoW version” ports).
_TURTLE_CUSTOM_DESC_RE = re.compile(
    r"(?:"
    r"custom[\-\s]?made for turtle|"
    r"custom for turtle|"
    r"built for Turtle\s*WoW|"
    r"built for TurtleWoW|"
    r"Made for TWoW|"
    r"Made for Turtle\s*WoW|"
    r"Made for TurtleWoW"
    r")",
    re.IGNORECASE,
)
_turtle_badge_pm: QPixmap | None = None


def is_turtle_wow_custom_addon(entry: dict[str, Any] | None) -> bool:
    """True when catalog marks or name/folder heuristics say Turtle-custom."""
    if not entry:
        return False
    for key in _TURTLE_CUSTOM_FLAGS:
        if entry.get(key) is True:
            return True
    tags = entry.get("tags")
    if isinstance(tags, (list, tuple, set)):
        for tag in tags:
            if str(tag).strip().lower() in _TURTLE_CUSTOM_FLAGS:
                return True
    for field in ("name", "folder"):
        text = str(entry.get(field) or "").strip()
        if text and _TURTLE_CUSTOM_NAME_RE.search(text):
            return True
    desc = str(entry.get("description") or "")
    if desc and _TURTLE_CUSTOM_DESC_RE.search(desc):
        return True
    return False


def _turtle_wow_badge_pixmap() -> QPixmap:
    """Cached splash raven icon scaled for AddonRow height."""
    global _turtle_badge_pm
    if _turtle_badge_pm is not None:
        return _turtle_badge_pm

    pm = QPixmap()
    for name in ("ichalaunch.png", "ichalaunch.ico"):
        path = theme_file(name)
        if not path.exists():
            continue
        src = QPixmap(str(path))
        if src.isNull():
            continue
        pm = src.scaled(
            _TURTLE_BADGE_PX,
            _TURTLE_BADGE_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        break
    _turtle_badge_pm = pm
    return pm


def format_updated_stamp(meta: dict[str, Any] | None) -> str | None:
    """Human date from installed_addons / installed_mods metadata."""
    if not meta:
        return None
    raw = meta.get("updated_at") or meta.get("installed_at") or meta.get("commit_date")
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        else:
            text = str(raw).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text:
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y")
    except (TypeError, ValueError, OSError):
        return None
def status_with_stamp(base: str, meta: dict[str, Any] | None = None) -> str:
    """Append · date for Up to date rows when metadata has a stamp."""
    if not base.startswith("Up to date"):
        return base
    stamp = format_updated_stamp(meta)
    return f"{base} · {stamp}" if stamp else base


def mod_author(mod: dict[str, Any] | None) -> str | None:
    """Best-effort creator credit for a client mod catalog entry."""
    if not mod:
        return None
    explicit = str(mod.get("author") or "").strip()
    if explicit:
        return explicit
    mid = str(mod.get("id") or "")
    if mid.startswith("hd_patch"):
        return "Project Reforged"
    src = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    addon_src = mod.get("addon_source") if isinstance(mod.get("addon_source"), dict) else {}
    repo_url = github_repo_browse_url(
        mod.get("repo"),
        mod.get("repo_url"),
        mod.get("repository"),
        mod.get("github"),
        mod.get("url"),
        src.get("repo"),
        src.get("url"),
        addon_src.get("repo"),
        addon_src.get("url"),
    )
    if repo_url:
        try:
            from ichalaunch.addons.github import parse_github_url

            parsed = parse_github_url(repo_url)
            if parsed and parsed.owner:
                return parsed.owner
        except Exception:  # noqa: BLE001
            pass
        parts = repo_url.replace("https://github.com/", "").split("/")
        if parts and parts[0]:
            return parts[0]
    for raw in (src.get("url"), addon_src.get("url")):
        text = str(raw or "").strip().lower()
        if "raw.githubusercontent.com/" in text:
            try:
                path = urlparse(str(raw)).path.strip("/").split("/")
                if len(path) >= 1:
                    return path[0]
            except Exception:  # noqa: BLE001
                continue
    return None


def addon_fork_label(entry: dict[str, Any] | None) -> str:
    """Display owner/repo for an addon catalog or installed row."""
    if not entry:
        return ""
    base = ""
    for raw in (
        entry.get("repo"),
        entry.get("url"),
        entry.get("repository"),
    ):
        url = github_repo_browse_url(raw)
        if not url:
            continue
        try:
            from ichalaunch.addons.github import parse_github_url

            parsed = parse_github_url(url)
            if parsed:
                base = f"{parsed.owner}/{parsed.repo}"
                break
        except Exception:  # noqa: BLE001
            pass
        tail = url.replace("https://github.com/", "").strip("/")
        if tail:
            base = tail.split("/")[0:2] and "/".join(tail.split("/")[0:2]) or tail
            break
    if not base:
        base = str(entry.get("label") or "").strip()
    if entry.get("archived"):
        return f"{base} (archived)" if base else "(archived)"
    return base


def fork_combo_label(entry: dict[str, Any] | None) -> str:
    """Fork picker combo text; prefers parsed repo name and archived suffix."""
    if not entry:
        return "?"
    label = addon_fork_label(entry)
    if label:
        return label
    return str(entry.get("label") or entry.get("repo") or "?")


def addon_version_label(
    entry: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Installed or catalog version string for addon rows."""
    meta = meta if isinstance(meta, dict) else {}
    entry = entry if isinstance(entry, dict) else {}

    def _format(raw: Any) -> str:
        from ichalaunch.addons.git_refs import (
            extract_semver_label,
            is_preferred_release_alias,
            is_usable_release_tag,
            is_version_tag,
            looks_like_timestamp_label,
        )

        text = str(raw or "").strip()
        if not text or looks_like_timestamp_label(text) or is_preferred_release_alias(text):
            return ""
        extracted = extract_semver_label(text)
        if extracted and (
            " " in text
            or "/" in text
            or text.lower().endswith((".zip", ".rar", ".7z"))
        ):
            return extracted
        if not is_usable_release_tag(text) and not is_version_tag(text):
            return extracted
        if not text.lower().startswith("v") and text[:1].isdigit():
            return f"v{text}"
        return text

    for raw in (
        meta.get("version"),
        meta.get("tag"),
        entry.get("tag"),
        entry.get("pin_release"),
    ):
        label = _format(raw)
        if label:
            return label
    try:
        from ichalaunch.addons.github import catalog_pin_tag, parse_entry_owner_repo
        from ichalaunch.addons.tip_index import lookup_display_version

        pin = catalog_pin_tag(entry)
        label = _format(pin)
        if label:
            return label
        parsed = parse_entry_owner_repo(entry)
        if parsed:
            return lookup_display_version(parsed[0], parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return ""
# Hub for Turtle WoW client tweaks/patches that ship without a dedicated repo.
TURTLEWOW_MODS_HUB = "https://github.com/RetroCro/TurtleWoW-Mods"


class _BrowseUrlCheckThread(QThread):
    """Background HEAD/GET probe so Open in Git never blocks the UI."""

    finished_check = Signal(str, bool)

    def __init__(self, url: str, parent: QObject | None = None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        from ichalaunch.addons.github import github_url_reachable

        ok = False
        try:
            ok = bool(github_url_reachable(self._url))
        except Exception:  # noqa: BLE001
            ok = False
        self.finished_check.emit(self._url, ok)


def apply_open_git_visibility(
    button: QPushButton,
    url: str | None,
    owner: QObject,
    *,
    defer: bool = False,
) -> None:
    """Show *Open in Git* only when the browse URL is live (cached or async check).

    Uses ``https://github.com/owner/repo`` (never a dead tag/download URL).
    When *defer* is True (or the owner is off-screen), skip the network probe so
    init/rebuild does not spawn a thread per row.
    """
    text = (url or "").strip() or None
    button.setVisible(False)
    if not text:
        button.setToolTip("No git repository link")
        return
    button.setToolTip(f"Open {text}")
    setattr(owner, "_git_url_deferred", text)

    owner_w = owner if isinstance(owner, QWidget) else None
    hidden = bool(
        owner_w is not None
        and (
            owner_w.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            or not owner_w.isVisible()
        )
    )
    if defer or hidden:
        return

    from ichalaunch.addons.github import github_url_reachable_cached

    cached = github_url_reachable_cached(text)
    if cached is True:
        button.setVisible(True)
        return
    if cached is False:
        return

    gen = int(getattr(owner, "_git_url_check_gen", 0) or 0) + 1
    setattr(owner, "_git_url_check_gen", gen)
    setattr(owner, "_git_url_pending", text)

    thread = _BrowseUrlCheckThread(text, owner)

    def _on_done(checked_url: str, ok: bool) -> None:
        if int(getattr(owner, "_git_url_check_gen", 0) or 0) != gen:
            return
        if checked_url != getattr(owner, "_git_url_pending", None):
            return
        # Row may have been destroyed during an update-scan list rebuild.
        try:
            if not button or button.parent() is None:
                return
        except RuntimeError:
            return
        button.setVisible(bool(ok))

    thread.finished_check.connect(_on_done)
    threads: list[QThread] = list(getattr(owner, "_git_url_threads", []) or [])
    threads = [t for t in threads if t.isRunning()]
    threads.append(thread)
    setattr(owner, "_git_url_threads", threads)
    thread.start()


def github_repo_browse_url(*candidates: Any) -> str | None:
    """Best-effort https://github.com/owner/repo from catalog/meta fields."""
    for raw in candidates:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if text.count("/") == 1 and "://" not in text and " " not in text:
            return f"https://github.com/{text}"
        try:
            from ichalaunch.addons.github import parse_github_url
            parsed = parse_github_url(text)
            if parsed:
                return f"https://github.com/{parsed.owner}/{parsed.repo}"
        except Exception:  # noqa: BLE001
            pass
        lower = text.lower()
        # github.com/... and raw.githubusercontent.com/owner/repo/...
        if "github.com" in lower or "githubusercontent.com" in lower:
            try:
                path = urlparse(text).path.strip("/")
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2:
                    return f"https://github.com/{parts[0]}/{parts[1]}"
            except Exception:  # noqa: BLE001
                continue
    return None


def mod_git_url(mod: dict[str, Any] | None) -> str | None:
    """Public git page for a client mod — per-item repo, else TurtleWoW-Mods hub."""
    if not mod:
        return None
    src = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    found = github_repo_browse_url(
        mod.get("repo_url"),
        mod.get("repo"),
        mod.get("github"),
        mod.get("url"),
        mod.get("info_url"),
        mod.get("repository"),
        (src or {}).get("repo"),
        (src or {}).get("url"),
        (src or {}).get("github"),
    )
    if found:
        return found
    # Catalog / ecosystem entries without a dedicated repo still link to the hub.
    return TURTLEWOW_MODS_HUB
def open_url_in_browser(url: str) -> bool:
    text = (url or "").strip()
    if not text:
        return False
    return bool(QDesktopServices.openUrl(QUrl(text)))


# https://discord.com/users/<id>, discordapp.com, or discord://-/users/<id>
_DISCORD_USER_RE = re.compile(
    r"(?:https?://(?:www\.)?discord(?:app)?\.com/users/|discord://-/users/)(\d+)",
    re.IGNORECASE,
)


def discord_user_id_from_url(url: str) -> str | None:
    """Return a Discord snowflake from a profile URL, or None if not a user link."""
    text = (url or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    m = _DISCORD_USER_RE.search(text)
    return m.group(1) if m else None


def _protocol_registered(scheme: str) -> bool | None:
    """True/False when we can check Windows registry; None means try anyway."""
    scheme = (scheme or "").strip().lower()
    if not scheme:
        return False
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for root, path in (
        (winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, scheme),
    ):
        try:
            with winreg.OpenKey(root, path):
                return True
        except OSError:
            continue
    return False


def _discord_protocol_registered() -> bool | None:
    """True/False when we can check Windows registry; None means try anyway."""
    return _protocol_registered("discord")


def _vesktop_executable() -> Path | None:
    """Locate a Vesktop (or legacy VencordDesktop) install, if present."""
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        candidates = (
            local / "vesktop" / "vesktop.exe",
            local / "Programs" / "vesktop" / "vesktop.exe",
            local / "VencordDesktop" / "VencordDesktop.exe",
            program_files / "vesktop" / "vesktop.exe",
            program_files / "Vesktop" / "vesktop.exe",
            program_files_x86 / "vesktop" / "vesktop.exe",
            program_files_x86 / "Vesktop" / "vesktop.exe",
        )
        for path in candidates:
            if path.is_file():
                return path
        return None
    if sys.platform == "darwin":
        mac = Path("/Applications/Vesktop.app/Contents/MacOS/Vesktop")
        return mac if mac.is_file() else None
    which = shutil.which("vesktop")
    return Path(which) if which else None


def _process_named_running(image_name: str) -> bool:
    """Best-effort check for a running process (Windows tasklist; else False)."""
    if sys.platform != "win32":
        return False
    name = (image_name or "").strip()
    if not name:
        return False
    try:
        kwargs: dict[str, Any] = {
            "args": ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            "capture_output": True,
            "text": True,
            "timeout": 5,
            "check": False,
        }
        # Avoid a flash console window when launched from the GUI.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
        completed = subprocess.run(**kwargs)
        out = (completed.stdout or "") + (completed.stderr or "")
        return name.lower() in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# Local CDP port used when we cold-start Vesktop so warm clicks can open profiles
# in the existing window (Vesktop's second-instance handler only focuses).
_VESKTOP_CDP_PORT = 9229
_DISCORD_IPC_CLIENT_ID = "122178054565183488"  # unused public-style id for handshake


def _launch_app_with_url(exe: Path, url: str, *extra_args: str) -> bool:
    """Start ``exe`` with ``url`` (and optional Chromium flags) as argv."""
    args = [url, *[a for a in extra_args if a]]
    try:
        # Prefer Qt detach so the launcher is not tied to Vesktop's lifetime.
        ok, _pid = QProcess.startDetached(str(exe), args)
        if ok:
            return True
    except (TypeError, RuntimeError, OSError):
        pass
    try:
        kwargs: dict[str, Any] = {"close_fds": True}
        if sys.platform == "win32":
            flags = 0
            for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                flags |= getattr(subprocess, name, 0)
            if flags:
                kwargs["creationflags"] = flags
        subprocess.Popen([str(exe), *args], **kwargs)
        return True
    except OSError:
        return False


def _windows_open_uri(uri: str) -> bool:
    """Open a URI via the OS shell (protocol handler), ignoring registry probes."""
    text = (uri or "").strip()
    if not text:
        return False
    if sys.platform == "win32":
        try:
            os.startfile(text)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
        try:
            import ctypes

            # > 32 means ShellExecute started the association successfully.
            rc = int(ctypes.windll.shell32.ShellExecuteW(None, "open", text, None, None, 1))
            return rc > 32
        except (AttributeError, OSError, ValueError):
            pass
    return bool(QDesktopServices.openUrl(QUrl(text)))


def _discord_ipc_paths() -> list[str]:
    if sys.platform == "win32":
        return [rf"\\.\pipe\discord-ipc-{i}" for i in range(10)]
    bases: list[str] = []
    for key in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        val = os.environ.get(key)
        if val:
            bases.append(val)
    bases.append("/tmp")
    paths: list[str] = []
    for base in bases:
        for i in range(10):
            paths.append(str(Path(base) / f"discord-ipc-{i}"))
    return paths


def _discord_ipc_encode(opcode: int, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", opcode, len(data)) + data


def _discord_ipc_read(sock: socket.socket, timeout: float = 2.0) -> tuple[int, dict[str, Any]] | None:
    sock.settimeout(timeout)
    try:
        hdr = sock.recv(8)
        if len(hdr) < 8:
            return None
        opcode, length = struct.unpack("<II", hdr)
        body = b""
        while len(body) < length:
            chunk = sock.recv(length - len(body))
            if not chunk:
                break
            body += chunk
        if len(body) < length:
            return None
        return opcode, json.loads(body.decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None


def _discord_ipc_open_user(user_id: str) -> bool:
    """Ask a running Discord/Vesktop arRPC server to open ``users/<id>``."""
    uid = (user_id or "").strip()
    if not uid.isdigit():
        return False
    payload = {
        "cmd": "DEEP_LINK",
        "args": {"type": "FEATURES", "params": {"path": f"users/{uid}"}},
        "nonce": str(uuid.uuid4()),
    }
    for path in _discord_ipc_paths():
        if sys.platform == "win32":
            try:
                pipe = open(path, "r+b", buffering=0)
            except OSError:
                continue
            try:
                pipe.write(_discord_ipc_encode(0, {"v": 1, "client_id": _DISCORD_IPC_CLIENT_ID}))
                pipe.flush()
                hdr = pipe.read(8)
                if len(hdr) < 8:
                    continue
                _op, length = struct.unpack("<II", hdr)
                body = pipe.read(length)
                if len(body) < length:
                    continue
                pipe.write(_discord_ipc_encode(1, payload))
                pipe.flush()
                hdr = pipe.read(8)
                if len(hdr) < 8:
                    continue
                _op, length = struct.unpack("<II", hdr)
                body = pipe.read(length)
                msg = json.loads(body.decode("utf-8")) if body else {}
                return msg.get("evt") is None
            except (OSError, json.JSONDecodeError, struct.error):
                continue
            finally:
                try:
                    pipe.close()
                except OSError:
                    pass
            continue

        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(path)
            sock.sendall(_discord_ipc_encode(0, {"v": 1, "client_id": _DISCORD_IPC_CLIENT_ID}))
            if _discord_ipc_read(sock) is None:
                continue
            sock.sendall(_discord_ipc_encode(1, payload))
            resp = _discord_ipc_read(sock)
            if resp is None:
                return False
            _opcode, msg = resp
            return msg.get("evt") is None
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return False


def _ws_recv_frame(sock: socket.socket) -> bytes:
    """Read one unmasked WebSocket binary/text frame (client role)."""
    hdr = sock.recv(2)
    if len(hdr) < 2:
        raise OSError("short ws header")
    b1, b2 = hdr[0], hdr[1]
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    mask = sock.recv(4) if masked else b""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    if masked and mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:
        raise OSError("ws closed")
    if opcode == 0x9:  # ping -> pong
        sock.sendall(bytes([0x8A, len(data)]) + data)
        return _ws_recv_frame(sock)
    return data


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)


def _cdp_connect(ws_url: str) -> socket.socket:
    """Minimal WebSocket client handshake for Chromium CDP."""
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock = socket.create_connection((host, port), timeout=2.0)
    sock.settimeout(3.0)
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise OSError("CDP handshake closed")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if "101" not in status_line:
        sock.close()
        raise OSError(f"CDP handshake failed: {status_line}")
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    ).decode("ascii")
    if expected not in buf.decode("latin-1", "replace"):
        # Some Chromium builds omit echoing checks under allow-origins; still proceed if 101.
        pass
    return sock


def _cdp_evaluate(ws_url: str, expression: str, await_promise: bool = True) -> Any:
    sock = _cdp_connect(ws_url)
    try:
        msg_id = 1
        _ws_send_text(
            sock,
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": await_promise,
                    },
                }
            ),
        )
        while True:
            raw = _ws_recv_frame(sock)
            data = json.loads(raw.decode("utf-8"))
            if data.get("id") != msg_id:
                continue
            result = (data.get("result") or {}).get("result") or {}
            if "exceptionDetails" in (data.get("result") or {}):
                return None
            return result.get("value")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _vesktop_cdp_page_ws_urls(ports: tuple[int, ...] | None = None) -> list[str]:
    urls: list[str] = []
    for port in ports or (_VESKTOP_CDP_PORT, 9222, 9223):
        try:
            with urlopen(f"http://127.0.0.1:{port}/json", timeout=0.4) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(tabs, list):
            continue
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            if tab.get("type") != "page":
                continue
            ws = tab.get("webSocketDebuggerUrl")
            if isinstance(ws, str) and ws:
                urls.append(ws)
    return urls


def _vesktop_cdp_open_user(user_id: str) -> bool:
    """Open a user profile in a running Vesktop via Chromium CDP + Vencord helpers."""
    uid = (user_id or "").strip()
    if not uid.isdigit():
        return False
    # Discord's openUserProfileModal expects an options object, not a bare id string.
    expression = f"""(async () => {{
  try {{
    if (!window.Vencord) return 'no-vencord';
    const mod = Vencord.Webpack.findByProps('openUserProfileModal');
    if (mod && mod.openUserProfileModal) {{
      const guildId = Vencord.Webpack.Common.SelectedGuildStore?.getGuildId?.() ?? undefined;
      const channelId = Vencord.Webpack.Common.SelectedChannelStore?.getChannelId?.() ?? undefined;
      mod.openUserProfileModal({{
        userId: '{uid}',
        guildId,
        channelId,
        analyticsLocation: {{ page: guildId ? 'Guild Channel' : 'DM Channel', section: 'Profile Popout' }}
      }});
      return 'ok-modal';
    }}
    const {{ FluxDispatcher, UserUtils, SelectedChannelStore }} = Vencord.Webpack.Common;
    if (UserUtils && UserUtils.fetchUser) await UserUtils.fetchUser('{uid}');
    FluxDispatcher.dispatch({{
      type: 'USER_PROFILE_MODAL_OPEN',
      userId: '{uid}',
      channelId: SelectedChannelStore?.getChannelId?.(),
      analyticsLocation: 'IchaLaunch'
    }});
    return 'ok-dispatch';
  }} catch (e) {{
    return 'err:' + String(e && e.message || e);
  }}
}})()"""
    for ws_url in _vesktop_cdp_page_ws_urls():
        try:
            value = _cdp_evaluate(ws_url, expression, await_promise=True)
        except OSError:
            continue
        if isinstance(value, str) and value.startswith("ok"):
            return True
    return False


def open_discord_user_profile(url_or_id: str) -> bool:
    """Open a Discord user profile in a desktop client when possible, else the browser.

    Order (warm Vesktop included):
    1. Chromium CDP ``openUserProfileModal`` when Vesktop was started with remote
       debugging (we pass this on cold launch).
    2. Discord IPC ``DEEP_LINK`` / arRPC when a ``discord-ipc-*`` pipe is listening.
    3. Launch Vesktop with ``discord://-/users/<id>`` even if already running
       (cold start navigates; warm builds at least focus the window).
    4. OS ``discord://`` via ``os.startfile`` / ShellExecute / Qt.
    5. HTTPS profile URL in the browser.
    """
    text = (url_or_id or "").strip()
    if not text:
        return False
    user_id = discord_user_id_from_url(text)
    if not user_id:
        return open_url_in_browser(text)
    https_url = f"https://discord.com/users/{user_id}"
    deep_url = f"discord://-/users/{user_id}"

    # Warm path that actually opens the profile modal without reloading Discord.
    if _vesktop_cdp_open_user(user_id):
        return True

    if _discord_ipc_open_user(user_id):
        return True

    vesktop = _vesktop_executable()
    if vesktop is not None:
        running = _process_named_running(vesktop.name)
        if running:
            # Newer Vesktop still ignores second-instance argv for navigation, but
            # launching with the deep link focuses the window; worth trying first.
            if _launch_app_with_url(vesktop, deep_url):
                # If CDP becomes available after focus, try once more (no-op usually).
                if _vesktop_cdp_open_user(user_id):
                    return True
            if _windows_open_uri(deep_url):
                if _vesktop_cdp_open_user(user_id):
                    return True
            # Fall through to browser — focus-only is worse than a working profile.
        else:
            # Cold start: argv discord:// is handled by loadUrl. Also enable CDP so
            # later warm clicks can open profiles inside the existing window.
            if _launch_app_with_url(
                vesktop,
                deep_url,
                f"--remote-debugging-port={_VESKTOP_CDP_PORT}",
                "--remote-allow-origins=*",
            ):
                return True

    if _windows_open_uri(deep_url):
        if _vesktop_cdp_open_user(user_id):
            return True
        registered = _discord_protocol_registered()
        # Only trust bare protocol success as terminal when a handler is registered
        # and Vesktop was not already our target (avoids silent no-ops).
        if registered and vesktop is None:
            return True

    return open_url_in_browser(https_url)


class FlowLayout(QLayout):
    """Simple left-to-right wrapping layout for chip rows."""
    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
    def count(self) -> int:
        return len(self._items)
    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None
    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None
    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)
    def hasHeightForWidth(self) -> bool:
        return True
    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)
    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)
    def sizeHint(self) -> QSize:
        return self.minimumSize()
    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_h = 0
        space = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + space
            if next_x - space > effective.right() and line_h:
                x = effective.x()
                y = y + line_h + space
                next_x = x + hint.width() + space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("class", "Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(8)
    @property
    def body(self) -> QVBoxLayout:
        return self._layout
class ModCheckRow(QWidget):
    """Compact row: [checkbox] Name [▸ details] — status [Update] [Open in Git] [Reinstall].
    Version and description stay collapsed behind the caret until expanded.
    Optional *contains* line (e.g. bundled companions) stays visible beneath the title.
    """
    toggled = Signal(str, bool)
    update_clicked = Signal(str)
    reinstall_clicked = Signal(str)
    open_git_clicked = Signal(str)
    def __init__(
        self,
        mod_id: str,
        title: str,
        description: str,
        checked: bool = False,
        *,
        author: str | None = None,
        contains: str | None = None,
        version: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # Layout child (not a QListWidget item) — must stay visible. AddonRow uses
        # WA_DontShowOnScreen + hide() because lists reveal via _reveal_item_widgets;
        # CLIENT has no such path, so those flags left an empty category panel.
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.mod_id = mod_id
        self._full_desc = (description or "").replace("\n", " ").strip()
        self._version = (version or "").strip()
        self._desc_expanded = False
        self._git_url: str | None = None
        self.setObjectName("ModCheckRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cb = ThemeCheckBox("", self)
        self.cb.setFixedSize(22, 22)
        self.cb.setChecked(checked)
        self.cb.toggled.connect(lambda v: self.toggled.emit(self.mod_id, v))
        name_lbl = QLabel(title, self)
        name_lbl.setObjectName("ModRowName")
        name_lbl.setWordWrap(False)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.desc_toggle = QPushButton("▸", self)
        self.desc_toggle.setObjectName("DescToggle")
        self.desc_toggle.setFlat(True)
        self.desc_toggle.setFixedSize(18, 22)
        apply_open_hand(self.desc_toggle)
        self.desc_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.desc_toggle.setToolTip("Show details")
        self.desc_toggle.clicked.connect(self._toggle_desc)
        self.author_lbl = QLabel("", self)
        self.author_lbl.setObjectName("Muted")
        self.author_lbl.setWordWrap(False)
        self.author_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        if author:
            self.author_lbl.setText(f"created by {author}")
        else:
            self.author_lbl.setVisible(False)
        sep2 = QLabel("—", self)
        sep2.setObjectName("Muted")
        self.status_lbl = QLabel("", self)
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(False)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.update_btn = GluePanelButton(
            "Update", self, role="primary", width=GLUE_ROW_W, height=GLUE_ROW_H
        )
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(lambda: self.update_clicked.emit(self.mod_id))
        self.open_git_btn = GluePanelButton("Open in Git", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
        self.open_git_btn.setVisible(False)
        self.open_git_btn.setToolTip("Open the repository in your browser")
        self.open_git_btn.clicked.connect(self._emit_open_git)
        self.reinstall_btn = GluePanelButton("Reinstall", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
        self.reinstall_btn.setVisible(False)
        self.reinstall_btn.setToolTip("Re-download and overwrite installed files")
        self.reinstall_btn.clicked.connect(lambda: self.reinstall_clicked.emit(self.mod_id))
        row.addWidget(self.cb, 0)
        row.addWidget(name_lbl, 0)
        row.addWidget(self.desc_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        if author:
            row.addWidget(self.author_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(sep2, 0)
        row.addWidget(self.status_lbl, 0)
        row.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.open_git_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.reinstall_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.contains_lbl = QLabel(self)
        self.contains_lbl.setObjectName("Muted")
        self.contains_lbl.setWordWrap(True)
        contains_text = (contains or "").strip()
        if contains_text:
            self.contains_lbl.setText(contains_text)
            self.contains_lbl.setVisible(True)
        else:
            self.contains_lbl.clear()
            self.contains_lbl.setVisible(False)
        self.version_lbl = QLabel(self)
        self.version_lbl.setObjectName("Muted")
        self.version_lbl.setWordWrap(False)
        self.version_lbl.setVisible(False)
        self.desc_lbl = QLabel(self)
        self.desc_lbl.setObjectName("Muted")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setVisible(False)
        outer.addLayout(row)
        outer.addWidget(self.contains_lbl)
        outer.addWidget(self.version_lbl)
        outer.addWidget(self.desc_lbl)
        self._apply_desc()
    def _has_dropdown(self) -> bool:
        return bool(self._full_desc or self._version)
    def _toggle_desc(self) -> None:
        self._desc_expanded = not self._desc_expanded
        self._apply_desc()
        self.updateGeometry()
    def _apply_desc(self) -> None:
        if not self._has_dropdown():
            self.version_lbl.clear()
            self.version_lbl.setVisible(False)
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setVisible(False)
            return
        self.desc_toggle.setVisible(True)
        if self._desc_expanded:
            if self._version:
                self.version_lbl.setText(f"Version {self._version}")
                self.version_lbl.setVisible(True)
            else:
                self.version_lbl.clear()
                self.version_lbl.setVisible(False)
            if self._full_desc:
                self.desc_lbl.setText(self._full_desc)
                self.desc_lbl.setVisible(True)
            else:
                self.desc_lbl.clear()
                self.desc_lbl.setVisible(False)
            self.desc_toggle.setText("▾")
            self.desc_toggle.setToolTip("Hide details")
        else:
            self.version_lbl.clear()
            self.version_lbl.setVisible(False)
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setText("▸")
            self.desc_toggle.setToolTip("Show details")
    def _emit_open_git(self) -> None:
        self.open_git_clicked.emit(self.mod_id)
    def set_git_url(self, url: str | None) -> None:
        self._git_url = (url or "").strip() or None
        apply_open_git_visibility(self.open_git_btn, self._git_url, self, defer=True)

    def set_version(self, version: str | None) -> None:
        self._version = (version or "").strip()
        self.version_lbl.setToolTip(f"Version {self._version}" if self._version else "")
        self._apply_desc()

    def kick_git_visibility(self) -> None:
        apply_open_git_visibility(self.open_git_btn, self._git_url, self, defer=False)
    def set_update_available(self, available: bool, detail: str = "") -> None:
        self.update_btn.setVisible(available)
        if available:
            self.update_btn.setText("Update")
            self.update_btn.setToolTip(detail or "Update available")
    def set_reinstall_visible(self, visible: bool) -> None:
        self.reinstall_btn.setVisible(visible)

    def flash_highlight(self, ms: int = 2200) -> None:
        """Brief gold flash so the user can find a newly selected/matched row."""
        self.setProperty("flashHighlight", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

        def _clear() -> None:
            self.setProperty("flashHighlight", False)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        QTimer.singleShot(max(400, int(ms)), _clear)


class AddonRow(QWidget):
    install_clicked = Signal(dict)
    update_clicked = Signal(dict)
    reinstall_clicked = Signal(dict)
    remove_clicked = Signal(str)
    open_git_clicked = Signal(dict)
    preview_clicked = Signal(dict)
    settings_clicked = Signal(dict)
    loaded_toggled = Signal(dict, bool)
    never_update_changed = Signal(dict, bool)
    fork_changed = Signal(dict)
    height_changed = Signal()
    def __init__(
        self,
        entry: dict,
        status: str = "available",
        *,
        modules: list[str] | None = None,
        never_update: bool = False,
        loaded: bool = True,
        meta: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # Off-screen BEFORE any child buttons — an unparented/on-screen QWidget is a HWND.
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.hide()
        self.entry = entry
        self._meta = meta if isinstance(meta, dict) else {}
        self._modules = [m for m in (modules or []) if m]
        self._modules_expanded = False
        self._never_update = bool(never_update)
        self._update_available = status.startswith("Update")
        self._status_text = status
        self.open_git_btn: GluePanelButton | None = None
        self.settings_btn: OptionsCogButton | None = None
        self.load_cb: ThemeCheckBox | None = None
        self._loaded = bool(loaded)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 2)
        root.setSpacing(2)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        is_installed = (
            status in ("Installed", "Not checked", "—", "Never update")
            or status.startswith("Up to date")
            or status.startswith("Update")
            or status.startswith("Never update")
        )
        if is_installed:
            from ichalaunch.addons.loadstate import UNLOAD_TOOLTIP

            self.load_cb = ThemeCheckBox("", self)
            self.load_cb.setFixedSize(22, 22)
            self.load_cb.setToolTip(UNLOAD_TOOLTIP)
            self.load_cb.blockSignals(True)
            self.load_cb.setChecked(self._loaded)
            self.load_cb.blockSignals(False)
            self.load_cb.toggled.connect(self._on_loaded_toggled)
            name_row.addWidget(self.load_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        if is_turtle_wow_custom_addon(entry):
            badge_pm = _turtle_wow_badge_pixmap()
            if not badge_pm.isNull():
                badge = QLabel(self)
                badge.setPixmap(badge_pm)
                badge.setFixedSize(badge_pm.size())
                badge.setToolTip(_TURTLE_BADGE_TIP)
                name_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        name = QLabel(entry.get("name", "?"), self)
        name.setStyleSheet("font-weight: 600; color: #F1C22D;")
        name_row.addWidget(name, 0)
        self._name_lbl = name
        self.modules_toggle = QPushButton("▸", self)
        self.modules_toggle.setObjectName("DescToggle")
        self.modules_toggle.setFlat(True)
        self.modules_toggle.setFixedSize(18, 20)
        apply_open_hand(self.modules_toggle)
        self.modules_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.modules_toggle.setVisible(len(self._modules) > 0)
        n_mod = len(self._modules)
        self.modules_toggle.setToolTip(
            f"Show {n_mod} nested module{'s' if n_mod != 1 else ''}" if n_mod else ""
        )
        self.modules_toggle.clicked.connect(self._toggle_modules)
        name_row.addWidget(self.modules_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addStretch(1)
        layout.addLayout(name_row, 1)
        self.status_lbl = QLabel(status, self)
        self._apply_status_style(status)
        layout.addWidget(self.status_lbl)
        git_url = github_repo_browse_url(
            entry.get("repo"),
            entry.get("url"),
            entry.get("repository"),
        )
        self._git_url = git_url
        if is_installed:
            show_update = self._update_available and not self._never_update
            self._update_btn = GluePanelButton(
                "Update", self, role="primary", width=GLUE_ROW_W, height=GLUE_ROW_H
            )
            self._update_btn.clicked.connect(self._on_update_clicked)
            self._update_btn.setVisible(show_update)
            # Narrow caret next to Update — on-demand QMenu (no InstantPopup).
            self._update_menu_btn = GluePanelButton(
                "▾", self, role="primary", width=GLUE_ROW_MENU_W, height=GLUE_ROW_H
            )
            self._update_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._update_menu_btn.setToolTip(
                "Never Update skips update checks and Update All. "
                "Clear later with Reinstall."
            )
            self._update_menu_btn.clicked.connect(self._popup_never_update_menu)
            self._update_menu_btn.setVisible(show_update)
            update_wrap = QWidget(self)
            update_wrap.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            update_l = QHBoxLayout(update_wrap)
            update_l.setContentsMargins(0, 0, 0, 0)
            update_l.setSpacing(2)
            update_l.addWidget(self._update_btn)
            update_l.addWidget(self._update_menu_btn)
            layout.addWidget(update_wrap)
            if git_url:
                btn_git = GluePanelButton(
                    "Open in Git", self, width=GLUE_ROW_W, height=GLUE_ROW_H
                )
                btn_git.clicked.connect(lambda: self.open_git_clicked.emit(entry))
                layout.addWidget(btn_git)
                self.open_git_btn = btn_git
                apply_open_git_visibility(btn_git, git_url, self, defer=True)
            if git_url or entry.get("source") == "github" or entry.get("tag"):
                btn_ri = GluePanelButton(
                    "Reinstall", self, width=GLUE_ROW_W, height=GLUE_ROW_H
                )
                btn_ri.setToolTip(
                    "Re-download and overwrite installed files "
                    "(also clears Never Update for this addon)"
                )
                btn_ri.clicked.connect(lambda: self.reinstall_clicked.emit(entry))
                layout.addWidget(btn_ri)
            btn_r = PassRemoveButton(self)
            btn_r.setToolTip("Remove this addon")
            btn_r.clicked.connect(
                lambda: self.remove_clicked.emit(entry.get("folder") or entry.get("name"))
            )
            layout.addWidget(btn_r)
            if git_url:
                btn_set = OptionsCogButton(self)
                btn_set.setToolTip(
                    "Repository settings — fork, version, and README preview"
                )
                btn_set.clicked.connect(lambda: self.settings_clicked.emit(entry))
                layout.addWidget(btn_set)
                self.settings_btn = btn_set
            self._refresh_never_update_ui()
        else:
            self._update_btn = None
            self._update_menu_btn = None
            btn = GluePanelButton("Install", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
            btn.clicked.connect(lambda: self.install_clicked.emit(entry))
            layout.addWidget(btn)
            if git_url:
                btn_git = GluePanelButton(
                    "Open in Git", self, width=GLUE_ROW_W, height=GLUE_ROW_H
                )
                btn_git.clicked.connect(lambda: self.open_git_clicked.emit(entry))
                layout.addWidget(btn_git)
                self.open_git_btn = btn_git
                apply_open_git_visibility(btn_git, git_url, self, defer=True)
        root.addLayout(layout)
        self.modules_panel = QLabel(self)
        self.modules_panel.setObjectName("Muted")
        self.modules_panel.setWordWrap(True)
        self.modules_panel.setVisible(False)
        root.addWidget(self.modules_panel)

    def preferred_height(self) -> int:
        return max(48, self.sizeHint().height())

    def _on_loaded_toggled(self, checked: bool) -> None:
        self._loaded = bool(checked)
        self.entry["loaded"] = self._loaded
        self.loaded_toggled.emit(self.entry, self._loaded)

    def set_loaded(self, loaded: bool) -> None:
        self._loaded = bool(loaded)
        self.entry["loaded"] = self._loaded
        if self.load_cb is None:
            return
        self.load_cb.blockSignals(True)
        self.load_cb.setChecked(self._loaded)
        self.load_cb.blockSignals(False)

    def _popup_never_update_menu(self) -> None:
        if self._update_menu_btn is None or self._never_update:
            return
        menu = QMenu(self)
        act = menu.addAction("Never Update")
        act.setToolTip(
            "Skip update checks and Update All for this addon. "
            "Clear with Reinstall."
        )
        act.triggered.connect(self._on_never_update_chosen)
        pos = self._update_menu_btn.mapToGlobal(self._update_menu_btn.rect().bottomLeft())
        menu.exec(pos)
        menu.deleteLater()

    def _on_never_update_chosen(self) -> None:
        """One-way: set Never Update (clear only via Reinstall)."""
        if self._never_update:
            return
        self._never_update = True
        # Defer so the transient menu can finish closing before list rebuild.
        QTimer.singleShot(0, lambda: self.never_update_changed.emit(self.entry, True))

    def _on_update_clicked(self) -> None:
        self.update_clicked.emit(self.entry)

    def _refresh_never_update_ui(self) -> None:
        show_update = self._update_available and not self._never_update
        if self._never_update:
            self.status_lbl.setText("Never update")
            self.status_lbl.setStyleSheet("color: #6e6678;")
        else:
            self.status_lbl.setText(self._status_text)
            self._apply_status_style(self._status_text)
        if self._update_btn is not None:
            self._update_btn.setVisible(show_update)
        if self._update_menu_btn is not None:
            # Caret only beside a visible Update button — never on Never Update rows.
            self._update_menu_btn.setVisible(show_update)

    def apply_status(self, status: str, *, never_update: bool | None = None) -> None:
        """Patch labels/buttons in place (no recreate — avoids HWND flashes)."""
        if never_update is not None:
            self._never_update = bool(never_update)
        self._status_text = status
        self._update_available = status.startswith("Update")
        try:
            self._refresh_never_update_ui()
        except RuntimeError:
            return

    def kick_git_visibility(self) -> None:
        """Run a deferred Open-in-Git probe once the row is on-screen."""
        if self.open_git_btn is None:
            return
        if self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
            return
        url = getattr(self, "_git_url", None) or getattr(self, "_git_url_deferred", None)
        apply_open_git_visibility(self.open_git_btn, url, self, defer=False)

    def _apply_status_style(self, status: str) -> None:
        if status.startswith("Update"):
            self.status_lbl.setStyleSheet("color: #F1C22D;")
        elif status.startswith("Up to date") or status == "Installed":
            self.status_lbl.setStyleSheet("color: #7c5cc4;")
        elif status.startswith("Never update"):
            self.status_lbl.setStyleSheet("color: #6e6678;")
        else:
            self.status_lbl.setObjectName("Muted")
            self.status_lbl.setStyleSheet("")

    def _toggle_modules(self) -> None:
        self._modules_expanded = not self._modules_expanded
        if self._modules_expanded and self._modules:
            lines = " · ".join(self._modules)
            self.modules_panel.setText(f"Modules: {lines}")
            self.modules_panel.setVisible(True)
            self.modules_toggle.setText("▾")
            self.modules_toggle.setToolTip("Hide nested modules")
        else:
            self.modules_panel.clear()
            self.modules_panel.setVisible(False)
            self.modules_toggle.setText("▸")
            n_mod = len(self._modules)
            self.modules_toggle.setToolTip(
                f"Show {n_mod} nested module{'s' if n_mod != 1 else ''}" if n_mod else ""
            )
        self.updateGeometry()
        self.height_changed.emit()
