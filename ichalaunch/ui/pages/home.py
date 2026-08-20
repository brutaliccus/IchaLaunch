"""Home / status page (Play lives on the bottom bar)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.core.paths import theme_file
from ichalaunch.game.launcher import detect_game, is_installed
from ichalaunch.mods.installer import detect_actual_state, load_mod_catalog
from ichalaunch.ui.widgets.common import Card
from ichalaunch.ui.widgets.countdown import LaunchCountdown

CATEGORY_ORDER = [
    "Performance & Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
]

DRAWER_MIN_W = 260
DRAWER_MAX_W = 320


class HomePage(QWidget):
    play_clicked = Signal()
    install_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        page = QVBoxLayout(self)
        page.setSpacing(0)
        page.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        root = QHBoxLayout(body)
        root.setSpacing(24)
        root.setContentsMargins(28, 24, 28, 12)

        # --- Left: fixed-ish side drawer of categorized mods ---
        left = QWidget()
        left.setObjectName("HomeModsDrawer")
        left.setMinimumWidth(DRAWER_MIN_W)
        left.setMaximumWidth(DRAWER_MAX_W)
        left.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)

        self.summary = Card()
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

        # --- Right: logo / brand ---
        right = QWidget()
        right.setObjectName("HomeBrandPane")
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 8, 8, 8)
        right_l.setSpacing(12)
        right_l.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_logo()

        sub = QLabel("Powered by IchaLaunch")
        sub.setObjectName("Subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status = QLabel("")
        self.status.setObjectName("Subtitle")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)

        self.loading_wrap = QWidget()
        load_l = QVBoxLayout(self.loading_wrap)
        load_l.setContentsMargins(0, 0, 0, 0)
        load_l.setSpacing(6)
        load_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_lbl = QLabel("Checking for updates…")
        self.loading_lbl.setStyleSheet("color: #F1C22D;")
        self.loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedSize(160, 6)
        self.loading_bar.setTextVisible(False)
        load_l.addWidget(self.loading_lbl)
        load_l.addWidget(self.loading_bar, 0, Qt.AlignmentFlag.AlignCenter)
        self.loading_wrap.setVisible(False)

        # Countdown sits centered under the RavenCraft logo (not under the mods drawer).
        self.countdown = LaunchCountdown()

        right_l.addStretch(1)
        right_l.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        right_l.addWidget(sub, 0, Qt.AlignmentFlag.AlignHCenter)
        right_l.addWidget(self.status, 0, Qt.AlignmentFlag.AlignHCenter)
        right_l.addWidget(self.loading_wrap, 0, Qt.AlignmentFlag.AlignHCenter)
        right_l.addWidget(self.countdown, 0, Qt.AlignmentFlag.AlignHCenter)
        right_l.addStretch(1)

        root.addWidget(left, 0)
        root.addWidget(right, 1)

        # Body fills the content panel down to the nav_bottom banner so the
        # installed-mods drawer can use the full available height.
        page.addWidget(body, 1)

        self.refresh()

    def _load_logo(self) -> None:
        path = theme_file("ravencraft.png")
        if path.exists():
            pix = QPixmap(str(path))
            if not pix.isNull():
                scaled = pix.scaledToWidth(260, Qt.TransformationMode.SmoothTransformation)
                self.logo.setPixmap(scaled)
                self.logo.setMinimumHeight(scaled.height())
                return
        self.logo.setText("RAVENCRAFT")
        self.logo.setObjectName("Brand")

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        self.loading_lbl.setText(msg if busy else "")
        self.loading_wrap.setVisible(busy)

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
            self.status.setText("Ready")
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
            self.status.setText("Client not found — use INSTALL or Settings")
            tip = QLabel(
                "Ravencraft uses the Turtle 1.18 client.<br>"
                "Click <b>INSTALL</b> in the bottom-right to pick a save location, or open "
                "<b>Settings</b> to point at an existing game folder."
            )
            tip.setWordWrap(True)
            tip.setObjectName("Muted")
            self.summary_host.addWidget(tip)
            self.summary_host.addStretch(1)
