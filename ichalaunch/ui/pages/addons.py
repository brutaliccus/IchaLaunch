"""Addons browse / install — scrollable lists that never grow the window."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.addons.github import load_catalog
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import (
    catalog_index,
    merge_addon_meta,
    resolve_catalog_entry,
    scan_installed_addon_folders,
)
from ichalaunch.ui.widgets.common import AddonRow, status_with_stamp
from ichalaunch.ui.widgets.dialogs import prompt_text

PAGE_SIZE = 80
INSTALLED_ROW_H = 56


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        # Extra top padding clears the floating MoA logo overhang
        layout.setContentsMargins(16, 28, 16, 12)
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

        # QListWidget always scrolls inside its viewport — won't stretch the window
        self.installed_list = QListWidget()
        self.installed_list.setSpacing(2)
        self.installed_list.setUniformItemSizes(True)
        self.installed_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.installed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.installed_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.installed_list.setMinimumHeight(80)

        self.avail_hdr = QLabel("Available")
        self.avail_hdr.setObjectName("SectionTitle")

        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self._install_selected)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.list.setMinimumHeight(80)

        action_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.next_btn = QPushButton("Next ▶")
        self.prev_btn.clicked.connect(lambda: self._page(-1))
        self.next_btn.clicked.connect(lambda: self._page(1))
        self.page_lbl = QLabel("")
        self.page_lbl.setObjectName("Muted")
        action_row.addStretch(1)
        action_row.addWidget(self.prev_btn)
        action_row.addWidget(self.page_lbl)
        action_row.addWidget(self.next_btn)

        # Search / filters / actions sit at the bottom near the play bar
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search catalog…")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self._search_timer.start())

        self.filter_box = QComboBox()
        self.filter_box.addItems(["Installed", "Available", "Update Available", "All"])
        self.filter_box.currentTextChanged.connect(self.refresh)

        self.cat_box = QComboBox()
        self.cat_box.addItem("All categories")
        self.cat_box.currentTextChanged.connect(self.refresh)

        check_btn = QPushButton("Check Updates")
        check_btn.clicked.connect(self.check_updates_requested.emit)
        self.check_btn = check_btn
        update_all_btn = QPushButton("Update All")
        update_all_btn.setObjectName("UpdateAllButton")
        update_all_btn.clicked.connect(self.update_all_requested.emit)
        rescan_btn = QPushButton("Rescan Disk")
        rescan_btn.clicked.connect(self.rescan_requested.emit)
        import_btn = QPushButton("Add from GitHub")
        import_btn.clicked.connect(self._open_github_import_dialog)

        tools.addWidget(self.search, 2)
        tools.addWidget(self.filter_box)
        tools.addWidget(self.cat_box)
        tools.addWidget(rescan_btn)
        tools.addWidget(check_btn)
        tools.addWidget(update_all_btn)
        tools.addWidget(import_btn)

        layout.addLayout(self.loading_row)
        layout.addWidget(self.updates_lbl)
        layout.addWidget(self.installed_hdr)
        layout.addWidget(self.installed_list, 1)
        layout.addWidget(self.avail_hdr)
        layout.addWidget(self.list, 2)
        layout.addLayout(action_row)
        layout.addLayout(tools)

        self._pending_updates: list[dict] = []
        self._addons_scan_done = False
        self._catalog_cache = load_catalog()
        self._dirty = True
        self._page_index = 0
        self._filtered_available: list[dict] = []

        cats = sorted({e.get("category") or "General" for e in self._catalog_cache})
        for c in cats:
            self.cat_box.addItem(c)
        self.set_checking(False)
        self.refresh()

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
        url = prompt_text(
            self,
            "Add from GitHub",
            "Paste a GitHub repository URL:",
            placeholder="https://github.com/owner/addon-repo",
            accept_text="Import",
        )
        if url:
            self.github_import_requested.emit(url)

    def set_updates(self, updates: list[dict]) -> None:
        self._pending_updates = updates
        self._addons_scan_done = True
        if updates:
            self.updates_lbl.setText(f"{len(updates)} update(s) available")
        else:
            self.updates_lbl.setText("")
        self.mark_dirty()
        self.refresh()
        self.badge_state_changed.emit()

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
        self.mark_dirty()
        self.refresh()
        self.badge_state_changed.emit()

    def reload_catalog(self) -> None:
        self._catalog_cache = load_catalog()
        self.mark_dirty()

    def _page(self, delta: int) -> None:
        max_page = max(0, (len(self._filtered_available) - 1) // PAGE_SIZE)
        self._page_index = max(0, min(max_page, self._page_index + delta))
        self._render_available_page()

    def _install_selected(self, *_args) -> None:
        item = self.list.currentItem()
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry and entry.get("repo"):
            self.install_requested.emit(entry)

    def _render_available_page(self) -> None:
        self.list.clear()
        start = self._page_index * PAGE_SIZE
        chunk = self._filtered_available[start : start + PAGE_SIZE]
        for entry in chunk:
            row = AddonRow(entry, status="available")
            row.install_clicked.connect(self.install_requested.emit)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setSizeHint(QSize(0, INSTALLED_ROW_H))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

        total = len(self._filtered_available)
        max_page = max(0, (total - 1) // PAGE_SIZE) if total else 0
        showing = f"{start + 1}–{min(start + PAGE_SIZE, total)}" if total else "0"
        self.page_lbl.setText(f"Page {self._page_index + 1}/{max_page + 1}  ·  {showing} of {total}")
        self.prev_btn.setEnabled(self._page_index > 0)
        self.next_btn.setEnabled(self._page_index < max_page)

        mode = self.filter_box.currentText()
        show_avail = mode in ("Available", "All")
        self.avail_hdr.setVisible(show_avail)
        self.list.setVisible(show_avail)
        self.prev_btn.setVisible(show_avail)
        self.next_btn.setVisible(show_avail)
        self.page_lbl.setVisible(show_avail)

    def refresh(self) -> None:
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

        self.installed_list.clear()
        shown_installed = 0
        show_installed = mode in ("Installed", "All", "Update Available")
        self.installed_hdr.setVisible(show_installed)
        self.installed_list.setVisible(show_installed)

        if mode == "Update Available":
            self.installed_hdr.setText("Updates Available")
        else:
            self.installed_hdr.setText("Installed")

        if show_installed:
            if mode == "All":
                self.installed_list.setMaximumHeight(260)
            else:
                self.installed_list.setMaximumHeight(16777215)

            cat_idx = catalog_index()
            # Folders claimed as modules of another installed primary
            child_of_pack: set[str] = set()
            for p, m in installed_meta.items():
                folders_list = m.get("folders") if isinstance(m.get("folders"), list) else None
                if not folders_list:
                    continue
                for f in folders_list:
                    if f and f.lower() != p.lower():
                        child_of_pack.add(f.lower())

            for folder in sorted(installed_folders, key=str.lower):
                if mode == "Update Available" and folder not in update_map:
                    continue
                # Case-insensitive settings lookup + live catalog gap-fill for display
                meta = installed_meta.get(folder) or {}
                if not meta:
                    for key, val in installed_meta.items():
                        if key.lower() == folder.lower():
                            meta = val
                            break
                # Collapse multi-module packs: hide child folders
                if meta.get("managed_by") or folder.lower() in child_of_pack:
                    continue
                cat, kind = resolve_catalog_entry(folder, cat_idx)
                # Prefix module (Bongos_ActionBar) when parent folder is also installed
                if kind == "prefix" and cat:
                    parent_name = (cat.get("folder") or cat.get("name") or "").strip()
                    if parent_name and any(f.lower() == parent_name.lower() for f in installed_folders):
                        continue
                meta = merge_addon_meta(folder, meta, cat, match_kind=kind or "exact")
                name = meta.get("name") or folder
                desc = meta.get("description") or meta.get("repository") or "Detected in Interface/AddOns"
                pack_folders = meta.get("folders") if isinstance(meta.get("folders"), list) else None
                if not pack_folders:
                    # Children may only be linked via managed_by
                    pack_folders = [
                        folder,
                        *[
                            f
                            for f, m in installed_meta.items()
                            if str(m.get("managed_by") or "").lower() == folder.lower()
                        ],
                    ]
                    # Also include disk modules that prefix-match this catalog parent
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
                if len(pack_folders) > 1:
                    modules = [f for f in pack_folders if f.lower() != folder.lower()]
                    mod_note = f"{len(pack_folders)} modules: {', '.join(modules)}"
                    if len(mod_note) > 90:
                        shown = modules[:4]
                        extra = len(modules) - len(shown)
                        mod_note = f"{len(pack_folders)} modules: {', '.join(shown)}"
                        if extra > 0:
                            mod_note += f", +{extra} more"
                    desc = mod_note
                category = meta.get("category") or "Installed"
                repo_url = meta.get("url") or (cat or {}).get("repo") or ""
                if not matches(name, desc, category, folder):
                    continue
                entry = {
                    "name": name,
                    "folder": folder,
                    "description": desc,
                    "category": category,
                    "repo": repo_url,
                    "source": meta.get("source", "detected"),
                }
                if folder in update_map:
                    status = "Update available"
                elif self._addons_scan_done:
                    status = status_with_stamp("Up to date", meta)
                else:
                    status = "Not checked"
                row = AddonRow(entry, status=status)
                row.update_clicked.connect(self.update_requested.emit)
                row.reinstall_clicked.connect(self.reinstall_requested.emit)
                row.remove_clicked.connect(self.remove_requested.emit)
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, INSTALLED_ROW_H))
                self.installed_list.addItem(item)
                self.installed_list.setItemWidget(item, row)
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
