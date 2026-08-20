"""Reusable UI widgets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(8)

    @property
    def body(self) -> QVBoxLayout:
        return self._layout


class ModCheckRow(QWidget):
    """Compact row: [checkbox] Name — truncated desc [▸] — status [Update] [Reinstall].

    Description takes remaining width and elides; Update/Reinstall stay fixed on the right.
    """

    toggled = Signal(str, bool)
    update_clicked = Signal(str)
    reinstall_clicked = Signal(str)

    def __init__(self, mod_id: str, title: str, description: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self.mod_id = mod_id
        self._full_desc = (description or "").replace("\n", " ").strip()
        self._desc_expanded = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 4, 4, 4)
        row.setSpacing(6)

        self.cb = QCheckBox()
        self.cb.setChecked(checked)
        self.cb.toggled.connect(lambda v: self.toggled.emit(self.mod_id, v))

        name_lbl = QLabel(title)
        name_lbl.setStyleSheet("font-weight: 600; color: #d8d8dc;")
        name_lbl.setWordWrap(False)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        sep1 = QLabel("—")
        sep1.setObjectName("Muted")

        self.desc_lbl = QLabel()
        self.desc_lbl.setObjectName("Muted")
        self.desc_lbl.setWordWrap(False)
        self.desc_lbl.setToolTip(self._full_desc)
        self.desc_lbl.setMinimumWidth(0)
        desc_policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        desc_policy.setHorizontalStretch(1)
        self.desc_lbl.setSizePolicy(desc_policy)

        self.desc_toggle = QPushButton("▸")
        self.desc_toggle.setObjectName("DescToggle")
        self.desc_toggle.setFlat(True)
        self.desc_toggle.setFixedSize(18, 22)
        self.desc_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desc_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.desc_toggle.setToolTip("Show full description")
        self.desc_toggle.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #8a8a92; padding: 0; }"
            "QPushButton:hover { color: #d8d8dc; }"
        )
        self.desc_toggle.setVisible(False)
        self.desc_toggle.clicked.connect(self._toggle_desc)

        sep2 = QLabel("—")
        sep2.setObjectName("Muted")

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(False)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("UpdateButton")
        self.update_btn.setVisible(False)
        # Compact min size so global QPushButton padding doesn't clip the label.
        self.update_btn.setMinimumSize(76, 28)
        self.update_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.update_btn.clicked.connect(lambda: self.update_clicked.emit(self.mod_id))

        self.reinstall_btn = QPushButton("Reinstall")
        self.reinstall_btn.setVisible(False)
        self.reinstall_btn.setStyleSheet("padding: 4px 12px;")
        self.reinstall_btn.setMinimumSize(84, 28)
        self.reinstall_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.reinstall_btn.setToolTip("Re-download and overwrite installed files")
        self.reinstall_btn.clicked.connect(lambda: self.reinstall_clicked.emit(self.mod_id))

        row.addWidget(self.cb, 0)
        row.addWidget(name_lbl, 0)
        row.addWidget(sep1, 0)
        row.addWidget(self.desc_lbl, 1)
        row.addWidget(self.desc_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(sep2, 0)
        row.addWidget(self.status_lbl, 0)
        row.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.reinstall_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._apply_desc()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._desc_expanded:
            self._apply_desc()

    def _toggle_desc(self) -> None:
        self._desc_expanded = not self._desc_expanded
        self._apply_desc()
        self.updateGeometry()

    def _apply_desc(self) -> None:
        if not self._full_desc:
            self.desc_lbl.clear()
            self.desc_toggle.setVisible(False)
            return
        if self._desc_expanded:
            self.desc_lbl.setWordWrap(True)
            expand_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            expand_policy.setHorizontalStretch(1)
            self.desc_lbl.setSizePolicy(expand_policy)
            self.desc_lbl.setText(self._full_desc)
            self.desc_toggle.setText("▾")
            self.desc_toggle.setToolTip("Hide full description")
            self.desc_toggle.setVisible(True)
            return
        self.desc_lbl.setWordWrap(False)
        collapse_policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        collapse_policy.setHorizontalStretch(1)
        self.desc_lbl.setSizePolicy(collapse_policy)
        width = max(0, self.desc_lbl.width())
        if width <= 1:
            self.desc_lbl.setText(self._full_desc)
            self.desc_toggle.setVisible(len(self._full_desc) > 48)
        else:
            elided = self.desc_lbl.fontMetrics().elidedText(
                self._full_desc, Qt.TextElideMode.ElideRight, width
            )
            self.desc_lbl.setText(elided)
            self.desc_toggle.setVisible(elided != self._full_desc)
        self.desc_toggle.setText("▸")
        self.desc_toggle.setToolTip("Show full description")

    def set_update_available(self, available: bool, detail: str = "") -> None:
        self.update_btn.setVisible(available)
        if available:
            self.update_btn.setText("Update available" if not detail else "Update")
            self.update_btn.setToolTip(detail or "Update available")

    def set_reinstall_visible(self, visible: bool) -> None:
        self.reinstall_btn.setVisible(visible)


class AddonRow(QWidget):
    install_clicked = Signal(dict)
    update_clicked = Signal(dict)
    reinstall_clicked = Signal(dict)
    remove_clicked = Signal(str)

    def __init__(self, entry: dict, status: str = "available", parent=None):
        super().__init__(parent)
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)
        name = QLabel(entry.get("name", "?"))
        name.setStyleSheet("font-weight: 600; color: #F1C22D;")
        desc_raw = (entry.get("description") or "").replace("\n", " ").strip()
        if len(desc_raw) > 90:
            desc_raw = desc_raw[:87] + "…"
        meta_bits = f"{entry.get('category', 'General')}  ·  {entry.get('source', 'catalog')}"
        sub = f"{meta_bits}  —  {desc_raw}" if desc_raw else meta_bits
        sub_lbl = QLabel(sub)
        sub_lbl.setObjectName("Muted")
        sub_lbl.setWordWrap(False)
        info.addWidget(name)
        info.addWidget(sub_lbl)
        layout.addLayout(info, 1)

        status_lbl = QLabel(status)
        if status.startswith("Update"):
            status_lbl.setStyleSheet("color: #F1C22D;")
        elif status.startswith("Up to date") or status == "Installed":
            status_lbl.setStyleSheet("color: #7c5cc4;")
        else:
            status_lbl.setObjectName("Muted")
        layout.addWidget(status_lbl)

        # Installed rows may show "Not checked" before an update scan completes.
        is_installed = (
            status in ("Installed", "Not checked", "—")
            or status.startswith("Up to date")
            or status.startswith("Update")
        )
        if is_installed:
            if status.startswith("Update"):
                btn_u = QPushButton("Update")
                btn_u.setObjectName("UpdateButton")
                btn_u.clicked.connect(lambda: self.update_clicked.emit(entry))
                layout.addWidget(btn_u)
            # Reinstall when a GitHub repo/url is known (settings or catalog merge)
            if entry.get("repo") or entry.get("url") or entry.get("source") == "github":
                btn_ri = QPushButton("Reinstall")
                btn_ri.setToolTip("Re-download and overwrite installed files")
                btn_ri.clicked.connect(lambda: self.reinstall_clicked.emit(entry))
                layout.addWidget(btn_ri)
            btn_r = QPushButton("Remove")
            btn_r.clicked.connect(lambda: self.remove_clicked.emit(entry.get("folder") or entry.get("name")))
            layout.addWidget(btn_r)
        else:
            btn = QPushButton("Install")
            btn.clicked.connect(lambda: self.install_clicked.emit(entry))
            layout.addWidget(btn)
