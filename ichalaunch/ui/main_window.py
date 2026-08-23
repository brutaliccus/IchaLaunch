"""Main window — borderless folder chrome, folder tabs, bottom play bar."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QTransform,
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
    QPushButton,
    QScrollBar,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.ui.widgets import dialogs as themed

_RESIZE_MARGIN = 6
_CORNER_RADIUS = 14
_TAB_STRIP_HEIGHT = 44
# ContentPanel top inset so page scroll clips below minimize/close (not under −/X).
# Matches chrome: inset_y(10) + glyph(28) + small gap below.
_CONTENT_TOP_CHROME = 44
# RavenCraft crest at ContentPanel top (was MoA). Larger than MoA wordmark was.
_RC_LOGO_WIDTH = 210
# Optional outer pad around the crest (kept for layout math; no glow drawn).
_RC_GLOW_PAD_X = 0
_RC_GLOW_PAD_Y = 0
# Quiet launcher self-update re-check while the app stays open (addons/client: launch only).
_PERIODIC_UPDATE_MS = 5 * 60 * 1000
# Let the window finish laying out / detecting game path before the first network scan.
_STARTUP_UPDATE_DELAY_MS = 1500
_NAV_BOTTOM_BANNER_H = 30
# Vertical center of the banner PNG — HOME art and mist fill meet here.
_NAV_BOTTOM_BANNER_MID_Y = _NAV_BOTTOM_BANNER_H // 2
# Draw strip this many px past each side so end spikes clip off (~20px wider total).
_NAV_BOTTOM_BANNER_OVERDRAW_X = 12
_GOLD = QColor("#F1C22D")
_CORNER_RADIUS_F = float(_CORNER_RADIUS)
# Main purple frame stroke — same as former QSS rgba(124, 92, 196, 0.45).
_FRAME_STROKE = QColor(124, 92, 196, 115)
# Mechagon top rail — horizontal strip, sits just below the purple shelf stroke.
# Drawn at 15px; tiled end-to-end; body fill starts below the rail.
_TOP_RAIL_NAME = "UIFrameMechagonVertical.PNG"
_TOP_RAIL_EXTERNAL = Path(r"C:\Users\jeb32\Downloads\UIFrameMechagonVertical.PNG")
_TOP_RAIL_DRAW_H = 15

# Main ContentPanel floor — opaque RavenCraft base, then tiles + wash on top.
_FLOOR_BASE = QColor("#181315")
_FLOOR_NAME = "UIFrameNecrolordBackground.PNG"
_FLOOR_EXTERNAL = Path(r"F:\wow-ui-textures\FrameGeneral\UIFrameNecrolordBackground.PNG")
# Soft floor: subtle darken vs first Necrolord preview (0.22/90 → slight nudge).
_FLOOR_TILE_OPACITY = 0.19
_FLOOR_WASH = QColor(24, 19, 21, 105)

# BottomBar mist FX — one row, bottom-left, tiled horizontally only.
_MIST_BASE = QColor("#100d0c")
_MIST_NAME = "6TJ_Polluted_mist_Stormy.PNG"
_MIST_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\Models\UI_Orc\6TJ_Polluted_mist_Stormy.PNG")
_MIST_WASH = QColor(16, 13, 12, 110)

# Custom arrow cursor (WoW Point) — tip is top-left pixel of the 32×32 PNG.
_CURSOR_POINT_NAME = "cursor_point.png"
_CURSOR_POINT_EXTERNAL = Path(r"F:\wow-ui-textures\CURSOR\Point.PNG")
_CURSOR_POINT_HOTSPOT = (0, 0)

# Bottom main-frame corner ornaments (bundled left_corners.png bottom crop + H-flip).
_SIDE_CORNERS_NAME = "left_corners.png"
_SIDE_CORNERS_SRC_H = 920
# Bottom-left L in the tall strip (top ornament unused — no top corners).
_BOTTOM_CORNER_SRC_Y = 765
_BOTTOM_CORNER_SRC_H = _SIDE_CORNERS_SRC_H - _BOTTOM_CORNER_SRC_Y  # 155
# Inner edge of the L-stem in source px — aligned to the purple stroke.
_SIDE_CORNERS_INNER_X = 20
_SIDE_CORNERS_INNER_Y = 20
# Vertical: lower inward → hang_y grows → corners sit lower (was 6; −5 → 1; −2 → −1; +4 → 3).
_BOTTOM_CORNERS_INWARD_Y = 3
# Horizontal: scoot L/R outward from that prior pose by this many px (not 6).
_BOTTOM_CORNERS_OUTWARD_X = 6
# Extra draw height on BL/BR (width stays aspect-scaled; height ignores AR).
_BOTTOM_CORNERS_STRETCH_Y = 20
# Paint inset X kept at the prior shared inward (6) so only hang_x moves art ~OUTWARD_X.
_BOTTOM_CORNERS_PAINT_INSET_X = 6
# Root gutter so out-set corners are not clipped at the window edge.
_FRAME_OUTSET_MARGIN = 24
# Chrome pad for −/X (bottom corners no longer crowd the top-right).
_CHROME_FRAME_PAD = 14

from ichalaunch import __version__
from ichalaunch.core.paths import theme_file


def _resolve_theme_texture(bundled_name: str, external: Path) -> Path | None:
    bundled = theme_file(bundled_name)
    if bundled.is_file():
        return bundled
    if external.is_file():
        return external
    return None


def _load_theme_texture(bundled_name: str, external: Path) -> QPixmap:
    path = _resolve_theme_texture(bundled_name, external)
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _load_point_cursor() -> QCursor | None:
    """Bundled theme PNG with absolute WoW-texture fallback; hotspot at arrow tip."""
    pm = _load_theme_texture(_CURSOR_POINT_NAME, _CURSOR_POINT_EXTERNAL)
    if pm.isNull():
        return None
    hx, hy = _CURSOR_POINT_HOTSPOT
    return QCursor(pm, hx, hy)


from ichalaunch.addons.github import (
    AddonUpdateCheckResult,
    GIT_REPAIR_STATUS,
    GITHUB_TOKEN_REJECTED_MSG,
    RATE_LIMIT_STATUS,
    WAITING_RATE_LIMIT_STATUS,
    check_addon_updates,
    format_github_error_message,
    has_github_token,
    has_pending_addon_scan_queue,
    install_from_github,
    rate_limit_exhausted,
    recently_checked_addon_updates,
    take_github_token_warning,
    uninstall_addon,
    update_addon,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import full_resync
from ichalaunch.core.filesystem import (
    PermissionScanResult,
    fix_game_permissions,
    is_protected_path,
    protected_location_guidance,
    scan_game_permissions,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import StatusProgress, status_only
from ichalaunch.core.self_update import (
    LauncherReleaseInfo,
    apply_windows_self_replace,
    check_latest_launcher_release,
    perform_launcher_update,
    read_cached_launcher_release,
)
from ichalaunch.game.client_install import (
    apply_bundled_realmlist,
    client_watch_dirs,
    install_client,
    settle_ravencraft_home,
    should_settle_existing,
    wow_exe_here,
)
from ichalaunch.game.launcher import (
    GAME_DOWNLOAD_URL,
    GOFILE_FILE_NAME,
    ensure_game_path_from_launcher,
    has_wow_exe,
    is_installed,
    launch_game,
    validate_install_location,
)
from ichalaunch.mods.installer import (
    ModUpdateCheckResult,
    apply_desired_state,
    check_mod_updates,
    ensure_desired_mods_synced,
    install_custom_dll_from_github,
    plan_manual_missing,
    plan_sync_changes,
    prepare_for_launch,
    recently_checked_mod_updates,
    update_mod,
    update_mods,
)
from ichalaunch.ui.pages.addons import AddonsPage
from ichalaunch.ui.pages.client import ClientPage
from ichalaunch.ui.pages.home import HomePage
from ichalaunch.ui.pages.settings import SettingsPage
from ichalaunch.ui.widgets.loading_bar import ThemeLoadingBar
from ichalaunch.ui.widgets.chrome_buttons import ChromeGlyphButton
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.launch_button import LaunchButton


def _format_minutes_since(settings_key: str) -> str:
    """Human age of a stored epoch timestamp, e.g. ``23 min`` — for cooldown logs."""
    try:
        minutes = int((time.time() - float(settings.get(settings_key))) / 60)
    except (TypeError, ValueError):
        return "unknown"
    return f"{minutes} min"


class NavTabButton(QPushButton):
    """Folder tab — Necrolord floor + optional gold pending-update badge."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self._badge = False
        self._floor = _load_theme_texture(_FLOOR_NAME, _FLOOR_EXTERNAL)
        self._tile_anchor: QWidget | None = None

    def set_tile_anchor(self, anchor: QWidget | None) -> None:
        self._tile_anchor = anchor

    def set_badge_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._badge:
            return
        self._badge = visible
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Match QSS tab radii — rounded top, flush bottom onto the purple shelf.
        # Extend 1px past the widget so antialiasing does not fringe the last
        # row (a hairline leak on the translucent frameless window).
        tab = QPainterPath()
        tab.setFillRule(Qt.FillRule.WindingFill)
        rect = QRectF(self.rect())
        rect.setBottom(rect.bottom() + 1.0)
        r = 9.0
        tab.moveTo(rect.left(), rect.bottom())
        tab.lineTo(rect.left(), rect.top() + r)
        tab.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        tab.lineTo(rect.right() - r, rect.top())
        tab.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        tab.lineTo(rect.right(), rect.bottom())
        tab.closeSubpath()
        painter.setClipPath(tab)
        origin = QPoint(0, 0)
        anchor = self._tile_anchor
        if anchor is not None:
            mapped = _map_via_global(anchor, self, QPoint(1, 0))
            if mapped is not None:
                origin = mapped
        _paint_floor_fill(painter, self.rect(), self._floor, tile_origin=origin)
        painter.end()
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


