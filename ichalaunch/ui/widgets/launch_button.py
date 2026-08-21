"""OctoWoW-style metallic launch button in RavenCraft colors."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand

# RavenCraft palette
_GOLD = QColor("#F1C22D")
_GOLD_SOFT = QColor("#E8C878")
_MUTED = QColor("#6a6358")
_PURPLE = QColor("#7c5cc4")


class LaunchButton(QPushButton):
    """PLAY / INSTALL / UPDATE chrome button (objectName PlayButton).

    Uses a generated 9-slice-friendly chrome PNG (beveled frame, recessed
    panel, purple bottom glow, inward gold triangles) with painted gold text.
    Hover is a gold border only — no full-button tint/glow overlay.
    """

    def __init__(self, text: str = "PLAY", parent=None):
        super().__init__(text, parent)
        self.setObjectName("PlayButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(200, 56)
        # Let paintEvent own the look — strip QSS chrome for this widget.
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet(
            "QPushButton#PlayButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._chrome: QPixmap | None = None
        self._chrome_pressed: QPixmap | None = None
        self._chrome_disabled: QPixmap | None = None
        self._load_chrome()

    def _load_chrome(self) -> None:
        path = theme_file("launch_btn_chrome.png")
        if not path.exists():
            self._chrome = None
            return
        base = QPixmap(str(path))
        if base.isNull():
            self._chrome = None
            return
        self._chrome = base
        # Subtle darken for press only — hover uses normal chrome + gold border.
        self._chrome_pressed = self._tint_pixmap(base, QColor(0, 0, 0, 45), brighten=-12)
        self._chrome_disabled = self._desaturate_pixmap(base)

    @staticmethod
    def _tint_pixmap(src: QPixmap, overlay: QColor, brighten: int = 0) -> QPixmap:
        out = QPixmap(src.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, src)
        if brighten:
            mode = QPainter.CompositionMode.CompositionMode_Plus if brighten > 0 else (
                QPainter.CompositionMode.CompositionMode_Multiply
            )
            p.setCompositionMode(mode)
            a = min(90, abs(brighten) * 3)
            c = QColor(255, 255, 255, a) if brighten > 0 else QColor(40, 36, 32, a)
            p.fillRect(out.rect(), c)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(out.rect(), overlay)
        p.end()
        return out

    @staticmethod
    def _desaturate_pixmap(src: QPixmap) -> QPixmap:
        img = src.toImage().convertToFormat(src.toImage().Format.Format_ARGB32)
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() == 0:
                    continue
                g = int(0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue())
                c.setRed(g)
                c.setGreen(g)
                c.setBlue(int(g * 0.92))
                c.setAlpha(int(c.alpha() * 0.75))
                img.setPixelColor(x, y, c)
        return QPixmap.fromImage(img)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        chrome = self._pick_chrome()
        if chrome is not None and not chrome.isNull():
            # Slight press inset for tactile feel
            draw_rect = rect.adjusted(1, 2, -1, 0) if self.isDown() else rect
            painter.drawPixmap(draw_rect, chrome)
        else:
            self._paint_fallback_chrome(painter, rect)

        if self.isEnabled() and (self.underMouse() or self.isDown()):
            self._paint_gold_border(painter, rect)

        self._paint_label(painter, rect)
        painter.end()

    def _pick_chrome(self) -> QPixmap | None:
        if not self.isEnabled():
            return self._chrome_disabled or self._chrome
        if self.isDown():
            return self._chrome_pressed or self._chrome
        return self._chrome

    def _paint_gold_border(self, painter: QPainter, rect: QRect) -> None:
        """Hover / pressed: RavenCraft gold outline only."""
        pen = QPen(_GOLD)
        pen.setWidth(2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)

    def _paint_fallback_chrome(self, painter: QPainter, rect: QRect) -> None:
        """Vector fallback if the chrome PNG is missing."""
        outer = rect.adjusted(2, 2, -2, -2)
        painter.setPen(QColor(90, 78, 70))
        painter.setBrush(QColor(28, 24, 22))
        painter.drawRoundedRect(outer, 6, 6)
        inner = outer.adjusted(8, 8, -8, -8)
        painter.setPen(QColor(160, 130, 70, 180))
        painter.setBrush(QColor(18, 14, 12))
        painter.drawRoundedRect(inner, 3, 3)
        # bottom glow
        glow = QColor(_PURPLE)
        for i in range(18):
            glow.setAlpha(max(0, 140 - i * 8))
            y = inner.bottom() - i
            painter.fillRect(inner.left() + 4, y, inner.width() - 8, 1, glow)

    def _paint_label(self, painter: QPainter, rect: QRect) -> None:
        text = (self.text() or "").upper()
        font = QFont(self.font())
        font.setFamily("Segoe UI")
        font.setBold(True)
        # Longer labels (e.g. REGISTER HERE) need a smaller size to fit chrome.
        n = len(text.replace(" ", ""))
        if n >= 12:
            px, spacing = 13, 1.0
        elif n >= 9:
            px, spacing = 16, 1.6
        else:
            px, spacing = 20, 2.5
        font.setPixelSize(px)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        painter.setFont(font)

        if not self.isEnabled():
            color = _MUTED
        elif self.isDown():
            color = _GOLD_SOFT
        else:
            color = _GOLD

        # Soft text shadow for recessed metal look
        text_rect = rect.adjusted(0, 0 if not self.isDown() else 1, 0, 0)
        shadow = QColor(0, 0, 0, 160)
        painter.setPen(shadow)
        painter.drawText(text_rect.adjusted(1, 2, 1, 2), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()
