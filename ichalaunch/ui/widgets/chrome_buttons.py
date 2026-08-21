"""Frameless window chrome — bare minimize / close glyphs (no plate)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ichalaunch.ui.widgets.cursors import apply_open_hand

_GOLD = QColor("#F1C22D")
_GOLD_DIM = QColor("#C9A01F")
_GOLD_BRIGHT = QColor("#FFE08A")


class ChromeGlyphButton(QWidget):
    """Transparent hit target with only a gold minimize bar or close X."""

    clicked = Signal()

    def __init__(self, kind: str, parent: QWidget | None = None):
        super().__init__(parent)
        assert kind in ("minimize", "close")
        self._kind = kind
        self._hover = False
        self.setObjectName("ChromeGlyphButton")
        self.setFixedSize(28, 28)
        apply_open_hand(self)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("Minimize" if kind == "minimize" else "Close")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Avoid WA_TranslucentBackground — it breaks QPainter on Windows hover.
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # No plate / border — glyph only; hover brightens gold.
        color = _GOLD_BRIGHT if self._hover else _GOLD
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        glyph = QPen(color, 2.15)
        glyph.setCapStyle(Qt.PenCapStyle.RoundCap)
        glyph.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glyph)

        if self._kind == "minimize":
            half = 5.5
            painter.drawLine(QPointF(cx - half, cy + 1.0), QPointF(cx + half, cy + 1.0))
        else:
            arm = 5.2
            painter.drawLine(QPointF(cx - arm, cy - arm), QPointF(cx + arm, cy + arm))
            painter.drawLine(QPointF(cx + arm, cy - arm), QPointF(cx - arm, cy + arm))
        painter.end()