class RavenCraftFloatingLogo(QWidget):
    """RavenCraft crest — rides ContentPanel top / folder shelf (no glow)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RavenCraftFloatingLogo")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pix = QPixmap()
        self._logo_h = 0
        self._load()

    def _load(self) -> None:
        path = theme_file("ravencraft.png")
        if not path.exists():
            self.hide()
            return
        src = QPixmap(str(path))
        if src.isNull():
            self.hide()
            return
        self._pix = src.scaledToWidth(_RC_LOGO_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self._logo_h = self._pix.height()
        self.setFixedSize(
            self._pix.width() + _RC_GLOW_PAD_X * 2,
            self._logo_h + _RC_GLOW_PAD_Y * 2,
        )
        self.show()

    @property
    def logo_offset_y(self) -> int:
        """Y of the crest top edge relative to this widget."""
        return _RC_GLOW_PAD_Y

    @property
    def logo_height(self) -> int:
        return self._logo_h

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._pix.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(_RC_GLOW_PAD_X, _RC_GLOW_PAD_Y, self._pix)


def _paint_floor_fill(
    painter: QPainter,
    rect: QRect,
    floor: QPixmap,
    *,
    tile_origin: QPoint | None = None,
) -> None:
    """Opaque RavenCraft base + tiled Necrolord + wash. tile_origin aligns across widgets."""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    painter.fillRect(rect, _FLOOR_BASE)
    tw = 0 if floor.isNull() else floor.width()
    th = 0 if floor.isNull() else floor.height()
    if tw > 0 and th > 0:
        ox = tile_origin.x() if tile_origin is not None else rect.left()
        oy = tile_origin.y() if tile_origin is not None else rect.top()
        # Align tile phase to a shared origin so TopNav and ContentPanel match.
        x0 = rect.left() - ((rect.left() - ox) % tw)
        y0 = rect.top() - ((rect.top() - oy) % th)
        painter.setOpacity(_FLOOR_TILE_OPACITY)
        y = y0
        while y < rect.bottom():
            x = x0
            while x < rect.right():
                painter.drawPixmap(x, y, floor)
                x += tw
            y += th
        painter.setOpacity(1.0)
        painter.fillRect(rect, _FLOOR_WASH)
    painter.restore()


def _paint_top_rail(
    painter: QPainter,
    rect: QRect,
    rail: QPixmap,
    *,
    draw_h: int = _TOP_RAIL_DRAW_H,
) -> None:
    """Tile the Mechagon rail along the content shelf, just below the purple stroke.

    Filename says Vertical; the PNG is already a wide horizontal strip. No rotate.
    """
    if rail.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return
    src_w = rail.width()
    src_h = rail.height()
    if src_w <= 0 or src_h <= 0:
        return
    h = min(int(draw_h), rect.height())
    if h <= 0:
        return
    tw = max(1, int(round(src_w * (h / float(src_h)))))
    tile = rail.scaled(
        tw,
        h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    x = rect.left()
    y = rect.top()
    while x < rect.right():
        painter.drawPixmap(x, y, tile)
        x += tw
    painter.restore()


def _paint_mist_fill(
    painter: QPainter,
    rect: QRect,
    mist: QPixmap,
    *,
    tile_h: int | None = None,
    tile_origin: QPoint | None = None,
) -> None:
    """Opaque mist base + one horizontal mist row + wash (BottomBar / banner spikes)."""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    painter.fillRect(rect, _MIST_BASE)
    if mist.isNull():
        painter.fillRect(rect, _MIST_WASH)
        return
    th = max(1, int(tile_h or rect.height()))
    tile = mist.scaledToHeight(th, Qt.TransformationMode.SmoothTransformation)
    tw = tile.width()
    if tw <= 0 or tile.height() <= 0:
        painter.fillRect(rect, _MIST_WASH)
        return
    ox = tile_origin.x() if tile_origin is not None else rect.left()
    oy = tile_origin.y() if tile_origin is not None else (rect.bottom() - tile.height() + 1)
    x0 = rect.left() - ((rect.left() - ox) % tw)
    y = oy
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    x = x0
    while x < rect.right():
        painter.drawPixmap(x, y, tile)
        x += tw
    painter.restore()
    painter.fillRect(rect, _MIST_WASH)


def _folder_frame_path(
    left: float,
    right: float,
    shelf_y: float,
    bottom: float,
    tab: QRectF | None = None,
    *,
    body_r: float = _CORNER_RADIUS_F,
) -> QPainterPath:
    """Closed purple body: sharp TL/TR, rounded BL/BR, continuous top shelf.

    *tab* is ignored — tabs sit on TopNav above this frame and do not open into
    the body. Fill rule is WindingFill so clip/fill use the enclosed interior.
    """
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    half_w = max(1.0, (right - left) * 0.5)
    half_h = max(1.0, (bottom - shelf_y) * 0.5)
    br = min(body_r, half_w, half_h)
    _ = tab  # kept so older call sites that pass a tab stay valid

    # Clockwise from bottom-left: full shelf, no selected-tab bump.
    path.moveTo(left, bottom - br)
    path.lineTo(left, shelf_y)
    path.lineTo(right, shelf_y)
    path.lineTo(right, bottom - br)
    path.quadTo(right, bottom, right - br, bottom)
    path.lineTo(left + br, bottom)
    path.quadTo(left, bottom, left, bottom - br)
    path.closeSubpath()
    return path


def _safe_map_to(src: QWidget, dest: QWidget, pos: QPoint) -> QPoint | None:
    """mapTo only when *dest* is an ancestor of *src* (Qt requirement)."""
    if src is dest:
        return QPoint(pos)
    if dest is None or src is None:
        return None
    if not dest.isAncestorOf(src):
        return None
    return src.mapTo(dest, pos)


def _safe_map_from(dest: QWidget, src: QWidget, pos: QPoint) -> QPoint | None:
    """mapFrom only when *src* is an ancestor of *dest*."""
    if src is dest:
        return QPoint(pos)
    if dest is None or src is None:
        return None
    if not src.isAncestorOf(dest):
        return None
    return dest.mapFrom(src, pos)


def _map_via_global(src: QWidget, dest: QWidget, pos: QPoint) -> QPoint | None:
    """Map *pos* from *src* into *dest* via global space (siblings / any common tree)."""
    if src is None or dest is None:
        return None
    if not src.isVisible() or not dest.isVisible():
        # Still valid if both are in the live tree (may be mid-layout).
        if src.window() is None or dest.window() is None:
            return None
    try:
        return dest.mapFromGlobal(src.mapToGlobal(pos))
    except Exception:  # noqa: BLE001
        return None


def _map_folder_path_to_widget(
    path: QPainterPath, root: QWidget, widget: QWidget
) -> QPainterPath:
    """Translate a Root-space folder path into widget-local coordinates.

    Prefer global mapping (works for any pair in the same window). Do not use
    root.mapTo(widget): that API requires *widget* to be an ancestor of *root*.
    """
    origin = _map_via_global(root, widget, QPoint(0, 0))
    if origin is None:
        origin = _safe_map_from(widget, root, QPoint(0, 0))
    if origin is None:
        return QPainterPath()
    mapped = QTransform.fromTranslate(float(origin.x()), float(origin.y())).map(path)
    mapped.setFillRule(Qt.FillRule.WindingFill)
    return mapped


def _rounded_bottom_rect_path(rect: QRectF, radius: float = _CORNER_RADIUS_F) -> QPainterPath:
    """Opaque fallback clip for BottomBar — BL/BR fillets, sharp top."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    r = min(radius, max(0.0, rect.width() * 0.5), max(0.0, rect.height()))
    path.moveTo(rect.left(), rect.top())
    path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom() - r)
    path.quadTo(rect.right(), rect.bottom(), rect.right() - r, rect.bottom())
    path.lineTo(rect.left() + r, rect.bottom())
    path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - r)
    path.closeSubpath()
    return path


def _interior_fill_clip(
    widget: QWidget,
    inner: QRect,
    host: "MainWindow | None",
    *,
    round_bottom: bool = False,
    empty_if_unmapped: bool = False,
    pad_top: float = 0.5,
    pad_bottom: float = 0.5,
) -> QPainterPath:
    """Chrome fill clip: filled folder interior ∩ widget inset (never exterior).

    QSS chrome is transparent, so a bad/empty clip would leave a see-through hole.
    Always fall back to the inset rect (optionally BL/BR rounded) when mapping or
    intersection fails — unless *empty_if_unmapped*. Intersecting with the widget
    inset prevents painting the exterior ring outside the purple stroke.
    *pad_top* / *pad_bottom* of 0 when the caller already inset that edge, or at
    sibling joins (banner) where a 0.5px shrink would punch a translucent hole.
    """
    rect = QRectF(inner)
    empty = QPainterPath()
    fallback = (
        _rounded_bottom_rect_path(rect)
        if round_bottom
        else QPainterPath()
    )
    if not round_bottom:
        fallback.addRect(rect)
        fallback.setFillRule(Qt.FillRule.WindingFill)

    if host is None:
        return empty if empty_if_unmapped else fallback
    root = host.centralWidget()
    if root is None:
        return empty if empty_if_unmapped else fallback
    folder = host.build_folder_stroke_path()
    if folder.isEmpty():
        return empty if empty_if_unmapped else fallback
    mapped = _map_folder_path_to_widget(folder, root, widget)
    if mapped.isEmpty():
        return empty if empty_if_unmapped else fallback
    # Interior only — never use the stroke outline / exterior complement.
    clipped = mapped.intersected(fallback)
    if clipped.isEmpty():
        return empty if empty_if_unmapped else fallback
    # Pull fill inside the purple L/R (and optional T/B) stroke. Zero T/B pad at
    # the banner joins so abutting widgets do not leave a 1px translucent seam.
    inset_path = QPainterPath()
    inset_path.addRect(rect.adjusted(0.5, pad_top, -0.5, -pad_bottom))
    inset_path.setFillRule(Qt.FillRule.WindingFill)
    tighter = clipped.intersected(inset_path)
    if tighter.isEmpty():
        clipped.setFillRule(Qt.FillRule.WindingFill)
        return clipped
    tighter.setFillRule(Qt.FillRule.WindingFill)
    return tighter


