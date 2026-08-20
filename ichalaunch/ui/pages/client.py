"""Client mods — category sidebar + one category panel at a time."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
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
from ichalaunch.ui.widgets.common import ModCheckRow, status_with_stamp
from ichalaunch.ui.widgets.dialogs import prompt_text

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
    reinstall_mod_requested = Signal(str)
    update_all_mods_requested = Signal()
    custom_dll_import_requested = Signal(str)
    badge_state_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        # Extra top padding clears the floating MoA logo overhang
        root.setContentsMargins(16, 28, 16, 12)
        root.setSpacing(10)

        title = QLabel("Client Fixes, Tweaks & Patches")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        hint = QLabel(
            "Select a category on the left. Checkboxes reflect desired state; Rescan syncs from disk. "
            "Use Add DLL from GitHub for a custom release (.dll / .zip)."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.loading_row = QHBoxLayout()
        self.loading_lbl = QLabel("")
        self.loading_lbl.setStyleSheet("color: #F1C22D;")
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(120)
        self.loading_row.addWidget(self.loading_lbl)
        self.loading_row.addWidget(self.loading_bar)
        self.loading_row.addStretch(1)
        root.addLayout(self.loading_row)

        self.updates_lbl = QLabel("")
        self.updates_lbl.setStyleSheet("color: #F1C22D;")
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
        self.cat_stack.setObjectName("ClientCatStack")
        self.cat_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.rows: dict[str, ModCheckRow] = {}
        self._pending_updates: dict[str, dict] = {}
        self._apply_pending = False
        self._client_mods_scan_done = False
        self._cat_hosts: dict[str, QVBoxLayout] = {}
        self._cat_index: dict[str, int] = {}
        self._side_layout = side_l
        self._side_stretch_added = False

        by_cat: dict[str, list] = {}
        for mod in load_mod_catalog():
            by_cat.setdefault(mod.get("category") or "Other", []).append(mod)
        cats = [c for c in CATEGORY_ORDER if c in by_cat] + [c for c in by_cat if c not in CATEGORY_ORDER]

        for i, cat in enumerate(cats):
            self._add_category_page(cat, by_cat[cat], i)

        side_l.addStretch(1)
        self._side_stretch_added = True
        body.addWidget(side)
        body.addWidget(self.cat_stack, 1)
        root.addLayout(body, 1)

        self.plan_lbl = QLabel("")
        self.plan_lbl.setObjectName("Muted")
        self.plan_lbl.setWordWrap(True)
        root.addWidget(self.plan_lbl)

        # Actions sit at the bottom near the play bar (avoids MoA logo collision)
        actions = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.clicked.connect(self.rescan_clicked.emit)
        self.check_btn = QPushButton("Check Updates")
        self.check_btn.clicked.connect(self.check_updates_requested.emit)
        self.update_all_btn = QPushButton("Update All")
        self.update_all_btn.setObjectName("UpdateAllButton")
        self.update_all_btn.setEnabled(False)
        self.update_all_btn.clicked.connect(self.update_all_mods_requested.emit)
        self.add_dll_btn = QPushButton("Add DLL from GitHub")
        self.add_dll_btn.clicked.connect(self._open_custom_dll_dialog)
        self.apply_btn = QPushButton("Apply Changes")
        self.apply_btn.clicked.connect(self.apply_clicked.emit)
        self._apply_pulse = False
        self._apply_pulse_timer = QTimer(self)
        self._apply_pulse_timer.setInterval(700)
        self._apply_pulse_timer.timeout.connect(self._pulse_apply_btn)
        actions.addWidget(self.rescan_btn)
        actions.addWidget(self.check_btn)
        actions.addWidget(self.update_all_btn)
        actions.addWidget(self.add_dll_btn)
        actions.addStretch(1)
        actions.addWidget(self.apply_btn)
        root.addLayout(actions)
        self.set_checking(False)

        if self.cat_btns:
            self._show_cat(0)
        self.refresh_from_settings()

    def _add_category_page(self, cat: str, mods: list[dict], index: int) -> None:
        btn = QPushButton(cat)
        btn.setObjectName("CatNavButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, idx=index: self._show_cat(idx))
        if self._side_stretch_added:
            # Insert before the trailing stretch spacer
            self._side_layout.insertWidget(self._side_layout.count() - 1, btn)
        else:
            self._side_layout.addWidget(btn)
        self.cat_btns.append(btn)
        self._cat_index[cat] = index

        page = QWidget()
        page.setObjectName("ClientCatPanel")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page_l = QVBoxLayout(page)
        page_l.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("ClientCatScroll")
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        host = QWidget()
        host.setObjectName("ClientCatHost")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        host.setMinimumWidth(0)
        host_l = QVBoxLayout(host)
        host_l.setContentsMargins(8, 8, 8, 8)
        host_l.setSpacing(8)
        self._cat_hosts[cat] = host_l
        for mod in mods:
            self._add_mod_row(mod, host_l)
        host_l.addStretch(1)
        scroll.setWidget(host)
        page_l.addWidget(scroll)
        self.cat_stack.addWidget(page)

    def _add_mod_row(self, mod: dict, host_l: QVBoxLayout | None = None) -> ModCheckRow:
        mid = mod["id"]
        if mid in self.rows:
            return self.rows[mid]
        note = mod.get("note") or ""
        desc = mod.get("description", "")
        if note:
            desc = f"{desc}  ({note})"
        if mod.get("kind") == "manual_link":
            desc = f"[Manual download] {desc}"
        if mod.get("user_defined"):
            desc = f"[Custom] {desc}"
        row = ModCheckRow(
            mid,
            mod["name"],
            desc,
            checked=bool(settings.desired_mods.get(mid, False)),
        )
        row.toggled.connect(self._on_toggle)
        row.update_clicked.connect(self.update_mod_requested.emit)
        row.reinstall_clicked.connect(self.reinstall_mod_requested.emit)
        cat = mod.get("category") or "Client Enhancements"
        layout = host_l or self._cat_hosts.get(cat)
        if layout is not None:
            # Insert before trailing stretch
            insert_at = max(0, layout.count() - 1)
            layout.insertWidget(insert_at, row)
        self.rows[mid] = row
        return row

    def ensure_mod_row(self, mod: dict) -> None:
        """Ensure a catalog (or newly registered user) mod has a checkbox row."""
        mid = mod.get("id")
        if not mid:
            return
        if mid not in self.rows:
            cat = mod.get("category") or "Client Enhancements"
            if cat not in self._cat_hosts:
                idx = len(self.cat_btns)
                self._add_category_page(cat, [mod], idx)
            else:
                self._add_mod_row(mod)
        self.refresh_from_settings()

    def sync_catalog_rows(self) -> None:
        """Add rows for any catalog/user mods that appeared since page construction."""
        for mod in load_mod_catalog():
            mid = mod.get("id")
            if not mid or mid in self.rows:
                continue
            cat = mod.get("category") or "Client Enhancements"
            if cat not in self._cat_hosts:
                self._add_category_page(cat, [mod], len(self.cat_btns))
            else:
                self._add_mod_row(mod)

    def _open_custom_dll_dialog(self) -> None:
        url = prompt_text(
            self,
            "Add DLL from GitHub",
            "Paste a GitHub repository URL that publishes a .dll (or .zip) release asset:",
            placeholder="https://github.com/owner/dll-repo",
            accept_text="Install",
        )
        if url:
            self.custom_dll_import_requested.emit(url)

    @property
    def pending_updates(self) -> list[dict]:
        return list(self._pending_updates.values())

    def has_pending_badge(self) -> bool:
        """True when CLIENT tab should show a gold update/apply badge."""
        return bool(self._pending_updates) or bool(self._apply_pending)

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        self.loading_lbl.setText(msg if busy else "")
        self.loading_bar.setVisible(busy)
        self.loading_lbl.setVisible(busy)
        self.check_btn.setEnabled(not busy)

    def set_updates(self, updates: list[dict]) -> None:
        self._pending_updates = {u["id"]: u for u in updates if u.get("id")}
        self._client_mods_scan_done = True
        n = len(self._pending_updates)
        if n:
            self.updates_lbl.setText(f"{n} client mod update(s) available")
        else:
            self.updates_lbl.setText("")
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()
        self.badge_state_changed.emit()

    def reset_scan_done(self) -> None:
        """Clear update-check completion (disk rescan is not an update scan)."""
        self._client_mods_scan_done = False

    def clear_pending_update(self, mod_id: str) -> None:
        self._pending_updates.pop(mod_id, None)
        n = len(self._pending_updates)
        if n:
            self.updates_lbl.setText(f"{n} client mod update(s) available")
        else:
            self.updates_lbl.setText("")
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()
        self.badge_state_changed.emit()

    def _show_cat(self, idx: int) -> None:
        self.cat_stack.setCurrentIndex(idx)
        for i, b in enumerate(self.cat_btns):
            b.setChecked(i == idx)

    def _on_toggle(self, mod_id: str, enabled: bool) -> None:
        settings.set_desired_mod(mod_id, enabled)
        if mod_id == "vanillafixes":
            settings.set("vanillafixes_enabled", enabled)
        self.refresh_plan()

    @staticmethod
    def _mod_can_reinstall(mod: dict) -> bool:
        """True when a downloadable source exists (same gate as update checks)."""
        kind = mod.get("kind")
        if kind in ("manual_link", "wdb_block", "config_script_memory"):
            return False
        return bool(mod.get("source"))

    def refresh_from_settings(self) -> None:
        self.sync_catalog_rows()
        desired = settings.desired_mods
        installed_meta = settings.installed_mods
        game = detect_game()
        actual = detect_actual_state(game) if game else {}
        catalog = {m["id"]: m for m in load_mod_catalog()}
        for mid, row in self.rows.items():
            row.cb.blockSignals(True)
            row.cb.setChecked(bool(desired.get(mid, False)))
            row.cb.blockSignals(False)
            can_ri = self._mod_can_reinstall(catalog.get(mid) or {})
            pending = self._pending_updates.get(mid)
            if pending:
                detail = f"{pending.get('local', '?')} → {pending.get('remote', '?')}"
                row.status_lbl.setText(f"Update available ({detail})")
                self._set_status_style(row.status_lbl, "StatusUpdate")
                row.set_update_available(True, detail)
                row.set_reinstall_visible(can_ri)
            elif actual.get(mid):
                if self._client_mods_scan_done:
                    row.status_lbl.setText(status_with_stamp("Up to date", installed_meta.get(mid)))
                    self._set_status_style(row.status_lbl, "StatusOk")
                else:
                    row.status_lbl.setText("Not checked")
                    self._set_status_style(row.status_lbl, "StatusMuted")
                row.set_update_available(False)
                row.set_reinstall_visible(can_ri)
            else:
                row.status_lbl.setText("Not installed")
                self._set_status_style(row.status_lbl, "StatusMuted")
                row.set_update_available(False)
                row.set_reinstall_visible(False)
        self.refresh_plan()

    @staticmethod
    def _set_status_style(lbl: QLabel, object_name: str) -> None:
        lbl.setStyleSheet("")
        if lbl.objectName() != object_name:
            lbl.setObjectName(object_name)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _pulse_apply_btn(self) -> None:
        if self.apply_btn.objectName() not in ("ApplyReadyButton", "ApplyReadyButtonPulse"):
            return
        self._apply_pulse = not self._apply_pulse
        name = "ApplyReadyButtonPulse" if self._apply_pulse else "ApplyReadyButton"
        self.apply_btn.setObjectName(name)
        self.apply_btn.style().unpolish(self.apply_btn)
        self.apply_btn.style().polish(self.apply_btn)

    def _set_apply_pending(self, pending: bool) -> None:
        """Highlight Apply Changes when installs/removes are pending; mute when clean."""
        pending = bool(pending)
        changed = pending != self._apply_pending
        self._apply_pending = pending
        if pending:
            if self.apply_btn.objectName() not in ("ApplyReadyButton", "ApplyReadyButtonPulse"):
                self.apply_btn.setObjectName("ApplyReadyButton")
                self.apply_btn.style().unpolish(self.apply_btn)
                self.apply_btn.style().polish(self.apply_btn)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setToolTip("Pending client mod changes — click to apply")
            if not self._apply_pulse_timer.isActive():
                self._apply_pulse = False
                self._apply_pulse_timer.start()
        else:
            self._apply_pulse_timer.stop()
            self._apply_pulse = False
            if self.apply_btn.objectName():
                self.apply_btn.setObjectName("")
                self.apply_btn.style().unpolish(self.apply_btn)
                self.apply_btn.style().polish(self.apply_btn)
            self.apply_btn.setEnabled(False)
            self.apply_btn.setToolTip("No pending client mod changes")
        if changed:
            self.badge_state_changed.emit()

    def refresh_plan(self) -> None:
        game = detect_game()
        if not game:
            self.plan_lbl.setText("Set a game path in Settings before applying mods.")
            self._set_apply_pending(False)
            return
        changes = plan_changes()
        if not changes:
            self.plan_lbl.setText("Desired state matches installed client.")
            self._set_apply_pending(False)
            return
        lines = ["Pending: " + " · ".join(c["detail"] for c in changes[:8])]
        if len(changes) > 8:
            lines.append(f"…and {len(changes) - 8} more")
        self.plan_lbl.setText("\n".join(lines))
        self._set_apply_pending(True)
