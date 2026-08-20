"""Main window — borderless, top tabs, bottom play bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

_RESIZE_MARGIN = 6

from ichalaunch import __version__
from ichalaunch.addons.github import (
    AddonUpdateCheckResult,
    check_addon_updates,
    install_from_github,
    recently_checked_addon_updates,
    uninstall_addon,
    update_addon,
)
from ichalaunch.config.settings import settings
from ichalaunch.core.detect import full_resync
from ichalaunch.core.filesystem import is_protected_path
from ichalaunch.core.logging_setup import log
from ichalaunch.game.launcher import install_game_stub, is_installed, launch_game, validate_install_location
from ichalaunch.mods.installer import (
    ModUpdateCheckResult,
    apply_desired_state,
    check_mod_updates,
    recently_checked_mod_updates,
    update_mod,
    update_mods,
)
from ichalaunch.ui.pages.addons import AddonsPage
from ichalaunch.ui.pages.client import ClientPage
from ichalaunch.ui.pages.home import HomePage
from ichalaunch.ui.pages.settings import SettingsPage


class Worker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    progress_pct = Signal(int)  # 0-100, or -1 for indeterminate

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            def progress(msg: str):
                self.status.emit(msg)
                self.progress_pct.emit(-1)

            kwargs = dict(self.kwargs)
            try:
                result = self.fn(*self.args, progress=progress, **kwargs)
            except TypeError:
                result = self.fn(*self.args, **kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("Worker failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"IchaLaunch {__version__}")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(780, 520)

        self._worker: Worker | None = None
        self._update_worker: Worker | None = None
        self._mod_update_worker: Worker | None = None
        self._drag_pos: QPoint | None = None
        self._checking_addons = False
        self._checking_mods = False
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._resize_origin: QPoint | None = None
        self._resize_geo: QRect | None = None
        self._pending_ok_handler = None
        self._current_nav = -1
        self._fitted = False

        self.setMouseTracking(True)
        self._fit_to_screen(initial=True)

        root = QWidget()
        root.setObjectName("Root")
        root.setMouseTracking(True)
        root.setStyleSheet(
            "QWidget#Root { background-color: #181412; border: 1px solid rgba(150, 131, 158, 0.22); }"
        )
        self.setCentralWidget(root)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Title bar ----
        title = QWidget()
        title.setObjectName("TitleBar")
        title.setFixedHeight(44)
        title_l = QHBoxLayout(title)
        title_l.setContentsMargins(16, 0, 8, 0)
        brand = QLabel("IchaLaunch")
        brand.setStyleSheet("color: #F1C22D; font-size: 16px; font-weight: 700;")
        sub = QLabel(f"  RavenCraft  ·  v{__version__}")
        sub.setObjectName("Muted")
        title_l.addWidget(brand)
        title_l.addWidget(sub)
        title_l.addStretch(1)

        min_btn = QPushButton("—")
        min_btn.setObjectName("WinBtn")
        min_btn.setFixedSize(36, 28)
        min_btn.clicked.connect(self.showMinimized)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(36, 28)
        close_btn.clicked.connect(self.close)
        title_l.addWidget(min_btn)
        title_l.addWidget(close_btn)

        # ---- Top nav tabs ----
        nav = QWidget()
        nav.setObjectName("TopNav")
        nav.setFixedHeight(44)
        nav_l = QHBoxLayout(nav)
        nav_l.setContentsMargins(12, 0, 12, 0)
        nav_l.setSpacing(4)
        self.nav_btns: list[QPushButton] = []
        for i, label in enumerate(["HOME", "ADDONS", "CLIENT", "SETTINGS"]):
            btn = QPushButton(label)
            btn.setObjectName("TopNavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, idx=i: self._nav(idx))
            nav_l.addWidget(btn)
            self.nav_btns.append(btn)
        nav_l.addStretch(1)

        # ---- Pages ----
        self.stack = QStackedWidget()
        self.home = HomePage()
        self.addons = AddonsPage()
        self.client = ClientPage()
        self.settings_page = SettingsPage()
        for page in (self.home, self.addons, self.client, self.settings_page):
            self.stack.addWidget(page)

        # ---- Bottom play bar ----
        bottom = QWidget()
        bottom.setObjectName("BottomBar")
        bottom.setFixedHeight(78)
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(16, 12, 4, 4)
        bot_l.setSpacing(14)

        prog_wrap = QVBoxLayout()
        prog_wrap.setSpacing(4)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setObjectName("Muted")
        self.progress = QProgressBar()
        self.progress.setObjectName("BottomProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setMinimumHeight(22)
        self.progress.hide()
        prog_wrap.addWidget(self.status_lbl)
        prog_wrap.addWidget(self.progress)

        self.play_btn = QPushButton("PLAY")
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(180, 54)
        self.play_btn.clicked.connect(self._on_play_or_install)

        grip = QSizeGrip(bottom)
        grip.setFixedSize(16, 16)
        grip.setToolTip("Drag to resize")

        bot_l.addLayout(prog_wrap, 1)
        bot_l.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bot_l.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        outer.addWidget(title)
        outer.addWidget(nav)
        outer.addWidget(self.stack, 1)
        outer.addWidget(bottom)

        # Wire
        self.home.play_clicked.connect(self._on_play_or_install)
        self.home.install_clicked.connect(self._install_or_browse)
        self.client.apply_clicked.connect(self._apply_mods)
        self.client.rescan_clicked.connect(self._resync)
        self.client.check_updates_requested.connect(self._check_mod_updates)
        self.client.update_mod_requested.connect(self._update_client_mod)
        self.client.update_all_mods_requested.connect(self._update_all_client_mods)
        self.addons.install_requested.connect(self._install_catalog_addon)
        self.addons.update_requested.connect(self._update_addon)
        self.addons.update_all_requested.connect(self._update_all_addons)
        self.addons.remove_requested.connect(self._remove_addon)
        self.addons.github_import_requested.connect(self._github_import)
        self.addons.check_updates_requested.connect(self._check_updates)
        self.addons.rescan_requested.connect(self._resync)
        self.settings_page.browse_clicked.connect(self._browse_game)
        self.settings_page.verify_clicked.connect(self._verify_game)

        self._refresh_play_button()
        self._nav(0)
        if is_installed():
            self._resync(silent=True)
            if settings.check_updates_on_startup():
                self._check_updates(silent=True)
                self._check_mod_updates(silent=True)

    # --- window chrome ---
    def _available_geo(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_to_screen(self, initial: bool = False) -> None:
        """Clamp max size to usable desktop; do not lock to a fixed size."""
        avail = self._available_geo()
        max_w = max(640, avail.width() - 24)
        max_h = max(480, avail.height() - 24)
        self.setMaximumSize(max_w, max_h)
        if initial or not self._fitted:
            w = min(1080, max_w)
            h = min(720, max_h)
            self.resize(w, h)
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())
            self._fitted = True
        else:
            # Shrink if somehow larger than the screen
            if self.width() > max_w or self.height() > max_h:
                self.resize(min(self.width(), max_w), min(self.height(), max_h))
            self._clamp_on_screen()

    def _clamp_on_screen(self) -> None:
        avail = self._available_geo()
        geo = self.frameGeometry()
        x = min(max(geo.x(), avail.left()), avail.right() - geo.width() + 1)
        y = min(max(geo.y(), avail.top()), avail.bottom() - geo.height() + 1)
        if x != geo.x() or y != geo.y():
            self.move(x, y)

    def _hit_resize_edges(self, pos: QPoint) -> tuple[bool, bool, bool, bool]:
        r = self.rect()
        m = _RESIZE_MARGIN
        return (
            pos.x() <= m,
            pos.x() >= r.width() - m,
            pos.y() <= m,
            pos.y() >= r.height() - m,
        )

    def _update_resize_cursor(self, edges: tuple[bool, bool, bool, bool]) -> None:
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._resize_edges is not None:
            self.releaseMouse()
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        """Edge-resize / cursor when children sit on the frameless border."""
        if isinstance(obj, QWidget) and obj.window() is self and not isinstance(obj, QSizeGrip):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = self.mapFromGlobal(event.globalPosition().toPoint())
                edges = self._hit_resize_edges(pos)
                if any(edges):
                    self._resize_edges = edges
                    self._resize_origin = event.globalPosition().toPoint()
                    self._resize_geo = QRect(self.geometry())
                    self._drag_pos = None
                    self.grabMouse()
                    return True
            elif et == QEvent.Type.MouseMove:
                if self._resize_edges is not None and self._resize_origin is not None and self._resize_geo is not None:
                    self._apply_edge_resize(event.globalPosition().toPoint())
                    return True
                if not (event.buttons() & Qt.MouseButton.LeftButton):
                    pos = self.mapFromGlobal(event.globalPosition().toPoint())
                    if self.rect().contains(pos):
                        self._update_resize_cursor(self._hit_resize_edges(pos))
            elif et == QEvent.Type.MouseButtonRelease and self._resize_edges is not None:
                self.releaseMouse()
                self._resize_edges = None
                self._resize_origin = None
                self._resize_geo = None
                pos = self.mapFromGlobal(event.globalPosition().toPoint())
                self._update_resize_cursor(self._hit_resize_edges(pos))
                return True
        return super().eventFilter(obj, event)

    def _apply_edge_resize(self, global_pos: QPoint) -> None:
        if self._resize_edges is None or self._resize_origin is None or self._resize_geo is None:
            return
        delta = global_pos - self._resize_origin
        left, right, top, bottom = self._resize_edges
        g = self._resize_geo
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        max_w, max_h = self.maximumWidth(), self.maximumHeight()
        if left:
            new_w = w - delta.x()
            if new_w < min_w:
                x = g.x() + w - min_w
                w = min_w
            elif new_w > max_w:
                x = g.x() + w - max_w
                w = max_w
            else:
                x = g.x() + delta.x()
                w = new_w
        if right:
            w = max(min_w, min(max_w, w + delta.x()))
        if top:
            new_h = h - delta.y()
            if new_h < min_h:
                y = g.y() + h - min_h
                h = min_h
            elif new_h > max_h:
                y = g.y() + h - max_h
                h = max_h
            else:
                y = g.y() + delta.y()
                h = new_h
        if bottom:
            h = max(min_h, min(max_h, h + delta.y()))
        self.setGeometry(x, y, w, h)
        self._clamp_on_screen()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._hit_resize_edges(pos)
            if any(edges):
                self._resize_edges = edges
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geo = QRect(self.geometry())
                self._drag_pos = None
                event.accept()
                return
            if pos.y() <= 44:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._resize_edges = None
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edges is not None and self._resize_origin is not None and self._resize_geo is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._apply_edge_resize(event.globalPosition().toPoint())
                event.accept()
                return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._clamp_on_screen()
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_edges is not None:
            self.releaseMouse()
        self._drag_pos = None
        self._resize_edges = None
        self._resize_origin = None
        self._resize_geo = None
        self._update_resize_cursor(self._hit_resize_edges(event.position().toPoint()))
        super().mouseReleaseEvent(event)

    def _nav(self, idx: int) -> None:
        if idx == self._current_nav:
            return
        self._current_nav = idx
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_btns):
            b.setChecked(i == idx)
        # Lightweight page updates only — never rebuild huge addon lists on switch
        if idx == 0:
            self.home.refresh()
        elif idx == 2:
            self.client.refresh_from_settings()
        elif idx == 3:
            self.settings_page.refresh()
        # Addons page keeps its current list until filters/rescan change

    def _refresh_play_button(self) -> None:
        if is_installed():
            self.play_btn.setText("PLAY")
        else:
            self.play_btn.setText("INSTALL")

    def _set_busy_ui(self, busy: bool, msg: str = "") -> None:
        self.play_btn.setEnabled(not busy)
        if busy:
            self.progress.show()
            self.progress.setRange(0, 0)  # indeterminate
            self.status_lbl.setText(msg or "Working…")
        else:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status_lbl.setText(msg or "Ready")

    def _refresh_check_loading(self) -> None:
        addon_busy = self._checking_addons
        mod_busy = self._checking_mods
        self.addons.set_checking(addon_busy, "Checking for updates…")
        self.client.set_checking(mod_busy, "Checking for updates…")
        if addon_busy and mod_busy:
            self.home.set_checking(True, "Checking addon & client updates…")
        elif addon_busy:
            self.home.set_checking(True, "Checking addon updates…")
        elif mod_busy:
            self.home.set_checking(True, "Checking client mod updates…")
        else:
            self.home.set_checking(False)

    def _resync(self, silent: bool = False) -> None:
        if not is_installed():
            if not silent:
                QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return
        result = full_resync()
        # Disk rescan is not an update-check — keep / reset scan-done so we don't
        # claim "Up to date" without a successful Check Updates pass.
        self.addons.reset_scan_done()
        self.client.reset_scan_done()
        self.client.refresh_from_settings()
        self.addons.mark_dirty()
        self.addons.refresh()
        self.home.refresh()
        self._refresh_play_button()
        if not silent:
            QMessageBox.information(
                self,
                "Rescan complete",
                f"Detected {len(result['addons'])} addon folder(s) and synced client mod checkboxes.",
            )

    def _busy(self, title: str, worker: Worker, on_ok=None) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "Another task is already running.")
            return
        self._set_busy_ui(True, title)
        worker.status.connect(lambda m: self.status_lbl.setText(m))
        worker.finished_ok.connect(self._on_worker_ok)
        worker.failed.connect(self._on_worker_fail)
        self._worker = worker
        self._pending_ok_handler = on_ok
        worker.start()

    def _on_worker_ok(self, result) -> None:
        self._set_busy_ui(False, "Ready")
        handler = self._pending_ok_handler
        self._pending_ok_handler = None
        if handler:
            handler(result)
        self.home.refresh()
        self.client.refresh_plan()
        self.addons.mark_dirty()
        self.addons.refresh()
        self.settings_page.refresh()
        self._refresh_play_button()

    def _on_worker_fail(self, msg: str) -> None:
        self._set_busy_ui(False, "Failed")
        QMessageBox.critical(self, "Error", msg)

    def _on_play_or_install(self) -> None:
        if is_installed():
            self._play()
        else:
            self._install_or_browse()

    def _play(self) -> None:
        try:
            launch_game()
            if settings.get("close_on_launch"):
                self.close()
            elif settings.get("minimize_on_launch"):
                self.showMinimized()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Launch failed", str(exc))

    def _install_or_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Ravencraft install folder", "C:/Games")
        if not path:
            return
        p = Path(path)
        ok, msg = validate_install_location(p)
        if not ok:
            QMessageBox.warning(self, "Protected location", msg)
            return
        if (p / "WoW.exe").exists():
            settings.game_path = str(p)
            self._resync(silent=True)
            QMessageBox.information(self, "Ready", f"Using existing client:\n{p}")
            self.home.refresh()
            self._refresh_play_button()
            return
        note = install_game_stub(p)
        QMessageBox.information(
            self,
            "Install folder ready",
            f"{note}\n\nSelected folder:\n{p}\n\n"
            "After copying client files (or browsing to an existing install), press PLAY.",
        )
        self.home.refresh()
        self._refresh_play_button()

    def _browse_game(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select game folder (contains WoW.exe)")
        if not path:
            return
        if is_protected_path(path):
            QMessageBox.warning(
                self,
                "Protected location",
                "This folder may cause permission issues with client mods.",
            )
        if not (Path(path) / "WoW.exe").exists():
            QMessageBox.warning(self, "Not a game folder", "WoW.exe was not found in that folder.")
            return
        settings.game_path = path
        self.settings_page.refresh()
        self._resync(silent=True)
        self._refresh_play_button()
        QMessageBox.information(self, "Saved", f"Game path set to:\n{path}")

    def _verify_game(self) -> None:
        if is_installed():
            QMessageBox.information(self, "Verify", f"WoW.exe found at:\n{settings.game_path}")
        else:
            QMessageBox.warning(self, "Verify", "Game not detected. Browse to a valid client folder.")

    def _apply_mods(self) -> None:
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return
        worker = Worker(apply_desired_state)
        self._busy(
            "Applying client mods…",
            worker,
            on_ok=lambda result: QMessageBox.information(
                self, "Client updated", "Changes applied:\n" + ("\n".join(result) if result else "(none)")
            ),
        )

    def _install_catalog_addon(self, entry: dict) -> None:
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return
        url = entry.get("repo")
        folder = entry.get("folder")
        worker = Worker(install_from_github, url, folder)
        self._busy(
            f"Installing {entry.get('name')}…",
            worker,
            on_ok=lambda name: QMessageBox.information(self, "Installed", f"Installed: {name}"),
        )

    def _github_import(self, url: str) -> None:
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return
        worker = Worker(install_from_github, url)
        self._busy(
            "Importing from GitHub…",
            worker,
            on_ok=lambda name: QMessageBox.information(
                self, "Installed from GitHub", f"Installed from GitHub: {name}"
            ),
        )

    def _update_addon(self, entry: dict) -> None:
        folder = entry.get("folder") or entry.get("name")
        if not folder:
            return
        worker = Worker(update_addon, folder)

        def on_ok(_result):
            self.addons.clear_pending_update(folder)
            self.status_lbl.setText(f"Updated {folder}")
            QMessageBox.information(self, "Updated", f"Updated {folder}")

        self._busy(f"Updating {folder}…", worker, on_ok=on_ok)

    def _update_all_addons(self) -> None:
        pending = self.addons.pending_updates
        folders = [u.get("folder") or u.get("name") for u in pending]
        folders = [f for f in folders if f]
        if not folders:
            self.status_lbl.setText("No updates available — run Check Updates first.")
            return
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return

        total = len(folders)

        def run_all(progress=None):
            ok: list[str] = []
            failed: list[tuple[str, str]] = []
            for i, folder in enumerate(folders, start=1):
                if progress:
                    progress(f"Updating {folder} ({i}/{total})…")
                try:
                    update_addon(folder, progress=progress)
                    ok.append(folder)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Update All failed for %s", folder)
                    failed.append((folder, str(exc)))
            return {"ok": ok, "failed": failed}

        def on_ok(result):
            ok_folders = set(result.get("ok") or [])
            remaining = [u for u in self.addons.pending_updates if u.get("folder") not in ok_folders]
            self.addons.set_updates(remaining)
            n_ok = len(ok_folders)
            failed = result.get("failed") or []
            if failed:
                names = ", ".join(f for f, _ in failed[:5])
                more = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
                self.status_lbl.setText(f"Updated {n_ok}/{total}; failed: {names}{more}")
            else:
                self.status_lbl.setText(f"Updated {n_ok} addon(s)")

        worker = Worker(run_all)
        self._busy(f"Updating {total} addon(s)…", worker, on_ok=on_ok)

    def _remove_addon(self, folder: str) -> None:
        if QMessageBox.question(self, "Remove addon", f"Remove {folder}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            uninstall_addon(folder)
            self.addons.mark_dirty()
            self.addons.refresh()
            self.home.refresh()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", str(exc))

    def _check_updates(self, silent: bool = False) -> None:
        """Quiet background check — status bar only, never blocks PLAY or shows a popup."""
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._update_worker and self._update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Update check already running…")
            return

        # Startup: skip quietly if we already checked within the cooldown window.
        if silent and recently_checked_addon_updates():
            return

        self.status_lbl.setText("Checking addon updates…")
        self._checking_addons = True
        self._refresh_check_loading()
        worker = Worker(check_addon_updates, respect_cooldown=False)

        def done(result):
            self._checking_addons = False
            self._refresh_check_loading()
            if isinstance(result, AddonUpdateCheckResult):
                updates = result.updates
                status = result.status_message
            else:
                updates = result or []
                status = None
            self.addons.set_updates(updates)
            if status:
                self.status_lbl.setText(status)
            elif updates:
                self.status_lbl.setText(f"{len(updates)} addon update(s) available")
            else:
                self.status_lbl.setText("Addons up to date")
            self._update_worker = None

        def fail(msg: str):
            self._checking_addons = False
            self._refresh_check_loading()
            self.status_lbl.setText(f"Update check failed: {msg[:80]}")
            self._update_worker = None

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._update_worker = worker
        worker.start()

    def _check_mod_updates(self, silent: bool = False) -> None:
        if not is_installed():
            if not silent:
                self.status_lbl.setText("Set a game path before checking updates")
            return
        if self._mod_update_worker and self._mod_update_worker.isRunning():
            if not silent:
                self.status_lbl.setText("Client mod update check already running…")
            return
        if silent and recently_checked_mod_updates():
            return

        self.status_lbl.setText("Checking client mod updates…")
        self._checking_mods = True
        self._refresh_check_loading()
        worker = Worker(check_mod_updates, respect_cooldown=False)

        def done(result):
            self._checking_mods = False
            self._refresh_check_loading()
            if isinstance(result, ModUpdateCheckResult):
                updates = result.updates
                status = result.status_message
                if result.skipped_recent:
                    self._mod_update_worker = None
                    return
            else:
                updates = result or []
                status = None
            self.client.set_updates(updates)
            if status:
                self.status_lbl.setText(status)
            elif updates:
                self.status_lbl.setText(f"{len(updates)} client mod update(s) available")
            else:
                self.status_lbl.setText("Client mods up to date")
            self._mod_update_worker = None

        def fail(msg: str):
            self._checking_mods = False
            self._refresh_check_loading()
            self.status_lbl.setText(f"Client mod check failed: {msg[:80]}")
            self._mod_update_worker = None

        worker.finished_ok.connect(done)
        worker.failed.connect(fail)
        self._mod_update_worker = worker
        worker.start()

    def _update_client_mod(self, mod_id: str) -> None:
        if not mod_id:
            return
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return

        def on_ok(_result):
            self.client.clear_pending_update(mod_id)
            self.status_lbl.setText(f"Updated {mod_id}")
            QMessageBox.information(self, "Updated", f"Updated client mod: {mod_id}")

        worker = Worker(update_mod, mod_id)
        self._busy(f"Updating {mod_id}…", worker, on_ok=on_ok)

    def _update_all_client_mods(self) -> None:
        pending = self.client.pending_updates
        ids = [u.get("id") for u in pending if u.get("id")]
        if not ids:
            self.status_lbl.setText("No client mod updates — run Check Updates first.")
            return
        if not is_installed():
            QMessageBox.warning(self, "No game", "Set a valid game path first.")
            return
        total = len(ids)

        def on_ok(_result):
            self.client.set_updates([])
            self.status_lbl.setText(f"Updated {total} client mod(s)")

        worker = Worker(update_mods, ids)
        self._busy(f"Updating {total} client mod(s)…", worker, on_ok=on_ok)