class TopNavStrip(QWidget):
    """Tab host — gutters are transparent holes; only NavTabButtons paint fill."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TopNav")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._tile_anchor: QWidget | None = None
        self._frame_host: MainWindow | None = None

    def set_tile_anchor(self, anchor: QWidget | None) -> None:
        self._tile_anchor = anchor
        for btn in self.findChildren(NavTabButton):
            btn.set_tile_anchor(anchor)

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def _mask_to_tabs(self) -> None:
        """Hit-test / clip only tab buttons so gutters are click-through holes."""
        region = QRegion()
        for btn in self.findChildren(NavTabButton):
            if btn.isVisible() and btn.width() > 0 and btn.height() > 0:
                region = region.united(QRegion(btn.geometry()))
        self.setMask(region)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._mask_to_tabs()

    def event(self, event) -> bool:
        ok = super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self._mask_to_tabs()
        return ok

    def paintEvent(self, event) -> None:  # noqa: N802
        # Gutters stay unpainted (true holes). Floor lives on each NavTabButton.
        return


class FolderFrameStroke(QWidget):
    """Click-through purple body outline (closed shelf, sharp TL/TR, rounded BL/BR)."""

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FolderFrameStroke")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._host = host
        self._rail = _load_theme_texture(_TOP_RAIL_NAME, _TOP_RAIL_EXTERNAL)

    def paintEvent(self, event) -> None:  # noqa: N802
        path = self._host.build_folder_stroke_path()
        if path.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pen = QPen(_FRAME_STROKE)
        pen.setWidthF(1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        # Rail just below the purple shelf (ContentPanel y=1). L/R inset.
        content = getattr(self._host, "_content_panel", None)
        if content is None or content.width() <= 0 or self._rail.isNull():
            return
        origin = _map_via_global(content, self, QPoint(0, 0))
        if origin is None:
            return
        rail_h = min(_TOP_RAIL_DRAW_H, max(1, content.height()))
        rail_rect = QRect(
            origin.x() + 1,
            origin.y() + 1,
            max(1, content.width() - 2),
            rail_h,
        )
        painter.setClipRect(rail_rect, Qt.ClipOperation.IntersectClip)
        _paint_top_rail(painter, rail_rect, self._rail)


class SideCornersOverlay(QWidget):
    """Main-app bottom corners from left_corners.png (no top ornaments).

    Uses the source bottom-left L as-is; bottom-right is a horizontal mirror so it
    stays a BR corner (not a rotated top piece). Drawn along the bottom of the
    folder body (ContentPanel→BottomBar). Vertical hang uses INWARD_Y (lower =
    sits further down); horizontal hang uses paint-inset baseline + OUTWARD_X.
    Click-through for resize/drag/page input. Must stack above the purple frame
    stroke.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SideCornersOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._left = QPixmap()
        self._right = QPixmap()
        self._frame_h = 0
        self._scaled_left = QPixmap()
        self._scaled_right = QPixmap()
        self._hang_x = 0
        self._hang_y = 0
        self._piece_h = 0
        path = theme_file(_SIDE_CORNERS_NAME)
        if path.is_file():
            src = QPixmap(str(path))
            if not src.isNull():
                # Tall strip is left-side TL+BL; keep only the bottom L (no top corners).
                # Equivalent bottom-edge layout to rotate(-90°)+flipV of the full strip.
                y0 = min(_BOTTOM_CORNER_SRC_Y, max(0, src.height() - 1))
                h = min(_BOTTOM_CORNER_SRC_H, max(1, src.height() - y0))
                bl = src.copy(0, y0, src.width(), h)
                self._left = bl
                # H-flip → true bottom-right (not a misplaced top ornament).
                self._right = bl.transformed(
                    QTransform().scale(-1, 1),
                    Qt.TransformationMode.SmoothTransformation,
                )

    @staticmethod
    def _scale_for_frame_height(frame_h: int) -> float:
        """Match prior side-strip thickness (full 920px strip vs frame height)."""
        h = max(int(frame_h), 1)
        denom = float(_SIDE_CORNERS_SRC_H - 2 * _SIDE_CORNERS_INNER_Y)
        hang_y_full = int(round(_SIDE_CORNERS_INNER_Y * h / denom)) if denom > 1 else 0
        return (h + 2 * hang_y_full) / float(_SIDE_CORNERS_SRC_H)

    @classmethod
    def hang_for_frame_height(cls, frame_h: int) -> tuple[int, int]:
        """Out-set (x, y): Y uses INWARD_Y; X uses prior paint-inset baseline + OUTWARD_X."""
        scale = cls._scale_for_frame_height(frame_h)
        hang_x = max(
            0,
            int(round(_SIDE_CORNERS_INNER_X * scale))
            - _BOTTOM_CORNERS_PAINT_INSET_X
            + _BOTTOM_CORNERS_OUTWARD_X,
        )
        hang_y = max(0, int(round(_SIDE_CORNERS_INNER_Y * scale)) - _BOTTOM_CORNERS_INWARD_Y)
        return hang_x, hang_y

    def prepare_for_frame(self, frame_h: int) -> tuple[int, int]:
        """Cache scaled BL/BR pixmaps for this frame height; return (hang_x, hang_y)."""
        h = max(int(frame_h), 1)
        hang_x, hang_y = self.hang_for_frame_height(h)
        scale = self._scale_for_frame_height(h)
        if (
            self._scaled_left.isNull()
            or self._frame_h != h
            or self._hang_x != hang_x
            or self._hang_y != hang_y
        ):
            if not self._left.isNull():
                # Width: normal scale. Height: same scale then +STRETCH_Y (IgnoreAspectRatio).
                draw_w = max(1, int(round(self._left.width() * scale)))
                draw_h = max(
                    1,
                    int(round(self._left.height() * scale)) + _BOTTOM_CORNERS_STRETCH_Y,
                )
                self._scaled_left = self._left.scaled(
                    draw_w,
                    draw_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled_right = self._right.scaled(
                    draw_w,
                    draw_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._piece_h = self._scaled_left.height()
            self._frame_h = h
            self._hang_x = hang_x
            self._hang_y = hang_y
        return hang_x, hang_y

    def opaque_inset_x(self) -> int:
        """Chrome clearance — bottom corners do not crowd −/X; keep a modest pad."""
        return _CHROME_FRAME_PAD

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._left.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        left = self._scaled_left
        right = self._scaled_right
        if left.isNull() or right.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Prior paint inset (inward=6 era); horizontal scoot comes from hang_x only.
        inset = _BOTTOM_CORNERS_PAINT_INSET_X
        painter.drawPixmap(inset, 0, left)
        painter.drawPixmap(self.width() - right.width() - inset, 0, right)


class ContentPanel(QWidget):
    """Folder body — Necrolord floor + Mechagon top rail, inside the closed purple frame."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ContentPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._floor = _load_theme_texture(_FLOOR_NAME, _FLOOR_EXTERNAL)
        self._frame_host: MainWindow | None = None

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def paintEvent(self, event) -> None:  # noqa: N802
        # Opaque card including the purple shelf pixel (y=0). Fill sits *behind*
        # the stroke overlay — clipping it to y=1 left a translucent hairline
        # between the tabs and the body. Rail still paints at y=1 on top.
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inset = 1
        inner = self.rect().adjusted(inset, 0, -inset, 0)
        clip = _interior_fill_clip(
            self, inner, self._frame_host, pad_top=0.0, pad_bottom=0.0
        )
        # Folder path top is the 0.5px stroke center, so intersection drops
        # pixel 0. Unite that row (L/R still inset) to keep the shelf opaque.
        shelf = QPainterPath()
        shelf_rect = QRectF(inner)
        shelf_rect.setHeight(1.0)
        shelf_rect.adjust(0.5, 0.0, -0.5, 0.0)
        if shelf_rect.width() > 0.0:
            shelf.addRect(shelf_rect)
            shelf.setFillRule(Qt.FillRule.WindingFill)
            clip = clip.united(shelf)
            clip.setFillRule(Qt.FillRule.WindingFill)
        painter.setClipPath(clip)
        _paint_floor_fill(painter, inner, self._floor, tile_origin=inner.topLeft())


class BottomBar(QWidget):
    """Play/status strip — mist FX anchored bottom-left, tiled horizontally only."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._mist = _load_theme_texture(_MIST_NAME, _MIST_EXTERNAL)
        self._mist_scaled = QPixmap()
        self._mist_scaled_h = 0
        self._frame_host: MainWindow | None = None

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def paintEvent(self, event) -> None:  # noqa: N802
        # Mist fill; purple L/R/B stroke is painted by FolderFrameStroke.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inset = 1
        inner = self.rect().adjusted(inset, 0, -inset, -inset)
        # Folder interior ∩ inset (BL/BR fallback) — never the exterior corner pockets.
        # pad_top=0 so the banner join is opaque (no 0.5px hole under the strip).
        painter.setClipPath(
            _interior_fill_clip(
                self,
                inner,
                self._frame_host,
                round_bottom=True,
                pad_top=0.0,
                pad_bottom=0.0,
            )
        )

        tile_h = max(1, inner.height())
        origin = QPoint(inner.left(), inner.bottom() - tile_h + 1)
        _paint_mist_fill(painter, inner, self._mist, tile_h=tile_h, tile_origin=origin)


class NavBottomBanner(QWidget):
    """Cached ravencraft.io nav strip between page content and the play bar."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("NavBottomBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedHeight(_NAV_BOTTOM_BANNER_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pix = QPixmap()
        self._floor = _load_theme_texture(_FLOOR_NAME, _FLOOR_EXTERNAL)
        self._mist = _load_theme_texture(_MIST_NAME, _MIST_EXTERNAL)
        self._frame_host: MainWindow | None = None
        path = theme_file("nav_bottom.png")
        if path.exists():
            src = QPixmap(str(path))
            if not src.isNull():
                self._pix = src

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def _home_art_behind(self) -> bool:
        """True when HOME artwork tucks under the grey bar (upper half only)."""
        host = self._frame_host
        if host is None:
            return False
        home = getattr(host, "home", None)
        bg = getattr(home, "talent_bg", None)
        return bool(bg is not None and bg.isVisible())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inset = 1
        # Full widget height — no T/B inset. A 0.5–1px shrink here is a hole
        # through the frameless translucent window at the ContentPanel / BottomBar joins.
        inner = self.rect().adjusted(inset, 0, -inset, 0)
        clip = _interior_fill_clip(
            self, inner, self._frame_host, pad_top=0.0, pad_bottom=0.0
        )
        painter.setClipPath(clip)
        mid = inner.top() + _NAV_BOTTOM_BANNER_MID_Y
        upper = QRect(inner.left(), inner.top(), inner.width(), max(0, mid - inner.top()))
        lower = QRect(inner.left(), mid, inner.width(), max(0, inner.bottom() - mid + 1))

        # Upper half: folder floor, minus the HOME-art tuck so the join is tight.
        origin = QPoint(inner.left(), inner.top())
        content = getattr(self._frame_host, "_content_panel", None)
        if content is not None:
            mapped = _map_via_global(content, self, QPoint(inset, 0))
            if mapped is not None:
                origin = mapped
        floor_clip = QPainterPath()
        floor_clip.addRect(QRectF(upper))
        floor_clip.setFillRule(Qt.FillRule.WindingFill)
        floor_clip = clip.intersected(floor_clip)
        if self._home_art_behind():
            art = getattr(getattr(self._frame_host, "home", None), "talent_bg", None)
            if art is not None:
                top_left = _map_via_global(art, self, QPoint(0, 0))
                if top_left is not None:
                    hole = QPainterPath()
                    hole.addRect(QRectF(QRect(top_left, art.size())))
                    hole.setFillRule(Qt.FillRule.WindingFill)
                    # Keep 1px opaque bands at the banner joins even when art tucks.
                    safe = QPainterPath()
                    safe.addRect(QRectF(upper).adjusted(0.0, 1.0, 0.0, 0.0))
                    safe.setFillRule(Qt.FillRule.WindingFill)
                    hole = hole.intersected(safe)
                    floor_clip = floor_clip.subtracted(hole)
        if not floor_clip.isEmpty():
            painter.setClipPath(floor_clip)
            _paint_floor_fill(painter, upper, self._floor, tile_origin=origin)

        # Lower half (spike valleys): same dark mist fill as the play bar.
        painter.setClipPath(clip)
        if not lower.isEmpty():
            bottom = getattr(self._frame_host, "_bottom_bar", None)
            tile_h = bottom.height() if bottom is not None and bottom.height() > 0 else lower.height()
            mist_origin = QPoint(lower.left(), lower.bottom() - tile_h + 1)
            if bottom is not None:
                mapped_mist = _map_via_global(bottom, self, QPoint(1, 0))
                if mapped_mist is not None:
                    mist_origin = QPoint(mapped_mist.x(), lower.bottom() - tile_h + 1)
            painter.setClipRect(lower, Qt.ClipOperation.IntersectClip)
            _paint_mist_fill(
                painter, lower, self._mist, tile_h=tile_h, tile_origin=mist_origin
            )
            painter.setClipPath(clip)

        if self._pix.isNull():
            return
        overdraw = _NAV_BOTTOM_BANNER_OVERDRAW_X
        draw = inner.adjusted(-overdraw, 0, overdraw, 0)
        painter.setClipRect(inner, Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(draw, self._pix)


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
            from ichalaunch.addons.github import GitHubRateLimitError

            if isinstance(exc, GitHubRateLimitError):
                log.warning("Worker failed: %s", exc)
            else:
                log.exception("Worker failed")
            self.failed.emit(format_github_error_message(exc))


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
        self._check_addon_pct = 0
        self._check_mod_pct = 0
        self._addon_check_status = ""
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._resize_origin: QPoint | None = None
        self._resize_geo: QRect | None = None
        self._pending_ok_handler = None
        # Queued addon install/update/reinstall jobs: (title, worker, on_ok, dedupe_key)
        self._addon_queue: list[tuple[str, Worker, object, str]] = []
        self._busy_status_base = ""
        self._current_nav = -1
        self._fitted = False
        self._startup_checks_scheduled = False
        self._permissions_skipped_path: str | None = None
        self._play_launch_lock_until = 0.0
        self._play_launch_timer = QTimer(self)
        self._play_launch_timer.setSingleShot(True)
        self._play_launch_timer.timeout.connect(self._release_play_launch_lock)
        self._addon_scan_resume_timer = QTimer(self)
        self._addon_scan_resume_timer.setSingleShot(True)
        self._addon_scan_resume_timer.timeout.connect(self._resume_queued_addon_scan)

        self.setMouseTracking(True)
        self._fit_to_screen(initial=True)

        self._point_cursor = _load_point_cursor()
        if self._point_cursor is not None:
            self.setCursor(self._point_cursor)

        root = QWidget()
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.setMouseTracking(True)
        if self._point_cursor is not None:
            root.setCursor(self._point_cursor)
        self.setCentralWidget(root)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        outer = QVBoxLayout(root)
        # Gutter so out-set corner ornaments are not clipped at the window edge.
        outer.setContentsMargins(
            _FRAME_OUTSET_MARGIN, 0, _FRAME_OUTSET_MARGIN, _FRAME_OUTSET_MARGIN
        )
        outer.setSpacing(0)

        # ---- Folder tabs (top chrome) — floor fill matches ContentPanel ----
        nav = TopNavStrip()
        self._top_nav = nav
        nav.set_frame_host(self)
        nav.setFixedHeight(_TAB_STRIP_HEIGHT)
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
            apply_open_hand(btn)
            btn.clicked.connect(lambda checked=False, idx=i: self._nav(idx))
            nav_l.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
            self.nav_btns.append(btn)
        nav_l.addStretch(1)

        # ---- Folder body (pages) — tiled floor via ContentPanel.paintEvent ----
        content = ContentPanel()
        self._content_panel = content
        content.set_frame_host(self)
        nav.set_tile_anchor(content)
        content.installEventFilter(self)
        content_l = QVBoxLayout(content)
        self._content_l = content_l
        # Top inset: stack/pages clip below −/X; floor still paints full panel behind.
        # Home uses 0 (see _nav) so Register Here can sit ~10px under the purple stroke.
        content_l.setContentsMargins(0, _CONTENT_TOP_CHROME, 0, 0)
        content_l.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")
        self.stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.stack.setStyleSheet("QStackedWidget#MainStack { background: transparent; }")
        # Parent to this window immediately — a parentless QWidget is a real HWND.
        self.home = HomePage(self)
        self.addons = AddonsPage(self)
        self.client = ClientPage(self)
        self.settings_page = SettingsPage(self)
        for page in (self.home, self.addons, self.client, self.settings_page):
            page.setAutoFillBackground(False)
            self.stack.addWidget(page)
        # Stretch so page bottom meets NavBottomBanner top (no dead gap above the strip).
        content_l.addWidget(self.stack, 1)

        # Minimize / close — Root children (like RC crest) so HOME art can stack
        # under them while still sitting inside the ContentPanel frame visually.
        self._btn_minimize = ChromeGlyphButton("minimize", root)
        self._btn_close = ChromeGlyphButton("close", root)
        self._btn_minimize.clicked.connect(self.showMinimized)
        self._btn_close.clicked.connect(self.close)
        self._btn_minimize.raise_()
        self._btn_close.raise_()

        # ---- Bottom play bar — mist FX via BottomBar.paintEvent ----
        bottom = BottomBar()
        self._bottom_bar = bottom
        bottom.set_frame_host(self)
        bottom.setFixedHeight(78)
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(16, 12, 4, 4)
        bot_l.setSpacing(14)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setMinimumWidth(120)
        self.status_lbl.setMaximumWidth(240)
        self.status_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        # Custom WoW loading bar — centered between status and PLAY.
        self.progress = ThemeLoadingBar()
        self.progress.setTextVisible(False)

        self.play_btn = LaunchButton("PLAY")
        self.play_btn.clicked.connect(self._on_play_or_install)

        grip = QSizeGrip(bottom)
        grip.setFixedSize(16, 16)
        grip.setToolTip("Drag to resize")

        bot_l.addWidget(self.status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        bot_l.addStretch(1)
        bot_l.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignVCenter)
        bot_l.addStretch(1)
        bot_l.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bot_l.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        # Decorative strip between pages and the play/progress bar (bundled offline asset).
        self._nav_bottom_banner = NavBottomBanner()
        self._nav_bottom_banner.set_frame_host(self)

        outer.addWidget(nav)
        outer.addWidget(content, 1)
        # 1px overlap so ContentPanel / banner / BottomBar joins stay opaque.
        outer.addSpacing(-1)
        outer.addWidget(self._nav_bottom_banner)
        outer.addSpacing(-1)
        outer.addWidget(bottom)

        # Purple closed body outline (full shelf). Above fills, below corners.
        self._frame_stroke = FolderFrameStroke(self, root)
        self._frame_stroke.raise_()

        # Bottom-left + mirrored bottom-right corner ornaments (content→play bar).
        # Stack above ContentPanel / BottomBar purple stroke (raised again after layout).
        self._side_corners = SideCornersOverlay(root)
        self._side_corners.raise_()

        # RavenCraft crest — straddles ContentPanel top border (click-through).
        self._rc_logo = RavenCraftFloatingLogo(root)
        self._rc_logo.raise_()

        self._update_window_mask()
        self._position_frame_stroke()
        self._position_side_corners()
        self._position_rc_logo()
        self._position_chrome_buttons()
        self._raise_side_corners()

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
        self.client.open_git_requested.connect(self._open_mod_git)
        self.settings_page.browse_clicked.connect(self._browse_game)
        self.settings_page.browse_addons_clicked.connect(self._browse_addons)
        self.settings_page.reset_addons_clicked.connect(self._reset_addons_path)
        self.settings_page.reset_client_link_clicked.connect(self._reset_client_link)
        self.settings_page.clear_cache_clicked.connect(self._clear_app_cache)
        self.settings_page.check_permissions_clicked.connect(self._check_game_permissions)
        self.settings_page.verify_clicked.connect(self._verify_game)

        self._refresh_play_button()
        self._nav(0)
        # If game_path is empty/invalid, detect WoW.exe next to the launcher EXE.
        if ensure_game_path_from_launcher() is not None:
            self.settings_page.refresh()
            self.home.refresh()
            self._refresh_play_button()
        if is_installed():
            self._resync(silent=True)
        self._refresh_nav_badges()
        self._apply_text_input_cursors()

        # Keep looking for updates while the app stays open (no reopen required).
        # First fire is deferred via singleShot in showEvent — timer only covers the interval.
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(_PERIODIC_UPDATE_MS)
        self._update_timer.timeout.connect(self._periodic_update_check)
        self._update_timer.start()

    # --- window chrome ---
    def build_folder_stroke_path(self) -> QPainterPath:
        """Purple outline in Root coords: closed body, full shelf, rounded BL/BR."""
        root = self.centralWidget()
        nav = getattr(self, "_top_nav", None)
        content = getattr(self, "_content_panel", None)
        bottom = getattr(self, "_bottom_bar", None)
        if root is None or nav is None or content is None or bottom is None:
            return QPainterPath()
        if content.width() <= 0 or bottom.height() <= 0:
            return QPainterPath()
        if not root.isAncestorOf(content) or not root.isAncestorOf(bottom):
            return QPainterPath()

        origin = _safe_map_to(content, root, QPoint(0, 0))
        bot_pt = _safe_map_to(bottom, root, QPoint(0, bottom.height()))
        if origin is None or bot_pt is None:
            return QPainterPath()
        # Stroke on pixel centers so 1px lines stay crisp.
        left = float(origin.x()) + 0.5
        right = float(origin.x() + content.width()) - 0.5
        shelf_y = float(origin.y()) + 0.5
        bot = float(bot_pt.y()) - 0.5
        return _folder_frame_path(left, right, shelf_y, bot)

    def _position_frame_stroke(self) -> None:
        stroke = getattr(self, "_frame_stroke", None)
        root = self.centralWidget()
        if stroke is None or root is None:
            return
        stroke.setGeometry(0, 0, root.width(), root.height())
        stroke.show()
        stroke.update()
        # Above fills / tabs (mouse-transparent); under corner art and −/X.
        stroke.raise_()
        corners = getattr(self, "_side_corners", None)
        if corners is not None:
            corners.raise_()
        logo = getattr(self, "_rc_logo", None)
        if logo is not None:
            logo.raise_()
        for btn in (
            getattr(self, "_btn_minimize", None),
            getattr(self, "_btn_close", None),
        ):
            if btn is not None:
                btn.raise_()

    def _update_window_mask(self) -> None:
        """Clip frameless window: sharp top corners, rounded bottom only."""
        w = float(self.width())
        h = float(self.height())
        r = float(_CORNER_RADIUS)
        path = QPainterPath()
        path.moveTo(0.0, 0.0)
        path.lineTo(w, 0.0)
        path.lineTo(w, max(0.0, h - r))
        path.quadTo(w, h, max(0.0, w - r), h)
        path.lineTo(min(w, r), h)
        path.quadTo(0.0, h, 0.0, max(0.0, h - r))
        path.closeSubpath()
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
        elif self._point_cursor is not None:
            self.setCursor(self._point_cursor)
        else:
            self.unsetCursor()

    def _apply_text_input_cursors(self) -> None:
        """Keep I-beam on editable fields; Point inherits elsewhere from MainWindow/Root."""
        for w in self.findChildren(QLineEdit):
            w.setCursor(Qt.CursorShape.IBeamCursor)
        for w in self.findChildren(QTextEdit):
            w.setCursor(Qt.CursorShape.IBeamCursor)
        for w in self.findChildren(QPlainTextEdit):
            w.setCursor(Qt.CursorShape.IBeamCursor)

    def _raise_side_corners(self) -> None:
        """Keep BL/BR ornaments above ContentPanel / BottomBar purple stroke and chrome."""
        overlay = getattr(self, "_side_corners", None)
        if overlay is None:
            return
        overlay.raise_()
        overlay.update()

    def _position_side_corners(self) -> None:
        """Pin bottom BL/BR corner art out-set from the folder body (overlap purple stroke)."""
        overlay = getattr(self, "_side_corners", None)
        content = getattr(self, "_content_panel", None)
        bottom = getattr(self, "_bottom_bar", None)
        root = self.centralWidget()
        if overlay is None or content is None or bottom is None or root is None:
            return
        origin = _safe_map_to(content, root, QPoint(0, 0))
        bottom_br = _safe_map_to(bottom, root, QPoint(0, bottom.height()))
        if origin is None or bottom_br is None:
            return
        frame_w = max(content.width(), 1)
        frame_h = max(bottom_br.y() - origin.y(), 1)
        hang_x, hang_y = overlay.prepare_for_frame(frame_h)
        piece_h = max(overlay._piece_h, 1)
        # Bottom strip only: hang below / beside the purple frame; no top corners.
        overlay.setGeometry(
            origin.x() - hang_x,
            bottom_br.y() - piece_h + hang_y,
            frame_w + 2 * hang_x,
            piece_h,
        )
        overlay.show()
        self._raise_side_corners()

    def _position_chrome_buttons(self) -> None:
        """Pin minimize/close to ContentPanel top-right (bottom corners stay clear)."""
        content = getattr(self, "_content_panel", None)
        btn_min = getattr(self, "_btn_minimize", None)
        btn_close = getattr(self, "_btn_close", None)
        root = self.centralWidget()
        if content is None or btn_min is None or btn_close is None or root is None:
            return
        overlay = getattr(self, "_side_corners", None)
        inset_x = 10
        if overlay is not None:
            inset_x = max(inset_x, overlay.opaque_inset_x())
        else:
            inset_x = max(inset_x, _CHROME_FRAME_PAD)
        inset_y = 10
        gap = 6
        origin = _safe_map_to(content, root, QPoint(0, 0))
        if origin is None:
            return
        cw = max(content.width(), 1)
        x_close = origin.x() + cw - inset_x - btn_close.width()
        x_min = x_close - gap - btn_min.width()
        y = origin.y() + inset_y
        btn_min.move(max(origin.x() + inset_x, x_min), y)
        btn_close.move(max(origin.x() + inset_x, x_close), y)
        btn_min.raise_()
        btn_close.raise_()
        btn_min.show()
        btn_close.show()
        # Corners must stay above the purple frame even after chrome raise.
        self._raise_side_corners()

    def _position_rc_logo(self) -> None:
        """Center RavenCraft crest on ContentPanel top border, between SETTINGS and panel right."""
        logo = getattr(self, "_rc_logo", None)
        root = self.centralWidget()
        content = getattr(self, "_content_panel", None)
        if logo is None or root is None or content is None or not self.nav_btns or logo.isHidden():
            return
        settings_btn = self.nav_btns[-1]
        # Horizontal: between SETTINGS tab's right edge and the content panel's right edge
        # (not the transparent tab-strip overhang / full window width).
        settings_pt = _safe_map_to(settings_btn, root, QPoint(settings_btn.width(), 0))
        content_origin = _safe_map_to(content, root, QPoint(0, 0))
        if settings_pt is None or content_origin is None:
            return
        left = settings_pt.x()
        right = content_origin.x() + content.width()
        # Reserve space for minimize/close chrome inside the framed panel.
        chrome_w = 0
        for btn in (getattr(self, "_btn_minimize", None), getattr(self, "_btn_close", None)):
            if btn is not None:
                chrome_w += btn.width() + 6
        frame_inset = _CHROME_FRAME_PAD + 16
        if chrome_w:
            right = min(right, content_origin.x() + content.width() - chrome_w - frame_inset)
        cx = (left + right) / 2.0
        x = int(round(cx - logo.width() / 2.0))
        # Keep the crest pixmap fully inside the zone — avoid rounded-mask / edge clip
        # (glow may feather outside).
        corner_safe = _CORNER_RADIUS + 6
        pix_left = x + _RC_GLOW_PAD_X
        pix_right = x + logo.width() - _RC_GLOW_PAD_X
        min_pix_left = left + 6
        max_pix_right = right - corner_safe
        if pix_left < min_pix_left:
            x += min_pix_left - pix_left
        if pix_right > max_pix_right:
            x -= pix_right - max_pix_right
        # Prefer fitting the full crest; if zone is too narrow, bias slightly left.
        pix_left = x + _RC_GLOW_PAD_X
        if pix_left < min_pix_left:
            x += min_pix_left - pix_left
        # Vertical: crest center on ContentPanel top purple border (half above / half
        # below) — same hang MoA used to have. Crest may clip above the window.
        content_top = content_origin.y()
        y = int(round(content_top - (logo.logo_offset_y + logo.logo_height / 2.0)))
        x = max(2 - _RC_GLOW_PAD_X, min(x, root.width() - logo.width() + _RC_GLOW_PAD_X - 2))
        logo.move(x, y)
        logo.raise_()
        self._position_chrome_buttons()

    def _refresh_chrome_fills(self) -> None:
        """Repaint folder fills after the stroke path may have changed."""
        for name in ("_top_nav", "_content_panel", "_nav_bottom_banner", "_bottom_bar"):
            w = getattr(self, name, None)
            if w is not None:
                w.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()
        self._update_window_mask()
        self._position_frame_stroke()
        self._position_side_corners()
        self._position_rc_logo()
        self._position_chrome_buttons()
        self._raise_side_corners()
        self._refresh_chrome_fills()
        # Reliable initial scan shortly after the UI is visible (not only on the 5‑min timer).
        if not self._startup_checks_scheduled:
            self._startup_checks_scheduled = True
            QTimer.singleShot(_STARTUP_UPDATE_DELAY_MS, self._run_startup_update_checks)
        if not getattr(self, "_addons_preload_scheduled", False):
            self._addons_preload_scheduled = True
            QTimer.singleShot(0, self._preload_hidden_addon_rows)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            themed.close_open_themed_dialogs(self)
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_window_mask()
        self._position_frame_stroke()
        self._position_side_corners()
        self._position_rc_logo()
        self._position_chrome_buttons()
        self._raise_side_corners()
        self._refresh_chrome_fills()

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
                    ChromeGlyphButton,
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
        if obj is getattr(self, "_content_panel", None) and event.type() == QEvent.Type.Resize:
            self._position_frame_stroke()
            self._position_side_corners()
            self._position_chrome_buttons()
            self._position_rc_logo()
            self._raise_side_corners()
            self._refresh_chrome_fills()
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
                    self._clamp_on_screen()
                    pos = self.mapFromGlobal(event.globalPosition().toPoint())
                    self._update_resize_cursor(self._hit_resize_edges(pos))
                    return True
                if self._drag_pos is not None:
                    self._drag_pos = None
                    self._clamp_on_screen()
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
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_moving = self._drag_pos is not None or self._resize_edges is not None
        if self._resize_edges is not None:
            self.releaseMouse()
        self._drag_pos = None
        self._resize_edges = None
        self._resize_origin = None
        self._resize_geo = None
        if was_moving:
            self._clamp_on_screen()
        self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseReleaseEvent(event)

    def _nav(self, idx: int) -> None:
        # Checkable QPushButton unchecks itself on re-click before clicked fires;
        # always sync tab chrome to stack index (including duplicate clicks).
        for i, b in enumerate(self.nav_btns):
            b.setChecked(i == idx)
        if idx == self._current_nav:
            return
        self._current_nav = idx
        self.stack.setCurrentIndex(idx)
        # HOME art/logo/countdown live on Root (not HomePage). Hide them whenever
        # we leave HOME so they cannot cover CLIENT / ADDONS / SETTINGS.
        if idx != 0:
            home = getattr(self, "home", None)
            hide_chrome = getattr(home, "_set_chrome_visible", None)
            if callable(hide_chrome):
                hide_chrome(False)
        # Home: no chrome inset (left Register near panel top; −/X are top-right).
        # Other pages: keep inset so scroll content stays clear of −/X.
        top = 0 if idx == 0 else _CONTENT_TOP_CHROME
        self._content_l.setContentsMargins(0, top, 0, 0)
        # Lightweight page updates only — never rebuild huge addon lists on switch
        if idx == 0:
            self.home.refresh()
        elif idx == 2:
            self.client.refresh_from_settings()
        elif idx == 3:
            self.settings_page.refresh()
        # Addons page keeps its current list until filters/rescan change
        stroke = getattr(self, "_frame_stroke", None)
        if stroke is not None:
            stroke.update()
        self._refresh_chrome_fills()

    def _preload_hidden_addon_rows(self) -> None:
        """Idle-build ADDONS rows after MainWindow is shown (user still on HOME)."""
        if self._current_nav == 1:
            return
        self.addons.preload_rows()

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

    def _run_startup_update_checks(self) -> None:
        """Quiet first-pass update scan after launch (respects the rescan cooldown)."""
        # Path may become valid after __init__ via co-located WoW / late settings.
        if not is_installed() and ensure_game_path_from_launcher() is not None:
            self.settings_page.refresh()
            self.home.refresh()
            self._resync(silent=True)
            self._refresh_play_button()

        log.info("Startup update checks beginning")
        self._check_launcher_update(silent=True)

        if not settings.check_updates_on_startup():
            self._refresh_nav_badges()
            return
        if not is_installed():
            log.info("Startup addon/mod checks skipped — no game path yet")
            self._refresh_nav_badges()
            return
        if rate_limit_exhausted():
            # Still attempt — check_* handlers stop early and surface RATE_LIMIT_STATUS.
            log.info("GitHub rate limit low at startup; attempting checks anyway")
        self._check_mod_updates(silent=True)
        if settings.should_startup_check_addons(has_token=has_github_token()):
            self._check_updates(silent=True)

    def _periodic_update_check(self) -> None:
        """Recurring silent launcher self-update only (addons/client: launch scan)."""
        if self._worker and self._worker.isRunning():
            return
        # Do not re-scan addons/client here — that runs once at startup (force).
        self._check_launcher_update(silent=True)

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

    def _worker_busy(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _format_busy_status(self, msg: str) -> str:
        """Append ``· N in queue`` when addon jobs are waiting."""
        base = (msg or "").strip() or "Working…"
        n = len(self._addon_queue)
        if n > 0:
            return f"{base} · {n} in queue"
        return base

    def _set_busy_status(self, msg: str) -> None:
        self._busy_status_base = (msg or "").strip()
        self.status_lbl.setText(self._format_busy_status(self._busy_status_base or "Working…"))

    def _lock_addon_filters(self, extra_busy: bool = False) -> None:
        """Disable addons filter combos while a scan or _busy worker is running."""
        busy = bool(
            extra_busy
            or getattr(self, "_checking_addons", False)
            or self._worker_busy()
        )
        try:
            self.addons.set_scanning(busy)
        except RuntimeError:
            pass

    def _is_play_launch_locked(self) -> bool:
        return time.monotonic() < self._play_launch_lock_until

    def _arm_play_launch_lock(self) -> None:
        """Spam guard: keep PLAY disabled for at least 5s during launch prep."""
        self._play_launch_lock_until = time.monotonic() + 5.0
        self.play_btn.setEnabled(False)
        self.play_btn.setText("LAUNCHING…")
        remaining_ms = int((self._play_launch_lock_until - time.monotonic()) * 1000) + 1
        self._play_launch_timer.start(max(1, remaining_ms))

    def _release_play_launch_lock(self) -> None:
        self._play_launch_lock_until = 0.0
        self._play_launch_timer.stop()
        if not self._worker_busy():
            self.play_btn.setEnabled(True)
            self._refresh_play_button()
            if self.progress.maximum() == 0:
                self.progress.hide()
                self.progress.setRange(0, 100)
                self.progress.setValue(0)
                self.progress.setFormat("%p%")

    def _fail_play_launch(self, status: str) -> None:
        self._play_launch_lock_until = 0.0
        self._play_launch_timer.stop()
        if not self._worker_busy():
            self.play_btn.setEnabled(True)
            self._refresh_play_button()
            if self.progress.maximum() == 0:
                self.progress.hide()
                self.progress.setRange(0, 100)
                self.progress.setValue(0)
                self.progress.setFormat("%p%")
        self.status_lbl.setText(status)

    def _show_play_launch_progress(self, msg: str) -> None:
        self.status_lbl.setText(msg)
        if not self._worker_busy():
            self.progress.show()
            self.progress.setRange(0, 0)
            self.progress.setFormat("")

    def _set_busy_ui(self, busy: bool, msg: str = "") -> None:
        if busy:
            self.play_btn.setEnabled(False)
        elif not self._is_play_launch_locked():
            self.play_btn.setEnabled(True)
        self._lock_addon_filters(extra_busy=busy)
        if busy:
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate until bytes known
            self.progress.setFormat("")
            self._set_busy_status(msg or "Working…")
        else:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("%p%")
            self._busy_status_base = ""
            self.status_lbl.setText(msg or "Ready")

    def _on_progress_pct(self, pct: int) -> None:
        """Update bottom bar: determinate 0–100, or busy when pct < 0."""
        if self.progress.isHidden():
            return
        if pct < 0:
            if self.progress.maximum() != 0:
                self.progress.setRange(0, 0)
                self.progress.setFormat("")
            return
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(pct))))
        self.progress.setFormat("%p%")

    def _refresh_check_loading(self) -> None:
        """Show addon/client update-check status on the bottom progress area.

        Does not flash the bottom bar for silent launcher-only periodic checks.
        Leaves an active download ``_busy`` UI alone.
        Uses determinate 0–100% (per-item check progress), not indeterminate.
        """
        addon_busy = self._checking_addons
        mod_busy = self._checking_mods
        self.addons.set_checking(addon_busy, "Checking for updates…")
        self.client.set_checking(mod_busy, "Checking for updates…")
        self._lock_addon_filters()

        if self._worker_busy():
            return

        if addon_busy and self._addon_check_status == GIT_REPAIR_STATUS:
            msg = GIT_REPAIR_STATUS
        elif addon_busy and mod_busy:
            msg = "Checking addon & client updates…"
        elif addon_busy:
            msg = self._addon_check_status or "Checking addon updates…"
        elif mod_busy:
            msg = "Checking client mod updates…"
        else:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("%p%")
            self._check_addon_pct = 0
            self._check_mod_pct = 0
            # Keep the last status text from done/fail handlers; only restore Ready
            # when still showing a checking… message.
            current = (self.status_lbl.text() or "").strip().lower()
            if current.startswith("checking"):
                self.status_lbl.setText("Ready")
            return

        self.progress.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(self._combined_check_pct())
        self.progress.setFormat("%p%")
        self.status_lbl.setText(msg)

    def _on_addon_check_status(self, msg: str) -> None:
        """Worker status → bottom-left label (repair text, then per-addon check)."""
        self._addon_check_status = (msg or "").strip()
        if self._checking_addons and not self._worker_busy():
            self._refresh_check_loading()

    def _combined_check_pct(self) -> int:
        """Blend addon/mod check percents when both run together."""
        if self._checking_addons and self._checking_mods:
            return max(0, min(100, (self._check_addon_pct + self._check_mod_pct) // 2))
        if self._checking_addons:
            return max(0, min(100, self._check_addon_pct))
        if self._checking_mods:
            return max(0, min(100, self._check_mod_pct))
        return 0

    def _on_check_progress_pct(self, kind: str, pct: int) -> None:
        """Determinate update-check progress from addon/mod workers."""
        if self._worker_busy():
            return
        if pct < 0:
            return
        value = max(0, min(100, int(pct)))
        if kind == "addons":
            if not self._checking_addons:
                return
            self._check_addon_pct = value
        elif kind == "mods":
            if not self._checking_mods:
                return
            self._check_mod_pct = value
        else:
            return
        if self.progress.isHidden():
            self.progress.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(self._combined_check_pct())
        self.progress.setFormat("%p%")

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
        if silent:
            # Do not rebuild addon rows during launch/silent sync (HWND spam).
            # Lists populate the first time the user opens ADDONS.
            pass
        else:
            self.addons.refresh()
        self.home.refresh()
        self._refresh_play_button()
        if not silent:
            themed.info(
                self,
                "Rescan complete",
                f"Detected {len(result['addons'])} addon folder(s) and synced client mod checkboxes.",
            )

    def _busy(
        self,
        title: str,
        worker: Worker,
        on_ok=None,
        *,
        queueable: bool = False,
        queue_key: str = "",
    ) -> None:
        """Run a background job; optionally queue addon jobs when one is already running."""
        if self._worker and self._worker.isRunning():
            if queueable:
                self._enqueue_addon_job(title, worker, on_ok, queue_key)
                return
            themed.info(self, "Busy", "Another task is already running.")
            return
        self._start_busy_job(title, worker, on_ok)

    def _enqueue_addon_job(
        self,
        title: str,
        worker: Worker,
        on_ok,
        queue_key: str = "",
    ) -> None:
        key = (queue_key or title).strip().lower()
        if key and any(item[3] == key for item in self._addon_queue):
            self._set_busy_status(self._busy_status_base or title)
            return
        self._addon_queue.append((title, worker, on_ok, key))
        self._set_busy_status(self._busy_status_base or title)

    def _start_busy_job(self, title: str, worker: Worker, on_ok=None) -> None:
        self._set_busy_ui(True, title)
        worker.status.connect(lambda m: self._set_busy_status(m))
        worker.progress_pct.connect(self._on_progress_pct)
        worker.finished_ok.connect(self._on_worker_ok)
        worker.failed.connect(self._on_worker_fail)
        self._worker = worker
        self._pending_ok_handler = on_ok
        worker.start()

    def _pump_addon_queue(self) -> bool:
        """Start the next queued addon job. Returns True if a job was started."""
        if self._worker and self._worker.isRunning():
            return True
        if not self._addon_queue:
            return False
        title, worker, on_ok, _key = self._addon_queue.pop(0)
        self._start_busy_job(title, worker, on_ok)
        return True

    def _maybe_warn_github_token(self) -> None:
        msg = take_github_token_warning()
        if msg:
            themed.warning(self, "GitHub token", msg)

    def _on_worker_ok(self, result) -> None:
        handler = self._pending_ok_handler
        self._pending_ok_handler = None
        restarting = False
        status_after = "Ready"
        busy_text = (self.status_lbl.text() or "").strip()
        if handler:
            try:
                # True is reserved for launcher self-update quit (skip widget teardown).
                restarting = bool(handler(result))
                after = (self.status_lbl.text() or "").strip()
                # Keep a handler-set completion line; do not persist busy ticks
                # such as "Writing realmlist.wtf…".
                if after and after != busy_text:
                    status_after = after
                    if " · " in status_after and status_after.endswith("in queue"):
                        status_after = status_after.rsplit(" · ", 1)[0].strip() or "Ready"
            except Exception as exc:  # noqa: BLE001
                log.exception("Post-worker handler failed")
                themed.error(self, "Error", str(exc))
                self.status_lbl.setText(f"Failed: {str(exc)[:80]}")
                if self._pump_addon_queue():
                    return
                self._set_busy_ui(False, f"Failed: {str(exc)[:80]}")
                return
        # Self-update calls quit() — do not touch widgets afterward (causes crashes).
        app = QApplication.instance()
        if restarting or (app is not None and app.closingDown()):
            return
        self.home.refresh()
        self.client.refresh_from_settings()
        self.addons.mark_dirty()
        self.addons.refresh()
        self.settings_page.refresh()
        self._refresh_play_button()
        self._refresh_nav_badges()
        if self._pump_addon_queue():
            return
        self._set_busy_ui(False, status_after)
        self._maybe_warn_github_token()

    def _on_worker_fail(self, msg: str) -> None:
        themed.error(self, "Error", msg)
        if self._pump_addon_queue():
            return
        self._set_busy_ui(False, f"Failed: {msg[:80]}")
        if msg != GITHUB_TOKEN_REJECTED_MSG:
            self._maybe_warn_github_token()

    def _on_play_or_install(self) -> None:
        if self._launcher_update_pending():
            self._apply_launcher_update()
            return
        if is_installed():
            self._play()
        else:
            self._install_or_browse()

    def _check_launcher_update(self, silent: bool = False) -> None:
        """Background check for a newer IchaLaunch GitHub release.

        Silent/startup/periodic checks never touch the bottom progress bar or
        busy PLAY state — only real download/install via ``_apply_launcher_update``.
        """
        if self._launcher_update_worker and self._launcher_update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Launcher update check already running…")
            return

        if rate_limit_exhausted():
            if silent:
                log.info("Skipping launcher update check — GitHub rate limit exhausted")
                self.status_lbl.setText(RATE_LIMIT_STATUS)
                return
            themed.error(self, "Launcher update check failed", RATE_LIMIT_STATUS)
            return

        cached = read_cached_launcher_release() if silent else None
        if cached is not None:
            log.info("Using cached launcher release check")
            if cached.update_available:
                self._latest_launcher_release = cached
                log.info("Launcher update available (cached): v%s", cached.version)
            else:
                self._latest_launcher_release = None
            self._refresh_play_button()
            return

        if not silent:
            self.status_lbl.setText("Checking for launcher updates…")

        worker = Worker(check_latest_launcher_release)

        def done(result):
            self._launcher_update_worker = None
            if isinstance(result, LauncherReleaseInfo) and result.update_available:
                self._latest_launcher_release = result
                if not silent:
                    self.status_lbl.setText(f"Launcher update available: v{result.version}")
                else:
                    # Quiet: badge + PLAY→UPDATE only; leave status for other work.
                    log.info("Launcher update available: v%s", result.version)
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
                # Rate-limit is worth surfacing; other silent failures stay in the log.
                if "rate limit" in brief.lower():
                    self.status_lbl.setText(RATE_LIMIT_STATUS)
            else:
                themed.error(self, "Launcher update check failed", msg)

        # Dedicated worker — do not use ``_busy`` / progress bar for the check itself.
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
        self._arm_play_launch_lock()
        self._show_play_launch_progress("Preparing to launch…")
        sync = plan_sync_changes()
        if sync:
            labels: list[str] = []
            for ch in sync[:4]:
                mid = ch.get("id") or "mod"
                prefix = "+" if ch.get("action") == "install" else "-"
                labels.append(f"{prefix}{mid}")
            names = ", ".join(labels)
            if len(sync) > 4:
                names += f" (+{len(sync) - 4} more)"
            worker = Worker(ensure_desired_mods_synced)

            def on_ok(done: list[str]) -> None:
                failures = [
                    ln[1:].strip()
                    for ln in (done or [])
                    if isinstance(ln, str) and ln.startswith("!")
                ]
                if failures:
                    self._fail_play_launch("Client mod sync failed")
                    themed.error(
                        self,
                        "Could not sync client mods",
                        "These client mod changes could not be applied:\n\n"
                        + "\n\n".join(failures),
                    )
                    return
                installed = [ln[2:] for ln in (done or []) if ln.startswith("+ ")]
                removed = [ln[2:] for ln in (done or []) if ln.startswith("- ")]
                parts: list[str] = []
                if installed:
                    parts.append(
                        f"installed {len(installed)} mod"
                        f"{'s' if len(installed) != 1 else ''}"
                    )
                if removed:
                    parts.append(
                        f"removed {len(removed)} mod"
                        f"{'s' if len(removed) != 1 else ''}"
                    )
                if parts:
                    line = "Synced client mods before launch: " + ", ".join(parts)
                    self.status_lbl.setText(line)
                    log.info("%s — +%s -%s", line, installed, removed)
                self._launch_prepared()

            self._busy(f"Syncing client mods ({names})…", worker, on_ok=on_ok)
            return
        self._launch_prepared()

    def _launch_prepared(self) -> None:
        try:
            manuals = plan_manual_missing()
            if manuals:
                themed.warning(
                    self,
                    "Before launch",
                    "These enabled mods need a manual download:\n\n"
                    + "\n\n".join(manuals),
                )
            self._show_play_launch_progress("Running pre-launch checks…")
            prep = prepare_for_launch()
            if prep.fixes:
                line = prep.status_line or "Pre-launch fixes applied"
                self.status_lbl.setText(line)
                log.info("%s — %s", line, "; ".join(prep.fixes))
            if prep.warnings:
                themed.warning(self, "Before launch", "\n".join(prep.warnings))
            if prep.permission_scan and prep.permission_scan.has_issues:
                if not self._offer_permission_fix(
                    prep.permission_scan,
                    allow_launch_anyway=True,
                ):
                    self._fail_play_launch("Launch cancelled")
                    return
            self._show_play_launch_progress("Launching game…")
            launch_game()
            self.status_lbl.setText("Game launched")
            themed.close_open_themed_dialogs(self)
            if settings.get("close_on_launch"):
                self.close()
            elif settings.get("minimize_on_launch"):
                self.showMinimized()
        except Exception as exc:  # noqa: BLE001
            self._fail_play_launch(f"Launch failed: {str(exc)[:80]}")
            themed.error(self, "Launch failed", str(exc))

    def _offer_permission_fix(
        self,
        scan: PermissionScanResult,
        *,
        allow_launch_anyway: bool = False,
        title: str = "Game folder permissions",
    ) -> bool:
        """Prompt to repair permissions. Returns True when the caller may continue."""
        if not scan.has_issues:
            return True
        game_key = str(scan.game)
        if allow_launch_anyway and self._permissions_skipped_path == game_key:
            return True

        if scan.protected_path:
            buttons: list[tuple[str, themed.DialogResult]] = [
                ("Change game path…", themed.DialogResult.Browse),
            ]
            if allow_launch_anyway:
                buttons.append(("Launch anyway", themed.DialogResult.Ok))
            buttons.append(("OK", themed.DialogResult.Cancel))
        else:
            buttons = [
                ("Fix permissions", themed.DialogResult.Yes),
            ]
            if allow_launch_anyway:
                buttons.append(("Launch anyway", themed.DialogResult.Ok))
            buttons.append(("Cancel", themed.DialogResult.Cancel))

        choice = themed.choice(
            self,
            title,
            scan.user_message(),
            buttons=buttons,
            kind="warning",
        )
        if choice == themed.DialogResult.Cancel:
            return False
        if choice == themed.DialogResult.Browse:
            self._browse_game()
            return False
        if choice == themed.DialogResult.Ok:
            self._permissions_skipped_path = game_key
            log.info("User chose to continue despite permission issues in %s", scan.game)
            return True

        self.status_lbl.setText("Repairing game folder permissions…")
        fix = fix_game_permissions(scan.game)
        if fix.fixes:
            log.info("Permission repair: %s", "; ".join(fix.fixes))
        if fix.warnings:
            log.warning("Permission repair warnings: %s", "; ".join(fix.warnings))

        rescan = scan_game_permissions(scan.game)
        if rescan.has_issues:
            msg = scan.user_message()
            if fix.warnings:
                msg += "\n\n" + "\n".join(fix.warnings)
            themed.warning(
                self,
                title,
                msg + "\n\nSome problems could not be fixed automatically.",
            )
            if allow_launch_anyway:
                return themed.question(
                    self,
                    title,
                    "Launch the game anyway? Access-denied crashes may still occur.",
                )
            return False

        self._permissions_skipped_path = None
        themed.info(
            self,
            title,
            "Permissions repaired.\n\n"
            + ("\n".join(fix.fixes) if fix.fixes else "Your user can now modify the game folder."),
        )
        self.status_lbl.setText("Ready")
        return True

    def _check_game_permissions(self) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        game = Path(settings.game_path)
        scan = scan_game_permissions(game)
        if not scan.has_issues:
            themed.info(
                self,
                "Game folder permissions",
                f"No permission problems found in:\n{game}",
            )
            return
        self._offer_permission_fix(scan, allow_launch_anyway=False)

    def _maybe_prompt_permissions_after_path_set(self, game_root: Path) -> None:
        scan = scan_game_permissions(game_root)
        if scan.has_issues:
            QTimer.singleShot(
                0,
                lambda s=scan: self._offer_permission_fix(
                    s,
                    allow_launch_anyway=False,
                    title="Game folder permissions",
                ),
            )

    def _install_or_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose parent folder (RavenCraft is created inside)",
            "C:/Games",
        )
        if not path:
            return
        p = Path(path)
        ok, msg = validate_install_location(p)
        if not ok:
            themed.warning(self, "Protected location", msg)
            return
        # Cheap WoW.exe check only — never rglob the picked tree on the GUI thread.
        existing = wow_exe_here(p)
        if existing is not None:
            if should_settle_existing(p, existing):
                existing = settle_ravencraft_home(p, existing)
            settings.game_path = str(existing)
            apply_bundled_realmlist(existing)
            self._resync(silent=True)
            themed.info(self, "Ready", f"Using existing client:\n{existing}")
            self.home.refresh()
            self.settings_page.refresh()
            self._refresh_play_button()
            self._maybe_prompt_permissions_after_path_set(existing)
            return

        # Zip discovery / magic / size / extract run in Worker after this dialog.
        self.status_lbl.setText("Preparing…")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        choice = themed.choice(
            self,
            "Install client",
            f"Your browser will open Gofile so you can download {GOFILE_FILE_NAME}.\n\n"
            "Click Download on that page (a VPN may be required). "
            "Leave this launcher open — it watches your Downloads folder and "
            "extracts into a RavenCraft folder inside the location you chose.\n\n"
            "If you already have the zip, choose Browse…",
            buttons=[
                ("Open Gofile", themed.DialogResult.Yes),
                ("Browse…", themed.DialogResult.Browse),
                ("Cancel", themed.DialogResult.Cancel),
            ],
        )
        if choice == themed.DialogResult.Cancel:
            self.status_lbl.setText("Ready")
            return
        if choice == themed.DialogResult.Browse:
            zipped = self._pick_client_zip()
            if zipped is None:
                self.status_lbl.setText("Ready")
                return
            self._begin_client_install(p, zip_path=zipped)
            return

        from ichalaunch.ui.widgets.common import open_url_in_browser

        open_url_in_browser(GAME_DOWNLOAD_URL)
        self._begin_client_install(p)

    def _pick_client_zip(self) -> Path | None:
        start = str(Path.home() / "Downloads")
        dirs = client_watch_dirs()
        if dirs:
            start = str(dirs[0])
        path, _filt = QFileDialog.getOpenFileName(
            self,
            f"Select {GOFILE_FILE_NAME}",
            start,
            "Zip archives (*.zip)",
        )
        if not path:
            return None
        return Path(path)

    def _begin_client_install(self, dest: Path, **kwargs) -> None:
        def on_ok(game_root):
            if not game_root:
                QTimer.singleShot(0, lambda d=dest: self._install_zip_fallback(d))
                return False
            root = Path(str(game_root or dest))
            self._resync(silent=True)
            self.home.refresh()
            self.settings_page.refresh()
            self._refresh_play_button()
            self.status_lbl.setText("Ready")
            # Defer the modal so _on_worker_ok can hide the busy bar first.
            # Must not return True — that skips _set_busy_ui(False) (self-update quit).
            QTimer.singleShot(
                0,
                lambda r=root: themed.info(
                    self,
                    "Install complete",
                    f"Client ready at:\n{r}\n\nAddOns:\n{settings.resolved_addons_path()}",
                ),
            )
            QTimer.singleShot(0, lambda r=root: self._maybe_prompt_permissions_after_path_set(r))
            return False

        if kwargs.get("zip_path"):
            title = "Extracting…"
        elif kwargs.get("auto_download"):
            title = "Downloading client…"
        else:
            title = "Waiting for download…"
        worker = Worker(install_client, dest, **kwargs)
        self._busy(title, worker, on_ok=on_ok)

    def _install_zip_fallback(self, dest: Path) -> None:
        while True:
            choice = themed.choice(
                self,
                "Download not found",
                f"Could not find {GOFILE_FILE_NAME} in Downloads after waiting.\n\n"
                "Browse to a zip you already have, try auto-download "
                "(Gofile API / Vikingfile — often slow), or cancel.",
                buttons=[
                    ("Browse…", themed.DialogResult.Browse),
                    ("Auto-download", themed.DialogResult.Ok),
                    ("Cancel", themed.DialogResult.Cancel),
                ],
            )
            if choice == themed.DialogResult.Cancel:
                self.status_lbl.setText("Install cancelled")
                return
            if choice == themed.DialogResult.Ok:
                self._begin_client_install(dest, auto_download=True)
                return
            zipped = self._pick_client_zip()
            if zipped is None:
                continue
            self._begin_client_install(dest, zip_path=zipped)
            return

    def _browse_game(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select game folder (contains WoW.exe)")
        if not path:
            return
        if is_protected_path(path):
            themed.warning(self, "Protected location", protected_location_guidance(path))
        if not has_wow_exe(Path(path)):
            themed.warning(self, "Not a game folder", "WoW.exe was not found in that folder.")
            return
        settings.game_path = path
        self.settings_page.refresh()
        self._resync(silent=True)
        self._refresh_play_button()
        themed.info(self, "Saved", f"Game path set to:\n{path}")
        self._maybe_prompt_permissions_after_path_set(Path(path))

    def _browse_addons(self) -> None:
        start = settings.resolved_addons_path() or settings.game_path or ""
        path = QFileDialog.getExistingDirectory(self, "Select AddOns folder", start)
        if not path:
            return
        settings.addons_path = path
        self.settings_page.refresh()
        if is_installed():
            self._resync(silent=True)
        themed.info(self, "Saved", f"AddOns path set to:\n{path}")

    def _reset_addons_path(self) -> None:
        path = settings.reset_addons_path_to_default()
        self.settings_page.refresh()
        if is_installed():
            self._resync(silent=True)
        if path:
            themed.info(self, "Reset", f"AddOns path reset to:\n{path}")
        else:
            themed.warning(self, "Reset", "Set a game path first to restore the default AddOns folder.")

    def _reset_client_link(self) -> None:
        if not str(settings.game_path or "").strip():
            themed.info(
                self,
                "Reset Client Link",
                "No WoW folder is linked. Use INSTALL or Browse to pick a location.",
            )
            return
        if not themed.question(
            self,
            "Reset Client Link",
            "Clear the saved WoW folder from launcher settings?\n\n"
            "PLAY will become INSTALL so you can choose a new install location. "
            "This does not delete any files on disk.",
        ):
            return
        settings.clear_client_link()
        self.settings_page.refresh()
        self.home.refresh()
        self._refresh_play_button()

    def _clear_app_cache(self) -> None:
        if not themed.question(
            self,
            "Clear Cache",
            "This will clear launcher settings, cached scan data, and saved preferences. "
            "Your game files and addons are not deleted.",
        ):
            return
        from ichalaunch.config.settings import clear_app_data

        clear_app_data()
        self.settings_page.refresh()
        self.home.refresh()
        self.client.refresh_from_settings()
        self.addons.refresh()
        self._refresh_play_button()
        self._refresh_nav_badges()
        themed.info(
            self,
            "Clear Cache",
            "Launcher data has been reset. Restart IchaLaunch if anything still looks outdated.",
        )

    def _verify_game(self) -> None:
        if is_installed():
            themed.info(self, "Verify", f"WoW.exe found at:\n{settings.game_path}")
        else:
            themed.warning(self, "Verify", "Game not detected. Browse to a valid client folder.")

    def _apply_mods(self) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(result):
            lines = result if isinstance(result, list) else []
            detail = "; ".join(lines[:4]) if lines else "no changes"
            more = f" (+{len(lines) - 4} more)" if len(lines) > 4 else ""
            self.status_lbl.setText(f"Client mods applied: {detail}{more}")
            # Per-mod lock/AV failures are tolerated by apply — surface them
            # loudly here so a stuck removal is never silent (nag loop).
            failures = [
                ln[1:].strip()
                for ln in lines
                if isinstance(ln, str) and ln.startswith("!")
            ]
            if failures:
                themed.error(
                    self,
                    "Some mod changes failed",
                    "These changes could not be applied:\n\n" + "\n\n".join(failures),
                )

        worker = Worker(apply_desired_state)
        self._busy("Applying client mods…", worker, on_ok=on_ok)

    def _install_catalog_addon(self, entry: dict) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        from ichalaunch.addons.github import has_github_token
        from ichalaunch.ui.widgets.dialogs import addon_install_picker_dialog

        install_entry = dict(entry)
        if has_github_token():
            picked = addon_install_picker_dialog(self, install_entry)
            if picked is None:
                return
            install_entry = picked
        url = install_entry.get("repo")
        folder = install_entry.get("folder") or install_entry.get("name") or ""
        name = install_entry.get("name") or folder or "addon"
        worker = Worker(install_from_github, url, folder)

        def on_ok(result_name):
            self.status_lbl.setText(f"Installed {result_name or name}")

        self._busy(
            f"Installing {name}…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key=f"install:{folder or url}",
        )

    def _github_import(self, url: str) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        worker = Worker(install_from_github, url)

        def on_ok(result_name):
            self.status_lbl.setText(f"Installed from GitHub: {result_name}")

        self._busy(
            "Importing from GitHub…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key=f"github:{url.strip().lower()}",
        )

    def _custom_dll_import(self, url: str) -> None:
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(mod):
            if isinstance(mod, dict):
                self.client.ensure_mod_row(mod)
                mid = mod.get("id")
                if mid:
                    self.client.focus_mod(str(mid))
                name = mod.get("name") or mid or "DLL"
                if mod.get("matched_existing"):
                    cat = mod.get("category") or "Client"
                    self.status_lbl.setText(f"Matched catalog mod: {name} ({cat})")
                else:
                    self.status_lbl.setText(f"Custom DLL installed: {name}")
            else:
                self.status_lbl.setText("Custom DLL installed")

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

        self._busy(
            f"Updating {folder}…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key=f"update:{folder}",
        )

    def _reinstall_addon(self, entry: dict) -> None:
        """Force re-download/overwrite regardless of current commit."""
        folder = entry.get("folder") or entry.get("name")
        if not folder:
            return
        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return
        from ichalaunch.core.detect import overlay_git_origin

        meta = overlay_git_origin(
            str(folder),
            settings.installed_addons.get(folder) or {},
        )
        tag = str(entry.get("tag") or meta.get("tag") or "").strip()
        repo = str(meta.get("repository") or entry.get("repository") or "").strip()
        # Prefer live .git origin / overlay meta over catalog entry repo.
        url = meta.get("url") or entry.get("repo") or entry.get("url") or ""
        if tag and "/" in repo:
            from ichalaunch.addons.github import github_tag_page_url

            owner, name = repo.split("/", 1)
            url = github_tag_page_url(owner, name, tag)
        elif not url and repo:
            url = f"https://github.com/{repo}"
        if not url:
            themed.warning(self, "Cannot reinstall", f"No GitHub URL for {folder}.")
            return

        def on_ok(_result):
            self.addons.clear_pending_update(folder)
            self.status_lbl.setText(f"Reinstalled {folder}")

        worker = Worker(install_from_github, url, folder)
        self._busy(
            f"Reinstalling {folder}…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key=f"reinstall:{folder}",
        )

    def _update_all_addons(self) -> None:
        pending = self.addons.pending_updates
        folders = [u.get("folder") or u.get("name") for u in pending]
        folders = [
            f
            for f in folders
            if f and not settings.is_addon_never_update(str(f))
        ]
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
                status_only(progress, f"Updating {folder} ({i}/{total})…")
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
        self._busy(
            f"Updating {total} addon(s)…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key="update-all-addons",
        )

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

    def _check_updates(self, silent: bool = False, periodic: bool = False, force: bool = False) -> None:
        """Quiet background check — status bar only, never blocks PLAY or shows a popup."""
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if not silent and not has_github_token():
            from ichalaunch.ui.widgets.dialogs import github_token_prompt_dialog

            token = github_token_prompt_dialog(self)
            if not token:
                return
        if self._update_worker and self._update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Update check already running…")
            return

        # Cooldown gate for automatic (silent/periodic) checks: skip if a scan ran
        # within the Settings auto-scan cooldown. Manual checks arrive with
        # silent=False and always run; force=True explicitly bypasses the cooldown.
        # A persisted within-scan queue may resume even inside the cooldown window.
        if (silent or periodic) and not force and recently_checked_addon_updates():
            if not has_pending_addon_scan_queue():
                log.info(
                    "Addon scan skipped — last scan %s ago (cooldown %d min)",
                    _format_minutes_since("last_addon_update_check"),
                    settings.auto_scan_cooldown_minutes(),
                )
                return

        if self._addon_scan_resume_timer.isActive():
            self._addon_scan_resume_timer.stop()

        self._addon_check_status = "Scanning addons…"
        self.status_lbl.setText("Scanning addons…")
        self._checking_addons = True
        self._check_addon_pct = 0
        self.addons.set_scanning(True)
        self._refresh_check_loading()
        worker = Worker(check_addon_updates, respect_cooldown=False)

        def done(result):
            self._checking_addons = False
            self._addon_check_status = ""
            self._check_addon_pct = 100
            if isinstance(result, AddonUpdateCheckResult):
                updates = result.updates
                status = result.status_message
                queued = bool(result.queued)
                resume_after = result.resume_after_sec
            else:
                updates = result or []
                status = None
                queued = False
                resume_after = None
            self.addons.set_updates(updates)
            if not self._checking_mods:
                if status:
                    self.status_lbl.setText(status)
                elif updates:
                    self.status_lbl.setText(f"{len(updates)} addon update(s) available")
                else:
                    self.status_lbl.setText("Addons up to date")
            if queued:
                wait_ms = max(5_000, int(resume_after or 0) * 1000)
                if resume_after is not None and int(resume_after) <= 0:
                    wait_ms = 5_000
                self._addon_scan_resume_timer.start(wait_ms)
                if not status and not self._checking_mods:
                    self.status_lbl.setText(WAITING_RATE_LIMIT_STATUS)
            self._refresh_check_loading()
            self._update_worker = None
            self._refresh_nav_badges()

        def fail(msg: str):
            self._checking_addons = False
            self._addon_check_status = ""
            if not self._checking_mods:
                self.status_lbl.setText(f"Update check failed: {msg[:80]}")
            self._refresh_check_loading()
            self._update_worker = None
            self._refresh_nav_badges()

        worker.status.connect(self._on_addon_check_status)
        worker.progress_pct.connect(lambda p: self._on_check_progress_pct("addons", p))
        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._update_worker = worker
        worker.start()

    def _resume_queued_addon_scan(self) -> None:
        """Continue a paced unauthenticated addon scan when the hour budget refreshes."""
        if not has_pending_addon_scan_queue():
            return
        if self._update_worker and self._update_worker.isRunning():
            self._addon_scan_resume_timer.start(30_000)
            return
        if not self._checking_mods:
            self.status_lbl.setText(WAITING_RATE_LIMIT_STATUS)
        self._check_updates(silent=True, force=True)

    def _check_mod_updates(self, silent: bool = False, periodic: bool = False, force: bool = False) -> None:
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._mod_update_worker and self._mod_update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Client mod update check already running…")
            return
        if (silent or periodic) and not force and recently_checked_mod_updates():
            log.info(
                "Client mod scan skipped — last scan %s ago (cooldown %d min)",
                _format_minutes_since("last_mod_update_check"),
                settings.auto_scan_cooldown_minutes(),
            )
            return

        self.status_lbl.setText("Checking client mod updates…")
        self._checking_mods = True
        self._check_mod_pct = 0
        self._refresh_check_loading()
        worker = Worker(check_mod_updates, respect_cooldown=False)

        def done(result):
            self._checking_mods = False
            self._check_mod_pct = 100
            if isinstance(result, ModUpdateCheckResult):
                updates = result.updates
                status = result.status_message
                if result.skipped_recent:
                    self._refresh_check_loading()
                    self._mod_update_worker = None
                    self._refresh_nav_badges()
                    return
            else:
                updates = result or []
                status = None
            self.client.set_updates(updates)
            if not self._checking_addons:
                if status:
                    self.status_lbl.setText(status)
                elif updates:
                    self.status_lbl.setText(f"{len(updates)} client mod update(s) available")
                else:
                    self.status_lbl.setText("Client mods up to date")
            self._refresh_check_loading()
            self._mod_update_worker = None
            self._refresh_nav_badges()

        def fail(msg: str):
            self._checking_mods = False
            if not self._checking_addons:
                self.status_lbl.setText(f"Client mod check failed: {msg[:80]}")
            self._refresh_check_loading()
            self._mod_update_worker = None
            self._refresh_nav_badges()

        worker.progress_pct.connect(lambda p: self._on_check_progress_pct("mods", p))
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

    def _open_mod_git(self, mod_id: str) -> None:
        from ichalaunch.mods.installer import load_mod_catalog
        from ichalaunch.ui.widgets.common import mod_git_url, open_url_in_browser

        catalog = {m["id"]: m for m in load_mod_catalog()}
        url = mod_git_url(catalog.get(mod_id) or {})
        if not url:
            self.status_lbl.setText(f"No git link for {mod_id}")
            return
        open_url_in_browser(url)

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
