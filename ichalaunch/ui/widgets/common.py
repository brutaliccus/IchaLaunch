"""Reusable UI widgets."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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

from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import (
    GLUE_ROW_H,
    GLUE_ROW_MENU_W,
    GLUE_ROW_W,
    GluePanelButton,
)
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox


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
    """Compact row: [checkbox] Name [▸ desc] — status [Update] [Open in Git] [Reinstall].
    Description stays collapsed behind the caret until expanded.
    """
    toggled = Signal(str, bool)
    update_clicked = Signal(str)
    reinstall_clicked = Signal(str)
    open_git_clicked = Signal(str)
    def __init__(self, mod_id: str, title: str, description: str, checked: bool = False, parent=None):
        super().__init__(parent)
        # Layout child (not a QListWidget item) — must stay visible. AddonRow uses
        # WA_DontShowOnScreen + hide() because lists reveal via _reveal_item_widgets;
        # CLIENT has no such path, so those flags left an empty category panel.
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.mod_id = mod_id
        self._full_desc = (description or "").replace("\n", " ").strip()
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
        self.desc_toggle.setToolTip("Show description")
        self.desc_toggle.setVisible(bool(self._full_desc))
        self.desc_toggle.clicked.connect(self._toggle_desc)
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
        row.addStretch(1)
        row.addWidget(sep2, 0)
        row.addWidget(self.status_lbl, 0)
        row.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.open_git_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.reinstall_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.desc_lbl = QLabel(self)
        self.desc_lbl.setObjectName("Muted")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setVisible(False)
        outer.addLayout(row)
        outer.addWidget(self.desc_lbl)
    def _toggle_desc(self) -> None:
        self._desc_expanded = not self._desc_expanded
        self._apply_desc()
        self.updateGeometry()
    def _apply_desc(self) -> None:
        if not self._full_desc:
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setVisible(False)
            return
        self.desc_toggle.setVisible(True)
        if self._desc_expanded:
            self.desc_lbl.setText(self._full_desc)
            self.desc_lbl.setVisible(True)
            self.desc_toggle.setText("▾")
            self.desc_toggle.setToolTip("Hide description")
        else:
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setText("▸")
            self.desc_toggle.setToolTip("Show description")
    def _emit_open_git(self) -> None:
        self.open_git_clicked.emit(self.mod_id)
    def set_git_url(self, url: str | None) -> None:
        self._git_url = (url or "").strip() or None
        apply_open_git_visibility(self.open_git_btn, self._git_url, self, defer=True)

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
    loaded_toggled = Signal(dict, bool)
    never_update_changed = Signal(dict, bool)
    height_changed = Signal()
    def __init__(
        self,
        entry: dict,
        status: str = "available",
        *,
        modules: list[str] | None = None,
        never_update: bool = False,
        loaded: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        # Off-screen BEFORE any child buttons — an unparented/on-screen QWidget is a HWND.
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.hide()
        self.entry = entry
        self._modules = [m for m in (modules or []) if m]
        self._modules_expanded = False
        self._never_update = bool(never_update)
        self._update_available = status.startswith("Update")
        self._status_text = status
        self.open_git_btn: GluePanelButton | None = None
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
        name = QLabel(entry.get("name", "?"), self)
        name.setStyleSheet("font-weight: 600; color: #F1C22D;")
        name_row.addWidget(name, 0)
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
                btn_prev = GluePanelButton("Preview", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
                btn_prev.setToolTip("Show repository README and details")
                btn_prev.clicked.connect(lambda: self.preview_clicked.emit(entry))
                layout.addWidget(btn_prev)
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
            btn_r = GluePanelButton("Remove", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
            btn_r.clicked.connect(lambda: self.remove_clicked.emit(entry.get("folder") or entry.get("name")))
            layout.addWidget(btn_r)
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
                btn_prev = GluePanelButton("Preview", self, width=GLUE_ROW_W, height=GLUE_ROW_H)
                btn_prev.setToolTip("Show repository README and details")
                btn_prev.clicked.connect(lambda: self.preview_clicked.emit(entry))
                layout.addWidget(btn_prev)
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
