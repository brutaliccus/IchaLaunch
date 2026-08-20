"""Client mods — category sidebar + one category panel at a time."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.config.settings import settings
from ichalaunch.game.launcher import detect_game
from ichalaunch.mods.installer import detect_actual_state, load_mod_catalog, plan_changes
from ichalaunch.ui.widgets.common import ModCheckRow

CATEGORY_ORDER = [
    "Performance & Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
]


class ClientPage(QWidget):
    apply_clicked = Signal()
    rescan_clicked = Signal()
    check_updates_requested = Signal()
    update_mod_requested = Signal(str)
    update_all_mods_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Client Fixes, Tweaks & Patches")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.clicked.connect(self.rescan_clicked.emit)
        self.check_btn = QPushButton("Check Updates")
        self.check_btn.clicked.connect(self.check_updates_requested.emit)
        self.update_all_btn = QPushButton("Update All")
        self.update_all_btn.setEnabled(False)
        self.update_all_btn.clicked.connect(self.update_all_mods_requested.emit)
        apply_btn = QPushButton("Apply Changes")
        apply_btn.clicked.connect(self.apply_clicked.emit)
        header.addWidget(self.rescan_btn)
        header.addWidget(self.check_btn)
        header.addWidget(self.update_all_btn)
        header.addWidget(apply_btn)
        root.addLayout(header)

        hint = QLabel(
            "Select a category on the left. Checkboxes reflect desired state; Rescan syncs from disk."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.loading_row = QHBoxLayout()
        self.loading_lbl = QLabel("")
        self.loading_lbl.setStyleSheet("color: #ffd700;")
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(120)
        self.loading_row.addWidget(self.loading_lbl)
        self.loading_row.addWidget(self.loading_bar)
        self.loading_row.addStretch(1)
        root.addLayout(self.loading_row)
        self.set_checking(False)

        self.updates_lbl = QLabel("")
        self.updates_lbl.setStyleSheet("color: #ffd700;")
        root.addWidget(self.updates_lbl)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Left category tabs
        side = QWidget()
        side.setObjectName("ClientCatNav")
        side.setFixedWidth(200)
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(0, 0, 0, 0)
        side_l.setSpacing(2)
        self.cat_btns: list[QPushButton] = []
        self.cat_stack = QStackedWidget()
        self.rows: dict[str, ModCheckRow] = {}
        self._pending_updates: dict[str, dict] = {}

        by_cat: dict[str, list] = {}
        for mod in load_mod_catalog():
            by_cat.setdefault(mod.get("category") or "Other", []).append(mod)
        cats = [c for c in CATEGORY_ORDER if c in by_cat] + [c for c in by_cat if c not in CATEGORY_ORDER]

        for i, cat in enumerate(cats):
            btn = QPushButton(cat)
            btn.setObjectName("CatNavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, idx=i: self._show_cat(idx))
            side_l.addWidget(btn)
            self.cat_btns.append(btn)

            page = QWidget()
            page_l = QVBoxLayout(page)
            page_l.setContentsMargins(0, 0, 0, 0)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            host = QWidget()
            host_l = QVBoxLayout(host)
            host_l.setContentsMargins(4, 0, 4, 0)
            host_l.setSpacing(8)
            for mod in by_cat[cat]:
                note = mod.get("note") or ""
                desc = mod.get("description", "")
                if note:
                    desc = f"{desc}  ({note})"
                if mod.get("kind") == "manual_link":
                    desc = f"[Manual download] {desc}"
                row = ModCheckRow(
                    mod["id"],
                    mod["name"],
                    desc,
                    checked=bool(settings.desired_mods.get(mod["id"], False)),
                )
                row.toggled.connect(self._on_toggle)
                row.update_clicked.connect(self.update_mod_requested.emit)
                host_l.addWidget(row)
                self.rows[mod["id"]] = row
            host_l.addStretch(1)
            scroll.setWidget(host)
            page_l.addWidget(scroll)
            self.cat_stack.addWidget(page)

        side_l.addStretch(1)
        body.addWidget(side)
        body.addWidget(self.cat_stack, 1)
        root.addLayout(body, 1)

        self.plan_lbl = QLabel("")
        self.plan_lbl.setObjectName("Muted")
        self.plan_lbl.setWordWrap(True)
        root.addWidget(self.plan_lbl)

        if self.cat_btns:
            self._show_cat(0)
        self.refresh_from_settings()

    @property
    def pending_updates(self) -> list[dict]:
        return list(self._pending_updates.values())

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        self.loading_lbl.setText(msg if busy else "")
        self.loading_bar.setVisible(busy)
        self.loading_lbl.setVisible(busy)
        self.check_btn.setEnabled(not busy)

    def set_updates(self, updates: list[dict]) -> None:
        self._pending_updates = {u["id"]: u for u in updates if u.get("id")}
        n = len(self._pending_updates)
        if n:
            self.updates_lbl.setText(f"{n} client mod update(s) available")
        else:
            self.updates_lbl.setText("")
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()

    def clear_pending_update(self, mod_id: str) -> None:
        self._pending_updates.pop(mod_id, None)
        n = len(self._pending_updates)
        if n:
            self.updates_lbl.setText(f"{n} client mod update(s) available")
        else:
            self.updates_lbl.setText("")
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()

    def _show_cat(self, idx: int) -> None:
        self.cat_stack.setCurrentIndex(idx)
        for i, b in enumerate(self.cat_btns):
            b.setChecked(i == idx)

    def _on_toggle(self, mod_id: str, enabled: bool) -> None:
        settings.set_desired_mod(mod_id, enabled)
        if mod_id == "vanillafixes":
            settings.set("vanillafixes_enabled", enabled)
        self.refresh_plan()

    def refresh_from_settings(self) -> None:
        desired = settings.desired_mods
        game = detect_game()
        actual = detect_actual_state(game) if game else {}
        for mid, row in self.rows.items():
            row.cb.blockSignals(True)
            row.cb.setChecked(bool(desired.get(mid, False)))
            row.cb.blockSignals(False)
            pending = self._pending_updates.get(mid)
            if pending:
                detail = f"{pending.get('local', '?')} → {pending.get('remote', '?')}"
                row.status_lbl.setText(f"Update available ({detail})")
                row.status_lbl.setStyleSheet("color: #ffd700;")
                row.set_update_available(True, detail)
            elif actual.get(mid):
                row.status_lbl.setText("Detected on disk")
                row.status_lbl.setStyleSheet("color: #4CAF50;")
                row.set_update_available(False)
            else:
                row.status_lbl.setText("Not installed")
                row.status_lbl.setStyleSheet("color: #8a8a92;")
                row.set_update_available(False)
        self.refresh_plan()

    def refresh_plan(self) -> None:
        game = detect_game()
        if not game:
            self.plan_lbl.setText("Set a game path in Settings before applying mods.")
            return
        changes = plan_changes()
        if not changes:
            self.plan_lbl.setText("Desired state matches installed client.")
            return
        lines = ["Pending: " + " · ".join(c["detail"] for c in changes[:8])]
        if len(changes) > 8:
            lines.append(f"…and {len(changes) - 8} more")
        self.plan_lbl.setText("\n".join(lines))
