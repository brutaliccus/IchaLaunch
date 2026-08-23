"""Client mods — category sidebar + one category panel at a time."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.config.settings import settings
from ichalaunch.game.launcher import detect_game
from ichalaunch.mods.installer import (
    apply_mod_toggle,
    apply_vanillafixes_dxvk_choice,
    detect_actual_state,
    load_mod_catalog,
    plan_changes,
    reconcile_exclusive_desired_mods,
    vanillafixes_dxvk_both_enabled,
)
from ichalaunch.ui.widgets.casting_bar_search_edit import CastingBarSearchEdit
from ichalaunch.ui.widgets.common import (
    ModCheckRow,
    mod_author,
    mod_git_url,
    open_url_in_browser,
    status_with_stamp,
)
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.dialogs import DialogResult, choice, github_import_dialog, warning
from ichalaunch.ui.widgets.glue_panel_button import GluePanelButton
from ichalaunch.ui.widgets.marble_bg import MarblePanel, MarbleScrollArea

CATEGORY_ORDER = [
    "Performance & Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
    "Custom",
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
    open_git_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        # Extra top padding clears the floating MoA logo overhang
        root.setContentsMargins(16, 28, 16, 12)
        root.setSpacing(10)

        title = QLabel("Client Fixes, Tweaks & Patches")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        hint = QLabel(
            "Select a category on the left. Checkboxes reflect desired state; Rescan syncs from disk. "
            "Use + Git Repo for a custom release (.dll / .zip)."
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

        # Left category tabs — marble panel under nav buttons
        side = MarblePanel()
        side.setObjectName("ClientCatNav")
        side.setFixedWidth(200)
        side_l = QVBoxLayout(side)
        # Inset so hover/checked fills sit inside the rounded purple frame
        # (top item must not square-overflow the panel corner).
        side_l.setContentsMargins(4, 4, 4, 4)
        side_l.setSpacing(2)
        self.cat_btns: list[QPushButton] = []
        self.cat_stack = QStackedWidget()
        self.cat_stack.setObjectName("ClientCatStack")
        self.cat_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.cat_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.rows: dict[str, ModCheckRow] = {}
        self._row_meta: dict[str, dict] = {}
        self._pending_updates: dict[str, dict] = {}
        self._apply_pending = False
        self._client_mods_scan_done = False
        self._cat_hosts: dict[str, QVBoxLayout] = {}
        self._cat_scrolls: dict[str, MarbleScrollArea] = {}
        self._cat_index: dict[str, int] = {}
        self._side_layout = side_l
        self._side_stretch_added = False
        self._search_q = ""
        self._vf_dxvk_prompted = False
        self._dxvk_gpu_warned = False

        by_cat: dict[str, list] = {}
        for mod in load_mod_catalog():
            cat = mod.get("category") or "Other"
            # Legacy user_mods may still say Client Enhancements — keep Custom together.
            if mod.get("user_defined") and cat != "Custom":
                cat = "Custom"
                mod = dict(mod)
                mod["category"] = "Custom"
            by_cat.setdefault(cat, []).append(mod)
        # Always reserve Custom so Add DLL can land there even before first custom mod.
        by_cat.setdefault("Custom", [])
        cats = [c for c in CATEGORY_ORDER if c in by_cat] + [
            c for c in by_cat if c not in CATEGORY_ORDER
        ]

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
        self.rescan_btn = GluePanelButton("Rescan")
        self.rescan_btn.clicked.connect(self.rescan_clicked.emit)
        self.check_btn = GluePanelButton("Check Updates")
        self.check_btn.clicked.connect(self.check_updates_requested.emit)
        self.update_all_btn = GluePanelButton("Update All", role="primary")
        self.update_all_btn.setEnabled(False)
        self.update_all_btn.clicked.connect(self.update_all_mods_requested.emit)
        self.add_dll_btn = GluePanelButton("+ Git Repo")
        self.add_dll_btn.setToolTip("Add a client DLL from a GitHub release")
        self.add_dll_btn.clicked.connect(self._open_custom_dll_dialog)
        self.apply_btn = GluePanelButton("Apply Changes", role="standard")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("No pending client mod changes")
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

        # Cross-category search (bottom of Client tab)
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search = CastingBarSearchEdit(object_name="ClientSearch")
        self.search.setPlaceholderText("Search all client fixes, tweaks & patches…")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.search.returnPressed.connect(self._apply_search)
        search_row.addWidget(self.search, 1)
        root.addLayout(search_row)

        self.set_checking(False)

        if self.cat_btns:
            self._show_cat(0)
        self.refresh_from_settings()
        self._reveal_rows(kick=False)

    def _add_category_page(self, cat: str, mods: list[dict], index: int) -> None:
        btn = QPushButton(cat)
        btn.setObjectName("CatNavButton")
        btn.setCheckable(True)
        apply_open_hand(btn)
        btn.clicked.connect(lambda checked=False, idx=index: self._show_cat(idx))
        if self._side_stretch_added:
            self._side_layout.insertWidget(self._side_layout.count() - 1, btn)
        else:
            self._side_layout.addWidget(btn)
        self.cat_btns.append(btn)
        self._cat_index[cat] = index

        page = QWidget()
        page.setObjectName("ClientCatPanel")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        page_l = QVBoxLayout(page)
        page_l.setContentsMargins(0, 0, 0, 0)
        scroll = MarbleScrollArea()
        scroll.setObjectName("ClientCatScroll")
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        self._cat_scrolls[cat] = scroll
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
            cat = "Custom"
        else:
            cat = mod.get("category") or "Client Enhancements"
        row = ModCheckRow(
            mid,
            mod["name"],
            desc,
            checked=bool(settings.desired_mods.get(mid, False)),
            author=mod_author(mod),
            parent=host_l.parentWidget() if host_l is not None else self,
        )
        row.toggled.connect(self._on_toggle)
        row.update_clicked.connect(self.update_mod_requested.emit)
        row.reinstall_clicked.connect(self.reinstall_mod_requested.emit)
        row.open_git_clicked.connect(self.open_git_requested.emit)
        row.set_git_url(mod_git_url(mod))
        self._row_meta[mid] = {
            "category": cat,
            "name": str(mod.get("name") or mid),
            "description": str(mod.get("description") or ""),
            "note": str(mod.get("note") or ""),
            "author": str(mod_author(mod) or ""),
        }
        layout = host_l or self._cat_hosts.get(cat)
        if layout is not None:
            insert_at = max(0, layout.count() - 1)
            layout.insertWidget(insert_at, row)
        row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        row.show()
        self.rows[mid] = row
        return row

    def ensure_mod_row(self, mod: dict) -> None:
        """Ensure a catalog (or newly registered user) mod has a checkbox row."""
        mid = mod.get("id")
        if not mid:
            return
        if mid not in self.rows:
            cat = mod.get("category") or "Client Enhancements"
            if mod.get("user_defined"):
                cat = "Custom"
                mod = dict(mod)
                mod["category"] = "Custom"
            if cat not in self._cat_hosts:
                idx = len(self.cat_btns)
                self._add_category_page(cat, [mod], idx)
            else:
                self._add_mod_row(mod)
        self.refresh_from_settings()

    def focus_mod(self, mod_id: str) -> None:
        """Switch to the mod's category tab, scroll to the row, and flash-highlight it."""
        row = self.rows.get(mod_id)
        meta = self._row_meta.get(mod_id) or {}
        cat = str(meta.get("category") or "")
        if not cat and row is None:
            return
        idx = self._cat_index.get(cat)
        if idx is not None:
            self._show_cat(idx)

        def _scroll_and_flash() -> None:
            r = self.rows.get(mod_id)
            if r is None:
                return
            scroll = self._cat_scrolls.get(cat)
            if scroll is not None and scroll.widget() is not None:
                scroll.ensureWidgetVisible(r, 24, 24)
            r.flash_highlight()

        QTimer.singleShot(40, _scroll_and_flash)

    def sync_catalog_rows(self) -> None:
        """Add rows for any catalog/user mods that appeared since page construction."""
        for mod in load_mod_catalog():
            mid = mod.get("id")
            if not mid or mid in self.rows:
                continue
            cat = mod.get("category") or "Client Enhancements"
            if mod.get("user_defined"):
                cat = "Custom"
                mod = dict(mod)
                mod["category"] = "Custom"
            if cat not in self._cat_hosts:
                self._add_category_page(cat, [mod], len(self.cat_btns))
            else:
                self._add_mod_row(mod)

    def _open_custom_dll_dialog(self) -> None:
        url = github_import_dialog(self, kind="dll")
        if url:
            self.custom_dll_import_requested.emit(url)

    def _apply_search(self) -> None:
        q = (self.search.text() or "").strip().lower()
        self._search_q = q
        matches: list[tuple[str, str]] = []
        for mid, row in self.rows.items():
            try:
                meta = self._row_meta.get(mid) or {}
                hay = " ".join(
                    [
                        mid,
                        str(meta.get("name") or ""),
                        str(meta.get("description") or ""),
                        str(meta.get("note") or ""),
                        str(meta.get("author") or ""),
                    ]
                ).lower()
                hit = (not q) or q in hay
                row.setVisible(hit)
                if hit:
                    matches.append((mid, str(meta.get("category") or "")))
            except RuntimeError:
                continue

        if not matches:
            return

        cat = matches[0][1]
        idx = self._cat_index.get(cat)
        if idx is not None and self.cat_stack.currentIndex() != idx:
            self._show_cat(idx)

    @property
    def pending_updates(self) -> list[dict]:
        return list(self._pending_updates.values())

    def has_pending_badge(self) -> bool:
        """True when CLIENT tab should show a gold update/apply badge."""
        return bool(self._pending_updates) or bool(self._apply_pending)

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        # Progress lives on the bottom bar; keep only the Check Updates button gated.
        self.loading_lbl.setText("")
        self.loading_lbl.setVisible(False)
        self.loading_bar.setVisible(False)
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

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._reveal_rows(kick=True)
        QTimer.singleShot(0, self._maybe_prompt_vf_dxvk_conflict)

    def _maybe_prompt_vf_dxvk_conflict(self) -> None:
        if self._vf_dxvk_prompted or not vanillafixes_dxvk_both_enabled():
            return
        self._vf_dxvk_prompted = True
        result = choice(
            self,
            "Choose launcher mode",
            "Both VanillaFixes and VanillaFixes + DXVK (Vulkan) are enabled, but only "
            "one can be active.\n\nWhich launcher do you want to keep?",
            [
                ("Regular VanillaFixes", DialogResult.No),
                ("VanillaFixes + DXVK", DialogResult.Yes),
            ],
            kind="warning",
        )
        keep = "vanillafixes" if result == DialogResult.No else "dxvk"
        changes = apply_vanillafixes_dxvk_choice(keep)
        for mid, state in changes.items():
            row = self.rows.get(mid)
            if row is None:
                continue
            row.cb.blockSignals(True)
            row.cb.setChecked(state)
            row.cb.blockSignals(False)
        self.refresh_plan()

    def _maybe_warn_dxvk_gpu(self) -> None:
        if self._dxvk_gpu_warned or not settings.desired_mods.get("dxvk"):
            return
        from ichalaunch.core.gpu_compat import assess_dxvk_gpu

        level, _gpus, message = assess_dxvk_gpu()
        if level == "ok":
            return
        self._dxvk_gpu_warned = True
        warning(self, "Graphics compatibility", message)

    def _reveal_rows(self, *, kick: bool = False) -> None:
        """Clear HWND-guard flags leftover from AddonRow and show catalog rows."""
        q = self._search_q
        for row in self.rows.values():
            row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            if not q:
                row.show()
            if kick:
                fn = getattr(row, "kick_git_visibility", None)
                if callable(fn):
                    fn()
        if q:
            self._apply_search()

    def _show_cat(self, idx: int) -> None:
        self.cat_stack.setCurrentIndex(idx)
        for i, b in enumerate(self.cat_btns):
            b.setChecked(i == idx)

    def _on_toggle(self, mod_id: str, enabled: bool) -> None:
        changes = apply_mod_toggle(mod_id, enabled)
        if not enabled and mod_id == "vanilla_helpers" and not changes:
            row = self.rows.get(mod_id)
            if row is not None:
                row.cb.blockSignals(True)
                row.cb.setChecked(True)
                row.cb.blockSignals(False)
        for mid, state in changes.items():
            if mid == mod_id:
                continue
            row = self.rows.get(mid)
            if row is None:
                continue
            row.cb.blockSignals(True)
            row.cb.setChecked(state)
            row.cb.blockSignals(False)
        desired = settings.desired_mods
        settings.set(
            "vanillafixes_enabled",
            bool(desired.get("vanillafixes") or desired.get("dxvk")),
        )
        if enabled and mod_id == "dxvk":
            QTimer.singleShot(0, self._maybe_warn_dxvk_gpu)
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
        game = detect_game()
        actual = detect_actual_state(game) if game else {}
        desired = reconcile_exclusive_desired_mods(
            dict(settings.desired_mods), actual=actual
        )
        if desired != settings.desired_mods:
            settings.set("desired_mods", desired)
            if desired.get("vanillafixes") or desired.get("dxvk"):
                settings.set(
                    "vanillafixes_enabled",
                    bool(desired.get("vanillafixes") or desired.get("dxvk")),
                )
        installed_meta = settings.installed_mods
        catalog = {m["id"]: m for m in load_mod_catalog()}
        for mid, row in self.rows.items():
            row.cb.blockSignals(True)
            row.cb.setChecked(bool(desired.get(mid, False)))
            row.cb.blockSignals(False)
            can_ri = self._mod_can_reinstall(catalog.get(mid) or {})
            row.set_git_url(mod_git_url(catalog.get(mid) or {}))
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
        if self._search_q:
            self._apply_search()
        self.refresh_plan()
        if vanillafixes_dxvk_both_enabled():
            QTimer.singleShot(0, self._maybe_prompt_vf_dxvk_conflict)

    @staticmethod
    def _set_status_style(lbl: QLabel, object_name: str) -> None:
        lbl.setStyleSheet("")
        if lbl.objectName() != object_name:
            lbl.setObjectName(object_name)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _pulse_apply_btn(self) -> None:
        if not isinstance(self.apply_btn, GluePanelButton):
            return
        if not self._apply_pending:
            return
        self._apply_pulse = not self._apply_pulse
        self.apply_btn.set_pulse(self._apply_pulse)

    def _set_apply_pending(self, pending: bool) -> None:
        """Highlight Apply Changes when installs/removes are pending; mute when clean."""
        pending = bool(pending)
        changed = pending != self._apply_pending
        self._apply_pending = pending
        if pending:
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_role("primary")
                self.apply_btn.set_pulse(False)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setToolTip("Pending client mod changes — click to apply")
            if not self._apply_pulse_timer.isActive():
                self._apply_pulse = False
                self._apply_pulse_timer.start()
        else:
            self._apply_pulse_timer.stop()
            self._apply_pulse = False
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_role("standard")
                self.apply_btn.set_pulse(False)
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
