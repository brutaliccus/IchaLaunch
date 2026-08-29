"""Home / status page (Play lives on the bottom bar)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.core.paths import theme_file
from ichalaunch.game.launcher import detect_game, is_installed
from ichalaunch.mods.installer import detect_actual_state, load_mod_catalog
from ichalaunch.ui.widgets.gradient_label import GradientLabel
from ichalaunch.ui.widgets.common import SpellbookPageButton, open_url_in_browser
from ichalaunch.ui.widgets.gallery_dots import GalleryDots
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton
from ichalaunch.ui.widgets.mods_forest_bg import HomeModsCard
from ichalaunch.ui.widgets.talent_bg import TalentFrameBackground
from ichalaunch.ui.widgets.title_lockup import TitleLockup

log = logging.getLogger("ichalaunch")

CATEGORY_ORDER = [
    "Performance / Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
    "Advanced",
]

# Wide enough for three title-case glue buttons ("Bug Report" on one line)
# between the left metal rail and the home art — uses the leftover strip.
DRAWER_MIN_W = 320
DRAWER_MAX_W = 320
# Near-touch mods drawer / right content edge; feather softens into the gap.
_SIDE_PAD_PX = 16
# Gap between mods Card bottom border and NavBottomBanner diamond strip.
_MODS_BANNER_GAP_PX = 16
# Gap above the Home link row — clear the metal top rail (20px at ContentPanel y=0)
# plus a few px so the buttons sit below the top art, not under it.
_HOME_TOP_PAD = 26
# Standard toolbar chrome (same plate / type as Rescan Disk), equal widths.
_HOME_LINK_H = GLUE_BTN_H
_HOME_LINK_GAP = 8
_HOME_LINK_W = (DRAWER_MIN_W - 2 * _HOME_LINK_GAP) // 3
_HOME_LINK_ROW_W = _HOME_LINK_W * 3 + 2 * _HOME_LINK_GAP

REGISTER_URL = "https://ravencraft.io/register"
DATABASE_URL = "https://database.ravencraft.io/"
BUG_REPORT_URL = "https://ravencraft.io/bug-tracker"
# Small inset from ContentPanel top / side when filling the brand rect.
# Must clear the purple shelf stroke (1px) so art never paints into the tab strip.
_ART_TOP_PAD_PX = 8
_ART_SIDE_INSET_PX = 2
_ART_BOTTOM_INSET_PX = 0
# Hide the hard art / black fringe under the grey banner bar (not into spike valleys).
# A few extra px closes the visible gap so rotating art meets the nav banner.
_ART_BANNER_TUCK_PX = 12
# Gap between a page arrow and the dots it hugs on the indicator row.
_ART_ARROW_DOTS_GAP_PX = 4
# The indicator row spans this fraction of the artwork's width rather than a
# fixed gap, so it stays right if the window or the art is ever resized. Short
# of 1.0 on purpose: at the extreme edges the arrows would sit on the frame's
# own ornate border.
_ART_ROW_SPAN = 0.92
# Gap between the position row and the MoA wordmark it sits above.
_ART_DOTS_GAP_PX = 6
# MoA wordmark prefer width, centered along the art bottom.
_MOA_ART_LOGO_W = 190  # ~5% under prior 200px prefer width
# Pad from art bottom for the MoA wordmark.
_BRAND_BOTTOM_PAD_PX = 8
# Extra MoA sit-down toward art bottom (above diamond strip).
_MOA_BOTTOM_NUDGE_PX = 4


def _home_link_button(text: str, url: str, tip: str) -> GluePanelButton:
    """Equal-sized Home-row glue button that opens ``url`` in the browser."""
    btn = GluePanelButton(text, width=_HOME_LINK_W, height=_HOME_LINK_H)
    btn.setToolTip(tip)
    btn.clicked.connect(lambda _checked=False, u=url: open_url_in_browser(u))
    return btn


class HomePage(QWidget):
    play_clicked = Signal()
    install_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#HomePage, HomePage { background: transparent; }")
        self.setObjectName("HomePage")
        self._flush_logged = False
        self._overlay_filters_installed = False
        self._overlays_on_root = False

        page = QVBoxLayout(self)
        page.setSpacing(0)
        page.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        self._body = body
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body.setStyleSheet("background: transparent;")
        root = QHBoxLayout(body)
        root.setSpacing(24)
        # No body top pad — only the left drawer insets the Home links (right stays flush).
        root.setContentsMargins(28, 0, 28, 0)

        # --- Left: fixed-ish side drawer of categorized mods ---
        left = QWidget()
        left.setObjectName("HomeModsDrawer")
        left.setMinimumWidth(DRAWER_MIN_W)
        left.setMaximumWidth(DRAWER_MAX_W)
        left.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._mods_drawer = left
        left_l = QVBoxLayout(left)
        # Top = Home-link clearance; bottom lifts Card off NavBottomBanner.
        left_l.setContentsMargins(0, _HOME_TOP_PAD, 0, _MODS_BANNER_GAP_PX)
        left_l.setSpacing(10)

        links = QWidget()
        links.setObjectName("HomeLinkRow")
        links.setFixedWidth(_HOME_LINK_ROW_W)
        self._home_link_row = links
        links_l = QHBoxLayout(links)
        links_l.setContentsMargins(0, 0, 0, 0)
        links_l.setSpacing(_HOME_LINK_GAP)

        self.register_btn = _home_link_button(
            "Register", REGISTER_URL, "Create a RavenCraft account"
        )
        self.database_btn = _home_link_button(
            "Database", DATABASE_URL, "Open the RavenCraft database"
        )
        self.bug_report_btn = _home_link_button(
            "Bug Report", BUG_REPORT_URL, "Report a RavenCraft bug"
        )
        for btn in (self.register_btn, self.database_btn, self.bug_report_btn):
            links_l.addWidget(btn)
        left_l.addWidget(links, 0, Qt.AlignmentFlag.AlignHCenter)

        self.summary = HomeModsCard()
        # Symmetric inset = Zaeya frame rail so the list and the 12px metal-v
        # scrollbar sit inside the picture frame rather than under its right stem.
        self.summary.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Reserve the 12px metal-v bar so category names are not painted
        # under it. Horizontal scrolling is off, so overrun is clipped.
        scroll.setViewportMargins(0, 0, 14, 0)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll_host = QWidget()
        scroll_host.setObjectName("HomeModsHost")
        self.summary_host = QVBoxLayout(scroll_host)
        self.summary_host.setContentsMargins(6, 2, 6, 8)
        self.summary_host.setSpacing(12)
        self.summary_host.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(scroll_host)
        self.summary.body.addWidget(scroll)
        left_l.addWidget(self.summary, 1)

        # --- Right: empty brand spacer (art/logo live on Root overlay) ---
        right = QWidget()
        right.setObjectName("HomeBrandPane")
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._brand_pane = right

        root.addWidget(left, 0)
        root.addWidget(right, 1)

        page.addWidget(body, 1)

        # Created as HomePage children; reparented to MainWindow Root so art can
        # share coordinates with NavBottomBanner (cousin of ContentPanel).
        self.talent_bg = TalentFrameBackground(self)
        self.talent_bg.frame_changed.connect(self._sync_brand_layout)

        # Page arrows hug the dots row. Disabled spellbook glyphs sit on the
        # art wash at 80% idle / 100% hover (Addons catalog keeps gold Up/Down).
        self.art_prev = SpellbookPageButton("prev", self, art="up")
        self.art_next = SpellbookPageButton("next", self, art="up")
        self.art_prev.setToolTip("Previous")
        self.art_next.setToolTip("Next")
        self.art_prev.clicked.connect(lambda: self.talent_bg.step(-1))
        self.art_next.clicked.connect(lambda: self.talent_bg.step(1))

        self.art_dots = GalleryDots(self)
        # turn_started fires when the turn begins, frame_changed when it lands
        # and when the manifest reloads with a different slide count.
        self.talent_bg.turn_started.connect(self._sync_gallery_dots)
        self.talent_bg.frame_changed.connect(self._sync_gallery_dots)
        self.art_dots.dot_clicked.connect(self.talent_bg.go_to)

        self.logo = QLabel(self)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._logo_src: QPixmap | None = None
        self._load_logo()

        right.installEventFilter(self)
        body.installEventFilter(self)
        left.installEventFilter(self)

        self.refresh()
        self._sync_brand_layout()

    def _logo_height(self) -> int:
        pm = self.logo.pixmap()
        if pm is not None and not pm.isNull():
            return pm.height()
        return max(self.logo.sizeHint().height(), self.logo.height(), 40)

    def _fit_logo(self, max_h: int, prefer_w: int = _MOA_ART_LOGO_W) -> tuple[int, int]:
        """Scale MoA wordmark for the centered art-bottom row."""
        src = self._logo_src
        if src is None or src.isNull():
            h = self._logo_height()
            w = max(self.logo.width(), self.logo.sizeHint().width(), 1)
            return w, h
        scaled = src.scaledToWidth(prefer_w, Qt.TransformationMode.SmoothTransformation)
        if scaled.height() > max_h > 0:
            scaled = src.scaledToHeight(max_h, Qt.TransformationMode.SmoothTransformation)
        self.logo.setPixmap(scaled)
        self.logo.setFixedSize(scaled.size())
        return scaled.width(), scaled.height()

    def _overlay_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.talent_bg,
            self.logo,
            self.art_prev,
            self.art_next,
            self.art_dots,
        )

    def _overlay_host(self) -> QWidget | None:
        """MainWindow Root — same parent tree as NavBottomBanner."""
        win = self.window()
        if win is None:
            return None
        root = win.centralWidget()
        return root if isinstance(root, QWidget) else None

    def _nav_bottom_banner(self) -> QWidget | None:
        win = self.window()
        banner = getattr(win, "_nav_bottom_banner", None)
        if isinstance(banner, QWidget):
            return banner
        return None

    def _content_panel(self) -> QWidget | None:
        win = self.window()
        panel = getattr(win, "_content_panel", None)
        if isinstance(panel, QWidget):
            return panel
        return None

    def _ensure_overlays_on_root(self) -> QWidget | None:
        """Reparent art/logo onto Root so flush uses banner.y() directly."""
        host = self._overlay_host()
        if host is None:
            return None
        for w in self._overlay_widgets():
            if w.parent() is not host:
                w.setParent(host)
                self._overlays_on_root = True
        if not self._overlay_filters_installed:
            host.installEventFilter(self)
            banner = self._nav_bottom_banner()
            if banner is not None:
                banner.installEventFilter(self)
            panel = self._content_panel()
            if panel is not None:
                panel.installEventFilter(self)
            self._overlay_filters_installed = True
        return host

    def _home_overlays_active(self) -> bool:
        """True only while HOME is the visible stack page."""
        if not self.isVisible():
            return False
        win = self.window()
        stack = getattr(win, "stack", None)
        if stack is not None and stack.currentWidget() is not self:
            return False
        return True

    def _gallery_dots_max_width(self, art_width: int) -> int:
        """Dots share the indicator row with the page arrows; leave them room.

        Measured against the SPAN the row now occupies rather than the whole
        frame, so the dots spread into the space the arrows were freed from
        without ever reaching them.
        """
        arrow = self.art_prev.width() or GLUE_BTN_H
        span = int(art_width * _ART_ROW_SPAN)
        return max(1, span - 2 * arrow - 4 * _ART_ARROW_DOTS_GAP_PX)

    def _sync_gallery_dots(self) -> None:
        dots = getattr(self, "art_dots", None)
        if dots is None or not dots.isVisible():
            return
        dots.set_state(
            self.talent_bg.slide_count(),
            self.talent_bg.display_index(),
            self._gallery_dots_max_width(max(1, self.talent_bg.width())),
        )

    def _set_chrome_visible(self, visible: bool) -> None:
        self.talent_bg.setVisible(visible)
        self.logo.setVisible(visible)
        pageable = visible and self.talent_bg.slide_count() > 1
        self.art_prev.setVisible(pageable)
        self.art_next.setVisible(pageable)
        self.art_dots.setVisible(pageable)
        banner = self._nav_bottom_banner()
        if banner is not None:
            banner.update()

    def _sync_brand_layout(self) -> None:
        """Fill brand rect with official art; MoA centered along art bottom.

        Overlays live on MainWindow Root (not in any VBox). Art fills the brand
        column (small side/top pads), bottom flush to the diamond strip — available
        rect wins over any aspect clamp (paint uses KeepAspectRatioByExpanding).
        MoA sits centered at the art bottom (same slot the countdown used).
        """
        mods = getattr(self, "_mods_drawer", None)
        if mods is None or not hasattr(self, "talent_bg"):
            return

        if not self._home_overlays_active():
            self._set_chrome_visible(False)
            return

        host = self._ensure_overlays_on_root()
        if host is None or host.width() <= 0 or host.height() <= 0:
            return

        side_pad = _SIDE_PAD_PX
        bottom_pad = _BRAND_BOTTOM_PAD_PX

        banner = self._nav_bottom_banner()
        panel = self._content_panel()
        win = self.window()
        rc = getattr(win, "_rc_logo", None)
        bottom_bar = getattr(win, "_bottom_bar", None)

        # Layout flush = diamond-strip TOP. Paint tucks only to the PNG midline
        # (under the grey bar) so art never pokes out through the spikes.
        banner_mid: int | None = None
        if banner is not None and banner.isVisible():
            banner_top = banner.mapTo(host, QPoint(0, 0)).y()
            banner_mid = banner_top + max(1, banner.height() // 2)
            art_bottom = banner_top
        elif panel is not None:
            art_bottom = panel.mapTo(host, QPoint(0, panel.height())).y()
        else:
            art_bottom = host.height()

        # Keep art inside the folder body (below top tabs / purple shelf stroke).
        if panel is not None:
            content_top = panel.mapTo(host, QPoint(0, 0)).y()
            content_right = panel.mapTo(host, QPoint(panel.width(), 0)).x()
            content_left = panel.mapTo(host, QPoint(0, 0)).x()
        else:
            content_top = 0
            content_right = host.width()
            content_left = 0

        if art_bottom <= content_top:
            return

        # Interior of purple stroke (ContentPanel inset), not the stroke itself.
        interior_top = content_top + _ART_TOP_PAD_PX
        interior_left = content_left + _ART_SIDE_INSET_PX
        interior_right = content_right - _ART_SIDE_INSET_PX
        interior_bottom = art_bottom - _ART_BOTTOM_INSET_PX

        mods_right = mods.mapTo(host, QPoint(mods.width(), 0)).x()
        art_left = max(interior_left, mods_right + side_pad)
        art_right = min(interior_right, content_right - side_pad)
        avail_w = art_right - art_left
        if avail_w < 64:
            art_left = interior_left + side_pad
            art_right = interior_right - side_pad
            avail_w = max(64, art_right - art_left)

        # Fill available brand rect (priority over prior 16:9 clamp).
        art_x = art_left
        art_y = interior_top
        art_w = max(64, avail_w)
        layout_h = max(64, interior_bottom - art_y)
        if art_y + layout_h > interior_bottom:
            layout_h = max(64, interior_bottom - art_y)
        # MoA uses layout_h (ends at banner top). talent_bg tucks a few
        # px under the grey bar so the hard/black art edge is hidden.
        paint_h = layout_h
        if banner is not None and banner.isVisible():
            tuck = _ART_BANNER_TUCK_PX
            if banner_mid is not None:
                tuck = min(tuck, max(0, banner_mid - art_bottom))
            paint_h = layout_h + tuck

        self.talent_bg.set_frame(art_x, art_y, art_w, paint_h)
        art = QRect(art_x, art_y, art_w, layout_h)

        # --- MoA centered along art BOTTOM (above diamond strip) ---
        max_logo_h = max(40, art.height() // 4)
        prefer_w = min(_MOA_ART_LOGO_W, max(120, art.width() // 4))
        logo_w, logo_h = self._fit_logo(max_h=max_logo_h, prefer_w=prefer_w)

        logo_x = art.x() + (art.width() - logo_w) // 2
        logo_y = art.y() + art.height() - logo_h - max(2, bottom_pad - _MOA_BOTTOM_NUDGE_PX)
        if logo_y < art.y():
            logo_y = art.y()
        self.logo.setGeometry(logo_x, logo_y, logo_w, logo_h)

        # --- indicator row above the MoA wordmark: [ < ]  • • • •  [ > ] ---
        arrow = self.art_prev.width() or GLUE_BTN_H
        gap = _ART_ARROW_DOTS_GAP_PX
        self.art_dots.set_state(
            self.talent_bg.slide_count(),
            self.talent_bg.display_index(),
            self._gallery_dots_max_width(art.width()),
        )
        dots_w = self.art_dots.width()
        dots_h = self.art_dots.height()
        row_h = max(arrow, dots_h)
        # Arrows pushed out to the ends of the span, dots centred between them.
        row_w = max(arrow * 2 + dots_w + gap * 2, int(art.width() * _ART_ROW_SPAN))
        row_x = art.x() + (art.width() - row_w) // 2
        row_y = logo_y - row_h - _ART_DOTS_GAP_PX
        # Arrows pinned to the ends of the span, dots centred in the gap between
        # them. Chaining each element off the previous one is what kept the whole
        # row bunched in the middle however wide the span was.
        self.art_prev.move(row_x, row_y + (row_h - arrow) // 2)
        self.art_next.move(row_x + row_w - arrow, row_y + (row_h - arrow) // 2)
        self.art_dots.move(
            row_x + (row_w - dots_w) // 2, row_y + (row_h - dots_h) // 2
        )

        self._set_chrome_visible(True)

        # Z-order (back → front on Root):
        #   bottom mist → under-banner fill → art → MoA → metal → portrait →
        #   banner → −/X → RC crest
        # Art above underfill is handled in MainWindow._position_frame_stroke so
        # banner-tuck shows through the PNG top pad. Do not leave bottom_bar last.
        if isinstance(bottom_bar, QWidget):
            bottom_bar.raise_()
        if banner is not None:
            banner.update()
        pos_frame = getattr(win, "_position_frame_stroke", None)
        if callable(pos_frame):
            pos_frame()
        else:
            under = getattr(win, "_banner_underfill", None)
            if isinstance(under, QWidget):
                under.raise_()
            self.talent_bg.raise_()
            self.logo.raise_()
            stroke = getattr(win, "_frame_stroke", None)
            if isinstance(stroke, QWidget):
                stroke.raise_()
            portrait = getattr(win, "_portrait_frame", None)
            if isinstance(portrait, QWidget):
                portrait.raise_()
            if banner is not None:
                banner.raise_()
            pos_chrome = getattr(win, "_position_chrome_buttons", None)
            if callable(pos_chrome):
                pos_chrome()
            else:
                for name in ("_btn_minimize", "_btn_close"):
                    btn = getattr(win, name, None)
                    if isinstance(btn, QWidget):
                        btn.raise_()
        if isinstance(rc, QWidget):
            rc.raise_()

        self.art_prev.raise_()
        self.art_next.raise_()
        self.art_dots.raise_()

        art_bottom_now = art.y() + art.height()
        gap = art_bottom - art_bottom_now
        if not self._flush_logged and art.height() > 0:
            self._flush_logged = True
            banner_pt = (
                banner.mapTo(host, QPoint(0, 0)) if banner is not None else None
            )
            log.info(
                "HOME art flush: art_bottom=%s banner_top=%s gap=%s art=%sx%s@%s,%s",
                art_bottom,
                banner_pt.y() if banner_pt is not None else None,
                gap,
                art.width(),
                art.height(),
                art.x(),
                art.y(),
            )

    def eventFilter(self, obj, event):  # noqa: ANN001
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            tracked = (
                getattr(self, "_brand_pane", None),
                getattr(self, "_body", None),
                getattr(self, "_mods_drawer", None),
                self._overlay_host(),
                self._nav_bottom_banner(),
                self._content_panel(),
            )
            if obj in tracked:
                self._sync_brand_layout()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._sync_brand_layout()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._sync_brand_layout()
        # Patch-9 is game-breaking when missing — prompt on Home (first surface).
        QTimer.singleShot(0, self._request_stock_patch9_prompt)
        QTimer.singleShot(0, self._request_high_farclip_prompt)

    def _request_stock_patch9_prompt(self) -> None:
        if not self._home_overlays_active():
            return
        win = self.window()
        fn = getattr(win, "_maybe_prompt_stock_patch9", None)
        if callable(fn):
            fn()

    def _request_high_farclip_prompt(self) -> None:
        if not self._home_overlays_active():
            return
        win = self.window()
        fn = getattr(win, "_maybe_prompt_high_farclip", None)
        if callable(fn):
            fn()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        super().hideEvent(event)
        self._set_chrome_visible(False)

    def _load_logo(self) -> None:
        path = theme_file("moa_logo.png")
        if path.exists():
            pix = QPixmap(str(path))
            if not pix.isNull():
                self._logo_src = pix
                scaled = pix.scaledToWidth(
                    _MOA_ART_LOGO_W, Qt.TransformationMode.SmoothTransformation
                )
                self.logo.setPixmap(scaled)
                self.logo.setFixedSize(scaled.size())
                return
        self._logo_src = None
        self.logo.setText("Mysteries of Azeroth")
        self.logo.setObjectName("Brand")

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        """No-op — update-check progress lives on the bottom bar (MainWindow)."""
        return

    def _clear_summary(self) -> None:
        while self.summary_host.count():
            item = self.summary_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    child = lay.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def _add_category_block(self, category: str, names: list[str]) -> None:
        block = QWidget()
        block_l = QVBoxLayout(block)
        block_l.setContentsMargins(0, 0, 0, 0)
        block_l.setSpacing(4)

        # The count used to sit on its own line under the heading, which left a
        # small number orphaned in the middle of a wide band, saying little and
        # asking for its own typographic style to say it. It now rides on the
        # heading's row, right aligned inside the same band, so the band carries
        # one line of information instead of two and the number reads as a
        # property of the category rather than as a second heading.
        # QFrame rather than QWidget: a bare QWidget does not paint a stylesheet
        # background or border, which silently dropped the band and left the
        # categories separated by colour alone again.
        heading_row = QFrame()
        heading_row.setObjectName("HomeModCategoryRow")
        # A plain QWidget does not paint a stylesheet background or border unless
        # it is told to. Without this the band and its rules are silently dropped
        # and the categories go back to being separated by colour alone.
        heading_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row_l = QHBoxLayout(heading_row)
        row_l.setContentsMargins(8, 3, 8, 3)
        row_l.setSpacing(8)

        # Gradient rather than flat gold: the site clips a vertical ramp to its
        # heading glyphs, and QSS cannot express that. See GradientLabel.
        title = GradientLabel(category)
        title.setObjectName("HomeModCategory")
        # Kept on: the drawer is narrow, and a non-wrapping QLabel reports its
        # full text width as its MINIMUM, which is what dragged the old display
        # face out under the scrollbar. At this size nothing needs to wrap, but
        # the guard costs nothing and a longer category name later will not clip.
        title.setWordWrap(True)
        row_l.addWidget(title, 1)

        count = QLabel(f"{len(names)} mod" if len(names) == 1 else f"{len(names)} mods")
        count.setObjectName("HomeModCategoryCount")
        row_l.addWidget(count, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        block_l.addWidget(heading_row)

        for name in names:
            item = QLabel(name)
            item.setObjectName("HomeModItem")
            item.setWordWrap(True)
            block_l.addWidget(item)

        self.summary_host.addWidget(block)

    def refresh(self) -> None:
        installed = is_installed()
        self._clear_summary()

        if installed:
            game = detect_game()
            actual = detect_actual_state(game) if game else {}
            catalog = load_mod_catalog()
            by_id = {m["id"]: m for m in catalog}
            enabled_ids = [mid for mid, on in actual.items() if on]

            if not enabled_ids:
                empty = QLabel("No client mods detected on disk")
                empty.setObjectName("Muted")
                empty.setWordWrap(True)
                self.summary_host.addWidget(empty)
            else:
                total = len(enabled_ids)
                hdr = TitleLockup(
                    "Installed client mods",
                    f"{total} enabled" if total != 1 else "1 enabled",
                )
                # SectionTitle is 21px. Cinzel's advance (258) is 4px wider
                # than this framed drawer after rails + scrollbar reserve +
                # host pad (254), so the parent clips the final s. 0.5pt at
                # 96dpi is 20px — and it must stay a pixel size: GradientLabel
                # fits from pixelSize and treats pointSizeF as 16px.
                hdr.title.setStyleSheet("font-size: 20px;")
                self.summary_host.addWidget(hdr)

                by_cat: dict[str, list[str]] = {}
                for mid in enabled_ids:
                    mod = by_id.get(mid) or {}
                    cat = mod.get("category") or "Other"
                    name = mod.get("name") or mid.replace("_", " ").title()
                    by_cat.setdefault(cat, []).append(name)

                cats = [c for c in CATEGORY_ORDER if c in by_cat] + [
                    c for c in by_cat if c not in CATEGORY_ORDER
                ]
                for cat in cats:
                    names = sorted(by_cat[cat], key=str.lower)
                    self._add_category_block(cat, names)

            self.summary_host.addStretch(1)
        else:
            tip = QLabel(
                "Ravencraft uses the Turtle 1.18 client.<br>"
                "Click <b>INSTALL</b> to choose a game home folder. Your browser will open "
                "Gofile — click Download for <b>twmoa_1181.zip</b> (a VPN may be required). "
                "The launcher grabs that file and extracts it, "
                "or open <b>Settings</b> to point at an existing install."
            )
            tip.setWordWrap(True)
            tip.setObjectName("Muted")
            self.summary_host.addWidget(tip)
            self.summary_host.addStretch(1)

        self._sync_brand_layout()
