"""Addons browse / install — scrollable lists that never grow the window."""

from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.addons.github import catalog_locks_updates, catalog_pin_tag, load_catalog
from ichalaunch.addons.loadstate import addon_disk_path, addon_is_loaded, set_addon_loaded
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import (
    catalog_index,
    merge_addon_meta,
    read_git_origin_url,
    resolve_catalog_entry,
    scan_installed_addon_folders,
)
from ichalaunch.game.launcher import resolve_addons_dir
from ichalaunch.ui.widgets.casting_bar_search_edit import CastingBarSearchEdit
from ichalaunch.ui.widgets.common import (
    AddonRow,
    addon_fork_label,
    addon_version_label,
    is_turtle_wow_custom_addon,
    open_url_in_browser,
    status_with_stamp,
)
from ichalaunch.ui.widgets.dialogs import (
    catalog_suggest_dialog,
    github_import_dialog,
    github_preview_dialog,
)
from ichalaunch.ui.widgets import dialogs as themed
from ichalaunch.ui.widgets.glue_combo import GlueComboBox
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton
from ichalaunch.ui.widgets.marble_bg import MarbleListWidget, MarblePanel

PAGE_SIZE = 80
INSTALLED_ROW_H = 48
# After the combo popup hides, Qt is still tearing down the native popup HWND
# and may still deliver currentTextChanged. Rebuilds in that window crash.
LIST_FREEZE_MS = 180
# Search must not rebuild on every keystroke — Available catalog is huge.
SEARCH_DEBOUNCE_MS = 220
_SCAN_TIP = "Wait for scan to finish"
_INIT_TIP = "Wait for addons to finish loading"


def _copied_addon_status(meta: dict | None) -> bool:
    """True when this folder was detected/copied, not an IchaLaunch-tracked install."""
    if not meta:
        return True
    if str(meta.get("installed_commit") or "").strip():
        return False
    if meta.get("updated_at") or meta.get("installed_at"):
        return False
    return True


def _hide_list_item_widgets(lw: MarbleListWidget) -> None:
    """Hide item widgets before clear so Qt never unparents a *visible* row.

    On Windows, setParent(None) while visible promotes the widget to a real
    top-level HWND — that is the rapid-fire mini-window spam after refresh.
    """
    try:
        count = lw.count()
    except RuntimeError:
        return
    for i in range(count):
        try:
            it = lw.item(i)
            if it is None:
                continue
            w = lw.itemWidget(it)
            if w is not None:
                w.hide()
                w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        except RuntimeError:
            continue


def _safe_clear_list(lw: MarbleListWidget) -> None:
    _hide_list_item_widgets(lw)
    try:
        lw.clear()
    except RuntimeError:
        return


def _mount_row(lw: MarbleListWidget, item: QListWidgetItem, row: AddonRow) -> None:
    """Attach a row as a viewport child. Never show() it here (HWND spam)."""
    row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    lw.addItem(item)
    lw.setItemWidget(item, row)
    # setItemWidget may show() internally — keep the row off-screen until reveal.
    row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)


def _reveal_item_widgets(lw: MarbleListWidget, page: QWidget | None = None) -> None:
    """Clear DontShowOnScreen only when the list and page are actually on screen.

    Do not call row.show() — that creates top-level HWNDs on Windows during first
    mount. setItemWidget already parented rows to the viewport.
    """
    page_ok = page is None or (
        page.isVisible() and not page.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    )
    if not lw.isVisible() or not page_ok:
        return
    try:
        lw.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        count = lw.count()
    except RuntimeError:
        return
    for i in range(count):
        try:
            it = lw.item(i)
            if it is None:
                continue
            w = lw.itemWidget(it)
            if w is None:
                continue
            w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        except RuntimeError:
            continue


