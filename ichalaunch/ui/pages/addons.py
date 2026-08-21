"""Addons browse / install — scrollable lists that never grow the window."""

from __future__ import annotations

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

from ichalaunch.addons.github import load_catalog
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
from ichalaunch.ui.widgets.common import AddonRow, open_url_in_browser, status_with_stamp
from ichalaunch.ui.widgets.dialogs import github_import_dialog, github_preview_dialog
from ichalaunch.ui.widgets import dialogs as themed
from ichalaunch.ui.widgets.glue_combo import GlueComboBox
from ichalaunch.ui.widgets.glue_panel_button import GluePanelButton
from ichalaunch.ui.widgets.marble_bg import MarbleListWidget, MarblePanel

PAGE_SIZE = 80
INSTALLED_ROW_H = 48


def _hide_list_item_widgets(lw: MarbleListWidget) -> None:
    """Hide item widgets before clear so Qt never unparents a *visible* row.

    On Windows, setParent(None) while visible promotes the widget to a real
    top-level HWND — that is the rapid-fire mini-window spam after refresh.
    """
    for i in range(lw.count()):
        it = lw.item(i)
        if it is None:
            continue
        w = lw.itemWidget(it)
        if w is not None:
            w.hide()
            w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)


def _safe_clear_list(lw: MarbleListWidget) -> None:
    _hide_list_item_widgets(lw)
    lw.clear()


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
    lw.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    for i in range(lw.count()):
        it = lw.item(i)
        if it is None:
            continue
        w = lw.itemWidget(it)
        if w is None:
            continue
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)


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

        # Catalog search — top of marble panel, right of the filter title
        self.search = CastingBarSearchEdit()
        self.search.setPlaceholderText("Search catalog…")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self._search_timer.start())

        self.filter_title = QLabel("Installed")
        self.filter_title.setObjectName("SectionTitle")
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.filter_title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        title_row.addWidget(self.search, 2)

        # Filters / actions sit at the bottom near the play bar
        tools = QHBoxLayout()
        self.filter_box = GlueComboBox()
        self.filter_box.blockSignals(True)
        self.filter_box.addItems(["Installed", "Available", "Update Available", "All"])
        self.filter_box.blockSignals(False)
        self.filter_box.currentTextChanged.connect(self.refresh)

        self.cat_box = GlueComboBox()
        self.cat_box.blockSignals(True)
        self.cat_box.addItem("All categories")
        self.cat_box.blockSignals(False)
        self.cat_box.currentTextChanged.connect(self.refresh)

        check_btn = GluePanelButton("Check Updates", self)
        check_btn.clicked.connect(self.check_updates_requested.emit)
        self.check_btn = check_btn
        update_all_btn = GluePanelButton("Update All", self, role="primary")
        update_all_btn.clicked.connect(self.update_all_requested.emit)
        rescan_btn = GluePanelButton("Rescan Disk", self)
        rescan_btn.clicked.connect(self.rescan_requested.emit)
        import_btn = GluePanelButton("+ Git Repo", self)
        import_btn.clicked.connect(self._open_github_import_dialog)

        tools.addWidget(self.filter_box)
        tools.addWidget(self.cat_box)
        tools.addWidget(rescan_btn)
        tools.addWidget(check_btn)
        tools.addWidget(update_all_btn)
        tools.addWidget(import_btn)

        # Outer marble window holding installed + available lists (and headers).
        addons_win = MarblePanel(radius=10.0)
        addons_win.setObjectName("AddonsWindow")
        win_l = QVBoxLayout(addons_win)
        win_l.setContentsMargins(10, 10, 10, 10)
        win_l.setSpacing(8)
        win_l.addLayout(title_row)
        win_l.addWidget(self.installed_hdr)
        win_l.addWidget(self.installed_list, 1)
        win_l.addWidget(self.avail_hdr)
        win_l.addWidget(self.list, 2)
        win_l.addLayout(action_row)

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
        # Do not refresh() here — building rows before the page is in the stack
        # (or while hidden) is the launch-time HWND spam. First paint is on show.

    def set_checking(self, busy: bool, msg: str = "Checking for updates…") -> None:
        # Progress lives on the bottom bar; keep only the Check Updates button gated.
        self.loading_lbl.setText("")
        self.loading_lbl.setVisible(False)
        self.loading_bar.setVisible(False)
        self.check_btn.setEnabled(not busy)

    @property
    def pending_updates(self) -> list[dict]:
        return list(self._pending_updates)

    def mark_dirty(self) -> None:
        self._dirty = True

    def _open_github_import_dialog(self) -> None:
        url = github_import_dialog(self, kind="addon")
        if url:
            self.github_import_requested.emit(url)

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
        # Scan complete must NEVER rebuild rows (Windows HWND spam).
        if not self._patch_installed_statuses():
            self.mark_dirty()
        self.badge_state_changed.emit()

    def _patch_installed_statuses(self) -> bool:
        """Update existing installed rows without clear/recreate. False if a rebuild is required."""
        if self._dirty:
            return False
        has_row = False
        update_map = {u["folder"]: u for u in self._pending_updates}
        installed_meta = settings.installed_addons
        mode = self.filter_box.currentText()
        for i in range(self.installed_list.count()):
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
            if never_u:
                status = "Never update"
            elif folder in update_map:
                status = "Update available"
            elif self._addons_scan_done:
                status = status_with_stamp("Up to date", installed_meta.get(folder))
            else:
                status = "Not checked"
            row.apply_status(status, never_update=never_u)
            if mode == "Update Available":
                item.setHidden(not status.startswith("Update"))
            else:
                item.setHidden(False)
        if not has_row:
            # Available-only view has no installed rows; scan must not rebuild that list.
            return mode == "Available"
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
        mode = self.filter_box.currentText()
        self.installed_list.setVisible(mode in ("Installed", "All", "Update Available"))
        self.list.setVisible(mode in ("Available", "All"))
        self.installed_list.setUpdatesEnabled(True)
        self.list.setUpdatesEnabled(True)
        _reveal_item_widgets(self.installed_list, self)
        _reveal_item_widgets(self.list, self)

    def _kick_deferred_git_checks(self) -> None:
        for lw in (self.installed_list, self.list):
            for i in range(lw.count()):
                it = lw.item(i)
                if it is None:
                    continue
                row = lw.itemWidget(it)
                kick = getattr(row, "kick_git_visibility", None)
                if callable(kick):
                    kick()

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
        if self._patch_installed_statuses():
            self.badge_state_changed.emit()
            return
        self.mark_dirty()
        if self.isVisible():
            self.refresh()
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
        if not self._patch_installed_statuses():
            self.mark_dirty()
            if self.isVisible():
                self.refresh()
        self.badge_state_changed.emit()

    def reload_catalog(self) -> None:
        self._catalog_cache = load_catalog()
        self.mark_dirty()

    def _page(self, delta: int) -> None:
        max_page = max(0, (len(self._filtered_available) - 1) // PAGE_SIZE)
        self._page_index = max(0, min(max_page, self._page_index + delta))
        self.list.setUpdatesEnabled(False)
        self.list.hide()
        try:
            self._render_available_page()
        finally:
            mode = self.filter_box.currentText()
            self.list.setVisible(mode in ("Available", "All"))
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

    def _render_available_page(self) -> None:
        _safe_clear_list(self.list)
        start = self._page_index * PAGE_SIZE
        chunk = self._filtered_available[start : start + PAGE_SIZE]
        for entry in chunk:
            # Parent to the (hidden-during-rebuild) list; mount hides until attached.
            row = AddonRow(entry, status="available", parent=self.list)
            row.install_clicked.connect(self.install_requested.emit)
            row.open_git_clicked.connect(self.open_git)
            row.preview_clicked.connect(self.open_preview)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setSizeHint(QSize(0, INSTALLED_ROW_H))
            _mount_row(self.list, item, row)

        total = len(self._filtered_available)
        max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
        showing = f"{start + 1}–{min(start + PAGE_SIZE, total)}" if total else "0"
        self.page_lbl.setText(f"Page {self._page_index + 1}/{max_page + 1}  ·  {showing} of {total}")
        self.prev_btn.setEnabled(self._page_index > 0)
        self.next_btn.setEnabled(self._page_index < max_page)

    def refresh(self) -> None:
        if (
            self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            and not self.isVisible()
            and not self._allow_hidden_build
        ):
            self._dirty = True
            return
        self.filter_box.blockSignals(True)
        self.cat_box.blockSignals(True)
        q = (self.search.text() or "").lower().strip()
        mode = self.filter_box.currentText()
        cat_filter = self.cat_box.currentText()
        installed_meta = settings.installed_addons
        disk_folders = set(scan_installed_addon_folders())
        installed_folders = set(installed_meta.keys()) | disk_folders
        installed_lower = {f.lower() for f in installed_folders}
        update_map = {u["folder"]: u for u in self._pending_updates}

        def matches(entry_name: str, desc: str, category: str, folder: str) -> bool:
            if cat_filter and cat_filter != "All categories" and category != cat_filter:
                return False
            if not q:
                return True
            blob = f"{entry_name} {desc} {category} {folder}".lower()
            return q in blob

        # Hide lists for the whole rebuild so new rows parented to them never flash,
        # and so clear() never unparents a still-visible item widget (HWND spam).
        self.installed_list.setUpdatesEnabled(False)
        self.list.setUpdatesEnabled(False)
        self.installed_list.hide()
        self.list.hide()
        self.installed_list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.list.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        try:
            _safe_clear_list(self.installed_list)
            shown_installed = 0
            show_installed = mode in ("Installed", "All", "Update Available")
            # Top title always reflects the filter; Installed section header only in All.
            if mode == "Update Available":
                self.filter_title.setText("Updates Available")
                self.installed_hdr.setText("Updates Available")
            elif mode == "Available":
                self.filter_title.setText("Available")
                self.installed_hdr.setText("Installed")
            elif mode == "All":
                self.filter_title.setText("All")
                self.installed_hdr.setText("Installed")
            else:
                self.filter_title.setText("Installed")
                self.installed_hdr.setText("Installed")
            self.installed_hdr.setVisible(show_installed and mode == "All")

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
                    if mode == "Update Available" and folder not in update_map:
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
                    search_blob = f"{desc} {' '.join(modules)}"
                    if not matches(name, search_blob, category, folder):
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
                        "tag": meta.get("tag") or "",
                        "loaded": loaded,
                    }
                    never_u = bool(meta.get("never_update")) or settings.is_addon_never_update(folder)
                    if never_u:
                        status = "Never update"
                    elif folder in update_map:
                        status = "Update available"
                    elif self._addons_scan_done:
                        status = status_with_stamp("Up to date", meta)
                    else:
                        status = "Not checked"
                    if status.startswith("Update"):
                        sort_pri = 0
                    elif never_u:
                        sort_pri = 2
                    else:
                        sort_pri = 1
                    rows_to_show.append(
                        (sort_pri, name.lower(), entry, status, modules, never_u, loaded)
                    )

                rows_to_show.sort(key=lambda t: (t[0], t[1]))
                for _pri, _name, entry, status, modules, never_u, loaded in rows_to_show:
                    row = AddonRow(
                        entry,
                        status=status,
                        modules=modules,
                        never_update=never_u,
                        loaded=loaded,
                        parent=self.installed_list,
                    )
                    row.update_clicked.connect(self.update_requested.emit)
                    row.reinstall_clicked.connect(self.reinstall_requested.emit)
                    row.remove_clicked.connect(self.remove_requested.emit)
                    row.open_git_clicked.connect(self.open_git)
                    row.preview_clicked.connect(self.open_preview)
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

            self._filtered_available = []
            if mode in ("Available", "All"):
                for entry in self._catalog_cache:
                    folder = entry.get("folder") or entry.get("name") or ""
                    if folder.lower() in installed_lower:
                        continue
                    if not entry.get("repo"):
                        continue
                    if not matches(entry.get("name", ""), entry.get("description", ""), entry.get("category", ""), folder):
                        continue
                    self._filtered_available.append(entry)

            self._page_index = 0
            self._render_available_page()
            self._dirty = False

            # Restore visibility from filter mode (lists were hidden for the rebuild).
            show_avail = mode in ("Available", "All")
            self.installed_list.setVisible(show_installed)
            self.list.setVisible(show_avail)
            self.avail_hdr.setVisible(show_avail and mode == "All")
            self.prev_btn.setVisible(show_avail)
            self.next_btn.setVisible(show_avail)
            self.page_lbl.setVisible(show_avail)
            self._want_installed_visible = show_installed
            self._want_avail_visible = show_avail
            if self._page_is_live():
                QTimer.singleShot(0, self._reveal_lists_if_current)
                self._git_kick_timer.start(300)
            else:
                self.installed_list.hide()
                self.list.hide()
        finally:
            self.filter_box.blockSignals(False)
            self.cat_box.blockSignals(False)
            self.installed_list.setUpdatesEnabled(True)
            self.list.setUpdatesEnabled(True)
