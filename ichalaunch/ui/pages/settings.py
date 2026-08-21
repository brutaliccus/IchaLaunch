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
from ichalaunch.config.settings import settings
from ichalaunch.ui.widgets.casting_bar_search_edit import (
    SETTINGS_MIN_H,
    CastingBarSearchEdit,
)
from ichalaunch.ui.widgets.glue_panel_button import GluePanelButton
from ichalaunch.ui.widgets.marble_bg import MarbleCard
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox


class SettingsPage(QWidget):
    browse_clicked = Signal()
    browse_addons_clicked = Signal()
    reset_addons_clicked = Signal()
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

        launch_card = MarbleCard()
        launch_card.body.setSpacing(10)
        launch_title = QLabel("Launch")
        launch_title.setObjectName("CardTitle")
        launch_card.body.addWidget(launch_title)
        self.cb_vf = ThemeCheckBox("Launch through VanillaFixes.exe when available")
        self.cb_vf.setChecked(bool(settings.get("vanillafixes_enabled", True)))
        self.cb_vf.toggled.connect(lambda v: settings.set("vanillafixes_enabled", v))
        self.cb_min = ThemeCheckBox("Minimize launcher when game starts")
        self.cb_min.setChecked(bool(settings.get("minimize_on_launch", False)))
        self.cb_min.toggled.connect(lambda v: settings.set("minimize_on_launch", v))
        self.cb_close = ThemeCheckBox("Close launcher when game starts")
        self.cb_close.setChecked(bool(settings.get("close_on_launch", False)))
        self.cb_close.toggled.connect(lambda v: settings.set("close_on_launch", v))
        for cb in (self.cb_vf, self.cb_min, self.cb_close):
            cb.setMinimumHeight(28)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            launch_card.body.addWidget(cb)

        upd_card = MarbleCard()
        upd_card.body.setSpacing(10)
        upd_title = QLabel("Updates")
        upd_title.setObjectName("CardTitle")
        upd_card.body.addWidget(upd_title)
        self.cb_auto_updates = ThemeCheckBox("Automatically Check For Updates On Startup")
        self.cb_auto_updates.setChecked(settings.check_updates_on_startup())
        self.cb_auto_updates.setToolTip(
            "When enabled, quietly checks launcher, addon, and client mod updates "
            "shortly after launch. While open, only the launcher self-update "
            "re-checks every 5 minutes (no progress bar)."
        )
        self.cb_auto_updates.toggled.connect(settings.set_check_updates_on_startup)
        self.cb_auto_updates.setMinimumHeight(28)
        self.cb_auto_updates.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        upd_card.body.addWidget(self.cb_auto_updates)

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
            "Raises the API limit (60 → 5,000 req/hour). Autosaves to local settings.json — never uploaded."
        )
        token_note.setObjectName("Muted")
        token_note.setWordWrap(True)
        gh_card.body.addWidget(token_note)

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

        layout.addWidget(title)
        layout.addWidget(game_card)
        layout.addWidget(addons_card)
        layout.addWidget(launch_card)
        layout.addWidget(upd_card)
        layout.addWidget(gh_card)
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
        # Avoid clobbering in-progress edits / firing textChanged autosave.
        stored = str(settings.get("github_token") or "")
        if self.token_edit.text() != stored:
            self.token_edit.blockSignals(True)
            self.token_edit.setText(stored)
            self.token_edit.blockSignals(False)
