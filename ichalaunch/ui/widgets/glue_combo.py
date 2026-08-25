"""Glue-Panel styled QComboBox with marbled popup list."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QFrame, QListView, QSizePolicy, QWidget

try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:  # pragma: no cover
    def _shiboken_is_valid(obj: object) -> bool:  # type: ignore[misc]
        return obj is not None

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, glue_chrome_pixmap
from ichalaunch.ui.widgets.marble_bg import load_marble_pixmap, paint_marble_tiled

_ARROW_UP = "UI-ScrollBar-ScrollDownButton-Up.PNG"
_ARROW_DOWN = "UI-ScrollBar-ScrollDownButton-Down.PNG"
_ARROW_UP_EXT = Path(r"F:\wow-ui-textures\Buttons") / _ARROW_UP
_ARROW_DOWN_EXT = Path(r"F:\wow-ui-textures\Buttons") / _ARROW_DOWN

_TEXT = QColor("#e6e0ee")
_TEXT_DIM = QColor("#8a8490")
_BORDER = QColor(124, 92, 196, 110)
# Caret art: 34×34 (~5% over prior 32×32). Idle: +2 X, +2 Y below V-center.
# Depressed (pressed / popup open / Down art): idle + (−2 X, +3 Y).
_ARROW_SIZE = 34
_ARROW_PAD_R = 6
_ARROW_X_NUDGE = 2
_ARROW_Y_NUDGE = 2
_ARROW_PRESS_DX = -2
_ARROW_PRESS_DY = 3
# Match GluePanelButton depressed label: content shifts +1px down with Down chrome.
_PRESS_DX = 0
_PRESS_DY = 1
_TEXT_PAD_L = 12
_TEXT_PAD_R = 46  # room for 34px caret + pad
# Native QComboBox popup HWND is still dying after hidePopup(); re-show in that
# window crashes inside Qt (showPopup / hidePopup / paint).
_POPUP_REENTRY_MS = 120


def _alive(obj: object | None) -> bool:
    if obj is None:
        return False
    try:
        return bool(_shiboken_is_valid(obj))
    except Exception:
        return False

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
            if not _alive(self) or not _alive(obj):
                return True
            try:
                painter = QPainter(obj)
            except RuntimeError:
                return True
            if painter.isActive():
                try:
                    paint_marble_tiled(
                        painter,
                        obj.rect(),  # type: ignore[arg-type]
                        self._tile,
                        radius=6.0,
                    )
                except RuntimeError:
                    return True
                finally:
                    try:
                        painter.end()
                    except RuntimeError:
                        pass
            return False
        try:
            return super().eventFilter(obj, event)
        except RuntimeError:
            return False


class GlueComboBox(QComboBox):
    """Closed control uses Glue-Panel Up/Down art; popup uses marbled fill.

    Caret: ScrollDownButton-Up when idle, -Down when open or mouse-pressed.
    Height matches GluePanelButton (GLUE_BTN_H) so filters/dialogs align.
    """

    popupShown = Signal()
    popupHidden = Signal()

    def __init__(self, parent=None, *, height: int = GLUE_BTN_H, min_width: int = 148):
        super().__init__(parent)
        self.setObjectName("GlueComboBox")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # Lock to primary glue height so app QSS min-height/padding cannot shrink us.
        h = int(height) if int(height) > 0 else GLUE_BTN_H
        self._glue_h = h
        self.setFixedHeight(h)
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        self.setMinimumWidth(int(min_width))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QComboBox#GlueComboBox {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            f"  min-height: {h}px;"
            f"  max-height: {h}px;"
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
        self._hiding_popup = False
        self._pressed = False
        self._show_blocked_until = 0.0
        view = _MarbleComboView(self)
        self.setView(view)
        view.installEventFilter(self)
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
        return QSize(w, self._glue_h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = self.sizeHint()
        return QSize(self.minimumWidth(), self._glue_h if self._glue_h else hint.height())

    def isPopupOpen(self) -> bool:
        # Trust the show/hide flags only. view.isVisible() is a false positive
        # (QListView can report visible while the native popup HWND is gone).
        return bool(self._hiding_popup or self._popup_open)

    def _mark_popup_closed(self) -> None:
        if not self._popup_open:
            return
        self._popup_open = False
        self._pressed = False
        # Don't paint while the native popup HWND is still being destroyed.
        try:
            self.popupHidden.emit()
        except RuntimeError:
            pass

    def _end_popup_hide(self) -> None:
        if not _alive(self):
            return
        self._hiding_popup = False
        self._pressed = False
        try:
            self.update()
        except RuntimeError:
            pass

    def showPopup(self) -> None:  # noqa: N802
        if not _alive(self):
            return
        try:
            if not self.isEnabled():
                return
        except RuntimeError:
            return
        if self._hiding_popup:
            return
        if self._popup_open:
            return
        if time.monotonic() < self._show_blocked_until:
            return
        view = self.view()
        try:
            if view is not None and view.isVisible():
                self._popup_open = True
                return
        except RuntimeError:
            return
        model = self.model()
        if model is not None:
            try:
                for i in range(self.count()):
                    model.setData(
                        model.index(i, 0),
                        int(Qt.AlignmentFlag.AlignCenter),
                        Qt.ItemDataRole.TextAlignmentRole,
                    )
            except RuntimeError:
                return
        self._popup_open = True
        try:
            self.popupShown.emit()
            self.update()
            super().showPopup()
        except RuntimeError:
            self._popup_open = False
            return
        self._style_popup_container()
        view = self.view()
        if view is not None:
            try:
                container = view.parentWidget()
            except RuntimeError:
                container = None
            if container is not None:
                container.installEventFilter(self)

    def hidePopup(self) -> None:  # noqa: N802
        if self._hiding_popup or not self._popup_open:
            return
        self._hiding_popup = True
        self._show_blocked_until = time.monotonic() + (_POPUP_REENTRY_MS / 1000.0)
        try:
            if _alive(self):
                super().hidePopup()
        except RuntimeError:
            pass
        self._mark_popup_closed()
        QTimer.singleShot(_POPUP_REENTRY_MS, self._end_popup_hide)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if et in (
            QEvent.Type.Hide,
            QEvent.Type.HideToParent,
            QEvent.Type.Close,
            QEvent.Type.Destroy,
        ):
            if not _alive(self):
                return True
            view = self.view()
            try:
                container = view.parentWidget() if view is not None else None
            except RuntimeError:
                container = None
            if obj in (view, container) and self._popup_open:
                self._mark_popup_closed()
        try:
            return super().eventFilter(obj, event)
        except RuntimeError:
            return False

    def changeEvent(self, event) -> None:  # noqa: N802
        try:
            super().changeEvent(event)
        except RuntimeError:
            return
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            try:
                self.hidePopup()
            except RuntimeError:
                pass

    def _style_popup_container(self) -> None:
        if not _alive(self):
            return
        try:
            view = self.view()
        except RuntimeError:
            return
        if view is None:
            return
        try:
            container = view.parentWidget()
        except RuntimeError:
            return
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
                        if not _alive(container):
                            return True
                        try:
                            painter = QPainter(container)
                        except RuntimeError:
                            return True
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
                            except RuntimeError:
                                return True
                            finally:
                                try:
                                    painter.end()
                                except RuntimeError:
                                    pass
                        return False
                    return False

            filt = _Filter(container)
            container.installEventFilter(filt)
            container._glue_marble_filt_obj = filt  # type: ignore[attr-defined]

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            try:
                super().mousePressEvent(event)
            except RuntimeError:
                pass
            return
        if not _alive(self) or not self.isEnabled() or self._hiding_popup:
            event.accept()
            return
        if self._popup_open:
            # Close only — do not let QComboBox hide+show on the same click
            # while the native popup HWND is still destroying.
            event.accept()
            self.hidePopup()
            return
        self._pressed = True
        try:
            self.update()
            super().mousePressEvent(event)
        except RuntimeError:
            self._pressed = False

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        if not _alive(self):
            return
        try:
            self.update()
            super().mouseReleaseEvent(event)
        except RuntimeError:
            pass

    def enterEvent(self, event) -> None:  # noqa: N802
        try:
            super().enterEvent(event)
            if _alive(self):
                self.update()
        except RuntimeError:
            pass

    def leaveEvent(self, event) -> None:  # noqa: N802
        try:
            super().leaveEvent(event)
            if _alive(self):
                self.update()
        except RuntimeError:
            pass

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not _alive(self):
            return
        try:
            painter = QPainter(self)
        except RuntimeError:
            return
        if not painter.isActive():
            return
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

        # Label + caret shift with depressed chrome (same offset as GluePanelButton).
        press_dx = _PRESS_DX if chrome_down else 0
        press_dy = _PRESS_DY if chrome_down else 0
        text = self.currentText() or ""
        font = QFont(self.font())
        font.setFamily("Segoe UI")
        font.setBold(True)
        font.setPixelSize(12 if len(text) >= 14 else 13)
        painter.setFont(font)
        color = _TEXT_DIM if not self.isEnabled() else _TEXT
        text_rect = rect.adjusted(
            _TEXT_PAD_L + press_dx,
            press_dy,
            -_TEXT_PAD_R + press_dx,
            press_dy,
        )
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawText(text_rect.adjusted(1, 1, 1, 1), align, text)
        painter.setPen(color)
        painter.drawText(text_rect, align, text)

        # Arrow caret — Up when idle, Down when open/pressed.
        # Idle: +_ARROW_X_NUDGE / V-center + _ARROW_Y_NUDGE; depressed: + press DX/DY.
        arrow = _load_arrow(
            _ARROW_DOWN if chrome_down else _ARROW_UP,
            _ARROW_DOWN_EXT if chrome_down else _ARROW_UP_EXT,
        )
        if not arrow.isNull():
            aw = _ARROW_SIZE
            caret_dx = _ARROW_PRESS_DX if chrome_down else 0
            caret_dy = _ARROW_PRESS_DY if chrome_down else 0
            ax = rect.right() - _ARROW_PAD_R - aw + _ARROW_X_NUDGE + caret_dx
            ay = rect.center().y() - aw // 2 + _ARROW_Y_NUDGE + caret_dy
            painter.drawPixmap(QRect(ax, ay, aw, aw), arrow)

        try:
            painter.end()
        except RuntimeError:
            pass
