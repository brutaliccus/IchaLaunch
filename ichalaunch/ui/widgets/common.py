"""Reusable UI widgets."""

from __future__ import annotations

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
    """Single compact line: [checkbox] Name — description — status [Update]."""

    toggled = Signal(str, bool)
    update_clicked = Signal(str)

    def __init__(self, mod_id: str, title: str, description: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self.mod_id = mod_id
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 4, 4, 4)
        row.setSpacing(6)

        self.cb = QCheckBox()
        self.cb.setChecked(checked)
        self.cb.toggled.connect(lambda v: self.toggled.emit(self.mod_id, v))

        name_lbl = QLabel(title)
        name_lbl.setStyleSheet("font-weight: 600; color: #d8d8dc;")

        sep1 = QLabel("—")
        sep1.setObjectName("Muted")

        desc_raw = (description or "").replace("\n", " ").strip()
        if len(desc_raw) > 72:
            desc_raw = desc_raw[:69] + "…"
        desc_lbl = QLabel(desc_raw)
        desc_lbl.setObjectName("Muted")
        desc_lbl.setWordWrap(False)
        desc_lbl.setToolTip(description or "")
        # Stretch/shrink here so long descriptions never crush the Update button.
        desc_lbl.setMinimumWidth(0)
        desc_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        sep2 = QLabel("—")
        sep2.setObjectName("Muted")

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(False)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self.update_btn = QPushButton("Update")
        self.update_btn.setVisible(False)
        # Compact padding + min size so global QPushButton padding doesn't clip the label.
        self.update_btn.setStyleSheet("padding: 4px 12px;")
        self.update_btn.setMinimumSize(76, 28)
        self.update_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.update_btn.clicked.connect(lambda: self.update_clicked.emit(self.mod_id))

        row.addWidget(self.cb, 0)
        row.addWidget(name_lbl, 0)
        row.addWidget(sep1, 0)
        row.addWidget(desc_lbl, 1)
        row.addWidget(sep2, 0)
        row.addWidget(self.status_lbl, 0)
        row.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_update_available(self, available: bool, detail: str = "") -> None:
        self.update_btn.setVisible(available)
        if available:
            self.update_btn.setText("Update available" if not detail else "Update")
            self.update_btn.setToolTip(detail or "Update available")


class AddonRow(QWidget):
    install_clicked = Signal(dict)
    update_clicked = Signal(dict)
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
        name.setStyleSheet("font-weight: 600; color: #ffd700;")
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
            status_lbl.setStyleSheet("color: #ffd700;")
        elif status in ("Installed", "Up to date"):
            status_lbl.setStyleSheet("color: #4CAF50;")
        else:
            status_lbl.setObjectName("Muted")
        layout.addWidget(status_lbl)

        # Installed rows may show "Not checked" before an update scan completes.
        is_installed = (
            status in ("Installed", "Up to date", "Not checked", "—")
            or status.startswith("Update")
        )
        if is_installed:
            if status.startswith("Update"):
                btn_u = QPushButton("Update")
                btn_u.clicked.connect(lambda: self.update_clicked.emit(entry))
                layout.addWidget(btn_u)
            btn_r = QPushButton("Remove")
            btn_r.clicked.connect(lambda: self.remove_clicked.emit(entry.get("folder") or entry.get("name")))
            layout.addWidget(btn_r)
        else:
            btn = QPushButton("Install")
            btn.clicked.connect(lambda: self.install_clicked.emit(entry))
            layout.addWidget(btn)
