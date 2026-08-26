"""Settings page."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch import __version__
from ichalaunch.core.logging_setup import log_dir
from ichalaunch.config.settings import settings
from ichalaunch.ui.widgets.casting_bar_search_edit import (
    SETTINGS_MIN_H,
    CastingBarSearchEdit,
)
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton
from ichalaunch.ui.widgets.marble_bg import MarbleCard
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox


class SettingsPage(QWidget):
    browse_clicked = Signal()
    browse_addons_clicked = Signal()
    reset_addons_clicked = Signal()
    reset_client_link_clicked = Signal()
    clear_cache_clicked = Signal()
    check_permissions_clicked = Signal()
    verify_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SettingsPage")
        self.setStyleSheet("QWidget#SettingsPage { background: transparent; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setStyleSheet(
            "QScrollArea#SettingsScroll, QScrollArea#SettingsScroll > QWidget > QWidget,"
            " QScrollArea#SettingsScroll QWidget#qt_scrollarea_viewport { background: transparent; border: none; }"
        )

        host = QWidget()
        host.setObjectName("SettingsHost")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("SectionTitle")

        game_card = MarbleCard()
        game_card.body.setSpacing(10)
        game_title = QLabel("Game location")
        game_title.setObjectName("CardTitle")
        game_card.body.addWidget(game_title)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = CastingBarSearchEdit(
            object_name="SettingsGamePath",
            read_only=True,
            clear_button=False,
            minimum_height=SETTINGS_MIN_H,
        )
        self.path_edit.setText(settings.game_path)
        browse = GluePanelButton("Browse…")
        browse.clicked.connect(self.browse_clicked.emit)
        verify = GluePanelButton("Verify")
        verify.clicked.connect(self.verify_clicked.emit)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        row.addWidget(verify)
        game_card.body.addLayout(row)
        note = QLabel("Avoid Program Files / Desktop / Downloads / Documents.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        game_card.body.addWidget(note)
        reset_link = GluePanelButton("Reset Client Link", width=148)
        reset_link.setToolTip(
            "Unlink the saved WoW folder so you can INSTALL to a new location. "
            "Does not delete files on disk."
        )
        reset_link.clicked.connect(self.reset_client_link_clicked.emit)
        game_card.body.addWidget(reset_link)
        reset_note = QLabel(
            "Used to reinstall the client. Clears the saved WoW folder so PLAY "
            "becomes INSTALL and you can choose a new location. Does not delete "
            "files on disk."
        )
        reset_note.setObjectName("Muted")
        reset_note.setWordWrap(True)
        game_card.body.addWidget(reset_note)

        addons_card = MarbleCard()
        addons_card.body.setSpacing(10)
        addons_title = QLabel("AddOns folder")
        addons_title.setObjectName("CardTitle")
        addons_card.body.addWidget(addons_title)
        addons_row = QHBoxLayout()
        addons_row.setSpacing(8)
        self.addons_edit = CastingBarSearchEdit(
            object_name="SettingsAddonsPath",
            read_only=True,
            clear_button=False,
            minimum_height=SETTINGS_MIN_H,
        )
        self.addons_edit.setText(settings.resolved_addons_path())
        browse_addons = GluePanelButton("Browse…")
        browse_addons.clicked.connect(self.browse_addons_clicked.emit)
        reset_addons = GluePanelButton("Reset to default")
        reset_addons.setToolTip("Use {game folder}\\Interface\\AddOns")
        reset_addons.clicked.connect(self.reset_addons_clicked.emit)
        addons_row.addWidget(self.addons_edit, 1)
        addons_row.addWidget(browse_addons)
        addons_row.addWidget(reset_addons)
        addons_card.body.addLayout(addons_row)
        addons_note = QLabel(
            "Defaults to Interface\\AddOns under the game folder when you set or change "
            "the game path. Override only if your addons live elsewhere."
        )
        addons_note.setObjectName("Muted")
        addons_note.setWordWrap(True)
        addons_card.body.addWidget(addons_note)

        upd_card = MarbleCard()
        upd_card.body.setSpacing(10)
        upd_title = QLabel("Updates")
        upd_title.setObjectName("CardTitle")
        upd_card.body.addWidget(upd_title)
        self.cb_auto_updates = ThemeCheckBox("Automatically Check For Updates On Startup")
        self.cb_auto_updates.setChecked(settings.check_updates_on_startup())
        self.cb_auto_updates.setToolTip(
            "When enabled, quietly checks launcher, addon, and client mod updates "
            "shortly after launch, then every 15 minutes while the launcher stays open. "
            "Addon and client updates compare your install to the shared catalog JSON "
            "(one request). Launcher, addon, and client checks share the same "
            "15-minute refresh. A GitHub token is optional for fork/version browsing."
        )
        self.cb_auto_updates.toggled.connect(settings.set_check_updates_on_startup)
        self.cb_auto_updates.setMinimumHeight(28)
        self.cb_auto_updates.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        upd_card.body.addWidget(self.cb_auto_updates)
        self.cb_toc_mismatch = ThemeCheckBox(
            "Prompt to fix addon folder / .toc name mismatches"
        )
        self.cb_toc_mismatch.setChecked(settings.auto_fix_addon_toc_mismatch())
        self.cb_toc_mismatch.setToolTip(
            "When enabled, addon disk scans (startup, Rescan Disk, game/AddOns path "
            "changes) ask you to rename folders whose names do not match their .toc "
            "file — the same prompts as Rescan Disk. Turn off to skip these prompts; "
            "scans still run."
        )
        self.cb_toc_mismatch.toggled.connect(settings.set_auto_fix_addon_toc_mismatch)
        self.cb_toc_mismatch.setMinimumHeight(28)
        self.cb_toc_mismatch.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        upd_card.body.addWidget(self.cb_toc_mismatch)

        privacy_card = MarbleCard()
        privacy_card.body.setSpacing(10)
        privacy_title = QLabel("Privacy")
        privacy_title.setObjectName("CardTitle")
        privacy_card.body.addWidget(privacy_title)
        self.cb_crash_reports = ThemeCheckBox(
            "Send crash and error reports to the maintainer"
        )
        self.cb_crash_reports.setChecked(
            bool(settings.get("crash_reporting_enabled", False))
        )
        self.cb_crash_reports.setToolTip(
            "When enabled, IchaLaunch automatically sends crash.log excerpts and "
            "significant error logs to the project maintainer (via a Cloudflare "
            "Worker that appends a comment on one GitHub crash-log issue). Off by "
            "default. No Discord tokens or GitHub PATs are included; reports are "
            "best-effort and never required."
        )
        self.cb_crash_reports.toggled.connect(
            lambda v: settings.set("crash_reporting_enabled", bool(v))
        )
        self.cb_crash_reports.setMinimumHeight(28)
        self.cb_crash_reports.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        privacy_card.body.addWidget(self.cb_crash_reports)
        privacy_note = QLabel(
            "Optional and off by default. Helps fix bugs; only sends redacted log "
            "excerpts when something crashes or a serious error is logged."
        )
        privacy_note.setObjectName("Muted")
        privacy_note.setWordWrap(True)
        privacy_card.body.addWidget(privacy_note)

        gh_card = MarbleCard()
        gh_card.body.setSpacing(10)
        gh_title = QLabel("GitHub API")
        gh_title.setObjectName("CardTitle")
        gh_card.body.addWidget(gh_title)
        token_row = QHBoxLayout()
        token_row.setSpacing(8)
        self.token_edit = CastingBarSearchEdit(
            object_name="SettingsGithubToken",
            read_only=False,
            clear_button=False,
            minimum_height=SETTINGS_MIN_H,
        )
        self.token_edit.setText(str(settings.get("github_token") or ""))
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Personal access token (optional)")
        self.token_edit.setMinimumWidth(280)
        self.token_edit.setToolTip("Saved automatically when you leave the field or click Save")
        self._token_save_timer = QTimer(self)
        self._token_save_timer.setSingleShot(True)
        self._token_save_timer.setInterval(500)
        self._token_save_timer.timeout.connect(self._save_github_token)
        self.token_edit.textChanged.connect(lambda: self._token_save_timer.start())
        self.token_edit.editingFinished.connect(self._save_github_token)
        token_save = GluePanelButton("Save")
        token_save.clicked.connect(lambda: self._save_github_token(force_feedback=True))
        self.token_status = QLabel("")
        self.token_status.setObjectName("Muted")
        self.token_status.setMinimumWidth(56)
        self._token_status_clear = QTimer(self)
        self._token_status_clear.setSingleShot(True)
        self._token_status_clear.setInterval(2000)
        self._token_status_clear.timeout.connect(lambda: self.token_status.setText(""))
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(token_save)
        token_row.addWidget(self.token_status)
        gh_card.body.addLayout(token_row)
        token_note = QLabel(
            "Optional. Addon update badges do not need a token. A token unlocks "
            "fork/version pickers and README previews (60 → 5,000 API req/hour). "
            "Saved in local settings.json. Sent only over HTTPS to GitHub "
            "(api.github.com / githubusercontent.com), never to other sites."
        )
        token_note.setObjectName("Muted")
        token_note.setWordWrap(True)
        gh_card.body.addWidget(token_note)

        maint_card = MarbleCard()
        maint_card.body.setSpacing(10)
        maint_title = QLabel("Maintenance")
        maint_title.setObjectName("CardTitle")
        maint_card.body.addWidget(maint_title)
        maint_row = QHBoxLayout()
        maint_row.setSpacing(8)
        clear_cache = GluePanelButton("Clear Cache", width=148, height=GLUE_BTN_H)
        clear_cache.setToolTip(
            "Reset launcher settings, cached scan data, and saved preferences. "
            "Does not delete game or addon files on disk."
        )
        clear_cache.clicked.connect(self.clear_cache_clicked.emit)
        check_permissions = GluePanelButton(
            "Check Game Permissions", width=220, height=GLUE_BTN_H
        )
        check_permissions.setToolTip(
            "Scan the linked WoW folder for read-only files and Windows permission "
            "problems that can cause access-denied crashes. If the game is in "
            "Downloads or another restricted folder, move it first — then browse "
            "to the new location and run this check again."
        )
        check_permissions.clicked.connect(self.check_permissions_clicked.emit)
        maint_row.addWidget(clear_cache)
        maint_row.addWidget(check_permissions)
        maint_row.addStretch(1)
        maint_card.body.addLayout(maint_row)
        maint_note = QLabel(
            "Clears saved paths, mod/addon tracking, GitHub token, update scan "
            "queues, and other launcher preferences. Your WoW client and AddOn "
            "folders on disk are not deleted.\n\n"
            "Check Game Permissions scans the game folder, Data/, WTF/, and Interface/. "
            "If the game is in Downloads or another restricted folder, move it to "
            "a normal location (e.g. C:\\Games) and browse to the new path before "
            "re-running the check. Otherwise IchaLaunch can repair read-only flags "
            "and ACLs."
        )
        maint_note.setObjectName("Muted")
        maint_note.setWordWrap(True)
        maint_card.body.addWidget(maint_note)

        about = MarbleCard()
        about.body.setSpacing(10)
        about_title = QLabel(f"IchaLaunch {__version__}")
        about_title.setObjectName("CardTitle")
        about.body.addWidget(about_title)
        about_sub = QLabel(
            "Styled after ichasarmory.quest · Client mods via RetroCro/TurtleWoW-Mods sources"
        )
        about_sub.setObjectName("Muted")
        about_sub.setWordWrap(True)
        about.body.addWidget(about_sub)
        about_logs = QLabel(
            f"Support logs: {log_dir()} (crash.log, ichalaunch.log)"
        )
        about_logs.setObjectName("Muted")
        about_logs.setWordWrap(True)
        about.body.addWidget(about_logs)

        layout.addWidget(title)
        layout.addWidget(game_card)
        layout.addWidget(addons_card)
        layout.addWidget(upd_card)
        layout.addWidget(privacy_card)
        layout.addWidget(gh_card)
        layout.addWidget(maint_card)
        layout.addWidget(about)
        layout.addStretch(1)

        scroll.setWidget(host)
        outer.addWidget(scroll)

    def _save_github_token(self, force_feedback: bool = False) -> None:
        self._token_save_timer.stop()
        value = self.token_edit.text().strip()
        current = str(settings.get("github_token") or "")
        if value != current:
            settings.set("github_token", value)
            if value:
                try:
                    from ichalaunch.addons.github import clear_addon_scan_queue

                    clear_addon_scan_queue()
                except Exception:  # noqa: BLE001
                    pass
            self.token_status.setText("Saved")
            self._token_status_clear.start()
        elif force_feedback:
            self.token_status.setText("Saved")
            self._token_status_clear.start()

    def refresh(self) -> None:
        self.path_edit.setText(settings.game_path)
        self.addons_edit.setText(settings.resolved_addons_path())
        self.cb_auto_updates.blockSignals(True)
        self.cb_auto_updates.setChecked(settings.check_updates_on_startup())
        self.cb_auto_updates.blockSignals(False)
        self.cb_toc_mismatch.blockSignals(True)
        self.cb_toc_mismatch.setChecked(settings.auto_fix_addon_toc_mismatch())
        self.cb_toc_mismatch.blockSignals(False)
        self.cb_crash_reports.blockSignals(True)
        self.cb_crash_reports.setChecked(
            bool(settings.get("crash_reporting_enabled", False))
        )
        self.cb_crash_reports.blockSignals(False)
        # Avoid clobbering in-progress edits / firing textChanged autosave.
        stored = str(settings.get("github_token") or "")
        if self.token_edit.text() != stored:
            self.token_edit.blockSignals(True)
            self.token_edit.setText(stored)
            self.token_edit.blockSignals(False)
