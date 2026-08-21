"""Glue-Panel styled QComboBox with marbled popup list."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QFrame, QListView, QSizePolicy, QWidget

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, glue_chrome_pixmap
from ichalaunch.ui.widgets.marble_bg import load_marble_pixmap, paint_marble_tiled

_ARROW_UP = "Arrow-Down-Up.PNG"
_ARROW_DOWN = "Arrow-Down-Down.PNG"
_ARROW_UP_EXT = Path(r"F:\wow-ui-textures\Buttons\Arrow-Down-Up.PNG")
_ARROW_DOWN_EXT = Path(r"F:\wow-ui-textures\Buttons\Arrow-Down-Down.PNG")

_TEXT = QColor("#e6e0ee")
_TEXT_DIM = QColor("#8a8490")
_BORDER = QColor(124, 92, 196, 110)
_ARROW_SIZE = 12
_ARROW_PAD_R = 12
# Arrow-Down PNG opaque content sits high in the 16px art (~3.5px above center);
# scale to 12px draw size → nudge down so the chevron centers on the closed control.
_ARROW_Y_NUDGE = 3
_TEXT_PAD_L = 12
_TEXT_PAD_R = 28  # room for caret

_ARROW_CACHE: dict[str, QPixmap] = {}


def _load_arrow(bundled: str, external: Path) -> QPixmap:
    hit = _ARROW_CACHE.get(bundled)
    if hit is not None:
        return hit
    path = theme_file(bundled)
    if not path.is_file():
        path = external
    pm = QPixmap(str(path)) if path.is_file() else QPixmap()
    _ARROW_CACHE[bundled] = pm
    return pm


class _MarbleComboView(QListView):
    """Popup list with inset-panel marble fill."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GlueComboPopup")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self._tile = load_marble_pixmap()
        self.setStyleSheet(
            "QListView#GlueComboPopup {"
            "  background: transparent;"
            "  border: none;"
            "  outline: none;"
            "  padding: 4px;"
            "  color: #e6e0ee;"
            "}"
            "QListView#GlueComboPopup::item {"
            "  min-height: 28px;"
            "  padding: 4px 10px;"
            "  border-radius: 4px;"
            "  color: #e6e0ee;"
            "  text-align: center;"
            "}"
            "QListView#GlueComboPopup::item:hover,"
            "QListView#GlueComboPopup::item:selected {"
            "  background-color: rgba(124, 92, 196, 0.42);"
            "  color: #ffffff;"
            "}"
        )
        vp = self.viewport()
        vp.setAutoFillBackground(False)
        vp.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        vp.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        vp.setStyleSheet("background: transparent; border: none;")
        vp.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.viewport() and event.type() == QEvent.Type.Paint:
            painter = QPainter(obj)
            if painter.isActive():
                try:
                    paint_marble_tiled(
                        painter,
                        obj.rect(),  # type: ignore[arg-type]
                        self._tile,
                        radius=6.0,
                    )
                finally:
                    painter.end()
            return False
        return super().eventFilter(obj, event)


