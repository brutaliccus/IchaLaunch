"""Home / status page (Play lives on the bottom bar)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
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
from ichalaunch.ui.widgets.common import open_url_in_browser
from ichalaunch.ui.widgets.countdown import LaunchCountdown
from ichalaunch.ui.widgets.launch_button import LaunchButton
from ichalaunch.ui.widgets.mods_forest_bg import HomeModsCard
from ichalaunch.ui.widgets.talent_bg import TalentFrameBackground

log = logging.getLogger("ichalaunch")

CATEGORY_ORDER = [
    "Performance & Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
]

DRAWER_MIN_W = 260
DRAWER_MAX_W = 320
# Near-touch mods drawer / right content edge; feather softens into the gap.
_SIDE_PAD_PX = 16
# Gap between mods Card bottom border and NavBottomBanner diamond strip.
_MODS_BANNER_GAP_PX = 16
# ContentPanel keeps a ~44px top chrome inset for min/close (right). Home cancels
# that inset so Register Here sits just under the panel top border.
_CONTENT_TOP_CHROME = 44
# Effective gap under the purple top stroke (negative cancel → pull Register up).
_HOME_TOP_PAD = 0
# Small inset from ContentPanel top / side when filling the brand rect.
_ART_TOP_PAD_PX = 6
# MoA wordmark prefer width along art bottom (right of countdown).
_MOA_ART_LOGO_W = 190  # ~5% under prior 200px prefer width
# Gap between countdown right edge and MoA left edge.
_COUNTDOWN_MOA_GAP_PX = 12
# Pad from art right / bottom for the countdown + MoA row.
_BRAND_BOTTOM_PAD_PX = 8
# Extra MoA sit-down toward art bottom (above diamond strip).
_MOA_BOTTOM_NUDGE_PX = 4


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
        # Negative top pulls under ContentPanel chrome inset so Register Here
        # lands ~_HOME_TOP_PAD below the purple top border (not a double gap).
        root.setContentsMargins(28, _HOME_TOP_PAD - _CONTENT_TOP_CHROME, 28, 0)

        # --- Left: fixed-ish side drawer of categorized mods ---
        left = QWidget()
        left.setObjectName("HomeModsDrawer")
        left.setMinimumWidth(DRAWER_MIN_W)
        left.setMaximumWidth(DRAWER_MAX_W)
        left.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._mods_drawer = left
        left_l = QVBoxLayout(left)
        # Lift Card off NavBottomBanner — art still flushes to the diamond strip.
        left_l.setContentsMargins(0, 0, 0, _MODS_BANNER_GAP_PX)
        left_l.setSpacing(10)

        self.register_btn = LaunchButton("REGISTER HERE")
        self.register_btn.setFixedSize(248, 56)
        self.register_btn.setToolTip("Create a RavenCraft account")
        self.register_btn.clicked.connect(
            lambda: open_url_in_browser("https://ravencraft.io/register")
        )
        left_l.addWidget(self.register_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        self.summary = HomeModsCard()
        self.summary.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll_host = QWidget()
        scroll_host.setObjectName("HomeModsHost")
        self.summary_host = QVBoxLayout(scroll_host)
        self.summary_host.setContentsMargins(2, 2, 6, 8)
        self.summary_host.setSpacing(12)
        self.summary_host.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(scroll_host)
        self.summary.body.addWidget(scroll)
        left_l.addWidget(self.summary, 1)

        # --- Right: empty brand spacer (art/logo/countdown live on Root overlay) ---
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

        self.logo = QLabel(self)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._logo_src: QPixmap | None = None
        self._load_logo()

        self.countdown = LaunchCountdown(self)
        self.countdown.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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
        """Scale MoA wordmark for the art-bottom row (right of countdown)."""
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
            self.countdown,
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
        """Reparent art/logo/countdown onto Root so flush uses banner.y() directly."""
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

    def _set_chrome_visible(self, visible: bool) -> None:
        self.talent_bg.setVisible(visible)
        self.logo.setVisible(visible)
        self.countdown.setVisible(visible)

    def _sync_brand_layout(self) -> None:
        """Fill brand rect with official art; MoA + countdown along art bottom.

        Overlays live on MainWindow Root (not in any VBox). Art fills the brand
        column (small side/top pads), bottom flush to the diamond strip — available
        rect wins over any aspect clamp (paint uses KeepAspectRatioByExpanding).
        MoA sits bottom-right, immediately right of the launch countdown.
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

        # Exact diamond-strip top in Root space (sibling under ContentPanel).
        if banner is not None and banner.isVisible():
            art_bottom = banner.mapTo(host, QPoint(0, 0)).y()
        elif panel is not None:
            art_bottom = panel.mapTo(host, QPoint(0, panel.height())).y()
        else:
            art_bottom = host.height()

        # Keep art inside the folder body (below top tabs).
        if panel is not None:
            content_top = panel.mapTo(host, QPoint(0, 0)).y()
            content_right = panel.mapTo(host, QPoint(panel.width(), 0)).x()
        else:
            content_top = 0
            content_right = host.width()

        if art_bottom <= content_top:
            return

        mods_right = mods.mapTo(host, QPoint(mods.width(), 0)).x()
        art_left = mods_right + side_pad
        art_right = content_right - side_pad
        avail_w = art_right - art_left
        if avail_w < 64:
            art_left = side_pad
            art_right = content_right - side_pad
            avail_w = max(64, art_right - art_left)

        # Fill available brand rect (priority over prior 16:9 clamp).
        art_x = art_left
        art_y = content_top + _ART_TOP_PAD_PX
        art_w = max(64, avail_w)
        art_h = max(64, art_bottom - art_y)
        if art_y + art_h > art_bottom:
            art_h = max(64, art_bottom - art_y)

        self.talent_bg.set_frame(art_x, art_y, art_w, art_h)
        art = self.talent_bg.geometry()

        # --- Countdown + MoA along art BOTTOM (above diamond strip) ---
        # MoA sits immediately right of countdown; pair right-aligned in the art.
        cd = self.countdown
        cd_hint = cd.sizeHint()
        cd_w = max(cd_hint.width(), 280)
        cd_h = max(cd_hint.height(), 1)

        max_logo_h = max(40, cd_h)
        prefer_w = min(_MOA_ART_LOGO_W, max(120, art.width() // 4))
        logo_w, logo_h = self._fit_logo(max_h=max_logo_h, prefer_w=prefer_w)

        row_h = max(cd_h, logo_h)
        row_y = art.y() + art.height() - row_h - bottom_pad
        if row_y < art.y():
            row_y = art.y()

        pair_w = cd_w + _COUNTDOWN_MOA_GAP_PX + logo_w
        # Right-align the pair; shrink countdown if the art is narrow.
        pair_right = art.x() + art.width() - bottom_pad
        pair_left = pair_right - pair_w
        if pair_left < art.x() + bottom_pad:
            pair_left = art.x() + bottom_pad
            max_pair = max(64, pair_right - pair_left)
            if pair_w > max_pair:
                overflow = pair_w - max_pair
                cd_w = max(200, cd_w - overflow)
                pair_w = cd_w + _COUNTDOWN_MOA_GAP_PX + logo_w
                pair_left = pair_right - pair_w
                if pair_left < art.x() + bottom_pad:
                    pair_left = art.x() + bottom_pad

        cd_x = pair_left
        cd_y = row_y + (row_h - cd_h) // 2
        cd.setGeometry(cd_x, cd_y, cd_w, cd_h)

        # Bottom-align MoA just above the art edge (not vertically centered with CD).
        logo_x = cd_x + cd_w + _COUNTDOWN_MOA_GAP_PX
        logo_y = art.y() + art.height() - logo_h - max(2, bottom_pad - _MOA_BOTTOM_NUDGE_PX)
        self.logo.setGeometry(logo_x, logo_y, logo_w, logo_h)

        self._set_chrome_visible(True)

        # Z-order (back → front on Root):
        #   art → MoA → countdown → banner → bottom bar → −/X → RC crest
        # Art is mouse-transparent and sits above ContentPanel so it paints over
        # the opaque floor, but the brand column starts at mods_right + side_pad
        # so the drawer / Register / card never sit under the art pixmap.
        self.talent_bg.raise_()
        self.logo.raise_()
        self.countdown.raise_()
        if banner is not None:
            banner.raise_()
        if isinstance(bottom_bar, QWidget):
            bottom_bar.raise_()
        # −/X are Root siblings (see MainWindow) — keep them above the art.
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

        cat_lbl = QLabel(category)
        cat_lbl.setObjectName("HomeModCategory")
        cat_lbl.setStyleSheet(
            "color: #F1C22D; font-size: 12px; font-weight: 600; padding-bottom: 2px;"
        )
        block_l.addWidget(cat_lbl)

        for name in names:
            item = QLabel(name)
            item.setObjectName("HomeModItem")
            item.setWordWrap(True)
            item.setStyleSheet(
                "color: #e6e0ee; font-size: 13px; padding-left: 14px;"
            )
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
                hdr = QLabel("Installed client mods")
                hdr.setObjectName("SectionTitle")
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
                "Click <b>INSTALL</b> in the bottom-right to pick a save location, or open "
                "<b>Settings</b> to point at an existing game folder."
            )
            tip.setWordWrap(True)
            tip.setObjectName("Muted")
            self.summary_host.addWidget(tip)
            self.summary_host.addStretch(1)

        self._sync_brand_layout()
