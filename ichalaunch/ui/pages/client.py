"""Client mods — category sidebar + one category panel at a time."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import LOCK_AV_VERIFY_MESSAGE
from ichalaunch.core.process import wow_exe_running
from ichalaunch.game.launcher import detect_game, sync_vanillafixes_enabled_from_desired
from ichalaunch.mods.client_mod_hints import (
    is_dll_injection_mod,
    should_show_mpq_patch_warning,
)
from ichalaunch.mods.client_presets import (
    APPLYABLE_PRESETS,
    FOG_PUSHBACK_ID,
    PRESET_CUSTOM,
    PRESET_HD_AIO,
    apply_client_preset,
    detect_matching_preset,
    fog_pushback_locked,
    mark_custom_preset,
)
from ichalaunch.mods.installer import (
    apply_mod_toggle,
    apply_vanillafixes_dxvk_choice,
    detect_actual_state,
    get_mod,
    load_mod_catalog,
    mod_contains_caption,
    mod_is_unverified,
    mod_version_label,
    plan_changes,
    reconcile_exclusive_desired_mods,
    resolve_mod_toggle,
    vanillafixes_dxvk_both_enabled,
)
from ichalaunch.mods.stock_patch import (
    STOCK_PATCH9_BANNER_TEXT,
    inspect_stock_patch9,
    should_offer_stock_patch9_reacquire,
)
from ichalaunch.ui.widgets.casting_bar_search_edit import CastingBarSearchEdit
from ichalaunch.ui.widgets.common import (
    MOD_EDIT_LOCKED_TIP,
    ModCheckRow,
    mod_author,
    mod_git_url,
    mod_open_url,
    open_url_in_browser,
    status_with_stamp,
)
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.dialogs import (
    DialogResult,
    choice,
    confirm,
    confirm_vanilla_tweaks_old,
    dll_security_exclusion_dialog,
    github_import_dialog,
    mpq_patch_warning_dialog,
    warning,
)
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton
from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel
from ichalaunch.ui.widgets.marble_bg import MarblePanel, MarbleScrollArea
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox
from ichalaunch.ui.widgets.theme_radio import ThemeRadioButton
from ichalaunch.ui.widgets.update_alert_badge import BadgeNavButton

LAUNCH_CATEGORY = "Launch"
FOG_BUNDLED_IN_HD_E_TIP = (
    "Fog Pushback is already included in Reforged HD Patch-E."
)
PRESETS_CATEGORY = "Presets"

CATEGORY_ORDER = [
    PRESETS_CATEGORY,
    "Performance & Fixes",
    "Client Enhancements",
    "HD Graphics",
    "Visual / QoL",
    LAUNCH_CATEGORY,
    "Custom",
]

# Title / plan / update-status share this extra left pad (page already has 16).
# Settings uses 24; 16+8 matches that and sits clear of the ~20px metal rail.
_HEADER_LEFT_INSET = 8
# Action row + search share this so plates and the bar clear the metal border.
_EDGE_INSET = 4


class ClientPage(QWidget):
    apply_clicked = Signal()
    rescan_clicked = Signal()
    check_updates_requested = Signal()
    update_mod_requested = Signal(str)
    reinstall_mod_requested = Signal(str)
    update_all_mods_requested = Signal()
    custom_dll_import_requested = Signal(str)
    reacquire_patch9_requested = Signal()
    badge_state_changed = Signal()
    open_git_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        # Small top clearance only — MainWindow already insets 66px for crest / caption.
        # 28px here was leftover MoA overhang padding and stacked a huge band under chrome.
        root.setContentsMargins(16, 6, 16, 12)
        root.setSpacing(8)

        title = QLabel("Client Fixes, Tweaks & Patches")
        title.setObjectName("SectionTitle")
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        title.setContentsMargins(_HEADER_LEFT_INSET, 0, 0, 0)
        root.addWidget(title)

        self._patch9_host = QWidget()
        self._patch9_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        patch9_l = QHBoxLayout(self._patch9_host)
        patch9_l.setContentsMargins(0, 0, 0, 0)
        patch9_l.setSpacing(8)
        self.patch9_lbl = QLabel(STOCK_PATCH9_BANNER_TEXT)
        self.patch9_lbl.setStyleSheet("color: #F1C22D;")
        self.patch9_lbl.setWordWrap(True)
        self.reacquire_patch9_btn = GluePanelButton(
            "Reacquire patch-9", role="primary", width=148, height=GLUE_BTN_H
        )
        self.reacquire_patch9_btn.setToolTip(
            "Download official Data/patch-9.mpq (~500 MB) into the client folder"
        )
        self.reacquire_patch9_btn.clicked.connect(self.reacquire_patch9_requested.emit)
        patch9_l.addWidget(self.patch9_lbl, 1)
        patch9_l.addWidget(self.reacquire_patch9_btn, 0)
        self._patch9_host.hide()
        root.addWidget(self._patch9_host)

        self._status_host = QWidget()
        self._status_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        status_l = QVBoxLayout(self._status_host)
        status_l.setContentsMargins(_HEADER_LEFT_INSET, 0, 0, 0)
        status_l.setSpacing(4)

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
        status_l.addLayout(self.loading_row)

        self.updates_lbl = QLabel("")
        self.updates_lbl.setStyleSheet("color: #F1C22D;")
        status_l.addWidget(self.updates_lbl)
        self._status_host.hide()
        root.addWidget(self._status_host)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Left category tabs — marble panel under nav buttons
        side = MarblePanel()
        side.setObjectName("ClientCatNav")
        side.setFixedWidth(200)
        side.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        side_l = QVBoxLayout(side)
        # Inset so hover/checked fills sit inside the rounded purple frame
        # (top item must not square-overflow the panel corner).
        side_l.setContentsMargins(4, 4, 4, 4)
        side_l.setSpacing(2)
        self.cat_btns: list[BadgeNavButton] = []
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
        self._game_edit_locked = False
        self._client_mods_scan_done = False
        self._cat_hosts: dict[str, QVBoxLayout] = {}
        self._cat_scrolls: dict[str, MarbleScrollArea] = {}
        self._cat_index: dict[str, int] = {}
        self._side_layout = side_l
        self._side_stretch_added = False
        self._search_q = ""
        self._vf_dxvk_prompted = False
        self._dxvk_gpu_warned = False
        self._applying_preset = False
        self._preset_radios: dict[str, ThemeRadioButton] = {}
        self._preset_hd_ultra_cb: ThemeCheckBox | None = None
        self._preset_button_group: QButtonGroup | None = None

        by_cat: dict[str, list] = {}
        for mod in load_mod_catalog():
            cat = mod.get("category") or "Other"
            # Legacy user_mods may still say Client Enhancements — keep Custom together.
            if mod.get("user_defined") and cat != "Custom":
                cat = "Custom"
                mod = dict(mod)
                mod["category"] = "Custom"
            if cat == LAUNCH_CATEGORY:
                cat = "Client Enhancements"
            by_cat.setdefault(cat, []).append(mod)
        # Always reserve Custom so Add DLL can land there even before first custom mod.
        by_cat.setdefault("Custom", [])
        # Launch is settings, not a catalog list — keep the tab even with no mods.
        by_cat.setdefault(LAUNCH_CATEGORY, [])
        by_cat.setdefault(PRESETS_CATEGORY, [])
        cats = [c for c in CATEGORY_ORDER if c in by_cat] + [
            c for c in by_cat if c not in CATEGORY_ORDER
        ]

        for i, cat in enumerate(cats):
            if cat == PRESETS_CATEGORY:
                self._add_presets_page(i)
            else:
                mods = [] if cat == LAUNCH_CATEGORY else by_cat[cat]
                self._add_category_page(cat, mods, i)

        self.launch_settings = LaunchSettingsPanel(self)
        launch_host = self._cat_hosts.get(LAUNCH_CATEGORY)
        if launch_host is not None:
            self._insert_row_before_stretch(launch_host, self.launch_settings)

        side_l.addStretch(1)
        self._side_stretch_added = True
        body.addWidget(side)
        body.addWidget(self.cat_stack, 1)
        root.addLayout(body, 1)

        self.plan_lbl = QLabel("")
        self.plan_lbl.setObjectName("Muted")
        self.plan_lbl.setWordWrap(True)
        self.plan_lbl.setContentsMargins(_HEADER_LEFT_INSET, 0, 0, 0)
        root.addWidget(self.plan_lbl)

        # Actions sit at the bottom near the play bar. 4px side inset so the
        # first/last plates do not clip the content-panel border.
        actions = QHBoxLayout()
        actions.setContentsMargins(_EDGE_INSET, 0, _EDGE_INSET, 0)
        actions.setSpacing(8)
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
        self.apply_btn.setProperty("flashHighlight", False)
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
        search_row.setContentsMargins(_EDGE_INSET, 0, _EDGE_INSET, 0)
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
        self._reveal_rows()
        self._game_lock_timer = QTimer(self)
        self._game_lock_timer.setInterval(2000)
        self._game_lock_timer.timeout.connect(self._poll_game_edit_lock)

    def _add_category_page(self, cat: str, mods: list[dict], index: int) -> None:
        btn = BadgeNavButton(cat)
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
        # Stretch first so _add_mod_row can insert before it. Inserting with
        # count()-1 *before* a stretch exists pushes the first mod to the bottom
        # (hd_dxvk was stuck last in HD Graphics for that reason).
        host_l.addStretch(1)
        for mod in mods:
            self._add_mod_row(mod, host_l)
        scroll.setWidget(host)
        page_l.addWidget(scroll, 1)
        self.cat_stack.addWidget(page)

    def _add_presets_page(self, index: int) -> None:
        btn = BadgeNavButton(PRESETS_CATEGORY)
        btn.setObjectName("CatNavButton")
        btn.setCheckable(True)
        apply_open_hand(btn)
        btn.clicked.connect(lambda checked=False, idx=index: self._show_cat(idx))
        if self._side_stretch_added:
            self._side_layout.insertWidget(self._side_layout.count() - 1, btn)
        else:
            self._side_layout.addWidget(btn)
        self.cat_btns.append(btn)
        self._cat_index[PRESETS_CATEGORY] = index

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
        host_l.setSpacing(10)
        self._cat_hosts[PRESETS_CATEGORY] = host_l
        self._cat_scrolls[PRESETS_CATEGORY] = scroll

        intro = QLabel(
            "Choose a preset to configure client mods automatically. "
            "Manual changes switch to Custom — use Apply Changes to install."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        host_l.addWidget(intro)

        self._preset_button_group = QButtonGroup(self)
        self._preset_button_group.setExclusive(True)

        preset_rows: list[tuple[str, str, str]] = [
            ("none", "None", "Client only — no preset-managed mods enabled."),
            (
                "basic",
                "Basic",
                "Core client DLLs, Vanilla Tweaks V2, VanillaFixes + DXVK, "
                "Auction Query, no1600, WDB cache block, Addon Script Memory = 0.",
            ),
            (
                "basic_plus",
                "Basic +",
                "Everything in Basic, plus PerfBoost, Patch-O, Pretty Night Sky, "
                "Epoch Water, DXVK 2.7.1, VanillaHelpers, HD Patches I, M, P.",
            ),
            (
                "hd_aio",
                "HD AIO",
                "Everything in Basic +, plus Reforged HD Patches A, B, C, D, E, G, S, T "
                "(standard textures by default). Fog Pushback is included via Patch E.",
            ),
            (
                "custom",
                "Custom",
                "Your mod selection no longer matches a preset. Pick None, Basic, "
                "Basic +, or HD AIO above to re-apply a profile.",
            ),
        ]
        for preset_id, title, blurb in preset_rows:
            row = QWidget()
            row_l = QVBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(4)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            radio = ThemeRadioButton(title, row)
            radio.setObjectName("ThemePresetRadio")
            self._preset_radios[preset_id] = radio
            self._preset_button_group.addButton(radio)
            head.addWidget(radio, 0)
            if preset_id == PRESET_HD_AIO:
                ultra = ThemeCheckBox("Ultra versions", row)
                ultra.setToolTip(
                    "Use Patch-T ultra base + Patch-U instead of standard Patch-T."
                )
                ultra.toggled.connect(self._on_preset_hd_ultra_toggled)
                self._preset_hd_ultra_cb = ultra
                head.addWidget(ultra, 0)
            head.addStretch(1)
            row_l.addLayout(head)
            desc = QLabel(blurb)
            desc.setObjectName("Muted")
            desc.setWordWrap(True)
            desc.setContentsMargins(28, 0, 0, 0)
            row_l.addWidget(desc)
            host_l.addWidget(row)
            if preset_id == PRESET_CUSTOM:
                radio.setEnabled(False)
            else:
                radio.toggled.connect(
                    lambda checked, pid=preset_id: self._on_preset_radio(pid, checked)
                )

        host_l.addStretch(1)
        scroll.setWidget(host)
        page_l.addWidget(scroll, 1)
        self.cat_stack.addWidget(page)
        self._sync_preset_radios()

    def _sync_preset_radios(self) -> None:
        preset_id = str(settings.get("client_preset") or PRESET_CUSTOM)
        hd_ultra = bool(settings.get("client_preset_hd_ultra"))
        if preset_id not in self._preset_radios:
            detected, detected_ultra = detect_matching_preset()
            preset_id = detected
            hd_ultra = detected_ultra
            if preset_id != PRESET_CUSTOM:
                settings.set("client_preset", preset_id)
                settings.set("client_preset_hd_ultra", hd_ultra)
        for pid, radio in self._preset_radios.items():
            radio.blockSignals(True)
            radio.setChecked(pid == preset_id)
            radio.blockSignals(False)
        if self._preset_hd_ultra_cb is not None:
            self._preset_hd_ultra_cb.blockSignals(True)
            self._preset_hd_ultra_cb.setChecked(hd_ultra)
            active_hd = preset_id == PRESET_HD_AIO and not self._game_edit_locked
            self._preset_hd_ultra_cb.setEnabled(active_hd)
            self._preset_hd_ultra_cb.blockSignals(False)

    def _on_preset_radio(self, preset_id: str, checked: bool) -> None:
        if not checked or self._game_edit_locked or self._applying_preset:
            return
        if preset_id not in APPLYABLE_PRESETS:
            return
        hd_ultra = False
        if preset_id == PRESET_HD_AIO and self._preset_hd_ultra_cb is not None:
            hd_ultra = self._preset_hd_ultra_cb.isChecked()
        self._apply_preset_choice(preset_id, hd_ultra=hd_ultra)

    def _on_preset_hd_ultra_toggled(self, checked: bool) -> None:
        del checked
        if self._game_edit_locked or self._applying_preset:
            return
        custom_radio = self._preset_radios.get(PRESET_HD_AIO)
        if custom_radio is None or not custom_radio.isChecked():
            return
        hd_ultra = (
            self._preset_hd_ultra_cb.isChecked()
            if self._preset_hd_ultra_cb is not None
            else False
        )
        self._apply_preset_choice(PRESET_HD_AIO, hd_ultra=hd_ultra)

    def _apply_preset_choice(self, preset_id: str, *, hd_ultra: bool) -> None:
        self._applying_preset = True
        try:
            apply_client_preset(preset_id, hd_ultra=hd_ultra)
            self.refresh_from_settings()
            self._sync_preset_radios()
        finally:
            self._applying_preset = False

    @staticmethod
    def _insert_row_before_stretch(layout: QVBoxLayout, row: QWidget) -> None:
        """Append a row immediately before a trailing stretch spacer, if any."""
        insert_at = layout.count()
        if insert_at > 0:
            last = layout.itemAt(insert_at - 1)
            if last is not None and last.spacerItem() is not None:
                insert_at -= 1
        layout.insertWidget(insert_at, row)

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
            if cat == LAUNCH_CATEGORY:
                cat = "Client Enhancements"
        contains_text = mod_contains_caption(mod)
        version = mod_version_label(mod, settings.installed_mods.get(mid))
        row = ModCheckRow(
            mid,
            mod["name"],
            desc,
            checked=bool(settings.desired_mods.get(mid, False)),
            author=mod_author(mod),
            contains=contains_text or None,
            version=version or None,
            has_settings=bool(mod.get("has_config")),
            parent=host_l.parentWidget() if host_l is not None else self,
        )
        row.toggled.connect(self._on_toggle)
        row.update_clicked.connect(self.update_mod_requested.emit)
        row.reinstall_clicked.connect(self.reinstall_mod_requested.emit)
        row.open_git_clicked.connect(self.open_git_requested.emit)
        row.open_link_clicked.connect(self._open_mod_link)
        row.settings_clicked.connect(self._open_mod_config)
        row.set_git_url(mod_git_url(mod))
        row.set_open_url(mod_open_url(mod))
        self._row_meta[mid] = {
            "category": cat,
            "name": str(mod.get("name") or mid),
            "description": str(mod.get("description") or ""),
            "note": str(mod.get("note") or ""),
            "author": str(mod_author(mod) or ""),
            "includes": contains_text,
            "version": version,
        }
        layout = host_l or self._cat_hosts.get(cat)
        if layout is not None:
            self._insert_row_before_stretch(layout, row)
        row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        row.show()
        row.set_editing_locked(self._game_edit_locked)
        self.rows[mid] = row
        if mid == FOG_PUSHBACK_ID:
            self._sync_fog_pushback_lock()
        return row

    def _open_mod_link(self, mod_id: str) -> None:
        mod = get_mod(mod_id) or {}
        url = mod_open_url(mod)
        if url:
            open_url_in_browser(url)

    def _open_mod_config(self, mod_id: str) -> None:
        if self._game_edit_locked:
            return
        if mod_id == "vanilla_tweaks":
            from ichalaunch.ui.widgets.dialogs import vanilla_tweaks_settings_dialog

            result = vanilla_tweaks_settings_dialog(self)
            if result and result.get("repatch"):
                self.reinstall_mod_requested.emit("vanilla_tweaks")
            return
        if mod_id == "vanilla_tweaks_old":
            from ichalaunch.ui.widgets.dialogs import vanilla_tweaks_old_settings_dialog

            result = vanilla_tweaks_old_settings_dialog(self)
            if result and result.get("repatch"):
                self.reinstall_mod_requested.emit("vanilla_tweaks_old")

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
            if cat == LAUNCH_CATEGORY:
                cat = "Client Enhancements"
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
            if cat == LAUNCH_CATEGORY:
                cat = "Client Enhancements"
            if cat not in self._cat_hosts:
                self._add_category_page(cat, [mod], len(self.cat_btns))
            else:
                self._add_mod_row(mod)

    def _open_custom_dll_dialog(self) -> None:
        if self._game_edit_locked:
            return
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
                        str(meta.get("includes") or ""),
                        str(meta.get("version") or ""),
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

    def _mod_category(self, mod_id: str) -> str:
        meta = self._row_meta.get(mod_id) or {}
        cat = str(meta.get("category") or "").strip()
        if cat:
            return cat
        mod = get_mod(mod_id) or {}
        if mod.get("user_defined"):
            return "Custom"
        return str(mod.get("category") or "Client Enhancements")

    def _categories_with_pending_badge(self) -> set[str]:
        """Categories that should show the update alert on the left nav."""
        cats: set[str] = set()
        for mid in self._pending_updates:
            cats.add(self._mod_category(mid))
        if self._apply_pending:
            for ch in plan_changes():
                action = ch.get("action")
                if action in ("error", ""):
                    continue
                mid = str(ch.get("id") or "").strip()
                if mid:
                    cats.add(self._mod_category(mid))
        return cats

    def _refresh_cat_badges(self) -> None:
        pending_cats = self._categories_with_pending_badge()
        for cat, idx in self._cat_index.items():
            if 0 <= idx < len(self.cat_btns):
                self.cat_btns[idx].set_badge_visible(cat in pending_cats)

    def _sync_status_host(self) -> None:
        """Collapse the update/progress strip when idle so the lists can grow."""
        show_load = self.loading_lbl.isVisible() or self.loading_bar.isVisible()
        show_upd = bool(self.updates_lbl.text())
        self.updates_lbl.setVisible(show_upd)
        self._status_host.setVisible(show_load or show_upd)

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        # Progress lives on the bottom bar; keep only the Check Updates button gated.
        self.loading_lbl.setText("")
        self.loading_lbl.setVisible(False)
        self.loading_bar.setVisible(False)
        self.check_btn.setEnabled(not busy)
        self._sync_status_host()

    def set_updates(self, updates: list[dict]) -> None:
        self._pending_updates = {u["id"]: u for u in updates if u.get("id")}
        self._client_mods_scan_done = True
        n = len(self._pending_updates)
        if n:
            self.updates_lbl.setText(f"{n} client mod update(s) available")
        else:
            self.updates_lbl.setText("")
        self._sync_status_host()
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()
        self._refresh_cat_badges()
        self._sync_game_lock_actions()
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
        self._sync_status_host()
        self.update_all_btn.setEnabled(n > 0)
        self.refresh_from_settings()
        self._refresh_cat_badges()
        self._sync_game_lock_actions()
        self.badge_state_changed.emit()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._reveal_rows()
        if getattr(self, "launch_settings", None) is not None:
            self.launch_settings.refresh()
        self._poll_game_edit_lock()
        if not self._game_lock_timer.isActive():
            self._game_lock_timer.start()
        QTimer.singleShot(0, self._maybe_prompt_vf_dxvk_conflict)
        # Patch-9 dialog is Home-first (MainWindow); Client keeps banner + fallback.
        QTimer.singleShot(0, self._maybe_prompt_stock_patch9)
        QTimer.singleShot(0, self._maybe_prompt_high_farclip)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._game_lock_timer.stop()
        super().hideEvent(event)

    def _poll_game_edit_lock(self) -> None:
        self._set_game_edit_locked(
            wow_exe_running(detect_game() or settings.game_path or None)
        )

    def _set_game_edit_locked(self, locked: bool) -> None:
        self._game_edit_locked = bool(locked)
        for row in self.rows.values():
            row.set_editing_locked(self._game_edit_locked)
        for pid, radio in self._preset_radios.items():
            if pid == PRESET_CUSTOM:
                continue
            radio.setEnabled(not self._game_edit_locked)
        self._sync_preset_radios()
        self._sync_fog_pushback_lock()
        self._sync_game_lock_actions()

    def _sync_fog_pushback_lock(self) -> None:
        locked = fog_pushback_locked()
        if locked and settings.desired_mods.get(FOG_PUSHBACK_ID):
            settings.set_desired_mod(FOG_PUSHBACK_ID, False)
        row = self.rows.get(FOG_PUSHBACK_ID)
        if row is None:
            return
        row.set_feature_locked(
            locked, FOG_BUNDLED_IN_HD_E_TIP if locked else ""
        )
        if locked:
            row.cb.blockSignals(True)
            row.cb.setChecked(False)
            row.cb.blockSignals(False)

    def _sync_game_lock_actions(self) -> None:
        locked = self._game_edit_locked
        pending = self._apply_pending
        if locked:
            self.apply_btn.setEnabled(False)
            self.apply_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            self._apply_pulse_timer.stop()
            self._apply_pulse = False
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_pulse(False)
            self.update_all_btn.setEnabled(False)
            self.update_all_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            self.add_dll_btn.setEnabled(False)
            self.add_dll_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            self.reacquire_patch9_btn.setEnabled(False)
            self.reacquire_patch9_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            return
        self.apply_btn.setEnabled(pending)
        self.apply_btn.setToolTip(
            "Pending client mod changes — click to apply"
            if pending
            else "No pending client mod changes"
        )
        if pending:
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_pulse(True)
            if not self._apply_pulse_timer.isActive():
                self._apply_pulse = True
                self._apply_pulse_timer.start()
        else:
            self._apply_pulse_timer.stop()
            self._apply_pulse = False
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_pulse(False)
        self.update_all_btn.setEnabled(bool(self._pending_updates))
        self.update_all_btn.setToolTip("")
        self.add_dll_btn.setEnabled(True)
        self.add_dll_btn.setToolTip("Add a client DLL from a GitHub release")
        self.reacquire_patch9_btn.setEnabled(True)
        self.reacquire_patch9_btn.setToolTip(
            "Download official Data/patch-9.mpq (~500 MB) into the client folder"
        )

    def _maybe_prompt_vf_dxvk_conflict(self) -> None:
        if self._game_edit_locked:
            return
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
        if self._dxvk_gpu_warned or not (
            settings.desired_mods.get("dxvk") or settings.desired_mods.get("hd_dxvk")
        ):
            return
        from ichalaunch.core.gpu_compat import assess_dxvk_gpu

        level, _gpus, message = assess_dxvk_gpu()
        if level == "ok":
            return
        self._dxvk_gpu_warned = True
        warning(self, "Graphics compatibility", message)

    def _maybe_show_dll_security_hint(self, mod_id: str, enabled: bool) -> None:
        if not enabled:
            return
        if settings.get("dismissed_dll_security_exclusion_hint"):
            return
        if settings.get("dll_security_exclusion_hint_shown"):
            return
        mod = get_mod(mod_id)
        if not is_dll_injection_mod(mod):
            return
        game = detect_game()
        folder = str(game) if game else (settings.game_path or "").strip()
        dismissed = dll_security_exclusion_dialog(self, folder)
        settings.set("dll_security_exclusion_hint_shown", True)
        if dismissed:
            settings.set("dismissed_dll_security_exclusion_hint", True)

    def _maybe_show_mpq_patch_warning(self, mod_id: str, enabled: bool) -> None:
        if not should_show_mpq_patch_warning(
            get_mod(mod_id),
            enabled=enabled,
            dismissed=bool(settings.get("dismissed_mpq_patch_warning")),
        ):
            return
        if mpq_patch_warning_dialog(self):
            settings.set("dismissed_mpq_patch_warning", True)

    def _reveal_rows(self) -> None:
        """Clear HWND-guard flags leftover from AddonRow and show catalog rows."""
        q = self._search_q
        for row in self.rows.values():
            row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            if not q:
                row.show()
        if q:
            self._apply_search()

    def _show_cat(self, idx: int) -> None:
        self.cat_stack.setCurrentIndex(idx)
        for i, b in enumerate(self.cat_btns):
            b.setChecked(i == idx)

    def _on_toggle(self, mod_id: str, enabled: bool) -> None:
        if self._game_edit_locked:
            row = self.rows.get(mod_id)
            if row is not None:
                row.cb.blockSignals(True)
                row.cb.setChecked(not enabled)
                row.cb.blockSignals(False)
            return
        if mod_id == FOG_PUSHBACK_ID and fog_pushback_locked():
            row = self.rows.get(mod_id)
            if row is not None:
                row.cb.blockSignals(True)
                row.cb.setChecked(False)
                row.cb.blockSignals(False)
            return
        if enabled and mod_id == "vanilla_tweaks_old":
            if not confirm_vanilla_tweaks_old(self):
                row = self.rows.get(mod_id)
                if row is not None:
                    row.cb.blockSignals(True)
                    row.cb.setChecked(False)
                    row.cb.blockSignals(False)
                return
        if not enabled:
            preview = resolve_mod_toggle(mod_id, False)
            cascade_off = [
                mid
                for mid, state in preview.items()
                if mid != mod_id and state is False
            ]
            if cascade_off and not self._confirm_disable_cascade(mod_id, cascade_off):
                row = self.rows.get(mod_id)
                if row is not None:
                    row.cb.blockSignals(True)
                    row.cb.setChecked(True)
                    row.cb.blockSignals(False)
                return
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
        sync_vanillafixes_enabled_from_desired(desired)
        if getattr(self, "launch_settings", None) is not None:
            self.launch_settings.refresh()
        if enabled and mod_id in ("dxvk", "hd_dxvk"):
            QTimer.singleShot(0, self._maybe_warn_dxvk_gpu)
        if enabled:
            QTimer.singleShot(0, lambda: self._maybe_show_dll_security_hint(mod_id, enabled))
            QTimer.singleShot(0, lambda: self._maybe_show_mpq_patch_warning(mod_id, enabled))
        if enabled and mod_id in ("vanilla_tweaks", "vanilla_tweaks_old"):
            QTimer.singleShot(0, lambda mid=mod_id: self._open_mod_config(mid))
        if not self._applying_preset:
            mark_custom_preset()
            self._sync_preset_radios()
        self._sync_fog_pushback_lock()
        self.refresh_plan()

    def _confirm_disable_cascade(self, mod_id: str, cascade_ids: list[str]) -> bool:
        """Ask before disabling a mod that also turns off dependents."""
        primary = get_mod(mod_id) or {}
        primary_name = str(primary.get("name") or mod_id)
        lines: list[str] = []
        for mid in cascade_ids:
            mod = get_mod(mid) or {}
            lines.append(f"• {mod.get('name') or mid}")
        body = (
            f"Disabling {primary_name} will also disable:\n\n"
            + "\n".join(lines)
            + "\n\nContinue?"
        )
        if mod_id == "dxvk":
            body += (
                "\n\nThis removes the DXVK layer entirely (d3d9.dll / dxvk.conf). "
                "Tip: enable regular VanillaFixes if you want VanillaFixes "
                "without Vulkan."
            )
        elif mod_id == "hd_dxvk":
            body += (
                "\n\nVanillaFixes + DXVK (Vulkan) stays on; "
                "d3d9.dll and dxvk.conf revert to the VanillaFixes-bundled layer."
            )
        elif mod_id == "vanillafixes":
            body += (
                "\n\nMods that required VanillaFixes will be unchecked as well."
            )
        return confirm(self, "Also disable related mods?", body)

    def _refresh_patch9_banner(self) -> None:
        game = detect_game()
        status = inspect_stock_patch9(game) if game else None
        if should_offer_stock_patch9_reacquire(status):
            self.patch9_lbl.setText(STOCK_PATCH9_BANNER_TEXT)
            self._patch9_host.show()
        else:
            self._patch9_host.hide()

    def _maybe_prompt_stock_patch9(self) -> None:
        """Fallback if Home never ran; MainWindow owns once-per-session dismiss."""
        win = self.window()
        stack = getattr(win, "stack", None)
        if stack is not None and stack.currentWidget() is not self:
            return
        fn = getattr(win, "_maybe_prompt_stock_patch9", None)
        if callable(fn):
            fn()

    def _maybe_prompt_high_farclip(self) -> None:
        """Fallback if Home never ran; MainWindow owns once-per-session dismiss."""
        if self._game_edit_locked:
            return
        win = self.window()
        stack = getattr(win, "stack", None)
        if stack is not None and stack.currentWidget() is not self:
            return
        fn = getattr(win, "_maybe_prompt_high_farclip", None)
        if callable(fn):
            fn()

    @staticmethod
    def _mod_can_reinstall(mod: dict) -> bool:
        """True when a downloadable source exists (same gate as update checks)."""
        kind = mod.get("kind")
        if kind in ("manual_link", "wdb_block", "config_script_memory"):
            return False
        return bool(mod.get("source"))

    def refresh_from_settings(self) -> None:
        self.sync_catalog_rows()
        if getattr(self, "launch_settings", None) is not None:
            self.launch_settings.refresh()
        game = detect_game()
        actual = detect_actual_state(game) if game else {}
        desired = reconcile_exclusive_desired_mods(
            dict(settings.desired_mods), actual=actual
        )
        if desired != settings.desired_mods:
            settings.set("desired_mods", desired)
        sync_vanillafixes_enabled_from_desired(desired)
        installed_meta = settings.installed_mods
        catalog = {m["id"]: m for m in load_mod_catalog()}
        for mid, row in self.rows.items():
            row.cb.blockSignals(True)
            row.cb.setChecked(bool(desired.get(mid, False)))
            row.cb.blockSignals(False)
            can_ri = self._mod_can_reinstall(catalog.get(mid) or {})
            mod_entry = catalog.get(mid) or {}
            row.set_git_url(mod_git_url(mod_entry))
            row.set_open_url(mod_open_url(mod_entry))
            version = mod_version_label(catalog.get(mid), installed_meta.get(mid))
            row.set_version(version or None)
            meta = self._row_meta.get(mid)
            if meta is not None:
                meta["version"] = version
            pending = self._pending_updates.get(mid)
            meta_inst = installed_meta.get(mid)
            unverified = mod_is_unverified(mid, meta_inst if isinstance(meta_inst, dict) else None)
            if pending:
                detail = f"{pending.get('local', '?')} → {pending.get('remote', '?')}"
                row.status_lbl.setText(f"Update available ({detail})")
                row.status_lbl.setToolTip("")
                self._set_status_style(row.status_lbl, "StatusUpdate")
                row.set_update_available(True, detail)
                row.set_reinstall_visible(can_ri)
            elif unverified and (actual.get(mid) or desired.get(mid)):
                row.status_lbl.setText("Unverified")
                row.status_lbl.setToolTip(LOCK_AV_VERIFY_MESSAGE)
                self._set_status_style(row.status_lbl, "StatusWarning")
                row.set_update_available(False)
                row.set_reinstall_visible(can_ri)
            elif actual.get(mid):
                if self._client_mods_scan_done:
                    row.status_lbl.setText(status_with_stamp("Up to date", installed_meta.get(mid)))
                    self._set_status_style(row.status_lbl, "StatusOk")
                else:
                    row.status_lbl.setText("Not checked")
                    self._set_status_style(row.status_lbl, "StatusMuted")
                row.status_lbl.setToolTip("")
                row.set_update_available(False)
                row.set_reinstall_visible(can_ri)
            else:
                row.status_lbl.setText("Not installed")
                row.status_lbl.setToolTip("")
                self._set_status_style(row.status_lbl, "StatusMuted")
                row.set_update_available(False)
                row.set_reinstall_visible(False)
        if self._search_q:
            self._apply_search()
        self.refresh_plan()
        self._set_game_edit_locked(self._game_edit_locked)
        if vanillafixes_dxvk_both_enabled():
            QTimer.singleShot(0, self._maybe_prompt_vf_dxvk_conflict)
        self._refresh_patch9_banner()
        self._refresh_cat_badges()
        if not self._applying_preset:
            detected, ultra = detect_matching_preset(desired)
            stored = str(settings.get("client_preset") or "")
            if stored == PRESET_CUSTOM:
                pass
            elif detected == PRESET_CUSTOM:
                settings.set("client_preset", PRESET_CUSTOM)
            elif detected != stored or (
                detected == PRESET_HD_AIO
                and bool(settings.get("client_preset_hd_ultra")) != ultra
            ):
                settings.set("client_preset", detected)
                settings.set("client_preset_hd_ultra", ultra)
            self._sync_preset_radios()

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

    def _set_apply_flash_property(self, pending: bool) -> None:
        self.apply_btn.setProperty("flashHighlight", bool(pending))
        self.apply_btn.style().unpolish(self.apply_btn)
        self.apply_btn.style().polish(self.apply_btn)
        self.apply_btn.update()

    def _sync_row_pending_badges(self, changes: list[dict] | None) -> None:
        pending_ids: set[str] = set()
        for ch in changes or []:
            if ch.get("action") in ("error", ""):
                continue
            mid = str(ch.get("id") or "").strip()
            if mid:
                pending_ids.add(mid)
        for mid, row in self.rows.items():
            try:
                row.set_pending_change(mid in pending_ids)
            except RuntimeError:
                continue

    def _set_apply_pending(self, pending: bool) -> None:
        """Highlight Apply Changes when installs/removes are pending; mute when clean."""
        pending = bool(pending)
        changed = pending != self._apply_pending
        self._apply_pending = pending
        if pending:
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_role("primary")
            self._set_apply_flash_property(True)
        else:
            if isinstance(self.apply_btn, GluePanelButton):
                self.apply_btn.set_role("standard")
            self._set_apply_flash_property(False)
        self._sync_game_lock_actions()
        if changed:
            self._refresh_cat_badges()
            self.badge_state_changed.emit()

    def refresh_plan(self) -> None:
        game = detect_game()
        if not game:
            self.plan_lbl.setText("Set a game path in Settings before applying mods.")
            self._set_apply_pending(False)
            self._sync_row_pending_badges([])
            return
        changes = plan_changes()
        if not changes:
            self.plan_lbl.setText("Desired state matches installed client.")
            self._set_apply_pending(False)
            self._sync_row_pending_badges([])
            return
        lines = ["Pending: " + " · ".join(c["detail"] for c in changes[:8])]
        if len(changes) > 8:
            lines.append(f"…and {len(changes) - 8} more")
        self.plan_lbl.setText("\n".join(lines))
        self._set_apply_pending(True)
        self._sync_row_pending_badges(changes)
