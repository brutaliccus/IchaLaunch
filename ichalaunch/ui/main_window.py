"""Main window — borderless folder chrome, folder tabs, bottom play bar."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
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

# How long a launched client is watched for an early failure, and how often.
# Long enough to catch umu refusing a Proton build or an unusable prefix,
# short enough that a working launch is not held up noticeably.
_LAUNCH_GRACE_S = 8.0
_LAUNCH_POLL_MS = 250

_RESIZE_MARGIN = 6
_CORNER_RADIUS = 14
_TAB_STRIP_HEIGHT = 44
# Label box inside the strip, after the plate's own 10px/4px padding. The tab
# sizes itself to its text, so this is what stops it outgrowing the strip.
_TAB_LABEL_H = _TAB_STRIP_HEIGHT - 14
_TAB_LABEL_W = 260
_TAB_PX_MIN = 11
_TAB_PX_MAX = 26
_TAB_LABEL_PX = 16
# Hide the glue-plate bottom stroke / L-corners under the content seam.
# Dest is widget height + this many px, top-aligned, so the extra bottom clips.
_TAB_ART_SHIFT_Y = 7
# ContentPanel top inset so page scroll clips below −/X and the crest caption.
# Extra px below the chrome glyphs clear the IchaLaunch word.
_CONTENT_TOP_CHROME = 66
# RavenCraft crest at ContentPanel top (was MoA). Larger than MoA wordmark was.
_RC_LOGO_WIDTH = 210
# Optional outer pad around the crest (kept for layout math; no glow drawn).
_RC_GLOW_PAD_X = 0
_RC_GLOW_PAD_Y = 0
# Secondary word under the crest — small vs the 210px art.
_RC_CAPTION_TEXT = "IchaLaunch"
_RC_CAPTION_PX = 15
_RC_CAPTION_GAP = 3
_RC_CAPTION_COLOR = QColor("#F1C22D")
# One 15-minute tick refreshes launcher + addon + client catalog compares.
_PERIODIC_UPDATE_MS = 15 * 60 * 1000
# Automatic scans (startup + periodic tick) always run in this order.
# Each step waits for the previous worker (or a sync skip) before the next starts.
_AUTO_UPDATE_STEPS = ("launcher", "addons", "client")
# Let the window finish laying out / detecting game path before the first network scan.
_STARTUP_UPDATE_DELAY_MS = 1500
_NAV_BOTTOM_BANNER_H = 30
# Vertical center of the banner PNG — solid bar vs hanging spike valleys.
_NAV_BOTTOM_BANNER_MID_Y = _NAV_BOTTOM_BANNER_H // 2
# Spike-valley height (PNG alpha below the bar). Filled behind the PNG with
# play black (#100d0c); the unkeyed pixmap paints on top.
_NAV_BOTTOM_BANNER_SPIKE_H = _NAV_BOTTOM_BANNER_H - _NAV_BOTTOM_BANNER_MID_Y
# Draw strip this many px past each side so end spikes clip off (~20px wider total).
_NAV_BOTTOM_BANNER_OVERDRAW_X = 12
# Source PNG is 1920×38; transparent top pad is y=0–6. Scaled to 30px that
# is ~6px. Hang the ContentPanel floor onto the solid bar so the pad is not
# a desktop seam. Do not grow BottomBar to cover this — that shrinks PLAY.
_NAV_BOTTOM_BANNER_PANEL_OVERLAP = 6
# Play/status strip — PLAY 56 + 80px UPDATE glow. Height only; do not inset
# L/R to "fix" height. Under-banner play fill is a dedicated layer, not 88px.
_BOTTOM_BAR_H = 88
_CORNER_RADIUS_F = float(_CORNER_RADIUS)
# Main ContentPanel floor — opaque RavenCraft base, then tiles + wash on top.
_FLOOR_BASE = QColor("#181315")
# Generic-metal chrome — single edge + TL corner sources (BorderFrameArt /
# CornerFrameArt). Edges are rotated/flipped so the light-grey lip faces
# outward; TR/BL/BR corners are mirrors of the TL source.
_METAL_EDGE_NAME = "metal_edge.png"
_METAL_CORNER_NAME = "metal_corner.png"
# Edge thickness on the window edge (not inset into the page).
_METAL_EDGE_DRAW = 20
# Full L corner (165×165, ~27px arms) scaled so arms match the 20px rails.
# Do not crop the arms — edges meet the arm tips.
_METAL_CORNER_DRAW = 122
# 1px past the folder box — matches rail dest (box.x()-1). Fringe is trimmed
# off the crops so this is the metal lip, not a black halo.
_METAL_CORNER_HANG = 1
# Hairline where tiled edges meet corner arm tips (not a tuck-under stub).
_METAL_ARM_JOIN = 1
# Floor past each metal dest (AA / hang) so desktop cannot peek.
# Keep this small — a wide band reads as a #181315 outline.
_METAL_FLOOR_OUTSET = 2
# Side rails run down to the solid banner bar (not the crystal tip, not the
# spike valleys). +2 tucks under the opaque bar so the join has no hairline.
_METAL_RAIL_BANNER_TUCK = _NAV_BOTTOM_BANNER_MID_Y + 2
# Portrait chrome — individual crops from UIFramePortrait.PNG (256×1024).
# Atlas: bottom (0,123,256,11); left (8,268,13,120); right (121,405,13,119);
# BL L (8,370,32,32); BR L (102,504,32,32). Paint 1:1 — no scale smear.
_PORTRAIT_EDGE_BOTTOM_NAME = "portrait_edge_bottom.png"
_PORTRAIT_EDGE_LEFT_NAME = "portrait_edge_left.png"
_PORTRAIT_EDGE_RIGHT_NAME = "portrait_edge_right.png"
_PORTRAIT_CORNER_BL_NAME = "portrait_corner_bl.png"
_PORTRAIT_CORNER_BR_NAME = "portrait_corner_br.png"
# Outer-lip pixels (alpha>16) registered onto the shared play-frame box.
_PORTRAIT_BL_OX = 2
_PORTRAIT_BL_OY = 29
_PORTRAIT_BR_OX = 29
_PORTRAIT_BR_OY = 29
_PORTRAIT_LEFT_OX = 2
_PORTRAIT_RIGHT_OX = 10
_PORTRAIT_BOT_OY = 8
# Row in the 32px corners where the L bend starts (stem above, arm below).
_PORTRAIT_ARM_JOIN = 21
# Shared join: tiles overlap this many px into the corner arms.
_PORTRAIT_JOIN = 8
# Slight outward x so L/R rails + BL/BR sit on the metal rail (box.x()-1)
# without hanging past the window lip. Keep corners locked to their bars.
_PORTRAIT_OUTER_NUDGE = 1
_FLOOR_NAME = "UIFrameNecrolordBackground.PNG"
_FLOOR_EXTERNAL = Path(r"F:\wow-ui-textures\FrameGeneral\UIFrameNecrolordBackground.PNG")
# Soft floor: subtle darken vs first Necrolord preview (0.22/90 → slight nudge).
_FLOOR_TILE_OPACITY = 0.19
_FLOOR_WASH = QColor(24, 19, 21, 105)
_FLOOR_LIGHTING_NAME = "Legion_DH_Lighting_02.PNG"
_FLOOR_LIGHTING_EXTERNAL = Path(
    r"F:\wow-ui-textures\GLUES\Models\UI_DemonHunter\Legion_DH_Lighting_02.PNG"
)
_FLOOR_LIGHTING_OPACITY = 0.50
_FLOOR_LIGHTING_PIX: QPixmap | None = None

# BottomBar mist FX — one row, bottom-left, tiled horizontally only.
_MIST_BASE = QColor("#100d0c")
_MIST_NAME = "6TJ_Polluted_mist_Stormy.PNG"
_MIST_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\Models\UI_Orc\6TJ_Polluted_mist_Stormy.PNG")
_MIST_WASH = QColor(16, 13, 12, 110)

# Custom arrow cursor (WoW Point) — tip is top-left pixel of the 32×32 PNG.
_CURSOR_POINT_NAME = "cursor_point.png"
_CURSOR_POINT_EXTERNAL = Path(r"F:\wow-ui-textures\CURSOR\Point.PNG")
_CURSOR_POINT_HOTSPOT = (0, 0)

# L/R gutter so metal hang (1) + floor outset (2) are not clipped.
# Was 24 for BL/BR left_corners ornaments — those are gone; 24 pinched the
# play cluster ~48px. 4 is enough for the remaining TL/TR lip.
_FRAME_OUTSET_MARGIN = 4
# Bottom gutter stays 24 so the 88px play strip keeps its sit-height
# (PLAY ~40px from the window bottom). Do not steal width to fix height.
_FRAME_OUTSET_BOTTOM = 24
# −/X sit inside the framed panel, clear of the TR vertical stem (~20) and
# below the top arm (~20). Long horizontal arms do not cover the buttons.
_CHROME_BTN_INSET_X = 44
# Below the 20px top rail / corner top arm.
_CHROME_BTN_INSET_Y = 24

from ichalaunch import __version__
from ichalaunch.core.paths import theme_file
from ichalaunch.ui.theme_fonts import (
    chrome_family,
    ink_centered_rect,
)
from ichalaunch.ui.widgets.update_alert_badge import paint_update_alert_badge

_RC_CAPTION_FONT_PATH = theme_file("fonts", "LifeCraft_Font.ttf")
_LIFECRAFT_FAMILY: str | None = None
_LIFECRAFT_LOAD_ATTEMPTED = False


def _lifecraft_family() -> str | None:
    """Register the bundled LifeCraft face once; None if the file is missing."""
    global _LIFECRAFT_FAMILY, _LIFECRAFT_LOAD_ATTEMPTED
    if _LIFECRAFT_LOAD_ATTEMPTED:
        return _LIFECRAFT_FAMILY
    _LIFECRAFT_LOAD_ATTEMPTED = True
    path = _RC_CAPTION_FONT_PATH
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id == -1:
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        return None
    _LIFECRAFT_FAMILY = families[0]
    return _LIFECRAFT_FAMILY


def _caption_font() -> QFont | None:
    """Crest caption only — LifeCraft, not the Cinzel chrome family."""
    family = _lifecraft_family()
    if not family:
        return None
    font = QFont(family)
    font.setPixelSize(_RC_CAPTION_PX)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _pixmap_opaque_bottom(pix: QPixmap) -> int:
    """First row below the last non-transparent pixel (pixmap height if empty)."""
    if pix.isNull():
        return 0
    img = pix.toImage()
    h = img.height()
    w = img.width()
    step = 2 if w > 2 else 1
    for y in range(h - 1, -1, -1):
        for x in range(0, w, step):
            if img.pixelColor(x, y).alpha() > 16:
                return y + 1
    return h


# Resolved once on first use: a platform name cannot change inside a process,
# and this is consulted on every mouse move during a drag.
_SYSTEM_WINDOW_MOVE: bool | None = None


def _use_system_window_move() -> bool:
    """True on Wayland, where a client may not position its own window.

    Wayland has no protocol for setting a top-level's global position, so
    ``QWidget.move()`` on the window is discarded and ``setGeometry()`` cannot
    move an origin: the frameless window looks frozen, and dragging the left
    or top edge resizes from the wrong side. The compositor-side equivalents
    are ``QWindow.startSystemMove`` and ``startSystemResize``.

    False on Windows and X11, where the existing code path is correct.
    """
    global _SYSTEM_WINDOW_MOVE
    if _SYSTEM_WINDOW_MOVE is None:
        # platformName() does not raise before the application exists; it
        # answers "xcb" regardless of the real session, so asking early and
        # caching that would disable this silently and permanently.
        if QGuiApplication.instance() is None:
            return False
        name = QGuiApplication.platformName() or ""
        _SYSTEM_WINDOW_MOVE = name.lower().startswith("wayland")
    return _SYSTEM_WINDOW_MOVE


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
    AddonInstallResult,
    AddonUpdateCheckResult,
    GIT_REPAIR_STATUS,
    GITHUB_TOKEN_REJECTED_MSG,
    RATE_LIMIT_STATUS,
    UPDATE_CATALOG_UNAVAILABLE,
    check_addon_updates,
    finalize_install_after_toc_renames,
    format_github_error_message,
    has_github_token,
    install_from_github,
    rate_limit_exhausted,
    recently_checked_addon_updates,
    take_github_token_warning,
    uninstall_addon,
    update_addon,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import full_resync, scan_mismatched_toc_addon_folders
from ichalaunch.core.filesystem import (
    AddonTocMismatch,
    PermissionScanResult,
    fix_game_permissions,
    is_protected_path,
    protected_location_guidance,
    rename_addon_folder_to_toc,
    scan_game_permissions,
    take_pending_toc_mismatches,
)
from ichalaunch.core.logging_setup import log
from ichalaunch.core.process import (
    StatusProgress,
    status_only,
    wow_exe_may_be_running,
)
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
from ichalaunch.game.realm_status import (
    PROBE_FIRST_DELAY_MS,
    PROBE_INTERVAL_MS,
    RealmProbe,
    jittered_probe_delay_ms,
    next_probe_backoff_ms,
    probe_logon,
    realm_ping_disabled,
)
from ichalaunch.game.launcher import (
    GAME_DOWNLOAD_URL,
    GOFILE_FILE_NAME,
    VF_LAUNCH_ASK,
    detect_game,
    ensure_game_path_from_launcher,
    has_wow_exe,
    is_installed,
    launch_game,
    validate_install_location,
    vanillafixes_launch_decision,
    vanillafixes_reinstall_mod_id,
)
from ichalaunch.mods.installer import (
    ModUpdateCheckResult,
    apply_desired_state,
    check_mod_updates,
    ensure_desired_mods_synced,
    format_mod_verify_warning,
    install_custom_dll_from_github,
    plan_manual_missing,
    plan_sync_changes,
    prepare_for_launch,
    recently_checked_mod_updates,
    split_mod_apply_results,
    update_mod,
    update_mods,
)
from ichalaunch.mods.superwow_support import maybe_show_superwow_after_mod_failures
from ichalaunch.ui.pages.addons import AddonsPage
from ichalaunch.ui.pages.client import ClientPage
from ichalaunch.ui.pages.home import HomePage
from ichalaunch.ui.pages.settings import SettingsPage
from ichalaunch.ui.widgets.loading_bar import ThemeLoadingBar
from ichalaunch.ui.widgets.chrome_buttons import ChromeGlyphButton
from ichalaunch.ui.widgets.contributor_portrait import ContributorPortrait
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import (
    glue_floor_chrome_pixmap,
    tint_image_toward_color,
)
from ichalaunch.ui.widgets.launch_button import LaunchButton, UpdateLaunchButton
from ichalaunch.ui.widgets.realm_ping import (
    PING_DOT_GAP,
    RealmPingDot,
    ping_overlay_x,
)


def _format_minutes_since(settings_key: str) -> str:
    """Human age of a stored epoch timestamp, e.g. ``23 min`` — for cooldown logs."""
    try:
        minutes = int((time.time() - float(settings.get(settings_key))) / 60)
    except (TypeError, ValueError):
        return "unknown"
    return f"{minutes} min"


def _client_mod_failure_dialog_body(
    failures: list[str],
    *,
    lead: str = "These changes could not be applied:",
) -> str:
    """Build Apply/sync failure dialog text; ensure end-the-game guidance."""
    from ichalaunch.core.filesystem import END_GAME_PROCESS_HINT, has_end_game_guidance

    parts = [ln for ln in failures if isinstance(ln, str) and ln.strip()]
    body = f"{lead}\n\n" + "\n\n".join(parts) if parts else lead
    if has_end_game_guidance(body, strict=False):
        return body
    return (
        f"{body}\n\n{END_GAME_PROCESS_HINT} Then retry Apply."
    )


# Widget-scoped: kills the app-wide QPushButton 1px #7a6e88 frame. Size rules are
# repeated so this sheet cannot drop the file-QSS box model.
_TAB_BASE_QSS = (
    "QPushButton {"
    "  background: transparent;"
    "  border: none;"
    "  outline: none;"
    "  padding: 10px 18px 4px 18px;"
    "  margin: 2px 0 0 0;"
    "}"
    "QPushButton:hover, QPushButton:pressed, QPushButton:focus {"
    "  background: transparent;"
    "  border: none;"
    "  outline: none;"
    "}"
    "QPushButton:checked {"
    "  background: transparent;"
    "  border: none;"
    "  outline: none;"
    "  margin-top: 0;"
    "  padding-top: 12px;"
    "  padding-bottom: 6px;"
    "  min-height: 32px;"
    "}"
)


class NavTabButton(QPushButton):
    """Folder tab — floor-tinted Glue-Panel plate + optional update alert badge."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("TopNavButton")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAutoFillBackground(False)
        self.setFlat(True)
        # Widget-scoped: kill the app-wide QPushButton 1px #7a6e88 frame.
        # Repeat size rules so this sheet cannot drop the file-QSS box model.
        self._apply_chrome_font()
        self._badge = False
        self._tile_anchor: QWidget | None = None
        # Warm floor-tint caches so the first tab paint is snappy.
        for pressed in (False, True):
            for shade in ("idle", "hover", "selected"):
                glue_floor_chrome_pixmap(pressed=pressed, shade=shade)

    def set_tile_anchor(self, anchor: QWidget | None) -> None:
        # Kept for TopNavStrip; tabs no longer tile the Necrolord floor.
        self._tile_anchor = anchor

    def set_badge_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._badge:
            return
        self._badge = visible
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def _plate_shade(self) -> str:
        if self.isChecked():
            return "selected"
        if self.underMouse():
            return "hover"
        return "idle"

    def _apply_chrome_font(self) -> None:
        """Apply the chrome family at a fixed pixel size via the widget sheet.

        Not setFont(): the file sheet carries a `*` font-family rule, and a
        stylesheet beats setFont for any property it names, so an explicitly set
        family is silently discarded on the next polish. A widget-scoped rule is
        more specific than `*` and survives.

        Not paint-time either: sizeHint decides how wide the tab is, so a size
        applied only while painting would be laid out for the old one and clip.
        """
        family = chrome_family()
        px = _TAB_LABEL_PX
        self.setStyleSheet(
            _TAB_BASE_QSS
            + f'QPushButton {{ font-family: "{family}"; font-size: {px}px; font-weight: bold; }}'
        )
        self.updateGeometry()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._apply_chrome_font()

    def _label_color(self) -> QColor:
        if self.isChecked():
            return QColor("#F1C22D")
        if self.underMouse():
            return QColor("#e6e0ee")
        return QColor("#9990ab")

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        painter.setPen(Qt.PenStyle.NoPen)
        pm = glue_floor_chrome_pixmap(pressed=self.isDown(), shade=self._plate_shade())
        if pm.isNull():
            painter.setBrush(_FLOOR_BASE)
            painter.drawRoundedRect(rect.adjusted(0, 0, 0, 1), 6, 6)
        else:
            # Shift art down by extending dest past the widget; bottom clips
            # at the tab / ContentPanel seam so L-corners + bottom stroke hide.
            dest = QRect(rect.x(), rect.y(), rect.width(), rect.height() + _TAB_ART_SHIFT_Y)
            painter.drawPixmap(dest, pm)

        text = self.text() or ""
        font = QFont(self.font())
        painter.setFont(font)
        text_rect = rect.adjusted(0, 1 if self.isDown() else 0, 0, 0)
        text_rect = ink_centered_rect(text_rect, font, text)
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawText(text_rect.adjusted(1, 1, 1, 1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(self._label_color())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

        if self._badge:
            paint_update_alert_badge(painter, rect)
        painter.end()


class RavenCraftFloatingLogo(QWidget):
    """RavenCraft crest — rides ContentPanel top / folder shelf (no glow)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RavenCraftFloatingLogo")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pix = QPixmap()
        self._logo_h = 0
        self._caption_font: QFont | None = None
        self._caption_top = 0
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
        self._caption_font = _caption_font()
        art_bottom = _RC_GLOW_PAD_Y + _pixmap_opaque_bottom(self._pix)
        self._caption_top = art_bottom + _RC_CAPTION_GAP
        caption_h = 0
        if self._caption_font is not None:
            fm = QFontMetrics(self._caption_font)
            # +4: paint band is +2 and the 1px floor shadow sits below it.
            caption_h = max(fm.height(), fm.tightBoundingRect(_RC_CAPTION_TEXT).height()) + 4
        # Grow downward only so the crest stay-centered math (logo_height) is unchanged.
        need_h = max(self._logo_h, self._caption_top + caption_h) + _RC_GLOW_PAD_Y * 2
        self.setFixedSize(
            self._pix.width() + _RC_GLOW_PAD_X * 2,
            need_h,
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
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.drawPixmap(_RC_GLOW_PAD_X, _RC_GLOW_PAD_Y, self._pix)
        font = self._caption_font
        if font is None:
            return
        painter.setFont(font)
        fm = painter.fontMetrics()
        band_h = max(fm.height(), fm.tightBoundingRect(_RC_CAPTION_TEXT).height()) + 2
        band = QRect(0, self._caption_top, self.width(), band_h)
        # Soft floor shadow so gold stays readable on the Necrolord wash.
        painter.setPen(QColor(16, 13, 12, 200))
        painter.drawText(
            band.translated(1, 1),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _RC_CAPTION_TEXT,
        )
        painter.setPen(_RC_CAPTION_COLOR)
        painter.drawText(
            band,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _RC_CAPTION_TEXT,
        )


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
        painter.setOpacity(_FLOOR_TILE_OPACITY)
        # drawTiledPixmap's offset is the starting point WITHIN the pixmap, so
        # the shared tile phase above is preserved exactly. Qt's raster engine
        # blits this in one pass; the Python double loop cost an interpreter
        # iteration and a binding call per tile, which shows at 5120x2160.
        painter.drawTiledPixmap(
            rect, floor, QPoint((rect.left() - ox) % tw, (rect.top() - oy) % th)
        )
        painter.setOpacity(1.0)
        painter.fillRect(rect, _FLOOR_WASH)
    painter.restore()


def _floor_lighting_pixmap() -> QPixmap:
    """Legion DH lighting rotated 90° CW and tinted to the ContentPanel floor."""
    global _FLOOR_LIGHTING_PIX
    if _FLOOR_LIGHTING_PIX is not None:
        return _FLOOR_LIGHTING_PIX
    path = _resolve_theme_texture(_FLOOR_LIGHTING_NAME, _FLOOR_LIGHTING_EXTERNAL)
    if path is None:
        _FLOOR_LIGHTING_PIX = QPixmap()
        return _FLOOR_LIGHTING_PIX
    # Load/rotate on QImage so offscreen/headless Qt does not need a screen
    # paint engine. QPixmap.transformed() can native-abort there (issue #344).
    img = QImage(str(path))
    if img.isNull():
        _FLOOR_LIGHTING_PIX = QPixmap()
        return _FLOOR_LIGHTING_PIX
    rotated = img.transformed(
        QTransform().rotate(90.0),
        Qt.TransformationMode.FastTransformation,
    )
    tinted = tint_image_toward_color(rotated, _FLOOR_BASE, lift=1.0)
    _FLOOR_LIGHTING_PIX = (
        QPixmap.fromImage(tinted) if not tinted.isNull() else QPixmap()
    )
    return _FLOOR_LIGHTING_PIX


def _paint_floor_lighting(
    painter: QPainter,
    rect: QRect,
    *,
    origin: QPoint | None = None,
) -> None:
    """Soft top-left lighting pass over the RavenCraft floor."""
    pm = _floor_lighting_pixmap()
    if pm.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return
    ox = origin.x() if origin is not None else rect.left()
    oy = origin.y() if origin is not None else rect.top()
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    painter.setOpacity(_FLOOR_LIGHTING_OPACITY)
    painter.drawPixmap(ox, oy, pm)
    painter.setOpacity(1.0)
    painter.restore()


def _scale_theme_pixmap(pm: QPixmap, w: int, h: int) -> QPixmap:
    if pm.isNull() or w <= 0 or h <= 0:
        return QPixmap()
    return pm.scaled(
        w,
        h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _paint_tiled_h(painter: QPainter, rect: QRect, rail: QPixmap, draw_h: int) -> None:
    """Tile a horizontal metal edge. Scales the tile's height only; repeats in X."""
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
    tile = _scale_theme_pixmap(rail, tw, h)
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    x = rect.left()
    y = rect.top()
    while x < rect.right():
        painter.drawPixmap(x, y, tile)
        x += tw
    painter.restore()


def _paint_tiled_v(painter: QPainter, rect: QRect, rail: QPixmap, draw_w: int) -> None:
    """Tile a vertical metal edge. Scales the tile's width only; repeats in Y."""
    if rail.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return
    src_w = rail.width()
    src_h = rail.height()
    if src_w <= 0 or src_h <= 0:
        return
    w = min(int(draw_w), rect.width())
    if w <= 0:
        return
    th = max(1, int(round(src_h * (w / float(src_w)))))
    tile = _scale_theme_pixmap(rail, w, th)
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    x = rect.left()
    y = rect.top()
    while y < rect.bottom():
        painter.drawPixmap(x, y, tile)
        y += th
    painter.restore()


def _paint_mist_fill(
    painter: QPainter,
    rect: QRect,
    mist: QPixmap,
    *,
    tile_h: int | None = None,
    tile_origin: QPoint | None = None,
) -> None:
    """Opaque mist base + one horizontal mist row + wash (BottomBar only)."""
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
    """Closed folder body: sharp TL/TR, rounded BL/BR, continuous top shelf.

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
    rect keeps fill inside the folder (no exterior corner pockets).
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
    # Interior only — never the exterior complement.
    clipped = mapped.intersected(fallback)
    if clipped.isEmpty():
        return empty if empty_if_unmapped else fallback
    # Optional T/B pad at sibling joins so abutting widgets stay opaque.
    inset_path = QPainterPath()
    inset_path.addRect(rect.adjusted(0.0, pad_top, 0.0, -pad_bottom))
    inset_path.setFillRule(Qt.FillRule.WindingFill)
    tighter = clipped.intersected(inset_path)
    if tighter.isEmpty():
        clipped.setFillRule(Qt.FillRule.WindingFill)
        return clipped
    tighter.setFillRule(Qt.FillRule.WindingFill)
    return tighter


class TopNavStrip(QWidget):
    """Tab host — gutters are transparent holes; NavTabButtons paint glue plates."""

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
        # Gutters stay unpainted (true holes). Glue plates live on each NavTabButton.
        return


def _load_bundled_pixmap(name: str) -> QPixmap:
    path = theme_file(name)
    if not path.is_file():
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _mirror_pixmap(pm: QPixmap, *, horizontal: bool = False, vertical: bool = False) -> QPixmap:
    if pm.isNull() or (not horizontal and not vertical):
        return pm
    return pm.transformed(QTransform().scale(-1.0 if horizontal else 1.0, -1.0 if vertical else 1.0))


def _rotate_pixmap(pm: QPixmap, degrees: float) -> QPixmap:
    if pm.isNull() or degrees == 0:
        return pm
    return pm.transformed(QTransform().rotate(degrees), Qt.TransformationMode.FastTransformation)


def _metal_edge_orientations(src: QPixmap) -> tuple[QPixmap, QPixmap, QPixmap, QPixmap]:
    """Top/left/right/bottom rails with the light-grey lip on the outside.

    Source is a horizontal strip (light on top). Left = 90° CCW, right = 90° CW,
    bottom = vertical flip.
    """
    if src.isNull():
        empty = QPixmap()
        return empty, empty, empty, empty
    top = src
    left = _rotate_pixmap(src, -90.0)
    right = _rotate_pixmap(src, 90.0)
    bottom = _mirror_pixmap(src, vertical=True)
    return top, left, right, bottom


def _metal_corner_orientations(src: QPixmap) -> tuple[QPixmap, QPixmap, QPixmap, QPixmap]:
    """TL/TR/BL/BR from a left (TL) corner: H-flip, V-flip, and both.

    Keeps the full L geometry (both arms at full length) — no stub crop.
    """
    if src.isNull():
        empty = QPixmap()
        return empty, empty, empty, empty
    tl = src
    tr = _mirror_pixmap(src, horizontal=True)
    bl = _mirror_pixmap(src, vertical=True)
    br = _mirror_pixmap(src, horizontal=True, vertical=True)
    return tl, tr, bl, br


def _metal_line_is_fringe(img, *, x: int | None = None, y: int | None = None) -> bool:
    """True if this row/col is empty or flat black (atlas halo, not the lip)."""
    w, h = img.width(), img.height()
    if x is not None:
        n = h
        samples = (img.pixelColor(x, yy) for yy in range(h))
    else:
        n = w
        samples = (img.pixelColor(xx, y) for xx in range(w))
    fringe = 0
    for c in samples:
        if c.alpha() < 32 or (c.red() + c.green() + c.blue()) < 12:
            fringe += 1
    return fringe >= max(1, int(n * 0.85))


def _trim_metal_outer_fringe(
    pm: QPixmap,
    *,
    left: bool = False,
    right: bool = False,
    top: bool = False,
    bottom: bool = False,
) -> QPixmap:
    """Drop the crop's near-black outer halo so the metal lip is the dest edge."""
    if pm.isNull():
        return pm
    img = pm.toImage()
    w, h = img.width(), img.height()
    x0, y0, x1, y1 = 0, 0, w, h
    if left:
        while x0 < x1 - 1 and _metal_line_is_fringe(img, x=x0):
            x0 += 1
    if right:
        while x1 - 1 > x0 and _metal_line_is_fringe(img, x=x1 - 1):
            x1 -= 1
    if top:
        while y0 < y1 - 1 and _metal_line_is_fringe(img, y=y0):
            y0 += 1
    if bottom:
        while y1 - 1 > y0 and _metal_line_is_fringe(img, y=y1 - 1):
            y1 -= 1
    if x0 == 0 and y0 == 0 and x1 == w and y1 == h:
        return pm
    cropped = img.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    return QPixmap.fromImage(cropped)


def _metal_underfill_path(
    box: QRect,
    *,
    hang: int,
    ew: int,
    eh: int,
    side_stop: int,
) -> QPainterPath:
    """U-band under top / L / R metal. Stops at the banner tuck (no spike valleys)."""
    outset = _METAL_FLOOR_OUTSET
    outer = hang + outset
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    path.addRect(
        QRectF(
            box.x() - outer,
            box.y() - hang - outset,
            box.width() + 2 * outer,
            hang + eh + outset,
        )
    )
    y0 = float(box.y() - hang)
    side_h = float(max(1, side_stop - int(y0)))
    path.addRect(QRectF(box.x() - outer, y0, outer + ew, side_h))
    path.addRect(QRectF(box.x() + box.width() - ew, y0, ew + outer, side_h))
    return path


def _scaled_corner(pm: QPixmap, size: int) -> QPixmap:
    if pm.isNull() or size <= 0:
        return QPixmap()
    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class FolderFrameStroke(QWidget):
    """Click-through generic-metal top/side rails and TL/TR corners.

    No outer window stroke — metal chrome is the frame. Floor fills first
    under (and slightly past) the metal so the atlas pad is not a black halo.
    Side rails stop at the nav banner (tuck under the strip). No BL/BR
    generic-metal ornaments.
    """

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FolderFrameStroke")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._host = host
        self._floor = _load_theme_texture(_FLOOR_NAME, _FLOOR_EXTERNAL)
        edge_top, edge_left, edge_right, _edge_bottom = _metal_edge_orientations(
            _load_bundled_pixmap(_METAL_EDGE_NAME)
        )
        self._edge_top = _trim_metal_outer_fringe(edge_top, top=True)
        self._edge_left = _trim_metal_outer_fringe(edge_left, left=True)
        self._edge_right = _trim_metal_outer_fringe(edge_right, right=True)
        tl_src, tr_src, _bl_src, _br_src = _metal_corner_orientations(
            _load_bundled_pixmap(_METAL_CORNER_NAME)
        )
        self._tl = _scaled_corner(
            _trim_metal_outer_fringe(tl_src, left=True, top=True),
            _METAL_CORNER_DRAW,
        )
        self._tr = _scaled_corner(
            _trim_metal_outer_fringe(tr_src, right=True, top=True),
            _METAL_CORNER_DRAW,
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        box = self._host.folder_stroke_box()
        if box is None or box.width() <= 0 or box.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        hang = _METAL_CORNER_HANG
        join = _METAL_ARM_JOIN
        tl_w = max(self._tl.width(), 1)
        tl_h = max(self._tl.height(), 1)
        tr_w = max(self._tr.width(), 1)
        tr_h = max(self._tr.height(), 1)
        ew = _METAL_EDGE_DRAW
        eh = _METAL_EDGE_DRAW

        tl_pos = QPoint(box.x() - hang, box.y() - hang)
        tr_pos = QPoint(
            box.x() + box.width() - tr_w + hang,
            box.y() - hang,
        )

        # Tiled edges span between corner arm tips (not under cropped stubs).
        top_x0 = tl_pos.x() + tl_w - join
        top_x1 = tr_pos.x() + join
        top = QRect(
            top_x0,
            box.y() - 1,
            max(1, top_x1 - top_x0),
            eh,
        )

        # Rails run from the TL/TR vertical arm tips down to the solid banner
        # bar, then tuck under the opaque strip. Banner PNG is raised above.
        side_top = max(tl_pos.y() + tl_h, tr_pos.y() + tr_h) - join
        side_stop = box.y() + box.height()
        banner = getattr(self._host, "_nav_bottom_banner", None)
        if banner is not None and banner.height() > 0:
            banner_tl = _map_via_global(banner, self, QPoint(0, 0))
            if banner_tl is not None:
                side_stop = banner_tl.y() + _METAL_RAIL_BANNER_TUCK
        side_h = max(1, side_stop - side_top)
        left = QRect(box.x() - 1, side_top, ew, side_h)
        right = QRect(box.x() + box.width() - ew + 1, side_top, ew, side_h)

        # Floor first — U-band under and slightly outside the metal. No pen.
        # Side bands stop at side_stop; spike valleys use the under-banner fill.
        tile_origin = box.topLeft()
        panel = getattr(self._host, "_content_panel", None)
        if panel is not None:
            mapped = _map_via_global(panel, self, QPoint(0, 0))
            if mapped is not None:
                tile_origin = mapped
        under = _metal_underfill_path(
            box, hang=hang, ew=ew, eh=eh, side_stop=side_stop
        )
        painter.save()
        painter.setClipPath(under)
        _paint_floor_fill(
            painter,
            under.boundingRect().toAlignedRect().adjusted(-1, -1, 1, 1),
            self._floor,
            tile_origin=tile_origin,
        )
        _paint_floor_lighting(
            painter,
            under.boundingRect().toAlignedRect().adjusted(-1, -1, 1, 1),
            origin=tile_origin,
        )
        painter.restore()

        _paint_tiled_h(painter, top, self._edge_top, eh)
        _paint_tiled_v(painter, left, self._edge_left, ew)
        _paint_tiled_v(painter, right, self._edge_right, ew)

        if not self._tl.isNull():
            painter.drawPixmap(tl_pos, self._tl)
        if not self._tr.isNull():
            painter.drawPixmap(tr_pos, self._tr)


class PortraitPlayFrame(QWidget):
    """Click-through UIFramePortrait U-frame over the play bar.

    Stacked above the under-banner fills and below the nav-banner PNG so
    side rails tuck under the solid bar. Valley alpha shows play black,
    then these rails, then the unkeyed PNG.
    Ignores mouse events so PLAY, the update button, and the loading bar
    stay clickable.
    """

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PortraitPlayFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._host = host
        self._edge_bottom = _load_bundled_pixmap(_PORTRAIT_EDGE_BOTTOM_NAME)
        self._edge_left = _load_bundled_pixmap(_PORTRAIT_EDGE_LEFT_NAME)
        self._edge_right = _load_bundled_pixmap(_PORTRAIT_EDGE_RIGHT_NAME)
        self._bl = _load_bundled_pixmap(_PORTRAIT_CORNER_BL_NAME)
        self._br = _load_bundled_pixmap(_PORTRAIT_CORNER_BR_NAME)

    def paintEvent(self, event) -> None:  # noqa: N802
        frame = self._host.portrait_play_box()
        if frame is None or frame.width() <= 0 or frame.height() <= 0:
            return
        painter = QPainter(self)
        # Native 1:1 tiles — SmoothPixmapTransform smears the 8–9px lips.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        bl = self._bl
        br = self._br
        el = self._edge_left
        er = self._edge_right
        eb = self._edge_bottom
        if bl.isNull() or br.isNull():
            return

        nudge = _PORTRAIT_OUTER_NUDGE
        bl_pos = QPoint(
            frame.x() - _PORTRAIT_BL_OX - nudge, frame.bottom() - _PORTRAIT_BL_OY
        )
        br_pos = QPoint(
            frame.right() - _PORTRAIT_BR_OX + nudge, frame.bottom() - _PORTRAIT_BR_OY
        )
        join = _PORTRAIT_JOIN

        if not eb.isNull():
            by = frame.bottom() - _PORTRAIT_BOT_OY
            x0 = bl_pos.x() + bl.width() - join
            x1 = br_pos.x() + join
            bot = QRect(x0, by, max(1, x1 - x0), eb.height())
            _paint_tiled_h(painter, bot, eb, eb.height())

        # Start at the solid banner bar, not the crystal / spike-valley region.
        # Rails sit under the PNG and are only seen as they meet the bar.
        side_top = frame.y() + _NAV_BOTTOM_BANNER_MID_Y
        # Stop at the L inner join (top of the corner's horizontal arm). Adding
        # JOIN here used to equal corner height and poke past the rounded lip.
        side_stop = min(
            bl_pos.y() + _PORTRAIT_ARM_JOIN,
            br_pos.y() + _PORTRAIT_ARM_JOIN,
        )
        side_h = max(1, side_stop - side_top)
        if not el.isNull():
            lx = frame.x() - _PORTRAIT_LEFT_OX - nudge
            _paint_tiled_v(
                painter, QRect(lx, side_top, el.width(), side_h), el, el.width()
            )
        if not er.isNull():
            rx = frame.right() - _PORTRAIT_RIGHT_OX + nudge
            _paint_tiled_v(
                painter, QRect(rx, side_top, er.width(), side_h), er, er.width()
            )

        painter.drawPixmap(bl_pos, bl)
        painter.drawPixmap(br_pos, br)


class ContentPanel(QWidget):
    """Folder body — Necrolord floor inside the closed metal frame."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ContentPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._floor = _load_theme_texture(_FLOOR_NAME, _FLOOR_EXTERNAL)
        self._frame_host: MainWindow | None = None

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def paintEvent(self, event) -> None:  # noqa: N802
        # Opaque card including the top shelf pixel (y=0). Full-bleed floor —
        # metal rails overlay the edges; no 1px window-stroke gutter.
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inner = self.rect()
        painter.setClipPath(
            _interior_fill_clip(
                self, inner, self._frame_host, pad_top=0.0, pad_bottom=0.0
            )
        )
        _paint_floor_fill(painter, inner, self._floor, tile_origin=inner.topLeft())
        _paint_floor_lighting(painter, inner, origin=inner.topLeft())


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
        # Mist fill to the widget edges (no window-stroke gutter).
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inner = self.rect()
        # Folder interior ∩ widget (BL/BR fallback) — never the exterior corner pockets.
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


class NavBannerUnderFill(QWidget):
    """Body + play fills behind nav_bottom.png (same rect as the banner).

    Play black (#100d0c / _MIST_BASE) first, then body (#181315 / _FLOOR_BASE)
    on the solid-bar half so they meet on the opaque bar. Spike-valley and
    PNG-pad alpha are not see-through. Stacked under metal / portrait / PNG
    so the fill never sits on top of the banner art. Click-through.
    """

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("NavBannerUnderFill")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._host = host

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        inner = self.rect()
        painter.setClipPath(
            _interior_fill_clip(
                self, inner, self._host, pad_top=0.0, pad_bottom=0.0
            )
        )
        meet = min(_NAV_BOTTOM_BANNER_MID_Y, inner.height())
        # Play fill (full), then body on the upper/bar half. Seam is hidden
        # in the opaque solid bar. Do not paint mist texture here — that
        # read as a muddy slab; this is the play-strip black only.
        painter.fillRect(inner, _MIST_BASE)
        if meet > 0:
            painter.fillRect(
                QRect(inner.left(), inner.top(), inner.width(), meet),
                _FLOOR_BASE,
            )


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
        self._pix = _load_bundled_pixmap("nav_bottom.png")
        self._frame_host: MainWindow | None = None
        # Official ravencraft.io PNG as stored — no keying. Fills live on
        # NavBannerUnderFill underneath this widget.

    def set_frame_host(self, host: MainWindow | None) -> None:
        self._frame_host = host

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        inner = self.rect()
        clip = _interior_fill_clip(
            self, inner, self._frame_host, pad_top=0.0, pad_bottom=0.0
        )
        painter.setClipPath(clip)
        pix = self._pix
        if pix.isNull():
            return
        overdraw = _NAV_BOTTOM_BANNER_OVERDRAW_X
        draw = inner.adjusted(-overdraw, 0, overdraw, 0)
        painter.drawPixmap(draw, pix)


try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:  # pragma: no cover
    def _shiboken_is_valid(obj: object) -> bool:  # type: ignore[misc]
        return obj is not None


def _safe_worker_running(worker: Worker | None) -> bool:
    """True when *worker* is a live QThread that is still running."""
    if worker is None:
        return False
    try:
        if not _shiboken_is_valid(worker):
            return False
        return worker.isRunning()
    except RuntimeError:
        return False


def _call_when_worker_idle(worker: Worker | None, callback) -> None:
    """Run *callback* now if *worker* is idle, else after finished_ok or failed.

    The follow-up is deferred one event-loop turn so finished_ok UI slots
    (and QThread ``finished`` / deleteLater) can settle before the next
    auto-update step starts another worker. Stacking that work in the same
    turn as addon-scan UI apply has coincided with silent Qt aborts.
    """
    if not _safe_worker_running(worker):
        QTimer.singleShot(0, callback)
        return
    fired = False

    def _once(*_args):
        nonlocal fired
        if fired:
            return
        fired = True
        QTimer.singleShot(0, callback)

    worker.finished_ok.connect(_once)
    worker.failed.connect(_once)


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
            from ichalaunch.core.filesystem import is_lock_or_av_error, user_facing_os_error
            from requests.exceptions import (
                ChunkedEncodingError,
                ConnectionError as RequestsConnectionError,
                Timeout as RequestsTimeout,
            )

            # Expected environmental noise — warn only so opt-in ERROR reporting
            # does not flood the sticky crash issue (locks, offline DNS, rate limits).
            soft = (
                isinstance(exc, GitHubRateLimitError)
                or (isinstance(exc, OSError) and is_lock_or_av_error(exc))
                or isinstance(
                    exc,
                    (RequestsConnectionError, RequestsTimeout, ChunkedEncodingError),
                )
            )
            if soft:
                log.warning("Worker failed: %s", exc)
            else:
                log.exception("Worker failed")
            if isinstance(exc, OSError) and is_lock_or_av_error(exc):
                self.failed.emit(user_facing_os_error(exc))
            else:
                self.failed.emit(format_github_error_message(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"IchaLaunch {__version__}")
        icon_path = theme_file("ravencraft_icon.ico")
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
        self._realm_ping_worker: Worker | None = None
        # Strong refs to every started Worker until its thread has fully
        # finished. The done/fail slots clear the attributes above while the
        # QThread can still be inside run(); if that attribute held the last
        # Python reference, the C++ QThread was destroyed mid-run, which Qt
        # treats as fatal and aborts the process with no Python traceback.
        self._live_workers: set[Worker] = set()
        self._latest_launcher_release: LauncherReleaseInfo | None = None
        self._drag_pos: QPoint | None = None
        # Wayland only: a press landed on chrome and the compositor drag
        # starts if the pointer actually moves. See _use_system_window_move.
        self._system_move_pending = False
        self._checking_addons = False
        self._checking_mods = False
        self._check_addon_pct = 0
        self._check_mod_pct = 0
        self._addon_check_status = ""
        # True while apply dropped the scan gate so deferred list work can flush;
        # keeps _lock_addon_filters from re-arming scanning mid-settle.
        self._addon_check_settling = False
        self._silent_addon_check_retry_armed = False
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
        self._auto_update_seq_active = False
        self._auto_update_seq_catalogs = False
        self._auto_update_seq_periodic = False
        self._permissions_skipped_path: str | None = None
        self._play_launch_lock_until = 0.0
        self._play_launch_timer = QTimer(self)
        self._play_launch_timer.setSingleShot(True)
        self._play_launch_timer.timeout.connect(self._release_play_launch_lock)

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
        # L/R: metal hang only. Bottom: keep the 88px play strip's sit-height.
        outer.setContentsMargins(
            _FRAME_OUTSET_MARGIN, 0, _FRAME_OUTSET_MARGIN, _FRAME_OUTSET_BOTTOM
        )
        outer.setSpacing(0)

        # ---- Folder tabs (top chrome) — floor-tinted glue plates ----
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
        # 80px UPDATE glow (hole-matched to the 56 plate) needs this height.
        bottom.setFixedHeight(_BOTTOM_BAR_H)
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(16, 4, 4, 4)
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

        self.update_btn = UpdateLaunchButton()
        self.update_btn.clicked.connect(self._apply_launcher_update)
        self.play_btn = LaunchButton("PLAY")
        self.play_btn.clicked.connect(self._on_play_or_install)
        # Overlay on BottomBar — not in the PLAY HBox, so PLAY does not shift.
        self.realm_ping = RealmPingDot(bottom)
        self.realm_ping.raise_()

        play_cluster = QWidget(bottom)
        play_cluster.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        play_row = QHBoxLayout(play_cluster)
        play_row.setContentsMargins(0, 0, 0, 0)
        play_row.setSpacing(PING_DOT_GAP)
        play_row.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        play_row.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._play_cluster = play_cluster
        self._realm_ping_timer = QTimer(self)
        self._realm_ping_timer.setSingleShot(True)
        self._realm_ping_timer.timeout.connect(self._refresh_realm_ping)
        self._realm_ping_backoff_ms = PROBE_INTERVAL_MS
        self._realm_ping_next_at = 0.0
        self._realm_ping_last_at = 0.0

        # Expanding slot keeps PLAY pinned to the right when the rail is hidden.
        # Contributors and the loading bar share this slot (mutually exclusive).
        progress_slot = QWidget(bottom)
        progress_slot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        progress_slot.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        slot_l = QHBoxLayout(progress_slot)
        slot_l.setContentsMargins(0, 0, 0, 0)
        slot_l.setSpacing(0)

        contributors = QWidget(progress_slot)
        contributors.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        contributors.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        contrib_l = QVBoxLayout(contributors)
        contrib_l.setContentsMargins(0, 0, 0, 0)
        contrib_l.setSpacing(2)
        contrib_l.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        contributors_label = QLabel("Contributors")
        contributors_label.setObjectName("Muted")
        contributors_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        portraits = QHBoxLayout()
        portraits.setContentsMargins(0, 0, 0, 0)
        portraits.setSpacing(6)
        portraits.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        portraits.addWidget(
            ContributorPortrait(
                "contributor_01.jpg",
                border_name="CheckButtonGlow-Pink.PNG",
                url="https://discord.com/users/1080557702339633222",
                tooltip="Mynie",
            )
        )
        portraits.addWidget(
            ContributorPortrait(
                "contributor_02.jpg",
                border_name=None,
                fill_mode="circle_cutout",
                url="https://discord.com/users/608476640271663129",
                tooltip="Valheru",
            )
        )
        portraits.addWidget(
            ContributorPortrait(
                "contributor_03.jpg",
                border_name="CheckButtonHilight-Blue.PNG",
                crop_mode="cover",
                fill_mode="outer",
            )
        )
        contrib_l.addWidget(contributors_label, 0, Qt.AlignmentFlag.AlignHCenter)
        contrib_l.addLayout(portraits)
        self._contributors = contributors

        slot_l.addWidget(contributors)
        slot_l.addWidget(self.progress)
        self._progress_slot = progress_slot
        # Loading bar starts hidden — contributors fill the center slot.
        self._sync_contributors_with_progress()

        grip = QSizeGrip(bottom)
        grip.setFixedSize(16, 16)
        grip.setToolTip("Drag to resize")
        self._play_grip = grip
        self.realm_ping.raise_()

        bot_l.addWidget(self.status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        # Slot (not the hidden bar) owns leftover space so PLAY never recenters.
        bot_l.addWidget(progress_slot, 1, Qt.AlignmentFlag.AlignVCenter)
        bot_l.addWidget(play_cluster, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bot_l.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        # Decorative strip between pages and the play/progress bar (bundled offline asset).
        self._nav_bottom_banner = NavBottomBanner()
        self._nav_bottom_banner.set_frame_host(self)

        outer.addWidget(nav)
        outer.addWidget(content, 1)
        # Panel floor hangs onto the banner's solid bar (covers the ~6px PNG
        # top pad). Play black behind valleys is NavBannerUnderFill — do not
        # grow BottomBar (that shrinks PLAY).
        outer.addSpacing(-_NAV_BOTTOM_BANNER_PANEL_OVERLAP)
        outer.addWidget(self._nav_bottom_banner)
        # 1px overlap so the banner / play-strip join stays opaque.
        outer.addSpacing(-1)
        outer.addWidget(bottom)

        # Under-banner fills (play black + body) sit on Root, same rect as
        # the banner, below metal / portrait / PNG.
        self._banner_underfill = NavBannerUnderFill(self, root)

        # Metal top/sides/TL/TR. Portrait U-frame sits
        # under the banner PNG so rails tuck behind the solid bar.
        self._frame_stroke = FolderFrameStroke(self, root)
        self._frame_stroke.raise_()
        self._portrait_frame = PortraitPlayFrame(self, root)

        # RavenCraft crest — straddles ContentPanel top border (click-through).
        self._rc_logo = RavenCraftFloatingLogo(root)
        self._rc_logo.raise_()

        self._update_window_mask()
        self._position_frame_stroke()
        self._position_rc_logo()
        self._position_chrome_buttons()

        # Wire
        self.home.play_clicked.connect(self._on_play_or_install)
        self.home.install_clicked.connect(self._install_or_browse)
        self.client.apply_clicked.connect(self._apply_mods)
        self.client.rescan_clicked.connect(self._resync)
        self.client.check_updates_requested.connect(self._check_mod_updates)
        self.client.update_mod_requested.connect(self._update_client_mod)
        self.client.reinstall_mod_requested.connect(self._reinstall_client_mod)
        self.client.reacquire_patch9_requested.connect(self._reacquire_stock_patch9)
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
    def folder_stroke_box(self) -> QRect | None:
        """Integer folder box in Root coords (content top-left → bottom-bar bottom)."""
        root = self.centralWidget()
        content = getattr(self, "_content_panel", None)
        bottom = getattr(self, "_bottom_bar", None)
        if root is None or content is None or bottom is None:
            return None
        if content.width() <= 0 or bottom.height() <= 0:
            return None
        if not root.isAncestorOf(content) or not root.isAncestorOf(bottom):
            return None
        origin = _safe_map_to(content, root, QPoint(0, 0))
        bot_pt = _safe_map_to(bottom, root, QPoint(0, bottom.height()))
        if origin is None or bot_pt is None:
            return None
        return QRect(origin.x(), origin.y(), content.width(), bot_pt.y() - origin.y())

    def portrait_play_box(self) -> QRect | None:
        """Folder-width rect from nav-banner top through BottomBar bottom."""
        root = self.centralWidget()
        box = self.folder_stroke_box()
        bottom = getattr(self, "_bottom_bar", None)
        if root is None or box is None or bottom is None:
            return None
        top = box.y() + box.height() - bottom.height()
        banner = getattr(self, "_nav_bottom_banner", None)
        if banner is not None and banner.height() > 0:
            btl = _map_via_global(banner, root, QPoint(0, 0))
            if btl is not None:
                top = btl.y()
        bot_pt = _map_via_global(bottom, root, QPoint(0, bottom.height()))
        if bot_pt is None:
            return None
        height = bot_pt.y() - top
        if height <= 0:
            return None
        return QRect(box.x(), top, box.width(), height)

    def build_folder_stroke_path(self) -> QPainterPath:
        """Folder outline in Root coords: closed body, full shelf, rounded BL/BR."""
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
        # Integer edges so floor fill reaches the window (no 1px stroke inset).
        left = float(origin.x())
        right = float(origin.x() + content.width())
        shelf_y = float(origin.y())
        bot = float(bot_pt.y())
        return _folder_frame_path(left, right, shelf_y, bot)

    def _position_frame_stroke(self) -> None:
        stroke = getattr(self, "_frame_stroke", None)
        root = self.centralWidget()
        if stroke is None or root is None:
            return
        stroke.setGeometry(0, 0, root.width(), root.height())
        stroke.show()
        stroke.update()
        # Back → front: BottomBar mist → under-banner fills (play + body) →
        # metal rails → portrait U-frame → nav banner PNG → −/X / RC crest.
        # PLAY stays in BottomBar (below the 30px strip, not covered).
        under = getattr(self, "_banner_underfill", None)
        banner = getattr(self, "_nav_bottom_banner", None)
        if under is not None and banner is not None:
            under.setGeometry(banner.geometry())
            under.show()
            under.update()
            under.raise_()
        # HOME rotating art tucks under the banner; keep it above underfill so
        # the tuck shows through the PNG top pad (else floor fill reads as a gap).
        home = getattr(self, "home", None)
        talent = getattr(home, "talent_bg", None) if home is not None else None
        if isinstance(talent, QWidget) and talent.isVisible():
            talent.raise_()
            moa = getattr(home, "logo", None)
            if isinstance(moa, QWidget) and moa.isVisible():
                moa.raise_()
        stroke.raise_()
        portrait = getattr(self, "_portrait_frame", None)
        if portrait is not None:
            portrait.setGeometry(0, 0, root.width(), root.height())
            portrait.show()
            portrait.update()
            portrait.raise_()
        if banner is not None:
            banner.raise_()
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
            w = min(1280, max_w)
            h = min(853, max_h)
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
        if _use_system_window_move():
            # move() is discarded on Wayland, so there is nothing this can do;
            # keeping a window reachable is the compositor's job there.
            return
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

    def _position_chrome_buttons(self) -> None:
        """Pin minimize/close inside ContentPanel top-right, clear of metal TR/rail."""
        content = getattr(self, "_content_panel", None)
        btn_min = getattr(self, "_btn_minimize", None)
        btn_close = getattr(self, "_btn_close", None)
        root = self.centralWidget()
        if content is None or btn_min is None or btn_close is None or root is None:
            return
        inset_x = _CHROME_BTN_INSET_X
        inset_y = _CHROME_BTN_INSET_Y
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
        frame_inset = _CHROME_BTN_INSET_X + 8
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
        for name in (
            "_top_nav",
            "_content_panel",
            "_banner_underfill",
            "_nav_bottom_banner",
            "_bottom_bar",
        ):
            w = getattr(self, name, None)
            if w is not None:
                w.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()
        self._update_window_mask()
        self._position_frame_stroke()
        self._position_rc_logo()
        self._position_chrome_buttons()
        self._refresh_chrome_fills()
        # Reliable initial scan shortly after the UI is visible (not only on the 5‑min timer).
        if not self._startup_checks_scheduled:
            self._startup_checks_scheduled = True
            QTimer.singleShot(_STARTUP_UPDATE_DELAY_MS, self._run_startup_update_checks)
        if not getattr(self, "_toc_mismatch_flush_scheduled", False):
            self._toc_mismatch_flush_scheduled = True
            QTimer.singleShot(0, self._flush_pending_toc_mismatch_prompt)
        if not getattr(self, "_crash_opt_in_scheduled", False):
            self._crash_opt_in_scheduled = True
            QTimer.singleShot(0, self._maybe_prompt_crash_reporting_opt_in)
        if not getattr(self, "_addons_preload_scheduled", False):
            self._addons_preload_scheduled = True
            QTimer.singleShot(0, self._preload_hidden_addon_rows)
        if not getattr(self, "_realm_ping_scheduled", False):
            self._realm_ping_scheduled = True
            if not realm_ping_disabled():
                self._arm_realm_ping(PROBE_FIRST_DELAY_MS)
        else:
            self._resume_realm_ping_timer()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                themed.close_open_themed_dialogs(self)
                self._pause_realm_ping_timer()
            else:
                self._resume_realm_ping_timer()
        super().changeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._pause_realm_ping_timer()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_window_mask()
        self._position_frame_stroke()
        self._position_rc_logo()
        self._position_chrome_buttons()
        self._refresh_chrome_fills()
        self._fit_bottom_progress()

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._resize_edges is not None:
            self.releaseMouse()
        self._drain_workers_for_shutdown()
        super().closeEvent(event)

    def _drain_workers_for_shutdown(self) -> None:
        """Stop live worker threads before teardown drops their last references.

        Interpreter shutdown destroys whatever is left in ``_live_workers``;
        destroying a still-running QThread aborts the process (WER crash dump)
        even though the user just closed the window normally.
        """
        for worker in list(self._live_workers):
            try:
                if not worker.isRunning():
                    continue
                worker.requestInterruption()
                if not worker.wait(3000):
                    log.warning("Worker still running at shutdown; terminating")
                    worker.terminate()
                    worker.wait(1000)
            except RuntimeError:
                # Wrapper already invalidated — nothing left to stop.
                continue

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
                    RealmPingDot,
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

    def _release_pointer_after_handoff(self, target, event) -> None:
        """Tell the pressed widget the button is up, once the compositor has it.

        qtwayland delivers the release of a compositor-run drag to a null
        surface, so the widget that saw the press keeps its pressed and
        hovered look for good. Qt has fixed and un-fixed this more than once
        (QTBUG-97037), so post the release rather than assume; a duplicate
        release is harmless. This is what FramelessHelper, Telegram Desktop
        and Chromium's Ozone backend all do at the same point.
        """
        if not isinstance(target, QWidget):
            return
        QApplication.postEvent(
            target,
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(event.position()),
                QPointF(event.globalPosition()),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    def _compositor_owns_window_state(self) -> bool:
        """True while the compositor may refuse a move or resize outright.

        xdg-shell lets the server ignore both requests for a maximized or
        fullscreen toplevel, and neither Qt nor the protocol reports the
        refusal, so the only way not to eat the click is to not ask.
        """
        state = self.windowState()
        return bool(
            state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen)
        )

    def _start_system_move(self) -> bool:
        """Ask the compositor to drag the window.

        True means the platform made the request, not that the compositor
        honoured it: there is no acknowledgement in the protocol, and the
        Wayland backend returns true whenever the toplevel is initialised.
        """
        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemMove())
        except (RuntimeError, TypeError):
            return False

    def _start_system_resize(self, edges: tuple[bool, bool, bool, bool]) -> bool:
        """Ask the compositor to resize from *edges*. See _start_system_move."""
        left, right, top, bottom = edges
        qedges = Qt.Edge(0)  # falsy when no edge was hit
        if left:
            qedges |= Qt.Edge.LeftEdge
        if right:
            qedges |= Qt.Edge.RightEdge
        if top:
            qedges |= Qt.Edge.TopEdge
        if bottom:
            qedges |= Qt.Edge.BottomEdge
        if not qedges:
            return False
        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemResize(qedges))
        except (RuntimeError, TypeError):
            return False

    def _begin_window_drag(self, global_pos: QPoint) -> None:
        self._resize_edges = None
        self._resize_origin = None
        self._resize_geo = None
        if _use_system_window_move():
            # Arm only. The compositor drag starts on the first movement, not
            # here: asking on the press would consume a plain click on the
            # chrome, and _drag_pos stays None so the move() path is dormant.
            self._system_move_pending = not self._compositor_owns_window_state()
            self._drag_pos = None
            return
        self._drag_pos = global_pos - self.frameGeometry().topLeft()

    def eventFilter(self, obj, event):
        """Edge-resize on border; drag window from non-interactive chrome."""
        progress = getattr(self, "progress", None)
        if progress is not None and obj is progress:
            et = event.type()
            if et in (QEvent.Type.Show, QEvent.Type.Hide):
                self._sync_contributors_with_progress()
        if obj is getattr(self, "_content_panel", None) and event.type() == QEvent.Type.Resize:
            self._position_frame_stroke()
            self._position_chrome_buttons()
            self._position_rc_logo()
            self._refresh_chrome_fills()
        if isinstance(obj, QWidget) and obj.window() is self:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = self.mapFromGlobal(event.globalPosition().toPoint())
                edges = self._hit_resize_edges(pos)
                if any(edges) and not isinstance(obj, QSizeGrip):
                    # Hand the resize over instead of grabbing the mouse: on
                    # Wayland the compositor takes the pointer, so a grab
                    # taken here would never see its own release. Unlike the
                    # drag this stays on the press, which is where Qt's own
                    # QSizeGrip does it — a resize border has no click to
                    # protect.
                    if _use_system_window_move():
                        self._drag_pos = None
                        self._resize_edges = None
                        self._resize_origin = None
                        self._resize_geo = None
                        self._system_move_pending = False
                        if self._compositor_owns_window_state():
                            return super().eventFilter(obj, event)
                        self._start_system_resize(edges)
                        self._release_pointer_after_handoff(obj, event)
                        return True
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
                if self._system_move_pending and event.buttons() & Qt.MouseButton.LeftButton:
                    self._system_move_pending = False
                    self._start_system_move()
                    self._release_pointer_after_handoff(obj, event)
                    return True
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
                if self._system_move_pending:
                    # Pressed and released without moving: a plain click, and
                    # the widget under it has already had both events.
                    self._system_move_pending = False
                if self._drag_pos is not None:
                    self._drag_pos = None
                    self._clamp_on_screen()
                    return True
                self._drag_pos = None
        return super().eventFilter(obj, event)

    def _apply_edge_resize(self, global_pos: QPoint) -> None:
        if _use_system_window_move():
            # setGeometry() cannot move an origin on Wayland, so a left- or
            # top-edge drag would resize from the wrong side. Stated here as
            # well as at the call sites: this is the arithmetic that would be
            # wrong, and it should not depend on state set three frames away.
            return
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
                if _use_system_window_move():
                    self._drag_pos = None
                    self._resize_edges = None
                    self._resize_origin = None
                    self._resize_geo = None
                    self._system_move_pending = False
                    if self._compositor_owns_window_state():
                        super().mousePressEvent(event)
                        return
                    self._start_system_resize(edges)
                    self._release_pointer_after_handoff(self, event)
                    event.accept()
                    return
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
        if self._system_move_pending and event.buttons() & Qt.MouseButton.LeftButton:
            self._system_move_pending = False
            self._start_system_move()
            self._release_pointer_after_handoff(self, event)
            event.accept()
            return
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
        self._system_move_pending = False
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
        # HOME art/logo live on Root (not HomePage). Hide them whenever
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

    def prepare_addon_lists_before_show(self) -> None:
        """Build Addons lists while the splash is still up (local disk only).

        Network Check Updates stays deferred. Prebuilding here means
        launch → Addons → Check only needs a reveal flush, not a first paint
        clear()/setItemWidget race against a concurrent scan apply.
        """
        try:
            self.addons.preload_rows()
        except RuntimeError:
            pass

    def _launcher_update_pending(self) -> bool:
        # Applying an update means replacing the running executable, which
        # apply_windows_self_replace refuses to do anywhere but Windows. The
        # square update button is the only place that offer appears, so hide
        # it everywhere else rather than hijacking PLAY.
        if os.name != "nt":
            return False
        info = self._latest_launcher_release
        return bool(info and info.update_available)

    def _refresh_nav_badges(self) -> None:
        """Adventure Guide alert on folder tabs when that area has pending work."""
        if len(self.nav_btns) < 4:
            return
        # HOME — launcher self-update (square button left of PLAY)
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
        include_catalogs = bool(settings.check_updates_on_startup() and is_installed())
        if not settings.check_updates_on_startup():
            self._refresh_nav_badges()
        elif not is_installed():
            log.info("Startup addon/mod checks skipped — no game path yet")
            self._refresh_nav_badges()
        elif rate_limit_exhausted():
            # Still attempt — check_* handlers stop early and surface RATE_LIMIT_STATUS.
            log.info("GitHub rate limit low at startup; attempting checks anyway")
        self._start_auto_update_sequence(include_catalogs=include_catalogs, periodic=False)

    def _periodic_update_check(self) -> None:
        """One 15-minute silent refresh: launcher, then addons, then client mods."""
        if _safe_worker_running(self._worker):
            return
        include_catalogs = bool(settings.check_updates_on_startup() and is_installed())
        self._start_auto_update_sequence(include_catalogs=include_catalogs, periodic=True)

    def _start_auto_update_sequence(self, *, include_catalogs: bool, periodic: bool) -> None:
        """Launcher first, then addons, then client. Sequential — never parallel.

        Manual Check for updates on the addons/client tabs is unchanged.
        """
        if getattr(self, "_auto_update_seq_active", False):
            return
        self._auto_update_seq_active = True
        self._auto_update_seq_catalogs = include_catalogs
        self._auto_update_seq_periodic = periodic
        self._advance_auto_update_sequence(0)

    def _finish_auto_update_sequence(self) -> None:
        self._auto_update_seq_active = False

    def _advance_auto_update_sequence(self, index: int) -> None:
        """Start ``_AUTO_UPDATE_STEPS[index]``, then advance when that worker is idle."""
        if index >= len(_AUTO_UPDATE_STEPS):
            self._finish_auto_update_sequence()
            return
        step = _AUTO_UPDATE_STEPS[index]
        periodic = bool(getattr(self, "_auto_update_seq_periodic", False))
        catalogs = bool(getattr(self, "_auto_update_seq_catalogs", False))
        worker = None
        try:
            if step == "launcher":
                self._check_launcher_update(silent=True)
                worker = self._launcher_update_worker
            elif step == "addons":
                if not catalogs:
                    self._finish_auto_update_sequence()
                    return
                if settings.should_startup_check_addons(has_token=has_github_token()):
                    self._check_updates(silent=True, periodic=periodic)
                worker = self._update_worker
            elif step == "client":
                if not catalogs:
                    self._finish_auto_update_sequence()
                    return
                self._check_mod_updates(silent=True, periodic=periodic)
                worker = self._mod_update_worker
            else:
                self._finish_auto_update_sequence()
                return
        except Exception:
            log.exception("Auto update sequence failed at %s", step)
            self._finish_auto_update_sequence()
            return
        _call_when_worker_idle(
            worker, lambda i=index + 1: self._advance_auto_update_sequence(i)
        )

    def _refresh_play_button(self) -> None:
        if is_installed():
            self.play_btn.setText("PLAY")
        else:
            self.play_btn.setText("INSTALL")
        self._refresh_update_button()
        self._refresh_nav_badges()

    def _refresh_update_button(self) -> None:
        pending = self._launcher_update_pending()
        self.update_btn.set_pending(pending)
        info = self._latest_launcher_release
        if pending and info and getattr(info, "version", None):
            self.update_btn.setToolTip(f"Update IchaLaunch to v{info.version}")
        else:
            self.update_btn.setToolTip("Update IchaLaunch")
        self._fit_bottom_progress()

    def _play_cluster_trailing_px(self) -> int:
        """Width the PLAY cluster grew past a bare PLAY plate (UPDATE only)."""
        extra = 0
        update_btn = getattr(self, "update_btn", None)
        if update_btn is not None and not update_btn.isHidden():
            extra += update_btn.width() + PING_DOT_GAP
        return extra

    def _realm_ping_gap_right(self, bottom: QWidget) -> int:
        """Visible right edge of the PLAY–border gap (inner portrait chrome)."""
        right = bottom.width()
        root = self.centralWidget()
        frame = self.portrait_play_box()
        if root is not None and frame is not None and root.isAncestorOf(bottom):
            inner = QPoint(
                frame.right() - _PORTRAIT_RIGHT_OX + _PORTRAIT_OUTER_NUDGE,
                0,
            )
            mapped = bottom.mapFrom(root, inner)
            if mapped.x() > 0:
                right = min(right, mapped.x())
        return right

    def _position_realm_ping(self) -> None:
        """Center the ping in the open gap between PLAY and the right border."""
        ping = getattr(self, "realm_ping", None)
        play = getattr(self, "play_btn", None)
        bottom = getattr(self, "_bottom_bar", None)
        if ping is None or play is None or bottom is None:
            return
        if ping.parentWidget() is not bottom:
            ping.setParent(bottom)
        if play.width() <= 0 or bottom.width() <= 0:
            return
        play_tl = play.mapTo(bottom, QPoint(0, 0))
        play_right = play_tl.x() + play.width()
        border_right = self._realm_ping_gap_right(bottom)
        x = ping_overlay_x(play_right, border_right, ping.width())
        y = play_tl.y() + (play.height() - ping.height()) // 2
        min_x = play_right
        max_x = max(min_x, min(bottom.width(), border_right) - ping.width())
        if x > max_x:
            x = max_x
        if x < min_x:
            x = min_x
        y = max(0, min(y, max(0, bottom.height() - ping.height())))
        grip = getattr(self, "_play_grip", None)
        if grip is not None and not grip.isHidden():
            grip_tl = grip.mapTo(bottom, QPoint(0, 0))
            ping_rect = QRect(x, y, ping.width(), ping.height())
            grip_rect = QRect(grip_tl, grip.size())
            if ping_rect.intersects(grip_rect):
                y = max(0, grip_rect.y() - ping.height())
                ping_rect = QRect(x, y, ping.width(), ping.height())
            if ping_rect.intersects(grip_rect):
                x = max(min_x, grip_rect.x() - ping.width())
        ping.move(x, y)
        ping.raise_()
        if ping.isHidden():
            return
        ping.show()

    def _fit_bottom_progress(self) -> None:
        """Shorten the loading rail and recenter contributors for the PLAY cluster.

        UPDATE may grow the cluster left. The ping is overlaid to the right of
        PLAY and is not reserved here. ``reserve_trailing`` drops the rail's
        min/max so it never runs under UPDATE or PLAY.
        """
        progress = getattr(self, "progress", None)
        if progress is None:
            return
        extra = self._play_cluster_trailing_px()
        progress.reserve_trailing(extra)
        slot = getattr(self, "_progress_slot", None)
        if slot is not None and slot.layout() is not None:
            slot.layout().activate()
        contrib = getattr(self, "_contributors", None)
        if contrib is not None and contrib.layout() is not None:
            contrib.layout().activate()
        lay = getattr(self, "_bottom_bar", None)
        if lay is not None and lay.layout() is not None:
            lay.layout().activate()
        self._position_realm_ping()

    def _realm_ping_window_active(self) -> bool:
        return bool(self.isVisible() and not self.isMinimized())

    def _pause_realm_ping_timer(self) -> None:
        timer = getattr(self, "_realm_ping_timer", None)
        if timer is not None:
            timer.stop()

    def _arm_realm_ping(self, delay_ms: int) -> None:
        self._realm_ping_next_at = time.monotonic() + max(1, int(delay_ms)) / 1000.0
        self._pause_realm_ping_timer()
        if realm_ping_disabled() or not self._realm_ping_window_active():
            return
        self._realm_ping_timer.start(max(1, int(delay_ms)))

    def _resume_realm_ping_timer(self) -> None:
        if realm_ping_disabled() or not getattr(self, "_realm_ping_scheduled", False):
            return
        if not self._realm_ping_window_active():
            self._pause_realm_ping_timer()
            return
        if _safe_worker_running(getattr(self, "_realm_ping_worker", None)):
            return
        remaining_ms = int((self._realm_ping_next_at - time.monotonic()) * 1000)
        if remaining_ms < 1:
            last = getattr(self, "_realm_ping_last_at", 0.0) or 0.0
            if last and (time.monotonic() - last) < 1.0:
                remaining_ms = 1000
            else:
                remaining_ms = 1
        self._arm_realm_ping(remaining_ms)

    def _refresh_realm_ping(self) -> None:
        """Background TCP probe of the bundled logon host (never blocks PLAY)."""
        if realm_ping_disabled():
            return
        if not self._realm_ping_window_active():
            return
        if _safe_worker_running(self._realm_ping_worker):
            return
        self._realm_ping_last_at = time.monotonic()
        worker = Worker(probe_logon)

        def done(result):
            self._realm_ping_worker = None
            ok = False
            if isinstance(result, RealmProbe):
                self.realm_ping.set_probe(result)
                ok = bool(result.online)
            else:
                self.realm_ping.set_offline()
            self._realm_ping_backoff_ms = next_probe_backoff_ms(
                success=ok,
                previous_ms=self._realm_ping_backoff_ms,
            )
            self._arm_realm_ping(jittered_probe_delay_ms(self._realm_ping_backoff_ms))

        def fail(_msg):
            self._realm_ping_worker = None
            self.realm_ping.set_offline()
            self._realm_ping_backoff_ms = next_probe_backoff_ms(
                success=False,
                previous_ms=self._realm_ping_backoff_ms,
            )
            self._arm_realm_ping(jittered_probe_delay_ms(self._realm_ping_backoff_ms))

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._realm_ping_worker = worker
        self._track_worker(worker)
        worker.start()

    def _sync_contributors_with_progress(self) -> None:
        """Hide Contributors while the loading bar occupies the center slot."""
        contrib = getattr(self, "_contributors", None)
        progress = getattr(self, "progress", None)
        if contrib is None or progress is None:
            return
        contrib.setVisible(progress.isHidden())
        self._fit_bottom_progress()

    def _hide_progress_bar(self) -> None:
        self.progress.hide()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self._sync_contributors_with_progress()

    def _worker_busy(self) -> bool:
        busy = _safe_worker_running(self._worker)
        if not busy and self._worker is not None:
            self._worker = None
        return busy

    def _track_worker(self, worker: Worker) -> None:
        """Hold *worker* alive until its thread has actually terminated.

        Result slots may drop ``self._*_worker`` while ``run()`` is still
        unwinding on the worker thread. Destroying a running QThread is a
        Qt fatal error (silent abort), so the release only happens from the
        thread's own ``finished`` signal, after a guaranteed ``wait()``.
        """
        self._live_workers.add(worker)
        worker.finished.connect(lambda w=worker: self._release_worker(w))

    def _release_worker(self, worker: Worker) -> None:
        """Drop tracker refs and schedule C++ QThread deletion safely."""
        import warnings

        try:
            if _shiboken_is_valid(worker):
                worker.wait()  # finished has fired; returns once run() unwinds
        except RuntimeError:
            pass
        self._live_workers.discard(worker)
        for attr in (
            "_worker",
            "_update_worker",
            "_mod_update_worker",
            "_launcher_update_worker",
            "_realm_ping_worker",
        ):
            if getattr(self, attr, None) is worker:
                setattr(self, attr, None)
        # Disconnect before deleteLater so queued cross-thread deliveries cannot
        # land on a half-destroyed QThread (Qt aborts with no Python traceback).
        try:
            if _shiboken_is_valid(worker):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    for sig in (
                        worker.finished_ok,
                        worker.failed,
                        worker.status,
                        worker.progress_pct,
                        worker.finished,
                    ):
                        try:
                            sig.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                worker.deleteLater()
        except RuntimeError:
            pass

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
        if getattr(self, "_addon_check_settling", False):
            # Settle intentionally dropped the scan gate so deferred refresh/reveal
            # can flush — do not re-arm _scanning from _checking_addons.
            return
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
        self.update_btn.setEnabled(False)
        self.play_btn.setText("LAUNCHING…")
        remaining_ms = int((self._play_launch_lock_until - time.monotonic()) * 1000) + 1
        self._play_launch_timer.start(max(1, remaining_ms))

    def _release_play_launch_lock(self) -> None:
        self._play_launch_lock_until = 0.0
        self._play_launch_timer.stop()
        if not self._worker_busy():
            self.play_btn.setEnabled(True)
            self.update_btn.setEnabled(True)
            self._refresh_play_button()
            if self.progress.maximum() == 0:
                self._hide_progress_bar()

    def _fail_play_launch(self, status: str) -> None:
        self._play_launch_lock_until = 0.0
        self._play_launch_timer.stop()
        if not self._worker_busy():
            self.play_btn.setEnabled(True)
            self.update_btn.setEnabled(True)
            self._refresh_play_button()
            if self.progress.maximum() == 0:
                self._hide_progress_bar()
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
            self.update_btn.setEnabled(False)
        elif not self._is_play_launch_locked():
            self.play_btn.setEnabled(True)
            self.update_btn.setEnabled(True)
        self._lock_addon_filters(extra_busy=busy)
        if busy:
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate until bytes known
            self.progress.setFormat("")
            self._fit_bottom_progress()
            self._set_busy_status(msg or "Working…")
        else:
            self._hide_progress_bar()
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
        if getattr(self, "_addon_check_settling", False):
            # Settle dropped the scan gate so deferred list work can flush —
            # never call set_checking(True) here (that would re-arm _scanning).
            try:
                self.addons.set_check_busy(True)
            except RuntimeError:
                pass
        else:
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
            self._hide_progress_bar()
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

    def _prompt_addon_toc_renames(
        self, mismatches: list[AddonTocMismatch]
    ) -> list[str]:
        """Prompt for each renameable mismatch. Returns new folder names that were renamed."""
        renamed: list[str] = []
        for item in mismatches:
            if not item.can_rename:
                continue
            if not themed.confirm_addon_toc_rename(
                self, item.current_name, item.toc_name
            ):
                continue
            outcome = rename_addon_folder_to_toc(item.folder, item.toc_stem)
            if outcome.renamed and outcome.new_name:
                renamed.append(outcome.new_name)
                continue
            if outcome.status == "collision":
                themed.warning(
                    self,
                    "Could not rename addon",
                    outcome.detail
                    or (
                        f'Cannot rename "{outcome.old_name}" to "{outcome.new_name}" — '
                        f'a folder named "{outcome.new_name}" already exists.'
                    ),
                )
                continue
            if outcome.status in ("error", "missing"):
                themed.warning(
                    self,
                    "Could not rename addon",
                    outcome.detail or f'Could not rename "{outcome.old_name}".',
                )
        return renamed

    def _maybe_prompt_toc_mismatches(self) -> list[str]:
        """Disk-scan prompts when the setting is on. Guards against reentrant double prompts."""
        if not settings.auto_fix_addon_toc_mismatch():
            return []
        if getattr(self, "_toc_mismatch_prompting", False):
            return []
        if wow_exe_may_be_running(detect_game()):
            return []
        self._toc_mismatch_prompting = True
        try:
            return self._prompt_addon_toc_renames(scan_mismatched_toc_addon_folders())
        finally:
            self._toc_mismatch_prompting = False

    def _flush_pending_toc_mismatch_prompt(self) -> None:
        """Run deferred startup mismatch prompts once the window is visible."""
        if not getattr(self, "_toc_mismatch_prompt_pending", False):
            return
        self._toc_mismatch_prompt_pending = False
        renamed = self._maybe_prompt_toc_mismatches()
        if renamed:
            full_resync()
            self.addons.reset_scan_done()
            self.client.reset_scan_done()
            self.addons.mark_dirty()

    def _maybe_prompt_crash_reporting_opt_in(self) -> None:
        """One-shot first-launch prompt for optional crash/error reporting."""
        from ichalaunch.core import crash_report as cr

        if not cr.should_prompt_crash_reporting_opt_in():
            return
        result = themed.crash_reporting_opt_in_dialog(self)
        if result == themed.DialogResult.Yes:
            cr.enable_crash_reporting_from_opt_in()
            try:
                self.settings_page.refresh()
            except Exception:  # noqa: BLE001
                pass
            return
        # Not now, Don't show again, or dismiss — leave reporting off, never ask again.
        cr.mark_crash_reporting_opt_in_prompted()

    def _maybe_prompt_stock_patch9(self) -> None:
        """Once per session: missing/incomplete official patch-9 (Home-first)."""
        if getattr(self, "_patch9_prompted", False):
            return
        from ichalaunch.mods.stock_patch import (
            configured_game_for_stock_patch9,
            inspect_stock_patch9,
            should_offer_stock_patch9_reacquire,
        )

        # Configured folder only — no nested RavenCraft walk, no nearby search.
        game = configured_game_for_stock_patch9()
        if not game:
            return
        status = inspect_stock_patch9(game)
        if not should_offer_stock_patch9_reacquire(status):
            return
        self._patch9_prompted = True
        try:
            self.client._refresh_patch9_banner()
        except Exception:  # noqa: BLE001
            pass
        result = themed.choice(
            self,
            "Patch-9 missing or incomplete",
            "Patch-9 is missing or incomplete.\n\n"
            "Reacquire the official Data/patch-9.mpq (~500 MB) through the launcher?",
            [
                ("Later", themed.DialogResult.No),
                ("Reacquire", themed.DialogResult.Yes),
            ],
            kind="warning",
        )
        if result == themed.DialogResult.Yes:
            self._reacquire_stock_patch9()

    def _maybe_prompt_high_farclip(self) -> None:
        """Once per session: Config.wtf farclip above the stock 777 cap."""
        from ichalaunch.core.crash_report import reporting_suppressed

        if reporting_suppressed():
            return
        if getattr(self, "_farclip_prompted", False):
            return
        game = detect_game()
        if not game or not has_wow_exe(game):
            return
        if wow_exe_may_be_running(game):
            return
        from ichalaunch.game.config_wtf import farclip_too_high, set_farclip

        found = farclip_too_high(game)
        if found is None:
            return
        self._farclip_prompted = True
        result = themed.choice(
            self,
            "Farclip is set too high",
            (
                f"WTF/Config.wtf has farclip set to {found.display}. "
                "The stock 1.12 maximum is 777.\n\n"
                "Values above 777 can hide world geometry (buildings, terrain, barns).\n\n"
                "Set farclip to 777 now?"
            ),
            [
                ("Leave it", themed.DialogResult.No),
                ("Set to 777", themed.DialogResult.Yes),
            ],
            kind="warning",
        )
        if result != themed.DialogResult.Yes:
            return
        try:
            if set_farclip(game):
                self.status_lbl.setText("Set Config.wtf farclip to 777")
            else:
                self._farclip_prompted = False
        except OSError as exc:
            self._farclip_prompted = False
            themed.error(self, "Could not update Config.wtf", str(exc))

    def _apply_toc_mismatch_prompts(self, result: object) -> object:
        """UI-thread prompts for install mismatches collected on a worker."""
        if isinstance(result, AddonInstallResult):
            pending = list(result.mismatches)
            take_pending_toc_mismatches()
        elif isinstance(result, dict) and result.get("toc_mismatches"):
            pending = list(result.get("toc_mismatches") or [])
            take_pending_toc_mismatches()
        else:
            pending = take_pending_toc_mismatches()
        if not pending:
            return result
        if not settings.auto_fix_addon_toc_mismatch():
            return result
        renamed = self._prompt_addon_toc_renames(pending)
        if isinstance(result, AddonInstallResult):
            return finalize_install_after_toc_renames(result, renamed)
        if renamed:
            from ichalaunch.core.detect import sync_installed_addons_from_disk

            sync_installed_addons_from_disk()
        return result

    def _resync(self, silent: bool = False) -> None:
        if not is_installed():
            if not silent:
                themed.warning(self, "No game", "Set a valid game path first.")
            return
        renamed: list[str] = []
        if settings.auto_fix_addon_toc_mismatch():
            # Avoid modal dialogs during __init__ before the window is shown.
            if silent and not self.isVisible():
                self._toc_mismatch_prompt_pending = True
            else:
                renamed = self._maybe_prompt_toc_mismatches()
                self._toc_mismatch_prompt_pending = False
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
            msg = (
                f"Detected {len(result['addons'])} addon folder(s) "
                "and synced client mod checkboxes."
            )
            if renamed:
                msg += (
                    f"\n\nRenamed {len(renamed)} folder(s) to match the .toc: "
                    + ", ".join(renamed[:8])
                )
                if len(renamed) > 8:
                    msg += f" (+{len(renamed) - 8} more)"
            skipped = result.get("skipped_addons") or []
            if skipped:
                names = ", ".join(skipped[:8])
                more = f" (+{len(skipped) - 8} more)" if len(skipped) > 8 else ""
                msg += (
                    f"\n\nSkipped {len(skipped)} folder(s) whose folder name "
                    f"does not match the .toc: {names}{more}"
                )
            themed.info(self, "Rescan complete", msg)

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
        if _safe_worker_running(self._worker):
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
        self._track_worker(worker)
        worker.start()

    def _pump_addon_queue(self) -> bool:
        """Start the next queued addon job. Returns True if a job was started."""
        if _safe_worker_running(self._worker):
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
        result = self._apply_toc_mismatch_prompts(result)
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
        take_pending_toc_mismatches()
        themed.error(self, "Error", msg)
        if self._pump_addon_queue():
            return
        self._set_busy_ui(False, f"Failed: {msg[:80]}")
        if msg != GITHUB_TOKEN_REJECTED_MSG:
            self._maybe_warn_github_token()

    def _on_play_or_install(self) -> None:
        if is_installed():
            self._play()
        else:
            self._install_or_browse()

    def _check_launcher_update(self, silent: bool = False) -> None:
        """Background check for a newer IchaLaunch GitHub release.

        Silent/startup/periodic checks never touch the bottom progress bar or
        busy PLAY state — only real download/install via ``_apply_launcher_update``.
        """
        if _safe_worker_running(self._launcher_update_worker):
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
                    # Quiet: badge + square update button only; leave status for other work.
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
        self._track_worker(worker)
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
                installed, removed, verify_warns, failures = split_mod_apply_results(done)
                if failures:
                    self._fail_play_launch("Client mod sync failed")
                    themed.error(
                        self,
                        "Could not sync client mods",
                        _client_mod_failure_dialog_body(
                            failures,
                            lead="These client mod changes could not be applied:",
                        ),
                    )
                    maybe_show_superwow_after_mod_failures(self, failures, "sync")
                    return
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
                if verify_warns:
                    title, body = format_mod_verify_warning(verify_warns)
                    themed.warning(self, title, body)
                self._launch_prepared()

            self._busy(f"Syncing client mods ({names})…", worker, on_ok=on_ok)
            return
        self._launch_prepared()

    def _launch_prepared(self) -> None:
        try:
            self._maybe_prompt_high_farclip()
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
            game = detect_game()
            if game and vanillafixes_launch_decision(game) == VF_LAUNCH_ASK:
                if not self._offer_vanillafixes_reinstall_or_anyway():
                    return
                self._show_play_launch_progress("Launching game…")
                proc = launch_game(force_direct=True)
            else:
                self._show_play_launch_progress("Launching game…")
                proc = launch_game()
            if proc is not None and os.name != "nt":
                self._watch_launch(proc)
                return
            self._launch_succeeded()
        except Exception as exc:  # noqa: BLE001
            self._fail_play_launch(f"Launch failed: {str(exc)[:80]}")
            themed.error(self, "Launch failed", str(exc))

    def _launch_succeeded(self) -> None:
        self.status_lbl.setText("Game launched")
        themed.close_open_themed_dialogs(self)
        if settings.get("close_on_launch"):
            self.close()
        elif settings.get("minimize_on_launch"):
            self.showMinimized()

    def _watch_launch(self, proc: subprocess.Popen) -> None:
        """Give the child a moment to fail before calling the launch a success.

        Windows runs the client directly, so Popen returning means it started.
        Elsewhere it runs under umu, which exits non-zero when the Proton build,
        the prefix or the runtime is unusable -- and does so a second or two
        later, with its reasons in a log file rather than on any terminal. The
        window used to close on the line after Popen, so a launch that failed
        looked exactly like one that worked.

        Polled from a timer rather than waited on, because this runs on the GUI
        thread. Nothing is treated as failure except an early non-zero exit.
        """
        from ichalaunch.game.proton import launch_log_tail

        deadline = time.monotonic() + _LAUNCH_GRACE_S
        self._show_play_launch_progress(
            "Launching game… first run may download the Steam Linux Runtime"
        )

        def check() -> None:
            code = proc.poll()
            if code is None:
                if time.monotonic() < deadline:
                    QTimer.singleShot(_LAUNCH_POLL_MS, check)
                else:
                    self._launch_succeeded()
                return
            if code == 0:
                self._launch_succeeded()
                return
            tail = launch_log_tail()
            log.warning("Launch exited early with code %s", code)
            self._fail_play_launch(f"Launch failed (exit code {code})")
            themed.error(
                self,
                "Launch failed",
                f"The game exited immediately, with code {code}.\n\n"
                + (tail or "No output was captured from the launch."),
            )

        QTimer.singleShot(_LAUNCH_POLL_MS, check)

    def _offer_vanillafixes_reinstall_or_anyway(self) -> bool:
        """Prompt when VF is desired but VanillaFixes.exe cannot be used.

        Returns True when the caller should launch WoW.exe anyway.
        Reinstall starts a worker that retries launch; Cancel fails Play.
        """
        result = themed.choice(
            self,
            "VanillaFixes is missing",
            "VanillaFixes is enabled, but VanillaFixes.exe was not found "
            "in the game folder.\n\n"
            "Reinstall VanillaFixes, or launch WoW.exe without it?",
            [
                ("Launch Anyway", themed.DialogResult.No),
                ("Reinstall", themed.DialogResult.Yes),
            ],
            kind="warning",
        )
        if result == themed.DialogResult.Yes:
            self._reinstall_vanillafixes_then_launch()
            return False
        if result == themed.DialogResult.No:
            return True
        self._fail_play_launch("Launch cancelled")
        return False

    def _reinstall_vanillafixes_then_launch(self) -> None:
        mod_id = vanillafixes_reinstall_mod_id()

        def on_ok(_result) -> None:
            self.client.clear_pending_update(mod_id)
            self.status_lbl.setText(f"Reinstalled {mod_id}")
            self._launch_prepared()

        def on_fail(msg: str) -> None:
            self._fail_play_launch(f"VanillaFixes reinstall failed: {msg[:80]}")

        worker = Worker(update_mod, mod_id)
        worker.failed.connect(on_fail)
        self._busy(f"Reinstalling {mod_id}…", worker, on_ok=on_ok)

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
            status_lines = [
                ln
                for ln in lines
                if isinstance(ln, str) and not ln.startswith("~ ")
            ]
            detail = "; ".join(status_lines[:4]) if status_lines else "no changes"
            more = f" (+{len(status_lines) - 4} more)" if len(status_lines) > 4 else ""
            self.status_lbl.setText(f"Client mods applied: {detail}{more}")
            # Per-mod lock/AV failures are tolerated by apply — surface them
            # loudly here so a stuck removal is never silent (nag loop).
            _installed, _removed, verify_warns, failures = split_mod_apply_results(lines)
            if failures:
                themed.error(
                    self,
                    "Some mod changes failed",
                    _client_mod_failure_dialog_body(failures),
                )
                maybe_show_superwow_after_mod_failures(self, failures, "sync")
            elif verify_warns:
                title, body = format_mod_verify_warning(verify_warns)
                themed.warning(self, title, body)

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
            display = result_name or "addon"
            self.status_lbl.setText(f"Installed from GitHub: {display}")
            # Best-effort catalog suggestion — never blocks or undoes install.
            self._maybe_auto_submit_git_import(url, display_name=str(display))

        self._busy(
            "Importing from GitHub…",
            worker,
            on_ok=on_ok,
            queueable=True,
            queue_key=f"github:{url.strip().lower()}",
        )

    def _maybe_auto_submit_git_import(self, url: str, *, display_name: str = "") -> None:
        """Quietly suggest a newly imported repo for the Available catalog."""

        def job():
            from ichalaunch.addons.submit import try_auto_submit_after_git_import

            return try_auto_submit_after_git_import(url)

        worker = Worker(job)

        def on_submit_ok(result) -> None:
            if result is None:
                return  # already in catalog
            if getattr(result, "ok", False):
                base = f"Installed from GitHub: {display_name}" if display_name else "Installed from GitHub"
                self.status_lbl.setText(f"{base} · suggested for catalog")
            else:
                log.info(
                    "Auto catalog submit after git import: %s",
                    getattr(result, "message", result),
                )

        def on_submit_fail(msg: str) -> None:
            log.info("Auto catalog submit after git import failed: %s", msg)

        worker.finished_ok.connect(on_submit_ok)
        worker.failed.connect(on_submit_fail)
        self._track_worker(worker)
        worker.start()

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

        # Row Reinstall (and settings-cog Reinstall) must clear Never Update up front.
        # Install meta also writes never_update=False, but the Installed-row path
        # previously relied only on that — and in-place status patches kept the
        # sticky row._never_update flag, so the UI could stay "Never update"
        # even after a successful reinstall. Clear + persist here (catalog pins
        # like Bagshui are re-stamped by set_addon_never_update).
        self.addons.set_never_update(
            {"folder": str(folder), "name": str(folder)},
            False,
        )

        # Settings cog may pass an explicit fork/version selection; row Reinstall
        # keeps preferring live .git origin / installed meta over catalog fields.
        prefer = bool(entry.get("_prefer_selection"))
        meta = overlay_git_origin(
            str(folder),
            settings.installed_addons.get(folder) or {},
        )
        if prefer:
            tag = str(entry.get("tag") or entry.get("pin_release") or "").strip()
            repo = str(entry.get("repository") or meta.get("repository") or "").strip()
            url = str(entry.get("repo") or entry.get("url") or meta.get("url") or "").strip()
        else:
            tag = str(entry.get("tag") or meta.get("tag") or "").strip()
            repo = str(meta.get("repository") or entry.get("repository") or "").strip()
            url = meta.get("url") or entry.get("repo") or entry.get("url") or ""
        from ichalaunch.addons.gitlab import (
            gitlab_browse_url,
            gitlab_tag_page_url,
            parse_gitlab_url,
        )

        gl = parse_gitlab_url(str(url or ""))
        source = str(meta.get("source") or "").strip().lower()
        if gl or source == "gitlab":
            owner = gl.owner if gl else ""
            name = gl.repo if gl else ""
            if (not owner or not name) and "/" in repo:
                owner, name = repo.split("/", 1)
            if tag and owner and name:
                url = gitlab_tag_page_url(owner, name, tag)
            elif owner and name:
                url = gitlab_browse_url(owner, name)
        elif tag and "/" in repo:
            from ichalaunch.addons.github import github_tag_page_url

            owner, name = repo.split("/", 1)
            url = github_tag_page_url(owner, name, tag)
        elif not url and repo:
            url = f"https://github.com/{repo}"
        if not url:
            themed.warning(self, "Cannot reinstall", f"No GitHub or GitLab URL for {folder}.")
            return

        def on_ok(_result):
            self.addons.clear_pending_update(folder)
            self.status_lbl.setText(f"Reinstalled {folder}")

        install_kwargs: dict = {}
        if prefer and not tag:
            # Explicit "Latest" from settings — do not reuse a prior pin.
            install_kwargs["allow_stored_tag"] = False
        worker = Worker(install_from_github, url, folder, **install_kwargs)
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
            try:
                from ichalaunch.addons.pending_updates import replace_pending_updates_cache

                replace_pending_updates_cache(remaining)
            except Exception:  # noqa: BLE001
                pass
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
        # Gate on the busy flag as well as the QThread: finished_ok clears the
        # worker attribute while UI apply (set_updates / list patch) can still be
        # running. A second click in that window has caused silent Qt aborts.
        if (
            self._checking_addons
            or getattr(self, "_addon_check_settling", False)
            or _safe_worker_running(self._update_worker)
        ):
            if not silent:
                self.status_lbl.setText("Update check already running…")
            return
        # Manual AND silent/startup must wait until lists are idle. Starting a
        # worker (or applying set_updates) into a half-built / mid-reveal list
        # aborts Qt with no Python traceback.
        try:
            list_ready = bool(self.addons.update_check_ui_ready())
        except RuntimeError:
            list_ready = False
        if not force and not list_ready:
            if silent or periodic:
                self._arm_silent_addon_check_retry(periodic=periodic)
                return
            self.status_lbl.setText("Addons list still loading…")
            return

        # Cooldown gate for automatic (silent/periodic) checks: skip if a scan ran
        # within the hardcoded 15-minute refresh. Manual checks arrive with
        # silent=False and always run; force=True explicitly bypasses the gate.
        if (silent or periodic) and not force and recently_checked_addon_updates():
            log.info(
                "Addon scan skipped — last scan %s ago (refresh %d min)",
                _format_minutes_since("last_addon_update_check"),
                settings.auto_scan_cooldown_minutes(),
            )
            return

        self._silent_addon_check_retry_armed = False
        self._silent_addon_check_retries = 0
        self._addon_check_status = "Checking addon updates…"
        self.status_lbl.setText("Checking addon updates…")
        self._checking_addons = True
        self._addon_check_settling = False
        self._check_addon_pct = 0
        self.addons.set_scanning(True)
        self._refresh_check_loading()
        worker = Worker(check_addon_updates, respect_cooldown=False)

        def done(result):
            # Keep UI apply off the critical QThread teardown path as much as
            # possible: never let an exception escape a queued slot (that can
            # abort Qt), and serialize catalog reload through list work.
            # Keep Check locked until apply + deferred refresh/reveal finish.
            settle_tries = 0
            apply_tries = 0
            apply_phase = 0
            catalog_done = False
            catalog_refreshed = False
            if isinstance(result, AddonUpdateCheckResult):
                catalog_refreshed = bool(result.catalog_refreshed)

            def _addons_list_mutating() -> bool:
                """Any active or queued list mutation — no carve-outs."""
                try:
                    if self.addons.lists_mutating():
                        return True
                    # Visible Addons with unrevealed lists is still unsafe for apply.
                    if self.addons._page_is_live() and self.addons._lists_need_reveal():
                        return True
                    return False
                except RuntimeError:
                    return True

            def _drop_scan_gate_keep_check_locked() -> None:
                """Allow deferred refresh/reveal to flush; keep Check disabled."""
                self._addon_check_settling = True
                try:
                    # Keep button busy without re-arming the scan gate.
                    self.addons.set_check_busy(True)
                    self.addons.set_scanning(False)
                except RuntimeError:
                    pass

            def _finish_check() -> None:
                self._addon_check_settling = False
                self._checking_addons = False
                self._refresh_check_loading()
                self._update_worker = None
                self._refresh_nav_badges()

            def _settle() -> None:
                nonlocal settle_tries
                settle_tries += 1
                # Scan gate is already down (apply phase 0). Wait until deferred
                # rebuild/reveal/patch are fully idle before re-enabling Check.
                if settle_tries < 40 and _addons_list_mutating():
                    QTimer.singleShot(50, _settle)
                    return
                _finish_check()

            def _apply() -> None:
                nonlocal apply_tries, apply_phase, catalog_done
                apply_tries += 1
                # Phase 0: drop scan gate so mid-scan-open refresh/reveal can
                # finish BEFORE set_updates touches rows.
                if apply_phase == 0:
                    _drop_scan_gate_keep_check_locked()
                    apply_phase = 1
                if apply_phase == 1:
                    if apply_tries < 80 and _addons_list_mutating():
                        QTimer.singleShot(50, _apply)
                        return
                    apply_phase = 2
                if apply_phase == 2:
                    # Catalog fetch must NOT clear()/rebuild live lists during
                    # Check Updates apply — that overlapped set_updates/reveal
                    # and aborted Qt. Cache-only ingest; Available refreshes later.
                    if catalog_refreshed and not catalog_done:
                        catalog_done = True
                        try:
                            self.addons.ingest_catalog_update()
                        except RuntimeError:
                            pass
                    if apply_tries < 80 and _addons_list_mutating():
                        QTimer.singleShot(50, _apply)
                        return
                    apply_phase = 3
                updates: list = []
                status = None
                try:
                    self._addon_check_status = ""
                    self._check_addon_pct = 100
                    apply_updates = True
                    if isinstance(result, AddonUpdateCheckResult):
                        updates = list(result.updates or [])
                        status = result.status_message
                        # Failed / skipped scans must not wipe last-known pending.
                        apply_updates = (
                            not result.skipped_recent
                            and status != UPDATE_CATALOG_UNAVAILABLE
                        )
                    else:
                        updates = list(result or [])
                    if apply_updates:
                        self.addons.set_updates(updates)
                    if not self._checking_mods:
                        if status:
                            self.status_lbl.setText(status)
                        elif updates:
                            self.status_lbl.setText(
                                f"{len(updates)} addon update(s) available"
                            )
                        else:
                            self.status_lbl.setText("Addons up to date")
                    log.info(
                        "Addon update check applied (%d update(s))",
                        len(updates),
                    )
                except Exception:  # noqa: BLE001
                    log.exception("Addon update-check UI apply failed")
                    self._addon_check_status = ""
                QTimer.singleShot(50, _settle)

            _apply()
        def fail(msg: str):
            self._addon_check_status = ""
            if not self._checking_mods:
                self.status_lbl.setText(f"Update check failed: {msg[:80]}")
            settle_tries = 0

            def _settle() -> None:
                nonlocal settle_tries
                settle_tries += 1
                if not getattr(self, "_addon_check_settling", False):
                    self._addon_check_settling = True
                    try:
                        self.addons.set_check_busy(True)
                        self.addons.set_scanning(False)
                    except RuntimeError:
                        pass
                try:
                    mutating = bool(self.addons.lists_mutating())
                except RuntimeError:
                    mutating = False
                if settle_tries < 40 and mutating:
                    QTimer.singleShot(50, _settle)
                    return
                self._addon_check_settling = False
                self._checking_addons = False
                self._refresh_check_loading()
                self._update_worker = None
                self._refresh_nav_badges()

            QTimer.singleShot(50, _settle)

        worker.status.connect(self._on_addon_check_status)
        worker.progress_pct.connect(lambda p: self._on_check_progress_pct("addons", p))
        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._update_worker = worker
        self._track_worker(worker)
        worker.start()

    def _arm_silent_addon_check_retry(self, *, periodic: bool) -> None:
        """Defer startup/periodic addon check until lists are idle (no busy-loop)."""
        if self._silent_addon_check_retry_armed:
            return
        retries = int(getattr(self, "_silent_addon_check_retries", 0))
        if retries >= 40:
            log.info("Silent addon check deferred too long — giving up until next trigger")
            self._silent_addon_check_retries = 0
            return
        self._silent_addon_check_retry_armed = True
        self._silent_addon_check_retries = retries + 1

        def _retry() -> None:
            self._silent_addon_check_retry_armed = False
            if self._checking_addons or getattr(self, "_addon_check_settling", False):
                return
            if _safe_worker_running(self._update_worker):
                return
            try:
                ready = bool(self.addons.update_check_ui_ready())
            except RuntimeError:
                ready = False
            if not ready:
                # Keep trying briefly — preload/reveal usually finishes quickly.
                self._arm_silent_addon_check_retry(periodic=periodic)
                return
            self._silent_addon_check_retries = 0
            self._check_updates(silent=True, periodic=periodic)

        QTimer.singleShot(250, _retry)
    def _check_mod_updates(self, silent: bool = False, periodic: bool = False, force: bool = False) -> None:
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._checking_mods or _safe_worker_running(self._mod_update_worker):
            if not silent:
                self.status_lbl.setText("Client mod update check already running…")
            return
        if (silent or periodic) and not force and recently_checked_mod_updates():
            log.info(
                "Client mod scan skipped — last scan %s ago (refresh %d min)",
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
            self._check_mod_pct = 100
            try:
                if isinstance(result, ModUpdateCheckResult):
                    if result.skipped_recent:
                        return
                    updates = result.updates
                    status = result.status_message
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
            except Exception:  # noqa: BLE001
                log.exception("Client mod update-check UI apply failed")
            finally:
                def _settle() -> None:
                    self._checking_mods = False
                    self._refresh_check_loading()
                    self._mod_update_worker = None
                    self._refresh_nav_badges()

                QTimer.singleShot(50, _settle)

        def fail(msg: str):
            if not self._checking_addons:
                self.status_lbl.setText(f"Client mod check failed: {msg[:80]}")

            def _settle() -> None:
                self._checking_mods = False
                self._refresh_check_loading()
                self._mod_update_worker = None
                self._refresh_nav_badges()

            QTimer.singleShot(50, _settle)

        worker.progress_pct.connect(lambda p: self._on_check_progress_pct("mods", p))
        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._mod_update_worker = worker
        self._track_worker(worker)
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

        def on_fail(msg: str) -> None:
            if mod_id == "superwow":
                maybe_show_superwow_after_mod_failures(self, [msg], "install")

        worker = Worker(update_mod, mod_id)
        worker.failed.connect(on_fail)
        self._busy(f"Reinstalling {mod_id}…", worker, on_ok=on_ok)

    def _reacquire_stock_patch9(self) -> None:
        """User-triggered download of official Data/patch-9.mpq (~500 MB)."""
        from ichalaunch.mods.stock_patch import reacquire_stock_patch9

        if not is_installed():
            themed.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(_result):
            self.status_lbl.setText("Reacquired official patch-9")

        worker = Worker(reacquire_stock_patch9, force=True)
        self._busy("Reacquiring patch-9…", worker, on_ok=on_ok)

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