class AddonsPage(QWidget):
    install_requested = Signal(dict)
    update_requested = Signal(dict)
    reinstall_requested = Signal(dict)
    update_all_requested = Signal()
    remove_requested = Signal(str)
    github_import_requested = Signal(str)
    check_updates_requested = Signal()
    rescan_requested = Signal()
    badge_state_changed = Signal()
    open_git_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Never a top-level window; stay off-screen until the user opens ADDONS.
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.hide()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        # Small top clearance only — MainWindow already insets for −/X and crest.
        layout.setContentsMargins(16, 6, 16, 12)
        layout.setSpacing(8)

        self.loading_row = QHBoxLayout()
        self.loading_lbl = QLabel("")
        self.loading_lbl.setStyleSheet("color: #F1C22D;")
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setFixedWidth(120)
        self.loading_bar.setTextVisible(False)
        self.loading_row.addWidget(self.loading_lbl)
        self.loading_row.addWidget(self.loading_bar)
        self.loading_row.addStretch(1)

        self.updates_lbl = QLabel("")
        self.updates_lbl.setStyleSheet("color: #F1C22D;")

        self.installed_hdr = QLabel("Installed")
        self.installed_hdr.setObjectName("SectionTitle")

        # MarbleListWidget scrolls inside its viewport — won't stretch the window
        self.installed_list = MarbleListWidget(self)
        self.installed_list.setSpacing(2)
        self.installed_list.setUniformItemSizes(False)
        self.installed_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.installed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.installed_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.installed_list.setMinimumHeight(80)
        self.installed_list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.installed_list.hide()

        self.avail_hdr = QLabel("Available")
        self.avail_hdr.setObjectName("SectionTitle")

        self.list = MarbleListWidget(self)
        self.list.setSpacing(2)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self._install_selected)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.list.setMinimumHeight(80)
        self.list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.list.hide()

        action_row = QHBoxLayout()
        self.prev_btn = GluePanelButton("◀ Prev", self)
        self.next_btn = GluePanelButton("Next ▶", self)
        self.prev_btn.clicked.connect(lambda: self._page(-1))
        self.next_btn.clicked.connect(lambda: self._page(1))
        self.page_lbl = QLabel("")
        self.page_lbl.setObjectName("Muted")
        action_row.addStretch(1)
        action_row.addWidget(self.prev_btn)
        action_row.addWidget(self.page_lbl)
        action_row.addWidget(self.next_btn)

        # Catalog search — top of marble panel, right of Installed / category filters
        # Match GlueCombo / toolbar button height (GLUE_BTN_H) in this row.
        self.search = CastingBarSearchEdit(minimum_height=GLUE_BTN_H)
        self.search.setFixedHeight(GLUE_BTN_H)
        self.search.setPlaceholderText("Search catalog…")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search_changed)
        self.search.textChanged.connect(self._on_search_text)

        self.filter_box = GlueComboBox(min_width=160, height=GLUE_BTN_H)
        self.filter_box.blockSignals(True)
        self.filter_box.addItems(["Installed", "Available", "Update Available", "All"])
        self.filter_box.blockSignals(False)
        self.filter_box.currentTextChanged.connect(self._on_filter_changed)
        self.filter_box.popupShown.connect(self._on_combo_popup_shown)
        self.filter_box.popupHidden.connect(self._on_combo_popup_hidden)

        self.cat_box = GlueComboBox(min_width=160, height=GLUE_BTN_H)
        self.cat_box.blockSignals(True)
        self.cat_box.addItem("All categories")
        self.cat_box.blockSignals(False)
        self.cat_box.currentTextChanged.connect(self._on_filter_changed)
        self.cat_box.popupShown.connect(self._on_combo_popup_shown)
        self.cat_box.popupHidden.connect(self._on_combo_popup_hidden)

        # Filter dropdowns replace the old section title; search stays on the right.
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.filter_box, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.cat_box, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        title_row.addWidget(self.search, 2)

        # Actions sit at the bottom near the play bar
        tools = QHBoxLayout()
        check_btn = GluePanelButton("Check Updates", self)
        check_btn.clicked.connect(self.check_updates_requested.emit)
        self.check_btn = check_btn
        update_all_btn = GluePanelButton("Update All", self, role="primary")
        update_all_btn.clicked.connect(self.update_all_requested.emit)
        rescan_btn = GluePanelButton("Rescan Disk", self)
        rescan_btn.clicked.connect(self.rescan_requested.emit)
        import_btn = GluePanelButton("+ Git Repo", self)
        import_btn.clicked.connect(self._open_github_import_dialog)
        suggest_btn = GluePanelButton("Suggest for catalog", self, width=168)
        suggest_btn.setToolTip(
            "Propose a public GitHub addon for the shared Available list "
            "(no GitHub login — posts to the configured HTTPS endpoint)."
        )
        suggest_btn.clicked.connect(self._open_catalog_suggest_dialog)
        self.suggest_btn = suggest_btn

        tools.addStretch(1)
        tools.addWidget(rescan_btn)
        tools.addWidget(check_btn)
        tools.addWidget(update_all_btn)
        tools.addWidget(import_btn)
        tools.addWidget(suggest_btn)

        # Outer marble window: installed section and available section are siblings.
        # Pagination lives *inside* the available section so Prev/Next never steals
        # height from (or reveals) the installed list.
        self.installed_section = QWidget(self)
        self.installed_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        inst_l = QVBoxLayout(self.installed_section)
        inst_l.setContentsMargins(0, 0, 0, 0)
        inst_l.setSpacing(8)
        inst_l.addWidget(self.installed_hdr)
        inst_l.addWidget(self.installed_list, 1)

        self.avail_section = QWidget(self)
        self.avail_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        avail_l = QVBoxLayout(self.avail_section)
        avail_l.setContentsMargins(0, 0, 0, 0)
        avail_l.setSpacing(8)
        avail_l.addWidget(self.avail_hdr)
        avail_l.addWidget(self.list, 1)
        avail_l.addLayout(action_row)
        self.installed_section.hide()
        self.avail_section.hide()

        addons_win = MarblePanel(radius=10.0)
        addons_win.setObjectName("AddonsWindow")
        win_l = QVBoxLayout(addons_win)
        win_l.setContentsMargins(10, 10, 10, 10)
        win_l.setSpacing(8)
        win_l.addLayout(title_row)
        win_l.addWidget(self.installed_section, 1)
        win_l.addWidget(self.avail_section, 1)

        layout.addLayout(self.loading_row)
        layout.addWidget(self.updates_lbl)
        layout.addWidget(addons_win, 1)
        layout.addLayout(tools)

        self._pending_updates: list[dict] = []
        self._addons_scan_done = False
        self._catalog_cache = load_catalog()
        self._dirty = True
        self._page_index = 0
        self._filtered_available: list[dict] = []
        self._allow_hidden_build = False
        self._want_installed_visible = True
        self._want_avail_visible = False
        self._refreshing = False
        self._scanning = False
        self._lists_ready = False
        self._available_base: list[dict] = []
        self._available_base_ready = False
        self._installed_lower: set[str] = set()
        self._rendering_avail = False
        self._pending_avail_search = False
        self._list_freeze_until = 0.0
        # True when installed_list was built with only update rows (Update Available refresh).
        self._installed_built_for_updates_only = False
        self._pending_list_work: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(LIST_FREEZE_MS)
        self._flush_timer.timeout.connect(self._on_flush_timer)
        self._git_kick_timer = QTimer(self)
        self._git_kick_timer.setSingleShot(True)
        self._git_kick_timer.setInterval(300)
        self._git_kick_timer.timeout.connect(self._kick_deferred_git_checks)

        cats = sorted({e.get("category") or "General" for e in self._catalog_cache})
        self.cat_box.blockSignals(True)
        for c in cats:
            self.cat_box.addItem(c)
        self.cat_box.blockSignals(False)
        self.set_checking(False)
        self._sync_filter_lock()
        # Do not refresh() here — building rows before the page is in the stack
        # (or while hidden) is the launch-time HWND spam. First paint is on show.

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        # Progress lives on the bottom bar; keep only the Check Updates button gated.
        self.loading_lbl.setText("")
        self.loading_lbl.setVisible(False)
        self.loading_bar.setVisible(False)
        self.check_btn.setEnabled(not busy)
        if busy:
            self.set_scanning(True)

    def set_scanning(self, busy: bool) -> None:
        """Disable filter combos while an update scan or list-rebuild worker runs."""
        self._scanning = bool(busy)
        self._sync_filter_lock()

    def _sync_filter_lock(self) -> None:
        """Combo stays off until first list is built, during scan/rebuild, and post-popup cooldown."""
        cooling = time.monotonic() < getattr(self, "_list_freeze_until", 0.0)
        lock = (
            not self._lists_ready
            or self._scanning
            or self._refreshing
            or self._rendering_avail
            or cooling
        )
        if not self._lists_ready:
            tip = _INIT_TIP
        elif lock:
            tip = _SCAN_TIP
        else:
            tip = ""
        for box in (self.filter_box, self.cat_box):
            try:
                popup_open = False
                is_open = getattr(box, "isPopupOpen", None)
                if callable(is_open):
                    popup_open = bool(is_open())
                if lock and popup_open:
                    # Never touch enable/hide while the native popup HWND is
                    # still tearing down (GlueCombo isPopupOpen includes that).
                    continue
                if lock:
                    box.setEnabled(False)
                    box.setToolTip(tip)
                else:
                    box.setEnabled(True)
                    box.setToolTip("")
            except RuntimeError:
                continue

    @property
    def pending_updates(self) -> list[dict]:
        return list(self._pending_updates)

    def mark_dirty(self) -> None:
        self._dirty = True

    def _open_github_import_dialog(self) -> None:
        url = github_import_dialog(self, kind="addon")
        if url:
            self.github_import_requested.emit(url)

    def _catalog_categories(self) -> list[str]:
        cats = sorted(
            {
                str(e.get("category") or "").strip()
                for e in (self._catalog_cache or [])
                if str(e.get("category") or "").strip()
            }
        )
        return cats

    def _open_catalog_suggest_dialog(self) -> None:
        catalog_suggest_dialog(
            self,
            categories=self._catalog_categories(),
            catalog_entries=self._catalog_cache,
        )

    def open_preview(self, entry: dict) -> None:
        from ichalaunch.ui.widgets.common import github_repo_browse_url

        folder = entry.get("folder") or entry.get("name")
        git_origin = None
        if folder:
            disk = addon_disk_path(str(folder))
            if disk is not None:
                git_origin = read_git_origin_url(disk)
        url = github_repo_browse_url(
            git_origin,
            entry.get("repo"),
            entry.get("url"),
            entry.get("repository"),
        )
        if url:
            github_preview_dialog(self, url)

    def open_addon_settings(self, entry: dict) -> None:
        from ichalaunch.ui.widgets.dialogs import addon_settings_dialog

        folder = str(entry.get("folder") or entry.get("name") or "")
        meta = settings.installed_addons.get(folder) if folder else None
        updated = addon_settings_dialog(self, entry, meta=meta)
        if not updated:
            return
        entry.clear()
        entry.update(updated)
        folder_key = str(entry.get("folder") or entry.get("name") or "")
        if folder_key and isinstance(meta, dict):
            tag = str(entry.get("tag") or entry.get("pin_release") or "").strip()
            if tag:
                meta = dict(meta)
                meta["tag"] = tag
                meta["version"] = tag
                settings.installed_addons[folder_key] = meta
                settings.save()
        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item is None:
                continue
            row = self.installed_list.itemWidget(item)
            if isinstance(row, AddonRow) and str(row.entry.get("folder") or "") == folder_key:
                row.entry.clear()
                row.entry.update(updated)
                break

    def set_addon_loaded_ui(self, entry: dict, loaded: bool) -> None:
        folder = str(entry.get("folder") or entry.get("name") or "")
        if not folder:
            return
        try:
            set_addon_loaded(folder, loaded)
        except Exception as exc:  # noqa: BLE001
            from ichalaunch.addons.loadstate import GAME_LOCK_MESSAGE, addon_move_error_text

            themed.error(self, "Load / unload failed", addon_move_error_text(exc) or GAME_LOCK_MESSAGE)
            for i in range(self.installed_list.count()):
                it = self.installed_list.item(i)
                if it is None:
                    continue
                row = self.installed_list.itemWidget(it)
                if isinstance(row, AddonRow) and str(row.entry.get("folder") or "") == folder:
                    row.set_loaded(not loaded)
            return
        entry["loaded"] = loaded

    def set_updates(self, updates: list[dict]) -> None:
        # Drop Never Update packs (and any stale pending entries for them)
        filtered = [
            u
            for u in updates
            if u.get("folder") and not settings.is_addon_never_update(str(u.get("folder")))
        ]
        self._pending_updates = filtered
        self._addons_scan_done = True
        if filtered:
            self.updates_lbl.setText(f"{len(filtered)} update(s) available")
        else:
            self.updates_lbl.setText("")
        # Scan callback must NEVER clear()/rebuild lists. In-place status only,
        # and only after the combo popup + cooldown have settled.
        self._request_list_work("patch")
        self.badge_state_changed.emit()

    def _combo_popup_open(self) -> bool:
        for box in (self.filter_box, self.cat_box):
            is_open = getattr(box, "isPopupOpen", None)
            if callable(is_open) and is_open():
                return True
        return False

    def _lists_frozen(self) -> bool:
        """True while a combo popup is up or we are inside the post-hide cooldown."""
        if self._combo_popup_open():
            return True
        return time.monotonic() < self._list_freeze_until

    def _on_combo_popup_shown(self) -> None:
        # Cancel any pending flush — opening again means Qt is using the popup HWND.
        self._flush_timer.stop()

    def _on_combo_popup_hidden(self) -> None:
        # Do not rebuild on this event. Hide fires in the middle of QComboBox
        # teardown; currentTextChanged may still be delivered after _popup_open
        # is already False. Hold list mutations for LIST_FREEZE_MS.
        self._list_freeze_until = time.monotonic() + (LIST_FREEZE_MS / 1000.0)
        self._sync_filter_lock()
        self._arm_flush_timer()

    def _on_filter_changed(self, *_args) -> None:
        """Combo changed — never full-rebuild synchronously (popup still dying)."""
        src = self.sender()
        if src is self.cat_box:
            self._available_base_ready = False
        self._pending_list_work.add("mode")
        if (
            self._combo_popup_open()
            or time.monotonic() < self._list_freeze_until
            or self._refreshing
            or self._rendering_avail
        ):
            self._arm_flush_timer()
            return
        self._apply_filter_mode()

    def _on_search_text(self, *_args) -> None:
        self._search_timer.start()

    def _on_search_changed(self) -> None:
        """Debounced search. Available never takes the heavy `_do_refresh` path."""
        try:
            mode = self.filter_box.currentText()
        except RuntimeError:
            return
        if mode in ("Available", "All"):
            self._schedule_avail_search()
            if mode == "All":
                self._hide_installed_by_search()
            return
        # Installed / Update Available: hide existing rows — no catalog rebuild.
        self._apply_installed_row_visibility()

    def _pending_update_folder_set(self) -> set[str]:
        return {
            str(u.get("folder") or "").lower()
            for u in self._pending_updates
            if u.get("folder")
        }

    def _apply_installed_row_visibility(self) -> None:
        """In-place hide for search and Update Available — never clear() rows."""
        try:
            mode = self.filter_box.currentText()
            q = (self.search.text() or "").lower().strip()
            count = self.installed_list.count()
        except RuntimeError:
            return
        update_folders = self._pending_update_folder_set()
        for i in range(count):
            try:
                item = self.installed_list.item(i)
                if item is None:
                    continue
                row = self.installed_list.itemWidget(item)
                if not isinstance(row, AddonRow):
                    # Empty-state placeholders stay visible.
                    continue
                matches = self._entry_matches_search(row.entry, q)
                if mode == "Update Available":
                    folder = str(row.entry.get("folder") or row.entry.get("name") or "")
                    status = str(getattr(row, "_status_text", "") or "")
                    has_update = folder.lower() in update_folders or status.startswith("Update")
                    item.setHidden(not (has_update and matches))
                else:
                    item.setHidden(not matches)
            except RuntimeError:
                continue

    def _hide_installed_by_search(self) -> None:
        """Back-compat alias — search uses the same visibility rules as the filter."""
        self._apply_installed_row_visibility()

    @staticmethod
    def _entry_matches_search(entry: dict, q: str) -> bool:
        if not q:
            return True
        folder = entry.get("folder") or entry.get("name") or ""
        blob = (
            f"{entry.get('name', '')} {entry.get('description', '')} "
            f"{entry.get('category', '')} {folder} "
            f"{addon_fork_label(entry)} {addon_version_label(entry)}"
        ).lower()
        return q in blob

    def _ensure_available_base(self, *, force: bool = False) -> None:
        """Build the Available catalog dataset only — no list widgets, no clear()."""
        if self._available_base_ready and not force:
            return
        try:
            cat_filter = self.cat_box.currentText()
        except RuntimeError:
            cat_filter = "All categories"
        installed_lower = self._installed_lower
        if not installed_lower:
            try:
                disk = set(scan_installed_addon_folders())
            except Exception:
                disk = set()
            installed_lower = {f.lower() for f in disk} | {
                k.lower() for k in settings.installed_addons
            }
            self._installed_lower = installed_lower
        base: list[dict] = []
        for entry in self._catalog_cache:
            folder = entry.get("folder") or entry.get("name") or ""
            if folder.lower() in installed_lower:
                continue
            if not entry.get("repo"):
                continue
            cat = entry.get("category") or "General"
            if cat_filter and cat_filter != "All categories" and cat != cat_filter:
                continue
            base.append(entry)
        # TW-custom (raven badge) first, then others — alphabetical within each group.
        base.sort(
            key=lambda e: (
                0 if is_turtle_wow_custom_addon(e) else 1,
                str(e.get("name") or e.get("folder") or "").lower(),
            )
        )
        self._available_base = base
        self._available_base_ready = True

    def _schedule_avail_search(self) -> None:
        """Coalesce rapid typing into one paginated Available re-render. Never `_do_refresh`."""
        self._pending_avail_search = True
        try:
            self._ensure_available_base()
        except RuntimeError:
            pass
        if self._refreshing or self._rendering_avail or self._lists_frozen():
            self._arm_flush_timer()
            return
        self._flush_avail_search()

    def _flush_avail_search(self) -> None:
        if not self._pending_avail_search:
            return
        try:
            self._ensure_available_base()
        except RuntimeError:
            self._arm_flush_timer()
            return
        if self._refreshing or self._rendering_avail or self._lists_frozen():
            self._arm_flush_timer()
            return
        self._pending_avail_search = False
        self._rendering_avail = True
        self._sync_filter_lock()
        try:
            q = (self.search.text() or "").lower().strip()
            self._filtered_available = [
                e for e in self._available_base if self._entry_matches_search(e, q)
            ]
            self._page_index = 0
            try:
                self.list.setUpdatesEnabled(False)
            except RuntimeError:
                pass
            self._render_available_page(light=True)
        except RuntimeError:
            pass
        finally:
            try:
                self.list.setUpdatesEnabled(True)
            except RuntimeError:
                pass
            self._rendering_avail = False
            self._sync_filter_lock()
            if self._pending_avail_search:
                QTimer.singleShot(0, self._flush_avail_search)

    def _apply_filter_mode(self) -> None:
        """Switch Installed/Available without a full disk-scan rebuild when possible."""
        if (
            self._combo_popup_open()
            or time.monotonic() < self._list_freeze_until
            or self._rendering_avail
        ):
            self._pending_list_work.add("mode")
            self._arm_flush_timer()
            return
        if self._refreshing:
            self._pending_list_work.add("mode")
            return
        try:
            mode = self.filter_box.currentText()
        except RuntimeError:
            return
        # Leaving an updates-only rebuild requires a full installed list again.
        if mode in ("Installed", "All") and self._installed_built_for_updates_only:
            self._request_list_work("refresh")
            return
        if mode in ("Installed", "Update Available", "All") and (
            self._dirty or self.installed_list.count() == 0
        ):
            self._request_list_work("refresh")
            return
        self._refreshing = True
        self._sync_filter_lock()
        try:
            # Filter selection is shown in the header dropdowns — no duplicate title.
            if mode in ("Available", "All"):
                self._ensure_available_base()
                q = (self.search.text() or "").lower().strip()
                self._filtered_available = [
                    e for e in self._available_base if self._entry_matches_search(e, q)
                ]
                self._page_index = 0
                try:
                    self.list.setUpdatesEnabled(False)
                except RuntimeError:
                    pass
                self._render_available_page(light=True)
                try:
                    self.list.setUpdatesEnabled(True)
                except RuntimeError:
                    pass
            self._apply_section_visibility(mode)
            # Fast path: Installed list is already built — hide non-matching rows
            # (Update Available) and re-apply search. Do not skip this or the
            # dropdown switch leaves every installed addon visible.
            if mode in ("Installed", "Update Available", "All"):
                self._apply_installed_row_visibility()
        except RuntimeError:
            self.mark_dirty()
        finally:
            self._refreshing = False
            self._sync_filter_lock()

    def _arm_flush_timer(self) -> None:
        self._flush_timer.start(LIST_FREEZE_MS)

    def _on_flush_timer(self) -> None:
        if self._lists_frozen():
            self._arm_flush_timer()
            return
        self._flush_list_work()
        self._sync_filter_lock()

    def _request_list_work(self, kind: str) -> None:
        self._pending_list_work.add(kind)
        if self._lists_frozen() or self._refreshing:
            self._arm_flush_timer()
            return
        self._flush_list_work()

    def _flush_list_work(self) -> None:
        if self._lists_frozen() or self._refreshing:
            self._arm_flush_timer()
            return
        work = set(self._pending_list_work)
        self._pending_list_work.clear()
        try:
            if "refresh" in work:
                self._do_refresh()
            elif "mode" in work:
                self._apply_filter_mode()
            if "patch" in work and not self._dirty and "refresh" not in work:
                # Scan path: in-place status only. Never escalate to clear/rebuild.
                self._patch_installed_statuses()
            if "reveal" in work:
                self._reveal_lists_if_current()
            if self._pending_avail_search:
                self._flush_avail_search()
        except RuntimeError:
            self.mark_dirty()

    def _apply_section_visibility(self, mode: str | None = None) -> None:
        """Show exactly one section for Available/Installed; both only for All.

        Installed must be fully hidden (not just covered) in Available mode so
        Prev/Next inside the available section cannot reveal it underneath.
        """
        if self._lists_frozen() and not self._refreshing:
            self._request_list_work("reveal")
            return
        mode = mode if mode is not None else self.filter_box.currentText()
        show_installed = mode in ("Installed", "All", "Update Available")
        show_avail = mode in ("Available", "All")
        self._want_installed_visible = show_installed
        self._want_avail_visible = show_avail
        try:
            self.installed_section.setVisible(show_installed)
            self.avail_section.setVisible(show_avail)
            self.installed_list.setVisible(show_installed)
            self.list.setVisible(show_avail)
            self.installed_hdr.setVisible(show_installed and mode == "All")
            self.avail_hdr.setVisible(show_avail and mode == "All")
            self.prev_btn.setVisible(show_avail)
            self.next_btn.setVisible(show_avail)
            self.page_lbl.setVisible(show_avail)
            if show_installed:
                self.installed_list.setMinimumHeight(80)
                if mode == "All":
                    self.installed_list.setMaximumHeight(260)
                else:
                    self.installed_list.setMaximumHeight(16777215)
            else:
                self.installed_list.setMinimumHeight(0)
                self.installed_list.setMaximumHeight(0)
        except RuntimeError:
            return

    def _installed_status_text(self, folder: str, meta: dict | None, never_u: bool) -> str:
        if never_u:
            return "Never update"
        if folder.lower() in self._pending_update_folder_set():
            return "Update available"
        if not self._addons_scan_done:
            return "Not checked"
        if _copied_addon_status(meta):
            return "Installed"
        return status_with_stamp("Up to date", meta)

    def _patch_installed_statuses(self) -> bool:
        """Update existing installed rows without clear/recreate. False if a rebuild is required.

        Never called as a path to clear() — scan only uses this for in-place text.
        """
        if self._lists_frozen() or self._refreshing:
            return False
        if self._dirty:
            return False
        has_row = False
        installed_meta = settings.installed_addons
        try:
            mode = self.filter_box.currentText()
            count = self.installed_list.count()
        except RuntimeError:
            return False
        for i in range(count):
            try:
                item = self.installed_list.item(i)
                if item is None:
                    continue
                row = self.installed_list.itemWidget(item)
                if not isinstance(row, AddonRow):
                    continue
                has_row = True
                folder = str(row.entry.get("folder") or row.entry.get("name") or "")
                never_u = bool(getattr(row, "_never_update", False)) or settings.is_addon_never_update(
                    folder
                )
                meta = installed_meta.get(folder) or {}
                status = self._installed_status_text(folder, meta, never_u)
                row.apply_status(status, never_update=never_u)
            except RuntimeError:
                return False
        if not has_row:
            # Available-only view has no installed rows; scan must not rebuild that list.
            return mode == "Available"
        self._apply_installed_row_visibility()
        return True

    def _page_is_live(self) -> bool:
        win = self.window()
        stack = getattr(win, "stack", None)
        if stack is not None and stack.currentWidget() is not self:
            return False
        return bool(self.isVisible()) and not self.testAttribute(
            Qt.WidgetAttribute.WA_DontShowOnScreen
        )

    def preload_rows(self) -> None:
        """Build lists while HOME is showing — rows stay off-screen / parented."""
        if not self._dirty:
            return
        self._allow_hidden_build = True
        try:
            self.refresh()
        finally:
            self._allow_hidden_build = False

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        win = self.window()
        stack = getattr(win, "stack", None)
        if stack is not None and stack.currentWidget() is not self:
            # addWidget / stack insert can flash a Show — do not build rows.
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            super().showEvent(event)
            return
        # Refresh while still off-screen so first-open does not spawn row HWNDs.
        if self._dirty:
            self.refresh()
        super().showEvent(event)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        QTimer.singleShot(0, self._reveal_lists_if_current)
        self._git_kick_timer.start(300)

    def _reveal_lists_if_current(self) -> None:
        if not self._page_is_live():
            return
        if self._lists_frozen():
            self._request_list_work("reveal")
            return
        self._apply_section_visibility()
        self.installed_list.setUpdatesEnabled(True)
        self.list.setUpdatesEnabled(True)
        if self._want_installed_visible:
            _reveal_item_widgets(self.installed_list, self)
        if self._want_avail_visible:
            _reveal_item_widgets(self.list, self)

    def _kick_deferred_git_checks(self) -> None:
        if not self._lists_ready or self._lists_frozen() or self._refreshing:
            self._git_kick_timer.start(LIST_FREEZE_MS)
            return
        for lw in (self.installed_list, self.list):
            try:
                count = lw.count()
            except RuntimeError:
                continue
            for i in range(count):
                try:
                    it = lw.item(i)
                    if it is None:
                        continue
                    row = lw.itemWidget(it)
                    kick = getattr(row, "kick_git_visibility", None)
                    if callable(kick):
                        kick()
                except RuntimeError:
                    continue

    def set_never_update(self, entry: dict, enabled: bool) -> None:
        folder = entry.get("folder") or entry.get("name")
        if not folder:
            return
        settings.set_addon_never_update(str(folder), bool(enabled))
        if enabled:
            self.clear_pending_update(str(folder))
        # Defer rebuild so any transient Never Update menu can finish closing first.
        QTimer.singleShot(0, self._finish_never_update_change)

    def _finish_never_update_change(self) -> None:
        self._request_list_work("patch")
        if self._dirty:
            self._request_list_work("refresh")
        self.badge_state_changed.emit()

    def open_git(self, entry: dict) -> None:
        from ichalaunch.ui.widgets.common import github_repo_browse_url

        folder = entry.get("folder") or entry.get("name")
        git_origin = None
        if folder:
            disk = addon_disk_path(str(folder))
            if disk is not None:
                git_origin = read_git_origin_url(disk)
        # Local .git origin wins over catalog/entry repo fields.
        url = github_repo_browse_url(
            git_origin,
            entry.get("repo"),
            entry.get("url"),
            entry.get("repository"),
        )
        if url:
            open_url_in_browser(url)

    def reset_scan_done(self) -> None:
        """Clear update-check completion (e.g. disk rescan is not an update scan)."""
        self._addons_scan_done = False
        self.mark_dirty()

    def clear_pending_update(self, folder: str) -> None:
        """Remove one folder from the pending-update list after a successful update."""
        before = len(self._pending_updates)
        self._pending_updates = [u for u in self._pending_updates if u.get("folder") != folder]
        if len(self._pending_updates) == before:
            return
        if self._pending_updates:
            self.updates_lbl.setText(f"{len(self._pending_updates)} update(s) available")
        else:
            self.updates_lbl.setText("")
        self._request_list_work("patch")
        if self._dirty:
            self._request_list_work("refresh")
        self.badge_state_changed.emit()

    def reload_catalog(self) -> None:
        """Reload Available entries from the current catalog snapshot.

        While HOME (or any non-ADDONS page) is showing, only refresh the cache
        and mark dirty. Mutating GlueComboBox items during startup preload has
        aborted Qt natively (WER Qt6Core.dll / 0xc0000409, no Python traceback).
        Combo categories rebuild the next time ADDONS is shown via refresh().
        """
        self._catalog_cache = load_catalog()
        self._available_base_ready = False
        self.mark_dirty()
        if not self._page_is_live():
            return
        # Refresh category filter options when remote catalog adds new categories.
        try:
            current = self.cat_box.currentText()
        except RuntimeError:
            current = "All categories"
        cats = sorted({e.get("category") or "General" for e in self._catalog_cache})
        self.cat_box.blockSignals(True)
        try:
            while self.cat_box.count() > 1:
                self.cat_box.removeItem(1)
            for c in cats:
                self.cat_box.addItem(c)
            idx = self.cat_box.findText(current)
            if idx >= 0:
                self.cat_box.setCurrentIndex(idx)
        finally:
            self.cat_box.blockSignals(False)
        self.refresh()
    def _page(self, delta: int) -> None:
        if self._lists_frozen() or self._refreshing or self._rendering_avail:
            return
        max_page = max(0, (len(self._filtered_available) - 1) // PAGE_SIZE)
        self._page_index = max(0, min(max_page, self._page_index + delta))
        self.list.setUpdatesEnabled(False)
        self.list.hide()
        try:
            self._render_available_page()
        finally:
            self._apply_section_visibility()
            self.list.setUpdatesEnabled(True)
            QTimer.singleShot(0, self._reveal_lists_if_current)
            self._git_kick_timer.start(300)

    def _install_selected(self, *_args) -> None:
        item = self.list.currentItem()
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry and entry.get("repo"):
            self.install_requested.emit(entry)

    def _render_available_page(self, *, light: bool = False) -> None:
        """Mount at most PAGE_SIZE available rows. Search uses light=True (no HWND dance)."""
        if light:
            self._clear_avail_page_light()
        else:
            _safe_clear_list(self.list)
        start = self._page_index * PAGE_SIZE
        chunk = self._filtered_available[start : start + PAGE_SIZE]
        for entry in chunk:
            try:
                row = AddonRow(entry, status="available", parent=self.list)
                row.install_clicked.connect(self.install_requested.emit)
                row.open_git_clicked.connect(self.open_git)
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setSizeHint(QSize(0, INSTALLED_ROW_H))
                if light:
                    self._mount_avail_row_light(item, row)
                else:
                    _mount_row(self.list, item, row)
            except RuntimeError:
                break

        total = len(self._filtered_available)
        if total == 0:
            q = (self.search.text() or "").strip()
            if q:
                msg = (
                    f'No Available addons matched “{q}”. '
                    "Use Suggest for catalog if this should be listed."
                )
            else:
                msg = "No Available addons to show."
            empty = QListWidgetItem(msg)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            try:
                self.list.addItem(empty)
            except RuntimeError:
                pass
        max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
        showing = f"{start + 1}–{min(start + PAGE_SIZE, total)}" if total else "0"
        try:
            self.page_lbl.setText(f"Page {self._page_index + 1}/{max_page + 1}  ·  {showing} of {total}")
            self.prev_btn.setEnabled(self._page_index > 0)
            self.next_btn.setEnabled(self._page_index < max_page)
        except RuntimeError:
            pass

    def _clear_avail_page_light(self) -> None:
        """Hide current page via setHidden, then clear — no DontShowOnScreen."""
        try:
            count = self.list.count()
        except RuntimeError:
            return
        for i in range(count):
            try:
                it = self.list.item(i)
                if it is None:
                    continue
                it.setHidden(True)
            except RuntimeError:
                continue
        try:
            self.list.clear()
        except RuntimeError:
            return

    def _mount_avail_row_light(self, item: QListWidgetItem, row: AddonRow) -> None:
        """Attach a search-page row without WA_DontShowOnScreen (that flag + clear() crashes)."""
        try:
            row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        except RuntimeError:
            return

    def refresh(self) -> None:
        """Request a list rebuild. Queued while a filter popup/cooldown is active."""
        self.mark_dirty()
        self._request_list_work("refresh")

    def _do_refresh(self) -> None:
        if (
            self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            and not self.isVisible()
            and not self._allow_hidden_build
        ):
            self._dirty = True
            return
        if self._lists_frozen() or self._refreshing:
            self._dirty = True
            self._pending_list_work.add("refresh")
            self._arm_flush_timer()
            return
        self._refreshing = True
        self._sync_filter_lock()
        self._search_timer.stop()
        self.filter_box.blockSignals(True)
        self.cat_box.blockSignals(True)
        try:
            self.filter_box.currentTextChanged.disconnect(self._on_filter_changed)
            self.cat_box.currentTextChanged.disconnect(self._on_filter_changed)
        except (RuntimeError, TypeError):
            pass
        q = (self.search.text() or "").lower().strip()
        mode = self.filter_box.currentText()
        cat_filter = self.cat_box.currentText()
        installed_meta = settings.installed_addons
        disk_folders = set(scan_installed_addon_folders())
        installed_folders = set(installed_meta.keys()) | disk_folders
        installed_lower = {f.lower() for f in installed_folders}
        self._installed_lower = installed_lower
        update_folders = self._pending_update_folder_set()

        # Hide lists for the whole rebuild so new rows parented to them never flash,
        # and so clear() never unparents a still-visible item widget (HWND spam).
        self.installed_list.setUpdatesEnabled(False)
        self.list.setUpdatesEnabled(False)
        self.installed_section.hide()
        self.avail_section.hide()
        self.installed_list.hide()
        self.list.hide()
        self.installed_list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        try:
            _safe_clear_list(self.installed_list)
            shown_installed = 0
            show_installed = mode in ("Installed", "All", "Update Available")
            # Section titles only when All shows both panels; otherwise the
            # header dropdown is the sole filter label.
            self.installed_hdr.setText("Installed")
            self.avail_hdr.setText("Available")
            self.installed_hdr.setVisible(show_installed and mode == "All")
            self._installed_built_for_updates_only = mode == "Update Available"

            if show_installed:
                if mode == "All":
                    self.installed_list.setMaximumHeight(260)
                else:
                    self.installed_list.setMaximumHeight(16777215)

                cat_idx = catalog_index()
                addons_dir = resolve_addons_dir(create=False)
                # Folders claimed as modules of another installed primary
                child_of_pack: set[str] = set()
                for p, m in installed_meta.items():
                    folders_list = m.get("folders") if isinstance(m.get("folders"), list) else None
                    if not folders_list:
                        continue
                    for f in folders_list:
                        if f and f.lower() != p.lower():
                            child_of_pack.add(f.lower())

                rows_to_show: list[tuple[int, str, dict, str, list[str], bool, bool]] = []
                for folder in installed_folders:
                    if mode == "Update Available" and folder.lower() not in update_folders:
                        continue
                    meta = installed_meta.get(folder) or {}
                    if not meta:
                        for key, val in installed_meta.items():
                            if key.lower() == folder.lower():
                                meta = val
                                break
                    if meta.get("managed_by") or folder.lower() in child_of_pack:
                        continue
                    cat, kind = resolve_catalog_entry(folder, cat_idx)
                    if kind == "prefix" and cat:
                        parent_name = (cat.get("folder") or cat.get("name") or "").strip()
                        if parent_name and any(f.lower() == parent_name.lower() for f in installed_folders):
                            continue
                    disk = addon_disk_path(folder, addons_dir=addons_dir)
                    origin = read_git_origin_url(disk) if disk is not None else None
                    meta = merge_addon_meta(
                        folder,
                        meta,
                        cat,
                        match_kind=kind or "exact",
                        git_origin=origin,
                    )
                    name = meta.get("name") or folder
                    desc = meta.get("description") or meta.get("repository") or "Detected in Interface/AddOns"
                    pack_folders = meta.get("folders") if isinstance(meta.get("folders"), list) else None
                    if not pack_folders:
                        pack_folders = [
                            folder,
                            *[
                                f
                                for f, m in installed_meta.items()
                                if str(m.get("managed_by") or "").lower() == folder.lower()
                            ],
                        ]
                        if cat and kind == "exact":
                            base = (cat.get("folder") or cat.get("name") or folder).strip()
                            base_l = base.lower()
                            for disk_f in installed_folders:
                                fl = disk_f.lower()
                                if fl == base_l:
                                    continue
                                if fl.startswith(base_l + "_") or fl.startswith(base_l + "-"):
                                    if disk_f not in pack_folders:
                                        pack_folders.append(disk_f)
                    pack_folders = sorted({f for f in pack_folders if f}, key=str.lower)
                    modules = (
                        [f for f in pack_folders if f.lower() != folder.lower()]
                        if len(pack_folders) > 1
                        else []
                    )
                    category = meta.get("category") or "Installed"
                    # Prefer merged meta (includes .git origin override); catalog only as last resort.
                    repo_url = meta.get("url") or (cat or {}).get("repo") or ""
                    if cat_filter and cat_filter != "All categories" and category != cat_filter:
                        continue
                    loaded = addon_is_loaded(folder, addons_dir=addons_dir)
                    if "loaded" in meta:
                        loaded = bool(meta.get("loaded")) if disk is None else loaded
                    entry = {
                        "name": name,
                        "folder": folder,
                        "description": desc,
                        "category": category,
                        "repo": repo_url,
                        "repository": meta.get("repository") or "",
                        "url": repo_url,
                        "source": meta.get("source", "detected"),
                        "tag": meta.get("tag") or catalog_pin_tag(cat) or "",
                        "loaded": loaded,
                    }
                    never_u = bool(meta.get("never_update")) or settings.is_addon_never_update(folder)
                    if kind == "exact" and catalog_locks_updates(cat):
                        never_u = True
                    status = self._installed_status_text(folder, meta, never_u)
                    if status.startswith("Update"):
                        sort_pri = 0
                    elif never_u:
                        sort_pri = 2
                    else:
                        sort_pri = 1
                    rows_to_show.append(
                        (sort_pri, name.lower(), entry, status, modules, never_u, loaded, meta)
                    )

                rows_to_show.sort(key=lambda t: (t[0], t[1]))
                for _pri, _name, entry, status, modules, never_u, loaded, row_meta in rows_to_show:
                    row = AddonRow(
                        entry,
                        status=status,
                        modules=modules,
                        never_update=never_u,
                        loaded=loaded,
                        meta=row_meta,
                        parent=self.installed_list,
                    )
                    row.update_clicked.connect(self.update_requested.emit)
                    row.reinstall_clicked.connect(self.reinstall_requested.emit)
                    row.remove_clicked.connect(self.remove_requested.emit)
                    row.open_git_clicked.connect(self.open_git)
                    row.settings_clicked.connect(self.open_addon_settings)
                    row.loaded_toggled.connect(self.set_addon_loaded_ui)
                    row.never_update_changed.connect(self.set_never_update)
                    item = QListWidgetItem()
                    item.setSizeHint(QSize(0, row.preferred_height()))

                    def _on_height(r=row, it=item) -> None:
                        it.setSizeHint(QSize(0, r.preferred_height()))
                        self.installed_list.doItemsLayout()

                    row.height_changed.connect(_on_height)
                    _mount_row(self.installed_list, item, row)
                    shown_installed += 1

                if shown_installed == 0:
                    if mode == "Update Available":
                        msg = "No updates found. Run Check Updates first."
                    else:
                        msg = "No installed addons matched. Use Rescan Disk or switch to Available."
                    empty = QListWidgetItem(msg)
                    empty.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.installed_list.addItem(empty)
                elif show_installed:
                    self._apply_installed_row_visibility()

            self._ensure_available_base(force=True)
            if mode in ("Available", "All"):
                self._filtered_available = [
                    e for e in self._available_base if self._entry_matches_search(e, q)
                ]
                self._page_index = 0
                self._render_available_page()
            else:
                self._filtered_available = []
            self._pending_avail_search = False
            self._dirty = False
            self._lists_ready = True

            # Restore visibility from filter mode (lists were hidden for the rebuild).
            self._apply_section_visibility(mode)
            if not self._page_is_live():
                self.installed_section.hide()
                self.avail_section.hide()
                self.installed_list.hide()
                self.list.hide()
            else:
                QTimer.singleShot(0, self._reveal_lists_if_current)
                self._git_kick_timer.start(300)
        except RuntimeError:
            self.mark_dirty()
        finally:
            self._refreshing = False
            self._sync_filter_lock()
            try:
                self.filter_box.currentTextChanged.connect(self._on_filter_changed)
                self.cat_box.currentTextChanged.connect(self._on_filter_changed)
            except (RuntimeError, TypeError):
                pass
            self.filter_box.blockSignals(False)
            self.cat_box.blockSignals(False)
            try:
                self.installed_list.setUpdatesEnabled(True)
                self.list.setUpdatesEnabled(True)
            except RuntimeError:
                pass
            if self._pending_list_work:
                if self._lists_frozen():
                    self._arm_flush_timer()
                else:
                    QTimer.singleShot(0, self._flush_list_work)
            elif self._pending_avail_search and self._available_base_ready:
                QTimer.singleShot(0, self._flush_avail_search)
