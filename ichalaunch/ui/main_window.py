"""Main window — borderless rounded chrome, folder tabs, bottom play bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSizeGrip,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.ui.widgets import dialogs as themed

_RESIZE_MARGIN = 6
_CORNER_RADIUS = 14
_TAB_STRIP_HEIGHT = 44
_MOA_LOGO_WIDTH = 290
# Soft haze padding — wide enough that “M” / last letter aren’t clipped; short vertically.
_MOA_GLOW_PAD_X = 56
_MOA_GLOW_PAD_Y = 12
# Quiet re-check while the launcher stays open (addons / mods / self-update).
_PERIODIC_UPDATE_MS = 5 * 60 * 1000
_GOLD = QColor("#F1C22D")

from ichalaunch import __version__
from ichalaunch.core.paths import theme_file
from ichalaunch.addons.github import (
    AddonUpdateCheckResult,
    RATE_LIMIT_STATUS,
    check_addon_updates,
    install_from_github,
    rate_limit_exhausted,
    recently_checked_addon_updates,
    uninstall_addon,
    update_addon,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import full_resync
from ichalaunch.core.filesystem import is_protected_path
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import StatusProgress
from ichalaunch.core.self_update import (
    LauncherReleaseInfo,
    apply_windows_self_replace,
    check_latest_launcher_release,
    perform_launcher_update,
)
from ichalaunch.game.launcher import (
    ensure_game_path_from_launcher,
    install_game_stub,
    is_installed,
    launch_game,
    validate_install_location,
)
from ichalaunch.mods.installer import (
    ModUpdateCheckResult,
    apply_desired_state,
    check_mod_updates,
    install_custom_dll_from_github,
    recently_checked_mod_updates,
    update_mod,
    update_mods,
)
from ichalaunch.ui.pages.addons import AddonsPage
from ichalaunch.ui.pages.client import ClientPage
from ichalaunch.ui.pages.home import HomePage
from ichalaunch.ui.pages.settings import SettingsPage
from ichalaunch.ui.widgets.launch_button import LaunchButton


class NavTabButton(QPushButton):
    """Folder tab with an optional gold pending-update badge."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._badge = False

    def set_badge_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._badge:
            return
        self._badge = visible
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._badge:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = 4.5
        center = QPointF(self.width() - radius - 7.0, radius + 5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_GOLD)
        painter.drawEllipse(center, radius, radius)