class GlueComboBox(QComboBox):
    """Closed control uses Glue-Panel Up/Down art; popup uses marbled fill.

    Caret: Arrow-Down-Up when idle, Arrow-Down-Down when open or mouse-pressed.
    """

    def __init__(self, parent=None, *, height: int = GLUE_BTN_H, min_width: int = 148):
        super().__init__(parent)
        self.setObjectName("GlueComboBox")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(int(height))
        self.setMinimumWidth(int(min_width))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QComboBox#GlueComboBox {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
            "QComboBox#GlueComboBox::drop-down {"
            "  subcontrol-origin: padding;"
            "  subcontrol-position: center right;"
            "  width: 0px;"
            "  border: none;"
            "  background: transparent;"
            "}"
            "QComboBox#GlueComboBox::down-arrow {"
            "  image: none;"
            "  width: 0px;"
            "  height: 0px;"
            "}"
        )
        self._popup_open = False
        self._pressed = False
        view = _MarbleComboView(self)
        self.setView(view)
        self.setMaxVisibleItems(12)
        # Warm chrome + arrows.
        glue_chrome_pixmap(pressed=False)
        glue_chrome_pixmap(pressed=True)
        _load_arrow(_ARROW_UP, _ARROW_UP_EXT)
        _load_arrow(_ARROW_DOWN, _ARROW_DOWN_EXT)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        texts = [self.itemText(i) for i in range(self.count())]
        if self.currentText():
            texts.append(self.currentText())
        widest = max((fm.horizontalAdvance(t) for t in texts), default=80)
        w = max(self.minimumWidth(), widest + _TEXT_PAD_L + _TEXT_PAD_R + 8)
        return QSize(w, self.height())

    def showPopup(self) -> None:  # noqa: N802
        model = self.model()
        if model is not None:
            for i in range(self.count()):
                model.setData(
                    model.index(i, 0),
                    int(Qt.AlignmentFlag.AlignCenter),
                    Qt.ItemDataRole.TextAlignmentRole,
                )
        self._popup_open = True
        self.update()
        super().showPopup()
        self._style_popup_container()

    def hidePopup(self) -> None:  # noqa: N802
        self._popup_open = False
        self._pressed = False
        self.update()
        super().hidePopup()

    def _style_popup_container(self) -> None:
        view = self.view()
        if view is None:
            return
        container = view.parentWidget()
        if container is None:
            return
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        container.setAutoFillBackground(False)
        container.setStyleSheet(
            "QFrame {"
            "  background: transparent;"
            "  border: none;"
            "}"
        )
        # Paint marble on the container so edges match inset panels.
        if not getattr(container, "_glue_marble_filter", False):
            container._glue_marble_filter = True  # type: ignore[attr-defined]
            tile = load_marble_pixmap()

            class _Filter(QObject):
                def eventFilter(self, obj, event):  # noqa: N802
                    if obj is container and event.type() == QEvent.Type.Paint:
                        painter = QPainter(container)
                        if painter.isActive():
                            try:
                                paint_marble_tiled(
                                    painter, container.rect(), tile, radius=8.0
                                )
                                pen = QPen(_BORDER)
                                pen.setWidth(1)
                                painter.setPen(pen)
                                painter.setBrush(Qt.BrushStyle.NoBrush)
                                painter.drawRoundedRect(
                                    container.rect().adjusted(0, 0, -1, -1), 8, 8
                                )
                            finally:
                                painter.end()
                        return False
                    return False

            filt = _Filter(container)
            container.installEventFilter(filt)
            container._glue_marble_filt_obj = filt  # type: ignore[attr-defined]

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

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
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = self.rect()
        chrome_down = self._popup_open or self._pressed
        pm = glue_chrome_pixmap(
            pressed=chrome_down,
            role="standard",
            disabled=not self.isEnabled(),
        )
        if pm.isNull():
            painter.setPen(QColor("#7a6e88"))
            painter.setBrush(QColor("#2c2632"))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        else:
            painter.drawPixmap(rect, pm)

        # Label (shifts slightly when depressed, matching GluePanelButton).
        text = self.currentText() or ""
        font = QFont(self.font())
        font.setFamily("Segoe UI")
        font.setBold(True)
        font.setPixelSize(12 if len(text) >= 14 else 13)
        painter.setFont(font)
        color = _TEXT_DIM if not self.isEnabled() else _TEXT
        text_rect = rect.adjusted(
            _TEXT_PAD_L,
            1 if chrome_down else 0,
            -_TEXT_PAD_R,
            0,
        )
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawText(text_rect.adjusted(1, 1, 1, 1), align, text)
        painter.setPen(color)
        painter.drawText(text_rect, align, text)

        # Arrow caret — Up when idle, Down when open/pressed.
        arrow = _load_arrow(
            _ARROW_DOWN if chrome_down else _ARROW_UP,
            _ARROW_DOWN_EXT if chrome_down else _ARROW_UP_EXT,
        )
        if not arrow.isNull():
            aw = _ARROW_SIZE
            ax = rect.right() - _ARROW_PAD_R - aw
            ay = (
                rect.center().y()
                - aw // 2
                + _ARROW_Y_NUDGE
                + (1 if chrome_down else 0)
            )
            painter.drawPixmap(QRect(ax, ay, aw, aw), arrow)

        painter.end()
