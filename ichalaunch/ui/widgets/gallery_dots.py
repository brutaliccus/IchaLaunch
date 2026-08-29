"""Position row for the Home gallery.

Thirty-one slides turning on an eleven second hold, and until now nothing said
where in them you were. ravencraft.io carries the same row under its own
slideshow, which is where the shape comes from.

Clickable: each dot owns the full pitch as its hit box, not just the seven
pixels it paints, so the target is the gap as well as the mark.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ichalaunch.ui.widgets.cursors import apply_open_hand

_DOT_PX = 7
_PITCH_PX = 18
_BAND_PAD_PX = 5
# Floor on the space between adjacent dots. With many slides the pitch shrinks
# naturally, which is fine, but never to the point where they merge into a bar.
_MIN_DOT_GAP_PX = 6

_ACTIVE = QColor("#F1C22D")
_IDLE = QColor("#a8977c")
_IDLE_ALPHA = 90


class GalleryDots(QWidget):
    """One dot per slide, the current one lit. Click one to go there."""

    dot_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        apply_open_hand(self)
        self._count = 0
        self._index = 0
        self._pitch = _PITCH_PX

    def set_state(self, count: int, index: int, max_width: int) -> None:
        """Size to *count* dots, lighting *index*, never wider than max_width."""
        count = max(0, int(count))
        pitch = _PITCH_PX
        if count > 0 and max_width > 0:
            # SPREAD across the span, do not merely avoid overflowing it. The
            # old cap at _PITCH_PX meant the row could never grow past its
            # natural width, so widening the strip between the arrows left the
            # dots bunched in the middle of it. Pitch is now the available width
            # divided by the count, floored so adjacent dots can never touch
            # however many slides there are, and the hit target is the pitch
            # rather than the dot, so spreading them makes clicking easier.
            pitch = max(_DOT_PX + _MIN_DOT_GAP_PX, max_width // count)
        changed = (count, pitch) != (self._count, self._pitch)
        self._count = count
        self._pitch = pitch
        self._index = index if 0 <= index < count else 0
        if changed:
            self.setFixedSize(max(1, count * pitch), _DOT_PX + 2 * _BAND_PAD_PX)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if self._count < 2:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            y = (self.height() - _DOT_PX) / 2.0
            left = (self.width() - self._count * self._pitch) / 2.0
            for i in range(self._count):
                x = left + i * self._pitch + (self._pitch - _DOT_PX) / 2.0
                if i == self._index:
                    painter.setBrush(_ACTIVE)
                    painter.drawEllipse(QRectF(x, y, _DOT_PX, _DOT_PX))
                else:
                    idle = QColor(_IDLE)
                    idle.setAlpha(_IDLE_ALPHA)
                    painter.setBrush(idle)
                    inset = 1.0
                    painter.drawEllipse(
                        QRectF(x + inset, y + inset, _DOT_PX - 2 * inset, _DOT_PX - 2 * inset)
                    )
        finally:
            if painter.isActive():
                painter.end()

    def _index_at(self, x: float) -> int:
        """Slide under *x*, or -1 outside the row."""
        if self._count < 1 or self._pitch <= 0:
            return -1
        left = (self.width() - self._count * self._pitch) / 2.0
        i = int((x - left) // self._pitch)
        return i if 0 <= i < self._count else -1

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        i = self._index_at(event.position().x())
        if i < 0:
            super().mousePressEvent(event)
            return
        event.accept()
        if i != self._index:
            self.dot_clicked.emit(i)