class MoaFloatingLogo(QWidget):
    """Mysteries of Azeroth wordmark — soft elliptical glow, click-through."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MoaFloatingLogo")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pix = QPixmap()
        self._logo_h = 0
        self._load()

    def _load(self) -> None:
        path = theme_file("moa_logo.png")
        if not path.exists():
            self.hide()
            return
        src = QPixmap(str(path))
        if src.isNull():
            self.hide()
            return
        self._pix = src.scaledToWidth(_MOA_LOGO_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self._logo_h = self._pix.height()
        self.setFixedSize(
            self._pix.width() + _MOA_GLOW_PAD_X * 2,
            self._logo_h + _MOA_GLOW_PAD_Y * 2,
        )
        self.show()

    @property
    def logo_offset_y(self) -> int:
        """Y of the wordmark top edge relative to this widget."""
        return _MOA_GLOW_PAD_Y

    @property
    def logo_height(self) -> int:
        return self._logo_h

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._pix.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        # Soft RavenCraft haze (grey / black / purple) — wider horizontally, shorter vertically.
        painter.save()
        painter.translate(cx, cy)
        painter.scale(1.22, 0.24)
        radius = max(self._pix.width() * 0.58, 1.0)
        glow = QRadialGradient(0.0, 0.0, radius)
        glow.setColorAt(0.0, QColor(55, 42, 78, 200))
        glow.setColorAt(0.28, QColor(32, 26, 40, 150))
        glow.setColorAt(0.55, QColor(18, 14, 22, 80))
        glow.setColorAt(0.82, QColor(10, 8, 12, 28))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        painter.restore()

        painter.drawPixmap(_MOA_GLOW_PAD_X, _MOA_GLOW_PAD_Y, self._pix)


class Worker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    progress_pct = Signal(int)  # 0-100, or -1 for indeterminate

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            progress = StatusProgress(
                lambda m: self.status.emit(m),
                lambda p: self.progress_pct.emit(p),
            )
            kwargs = dict(self.kwargs)
            try:
                result = self.fn(*self.args, progress=progress, **kwargs)
            except TypeError:
                result = self.fn(*self.args, **kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("Worker failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"IchaLaunch {__version__}")
        icon_path = theme_file("ichalaunch.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        # Transparent frame so Root's rounded corners are not filled with black.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(780, 520)

        self._worker: Worker | None = None
        self._update_worker: Worker | None = None
        self._mod_update_worker: Worker | None = None
        self._launcher_update_worker: Worker | None = None
        self._latest_launcher_release: LauncherReleaseInfo | None = None
        self._drag_pos: QPoint | None = None
        self._checking_addons = False
        self._checking_mods = False
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._resize_origin: QPoint | None = None
        self._resize_geo: QRect | None = None
        self._pending_ok_handler = None
        self._current_nav = -1
        self._fitted = False

        self.setMouseTracking(True)
        self._fit_to_screen(initial=True)

        root = QWidget()
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.setMouseTracking(True)
        self.setCentralWidget(root)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Folder tabs (top chrome) ----
        nav = QWidget()
        nav.setObjectName("TopNav")
        nav.setFixedHeight(_TAB_STRIP_HEIGHT)
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav.setMouseTracking(True)
        nav_l = QHBoxLayout(nav)
        # Less top margin so tab labels sit higher relative to the folder body
        nav_l.setContentsMargins(14, 2, 14, 0)
        nav_l.setSpacing(3)
        nav_l.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.nav_btns: list[NavTabButton] = []
        for i, label in enumerate(["HOME", "ADDONS", "CLIENT", "SETTINGS"]):
            btn = NavTabButton(label)
            btn.setObjectName("TopNavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, idx=i: self._nav(idx))
            nav_l.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
            self.nav_btns.append(btn)
        nav_l.addStretch(1)

        # ---- Folder body (pages) ----
        content = QWidget()
        content.setObjectName("ContentPanel")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content_panel = content
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(0, 0, 0, 0)
        content_l.setSpacing(0)

        self.stack = QStackedWidget()
        self.home = HomePage()
        self.addons = AddonsPage()
        self.client = ClientPage()
        self.settings_page = SettingsPage()
        for page in (self.home, self.addons, self.client, self.settings_page):
            self.stack.addWidget(page)
        content_l.addWidget(self.stack)

        # ---- Bottom play bar ----
        bottom = QWidget()
        bottom.setObjectName("BottomBar")
        bottom.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom.setFixedHeight(78)
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(16, 12, 4, 4)
        bot_l.setSpacing(14)

        prog_wrap = QVBoxLayout()
        prog_wrap.setSpacing(4)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setObjectName("Muted")
        self.progress = QProgressBar()
        self.progress.setObjectName("BottomProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setMinimumHeight(22)
        self.progress.hide()
        prog_wrap.addWidget(self.status_lbl)
        prog_wrap.addWidget(self.progress)

        self.play_btn = LaunchButton("PLAY")
        self.play_btn.clicked.connect(self._on_play_or_install)

        grip = QSizeGrip(bottom)
        grip.setFixedSize(16, 16)
        grip.setToolTip("Drag to resize")

        bot_l.addLayout(prog_wrap, 1)
        bot_l.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bot_l.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        outer.addWidget(nav)
        outer.addWidget(content, 1)
        outer.addWidget(bottom)

        # Mysteries of Azeroth wordmark — straddles ContentPanel top border (click-through).
        self._moa_logo = MoaFloatingLogo(root)
        self._moa_logo.raise_()

        self._update_window_mask()
        self._position_moa_logo()

        # Wire
        self.home.play_clicked.connect(self._on_play_or_install)
        self.home.install_clicked.connect(self._install_or_browse)
        self.client.apply_clicked.connect(self._apply_mods)
        self.client.rescan_clicked.connect(self._resync)
        self.client.check_updates_requested.connect(self._check_mod_updates)
        self.client.update_mod_requested.connect(self._update_client_mod)
        self.client.reinstall_mod_requested.connect(self._reinstall_client_mod)
        self.client.update_all_mods_requested.connect(self._update_all_client_mods)
        self.client.custom_dll_import_requested.connect(self._custom_dll_import)
        self.addons.install_requested.connect(self._install_catalog_addon)
        self.addons.update_requested.connect(self._update_addon)
        self.addons.reinstall_requested.connect(self._reinstall_addon)
        self.addons.update_all_requested.connect(self._update_all_addons)
        self.addons.remove_requested.connect(self._remove_addon)
        self.addons.github_import_requested.connect(self._github_import)
        self.addons.check_updates_requested.connect(self._check_updates)
        self.addons.rescan_requested.connect(self._resync)
        self.addons.badge_state_changed.connect(self._refresh_nav_badges)
        self.client.badge_state_changed.connect(self._refresh_nav_badges)
        self.settings_page.browse_clicked.connect(self._browse_game)
        self.settings_page.verify_clicked.connect(self._verify_game)

        self._refresh_play_button()
        self._nav(0)
        # If game_path is empty/invalid, detect WoW.exe next to the launcher EXE.
        if ensure_game_path_from_launcher() is not None:
            self.settings_page.refresh()
            self.home.refresh()
            self._refresh_play_button()
        # Always lightly check for a newer IchaLaunch release (one API call).
        self._check_launcher_update(silent=True)
        if is_installed():
            self._resync(silent=True)
            if settings.check_updates_on_startup():
                self._check_updates(silent=True)
                self._check_mod_updates(silent=True)
        self._refresh_nav_badges()

        # Keep looking for updates while the app stays open (no reopen required).
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(_PERIODIC_UPDATE_MS)
        self._update_timer.timeout.connect(self._periodic_update_check)
        self._update_timer.start()

    # --- window chrome ---
    def _update_window_mask(self) -> None:
        """Clip the frameless window to rounded corners (avoids square black corners)."""
        path = QPainterPath()
        path.addRoundedRect(
            0.0, 0.0, float(self.width()), float(self.height()),
            float(_CORNER_RADIUS), float(_CORNER_RADIUS),
        )
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _available_geo(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_to_screen(self, initial: bool = False) -> None:
        """Clamp max size to usable desktop; do not lock to a fixed size."""
        avail = self._available_geo()
        max_w = max(640, avail.width() - 24)
        max_h = max(480, avail.height() - 24)
        self.setMaximumSize(max_w, max_h)
        if initial or not self._fitted:
            w = min(1080, max_w)
            h = min(720, max_h)
            self.resize(w, h)
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())
            self._fitted = True
        else:
            # Shrink if somehow larger than the screen
            if self.width() > max_w or self.height() > max_h:
                self.resize(min(self.width(), max_w), min(self.height(), max_h))
            self._clamp_on_screen()

    def _clamp_on_screen(self) -> None:
        avail = self._available_geo()
        geo = self.frameGeometry()
        x = min(max(geo.x(), avail.left()), avail.right() - geo.width() + 1)
        y = min(max(geo.y(), avail.top()), avail.bottom() - geo.height() + 1)
        if x != geo.x() or y != geo.y():
            self.move(x, y)

    def _hit_resize_edges(self, pos: QPoint) -> tuple[bool, bool, bool, bool]:
        r = self.rect()
        m = _RESIZE_MARGIN
        return (
            pos.x() <= m,
            pos.x() >= r.width() - m,
            pos.y() <= m,
            pos.y() >= r.height() - m,
        )

    def _update_resize_cursor(self, edges: tuple[bool, bool, bool, bool]) -> None:
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def _position_moa_logo(self) -> None:
        """Center logo on ContentPanel top border, between SETTINGS and panel right."""
        logo = getattr(self, "_moa_logo", None)
        root = self.centralWidget()
        content = getattr(self, "_content_panel", None)
        if logo is None or root is None or content is None or not self.nav_btns or logo.isHidden():
            return
        settings_btn = self.nav_btns[-1]
        # Horizontal: between SETTINGS tab's right edge and the content panel's right edge
        # (not the transparent tab-strip overhang / full window width).
        left = settings_btn.mapTo(root, QPoint(settings_btn.width(), 0)).x()
        content_origin = content.mapTo(root, QPoint(0, 0))
        right = content_origin.x() + content.width()
        cx = (left + right) / 2.0
        x = int(round(cx - logo.width() / 2.0))
        # Keep the wordmark pixmap fully inside the zone — avoid rounded-mask / edge clip
        # of the leading “M” and trailing letter (glow may feather outside).
        corner_safe = _CORNER_RADIUS + 6
        pix_left = x + _MOA_GLOW_PAD_X
        pix_right = x + logo.width() - _MOA_GLOW_PAD_X
        min_pix_left = left + 6
        max_pix_right = right - corner_safe
        if pix_left < min_pix_left:
            x += min_pix_left - pix_left
        if pix_right > max_pix_right:
            x -= pix_right - max_pix_right
        # Prefer fitting the full wordmark; if zone is too narrow, bias slightly left
        # so the “M” stays clear of the right-corner mask.
        pix_left = x + _MOA_GLOW_PAD_X
        if pix_left < min_pix_left:
            x += min_pix_left - pix_left
        # Vertical: wordmark center on ContentPanel top purple border (half above / half below).
        content_top = content_origin.y()
        y = int(round(content_top - (logo.logo_offset_y + logo.logo_height / 2.0)))
        # Keep glow canvas inside the window mask (no top/side shave).
        y = max(2, y)
        x = max(2, min(x, root.width() - logo.width() - 2))
        logo.move(x, y)
        logo.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()
        self._update_window_mask()
        self._position_moa_logo()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_window_mask()
        self._position_moa_logo()

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._resize_edges is not None:
            self.releaseMouse()
        super().closeEvent(event)

    def _widget_is_interactive(self, widget: QWidget | None) -> bool:
        """True if clicks should stay with the control (not start a window drag)."""
        w = widget
        while w is not None and w is not self:
            if isinstance(
                w,
                (
                    QAbstractButton,
                    QAbstractSlider,
                    QAbstractSpinBox,
                    QAbstractItemView,
                    QComboBox,
                    QLineEdit,
                    QTextEdit,
                    QPlainTextEdit,
                    QScrollBar,
                    QSizeGrip,
                ),
            ):
                return True
            # Native scrollbars / viewport hosts often aren't the interactive class itself
            if w.objectName() == "qt_scrollarea_viewport":
                parent = w.parentWidget()
                if isinstance(parent, QAbstractItemView):
                    return True
            w = w.parentWidget()
        return False

    def _begin_window_drag(self, global_pos: QPoint) -> None:
        self._drag_pos = global_pos - self.frameGeometry().topLeft()
        self._resize_edges = None
        self._resize_origin = None
        self._resize_geo = None

    def eventFilter(self, obj, event):
        """Edge-resize on border; drag window from non-interactive chrome."""
        if isinstance(obj, QWidget) and obj.window() is self:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = self.mapFromGlobal(event.globalPosition().toPoint())
                edges = self._hit_resize_edges(pos)
                if any(edges) and not isinstance(obj, QSizeGrip):
                    self._resize_edges = edges
                    self._resize_origin = event.globalPosition().toPoint()
                    self._resize_geo = QRect(self.geometry())
                    self._drag_pos = None
                    self.grabMouse()
                    return True
                if not isinstance(obj, QSizeGrip) and not self._widget_is_interactive(obj):
                    self._begin_window_drag(event.globalPosition().toPoint())
                    # Do not consume — labels/panels still get the press; move is tracked below
            elif et == QEvent.Type.MouseMove:
                if self._resize_edges is not None and self._resize_origin is not None and self._resize_geo is not None:
                    self._apply_edge_resize(event.globalPosition().toPoint())
                    return True
                if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    self._clamp_on_screen()
                    return True
                if not (event.buttons() & Qt.MouseButton.LeftButton):
                    pos = self.mapFromGlobal(event.globalPosition().toPoint())
                    if self.rect().contains(pos):
                        self._update_resize_cursor(self._hit_resize_edges(pos))
            elif et == QEvent.Type.MouseButtonRelease:
                if self._resize_edges is not None:
                    self.releaseMouse()
                    self._resize_edges = None
                    self._resize_origin = None
                    self._resize_geo = None
                    pos = self.mapFromGlobal(event.globalPosition().toPoint())
                    self._update_resize_cursor(self._hit_resize_edges(pos))
                    return True
                self._drag_pos = None
        return super().eventFilter(obj, event)

    def _apply_edge_resize(self, global_pos: QPoint) -> None:
        if self._resize_edges is None or self._resize_origin is None or self._resize_geo is None:
            return
        delta = global_pos - self._resize_origin
        left, right, top, bottom = self._resize_edges
        g = self._resize_geo
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        max_w, max_h = self.maximumWidth(), self.maximumHeight()
        if left:
            new_w = w - delta.x()
            if new_w < min_w:
                x = g.x() + w - min_w
                w = min_w
            elif new_w > max_w:
                x = g.x() + w - max_w
                w = max_w
            else:
                x = g.x() + delta.x()
                w = new_w
        if right:
            w = max(min_w, min(max_w, w + delta.x()))
        if top:
            new_h = h - delta.y()
            if new_h < min_h:
                y = g.y() + h - min_h
                h = min_h
            elif new_h > max_h:
                y = g.y() + h - max_h
                h = max_h
            else:
                y = g.y() + delta.y()
                h = new_h
        if bottom:
            h = max(min_h, min(max_h, h + delta.y()))
        self.setGeometry(x, y, w, h)
        self._clamp_on_screen()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._hit_resize_edges(pos)
            if any(edges):
                self._resize_edges = edges
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geo = QRect(self.geometry())
                self._drag_pos = None
                event.accept()
                return
            # Fallback when press lands on the window itself (not a child)
            self._begin_window_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edges is not None and self._resize_origin is not None and self._resize_geo is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._apply_edge_resize(event.globalPosition().toPoint())
                event.accept()
                return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._clamp_on_screen()
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_edges is not None:
            self.releaseMouse()
        self._drag_pos = None
        self._resize_edges = None
        self._resize_origin = None
        self._resize_geo = None
        self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseReleaseEvent(event)

    def _nav(self, idx: int) -> None:
        if idx == self._current_nav:
            return
        self._current_nav = idx
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_btns):
            b.setChecked(i == idx)
        # Lightweight page updates only — never rebuild huge addon lists on switch
        if idx == 0:
            self.home.refresh()
        elif idx == 2:
            self.client.refresh_from_settings()
        elif idx == 3:
            self.settings_page.refresh()
        # Addons page keeps its current list until filters/rescan change

    def _launcher_update_pending(self) -> bool:
        info = self._latest_launcher_release
        return bool(info and info.update_available)

    def _refresh_nav_badges(self) -> None:
        """Gold dots on folder tabs when that area has pending work."""
        if len(self.nav_btns) < 4:
            return
        # HOME — launcher self-update (PLAY already becomes UPDATE)
        self.nav_btns[0].set_badge_visible(self._launcher_update_pending())
        # ADDONS — managed addon updates available
        self.nav_btns[1].set_badge_visible(bool(self.addons.pending_updates))
        # CLIENT — mod updates or Apply Changes pending
        self.nav_btns[2].set_badge_visible(self.client.has_pending_badge())
        # SETTINGS — unused for badges
        self.nav_btns[3].set_badge_visible(False)

    def _periodic_update_check(self) -> None:
        """Recurring quiet update detection while the launcher remains open."""
        if self._worker and self._worker.isRunning():
            return
        self._check_launcher_update(silent=True)
        if not is_installed():
            self._refresh_nav_badges()
            return
        if rate_limit_exhausted():
            log.info("Skipping periodic addon/mod checks — GitHub rate limit low")
            self._refresh_nav_badges()
            return
        self._check_updates(silent=True, periodic=True)
        self._check_mod_updates(silent=True, periodic=True)

    def _refresh_play_button(self) -> None:
        if self._launcher_update_pending():
            self.play_btn.setText("UPDATE")
            self._refresh_nav_badges()
            return
        if is_installed():
            self.play_btn.setText("PLAY")
        else:
            self.play_btn.setText("INSTALL")
        self._refresh_nav_badges()

    def _set_busy_ui(self, busy: bool, msg: str = "") -> None:
        self.play_btn.setEnabled(not busy)
        if busy:
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate until bytes known
            self.progress.setFormat("")
            self.status_lbl.setText(msg or "Working…")
        else:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("%p%")
            self.status_lbl.setText(msg or "Ready")

    def _on_progress_pct(self, pct: int) -> None:
        """Update bottom bar: determinate 0–100, or busy when pct < 0."""
        if not self.progress.isVisible():
            return
        if pct < 0:
            self.progress.setRange(0, 0)
            self.progress.setFormat("")
            return
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(pct))))
        self.progress.setFormat("%p%")

    def _refresh_check_loading(self) -> None:
        addon_busy = self._checking_addons
        mod_busy = self._checking_mods
        self.addons.set_checking(addon_busy, "Checking for updates…")
        self.client.set_checking(mod_busy, "Checking for updates…")
        if addon_busy and mod_busy:
            self.home.set_checking(True, "Checking addon & client updates…")
        elif addon_busy:
            self.home.set_checking(True, "Checking addon updates…")
        elif mod_busy:
            self.home.set_checking(True, "Checking client mod updates…")
        else:
            self.home.set_checking(False)

    def _resync(self, silent: bool = False) -> None:
        if not is_installed():
            if not silent:
                themed.warning(self, "No game", "Set a valid game path first.")
            return
        result = full_resync()
        # Disk rescan is not an update-check — keep / reset scan-done so we don't
        # claim "Up to date" without a successful Check Updates pass.
        self.addons.reset_scan_done()
        self.client.reset_scan_done()
        self.client.refresh_from_settings()
        self.addons.mark_dirty()
        self.addons.refresh()
        self.home.refresh()
        self._refresh_play_button()
        if not silent:
            themed.info(
                self,
                "Rescan complete",
                f"Detected {len(result['addons'])} addon folder(s) and synced client mod checkboxes.",
            )

    def _busy(self, title: str, worker: Worker, on_ok=None) -> None:
        if self._worker and self._worker.isRunning():
            themed.info(self, "Busy", "Another task is already running.")
            return
        self._set_busy_ui(True, title)
        worker.status.connect(lambda m: self.status_lbl.setText(m))
        worker.progress_pct.connect(self._on_progress_pct)
        worker.finished_ok.connect(self._on_worker_ok)
        worker.failed.connect(self._on_worker_fail)
        self._worker = worker
        self._pending_ok_handler = on_ok
        worker.start()

    def _on_worker_ok(self, result) -> None:
        self._set_busy_ui(False, "Ready")
        handler = self._pending_ok_handler
        self._pending_ok_handler = None
        restarting = False
        if handler:
            try:
                restarting = bool(handler(result))
            except Exception as exc:  # noqa: BLE001
                log.exception("Post-worker handler failed")
                themed.error(self, "Error", str(exc))
                self.status_lbl.setText(f"Failed: {str(exc)[:80]}")
                return
        # Self-update calls quit() — do not touch widgets afterward (causes crashes).
        app = QApplication.instance()
        if restarting or (app is not None and app.closingDown()):
            return
        self.home.refresh()
        self.client.refresh_plan()
        self.addons.mark_dirty()
        self.addons.refresh()
        self.settings_page.refresh()
        self._refresh_play_button()
        self._refresh_nav_badges()

    def _on_worker_fail(self, msg: str) -> None:
        self._set_busy_ui(False, "Failed")
        themed.error(self, "Error", msg)
        self.status_lbl.setText(f"Failed: {msg[:80]}")

    def _on_play_or_install(self) -> None:
        if self._launcher_update_pending():
            self._apply_launcher_update()
            return
        if is_installed():
            self._play()
        else:
            self._install_or_browse()

    def _check_launcher_update(self, silent: bool = False) -> None:
        """Background check for a newer IchaLaunch GitHub release."""
        if self._launcher_update_worker and self._launcher_update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Launcher update check already running…")
            return

        if not silent:
            self.status_lbl.setText("Checking for launcher updates…")

        worker = Worker(check_latest_launcher_release)

        def done(result):
            self._launcher_update_worker = None
            if isinstance(result, LauncherReleaseInfo) and result.update_available:
                self._latest_launcher_release = result
                self.status_lbl.setText(f"Launcher update available: v{result.version}")
                self._refresh_play_button()
                return
            self._latest_launcher_release = None
            self._refresh_play_button()
            if not silent:
                if result is None:
                    self.status_lbl.setText("No launcher release asset found")
                else:
                    self.status_lbl.setText("Launcher is up to date")

        def fail(msg: str):
            self._launcher_update_worker = None
            self._latest_launcher_release = None
            self._refresh_play_button()
            brief = msg[:80] if msg else "unknown error"
            if silent:
                log.warning("Launcher update check failed: %s", msg)
                # Quiet status only — do not steal focus from addon/mod checks.
                if "rate limit" in brief.lower():
                    self.status_lbl.setText(RATE_LIMIT_STATUS)
                else:
                    self.status_lbl.setText(f"Launcher update check failed: {brief}")
            else:
                themed.error(self, "Launcher update check failed", msg)

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._launcher_update_worker = worker
        worker.start()

    def _apply_launcher_update(self) -> None:
        info = self._latest_launcher_release
        if not info or not info.update_available:
            return

        def on_ok(staged):
            try:
                apply_windows_self_replace(staged)
            except Exception as exc:  # noqa: BLE001
                log.exception("Launcher self-replace failed")
                themed.error(self, "Update failed", str(exc))
                self.status_lbl.setText(f"Update failed: {str(exc)[:80]}")
                return False
            self.status_lbl.setText("Installing update and restarting…")
            # Helper waits for this process to exit, then swaps the EXE and relaunches.
            # Return True so _on_worker_ok skips UI refresh (quit tears down widgets).
            QApplication.instance().quit()
            return True

        worker = Worker(perform_launcher_update, info)
        self._busy(f"Downloading launcher v{info.version}…", worker, on_ok=on_ok)

    def _play(self) -> None:
        try:
            launch_game()
            if settings.get("close_on_launch"):
                self.close()
            elif settings.get("minimize_on_launch"):
                self.showMinimized()
        except Exception as exc:  # noqa: BLE001
            themed.error(self, "Launch failed", str(exc))

    def _install_or_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Ravencraft install folder", "C:/Games")
        if not path:
            return
        p = Path(path)
        ok, msg = validate_install_location(p)
        if not ok:
            themed.warning(self, "Protected location", msg)
            return
        if (p / "WoW.exe").exists():
            settings.game_path = str(p)
            self._resync(silent=True)
            themed.info(self, "Ready", f"Using existing client:\n{p}")
            self.home.refresh()
            self._refresh_play_button()
            return
        note = install_game_stub(p)
        themed.info(
            self,
            "Install folder ready",
            f"{note}\n\nSelected folder:\n{p}\n\n"
            "After copying client files (or browsing to an existing install), press PLAY.",
        )
        self.home.refresh()
        self._refresh_play_button()

    def _browse_game(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select game folder (contains WoW.exe)")
        if not path:
            return
        if is_protected_path(path):
            themed.warning(
                self,
                "Protected location",
                "This folder may cause permission issues with client mods.",
            )
        if not (Path(path) / "WoW.exe").exists():
            themed.warning(self, "Not a game folder", "WoW.exe was not found in that folder.")
            return
        settings.game_path = path
        self.settings_page.refresh()
        self._resync(silent=True)
        self._refresh_play_button()
        themed.info(self, "Saved", f"Game path set to:\n{path}")

    def _verify_game(self) -> None:
        if is_installed():
            themed.info(self, "Verify", f"WoW.exe found at:\n{settings.game_path}")
        else:
            themed.warning(self, "Verify", "Game not detected. Browse to a valid client folder.")

    def _apply_mods(self) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        worker = Worker(apply_desired_state)
        self._busy(
            "Applying client mods…",
            worker,
            on_ok=lambda result: themed.info(
                self, "Client updated", "Changes applied:\n" + ("\n".join(result) if result else "(none)")
            ),
        )

    def _install_catalog_addon(self, entry: dict) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        url = entry.get("repo")
        folder = entry.get("folder")
        worker = Worker(install_from_github, url, folder)
        self._busy(
            f"Installing {entry.get('name')}…",
            worker,
            on_ok=lambda name: themed.info(self, "Installed", f"Installed: {name}"),
        )

    def _github_import(self, url: str) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        worker = Worker(install_from_github, url)
        self._busy(
            "Importing from GitHub…",
            worker,
            on_ok=lambda name: themed.info(
                self, "Installed from GitHub", f"Installed from GitHub: {name}"
            ),
        )

    def _custom_dll_import(self, url: str) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(mod):
            if isinstance(mod, dict):
                self.client.ensure_mod_row(mod)
                name = mod.get("name") or mod.get("id") or "DLL"
                dlls = (mod.get("dlls_txt") or {}).get("add") or []
                extra = f"\nRegistered in dlls.txt: {', '.join(dlls)}" if dlls else ""
                themed.info(
                    self,
                    "Custom DLL installed",
                    f"Installed {name} and saved it as a Client checkbox.{extra}",
                )
            else:
                themed.info(self, "Custom DLL installed", "DLL installed from GitHub.")

        worker = Worker(install_custom_dll_from_github, url)
        self._busy("Installing custom DLL from GitHub…", worker, on_ok=on_ok)

    def _update_addon(self, entry: dict) -> None:
        folder = entry.get("folder") or entry.get("name")
        if not folder:
            return
        worker = Worker(update_addon, folder)

        def on_ok(_result):
            self.addons.clear_pending_update(folder)
            self.status_lbl.setText(f"Updated {folder}")

        self._busy(f"Updating {folder}…", worker, on_ok=on_ok)

    def _reinstall_addon(self, entry: dict) -> None:
        """Force re-download/overwrite regardless of current commit."""
        folder = entry.get("folder") or entry.get("name")
        if not folder:
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        meta = settings.installed_addons.get(folder) or {}
        url = entry.get("repo") or meta.get("url") or (
            f"https://github.com/{meta['repository']}" if meta.get("repository") else ""
        )
        if not url:
            themed.warning(self, "Cannot reinstall", f"No GitHub URL for {folder}.")
            return

        def on_ok(_result):
            self.addons.clear_pending_update(folder)
            self.status_lbl.setText(f"Reinstalled {folder}")

        worker = Worker(install_from_github, url, folder)
        self._busy(f"Reinstalling {folder}…", worker, on_ok=on_ok)

    def _update_all_addons(self) -> None:
        pending = self.addons.pending_updates
        folders = [u.get("folder") or u.get("name") for u in pending]
        folders = [f for f in folders if f]
        if not folders:
            self.status_lbl.setText("No updates available — run Check Updates first.")
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        total = len(folders)

        def run_all(progress=None):
            ok: list[str] = []
            failed: list[tuple[str, str]] = []
            for i, folder in enumerate(folders, start=1):
                if progress:
                    progress(f"Updating {folder} ({i}/{total})…")
                try:
                    update_addon(folder, progress=progress)
                    ok.append(folder)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Update All failed for %s", folder)
                    failed.append((folder, str(exc)))
            return {"ok": ok, "failed": failed}

        def on_ok(result):
            ok_folders = set(result.get("ok") or [])
            remaining = [u for u in self.addons.pending_updates if u.get("folder") not in ok_folders]
            self.addons.set_updates(remaining)
            n_ok = len(ok_folders)
            failed = result.get("failed") or []
            if failed:
                names = ", ".join(f for f, _ in failed[:5])
                more = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
                self.status_lbl.setText(f"Updated {n_ok}/{total}; failed: {names}{more}")
            else:
                self.status_lbl.setText(f"Updated {n_ok} addon(s)")

        worker = Worker(run_all)
        self._busy(f"Updating {total} addon(s)…", worker, on_ok=on_ok)

    def _remove_addon(self, folder: str) -> None:
        if not themed.question(self, "Remove addon", f"Remove {folder}?"):
            return
        try:
            uninstall_addon(folder)
            self.addons.mark_dirty()
            self.addons.refresh()
            self.home.refresh()
        except Exception as exc:  # noqa: BLE001
            themed.error(self, "Error", str(exc))

    def _check_updates(self, silent: bool = False, periodic: bool = False) -> None:
        """Quiet background check — status bar only, never blocks PLAY or shows a popup."""
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._update_worker and self._update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Update check already running…")
            return

        # Startup cooldown only — periodic checks skip it so the open app stays current.
        if silent and not periodic and recently_checked_addon_updates():
            return

        self.status_lbl.setText("Checking addon updates…")
        self._checking_addons = True
        self._refresh_check_loading()
        worker = Worker(check_addon_updates, respect_cooldown=False)

        def done(result):
            self._checking_addons = False
            self._refresh_check_loading()
            if isinstance(result, AddonUpdateCheckResult):
                updates = result.updates
                status = result.status_message
            else:
                updates = result or []
                status = None
            self.addons.set_updates(updates)
            if status:
                self.status_lbl.setText(status)
            elif updates:
                self.status_lbl.setText(f"{len(updates)} addon update(s) available")
            else:
                self.status_lbl.setText("Addons up to date")
            self._update_worker = None
            self._refresh_nav_badges()

        def fail(msg: str):
            self._checking_addons = False
            self._refresh_check_loading()
            self.status_lbl.setText(f"Update check failed: {msg[:80]}")
            self._update_worker = None

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._update_worker = worker
        worker.start()

    def _check_mod_updates(self, silent: bool = False, periodic: bool = False) -> None:
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._mod_update_worker and self._mod_update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Client mod update check already running…")
            return
        # Startup cooldown only — periodic checks skip it so the open app stays current.
        if silent and not periodic and recently_checked_mod_updates():
            return

        self.status_lbl.setText("Checking client mod updates…")
        self._checking_mods = True
        self._refresh_check_loading()
        worker = Worker(check_mod_updates, respect_cooldown=False)

        def done(result):
            self._checking_mods = False
            self._refresh_check_loading()
            if isinstance(result, ModUpdateCheckResult):
                updates = result.updates
                status = result.status_message
                if result.skipped_recent:
                    self._mod_update_worker = None
                    return
            else:
                updates = result or []
                status = None
            self.client.set_updates(updates)
            if status:
                self.status_lbl.setText(status)
            elif updates:
                self.status_lbl.setText(f"{len(updates)} client mod update(s) available")
            else:
                self.status_lbl.setText("Client mods up to date")
            self._mod_update_worker = None
            self._refresh_nav_badges()

        def fail(msg: str):
            self._checking_mods = False
            self._refresh_check_loading()
            self.status_lbl.setText(f"Client mod check failed: {msg[:80]}")
            self._mod_update_worker = None

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._mod_update_worker = worker
        worker.start()

    def _update_client_mod(self, mod_id: str) -> None:
        if not mod_id:
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(_result):
            self.client.clear_pending_update(mod_id)
            self.status_lbl.setText(f"Updated {mod_id}")

        worker = Worker(update_mod, mod_id)
        self._busy(f"Updating {mod_id}…", worker, on_ok=on_ok)

    def _reinstall_client_mod(self, mod_id: str) -> None:
        """Force re-download with prefer_latest, even if already current."""
        if not mod_id:
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(_result):
            self.client.clear_pending_update(mod_id)
            self.status_lbl.setText(f"Reinstalled {mod_id}")

        worker = Worker(update_mod, mod_id)
        self._busy(f"Reinstalling {mod_id}…", worker, on_ok=on_ok)

    def _update_all_client_mods(self) -> None:
        pending = self.client.pending_updates
        ids = [u.get("id") for u in pending if u.get("id")]
        if not ids:
            self.status_lbl.setText("No client mod updates — run Check Updates first.")
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        total = len(ids)

        def on_ok(_result):
            self.client.set_updates([])
            self.status_lbl.setText(f"Updated {total} client mod(s)")

        worker = Worker(update_mods, ids)
        self._busy(f"Updating {total} client mod(s)…", worker, on_ok=on_ok)
