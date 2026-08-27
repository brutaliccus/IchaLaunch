"""Themed modal dialogs matching the RavenCraft launcher look."""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GLUE_BTN_W, GluePanelButton
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox


class DialogResult(Enum):
    Yes = auto()
    No = auto()
    Ok = auto()
    Cancel = auto()
    Browse = auto()


_PRIMARY_RESULTS = frozenset({DialogResult.Yes, DialogResult.Ok, DialogResult.Browse})


def _themed_dialog_flags() -> Qt.WindowType:
    """Frameless modal flags without stay-on-top (avoids stealing game clicks)."""
    return Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint


def close_open_themed_dialogs(root: QWidget | None) -> None:
    """Close visible dialog descendants so they cannot intercept game input."""
    if root is None:
        return
    for dialog in root.findChildren(QDialog):
        if dialog.isVisible():
            dialog.close()


def _dialog_glue_width(label: str) -> int:
    n = len(label or "")
    if n >= 14:
        return 148
    if n >= 12:
        return 140
    if n >= 10:
        return 132
    return GLUE_BTN_W


def _dialog_glue_button(
    label: str,
    parent: QWidget | None = None,
    *,
    primary: bool = False,
) -> GluePanelButton:
    """Gold-bordered glue-panel action, matching Settings Browse / toolbar."""
    return GluePanelButton(
        label,
        parent,
        role="primary" if primary else "standard",
        width=_dialog_glue_width(label),
        height=GLUE_BTN_H,
    )


_LOADING_FORKS_TIP = "Loading forks from GitHub…"
_LOADING_VERSIONS_TIP = "Loading versions from GitHub…"
_LOADING_PREVIEW_TIP = "Wait for the preview to finish loading…"
_VERSIONS_LOADING_LABEL = "Loading…"
_VERSIONS_LOADING_DATA = "__loading__"


def _addon_fork_version_row(
    _parent: QWidget,
    *,
    fork_combo,
    version_combo,
) -> QHBoxLayout:
    """Fork + version combos (Open-in-Git lives on the title row, not here)."""
    row = QHBoxLayout()
    row.setSpacing(10)
    fork_lbl = QLabel("Fork")
    fork_lbl.setObjectName("Muted")
    ver_lbl = QLabel("Version")
    ver_lbl.setObjectName("Muted")
    for combo in (fork_combo, version_combo):
        combo.setFixedHeight(GLUE_BTN_H)
        combo.setSizePolicy(
            combo.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
    row.addWidget(fork_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(fork_combo, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(ver_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(version_combo, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    return row


def _addon_open_git_button(
    parent: QWidget,
    owner: QWidget,
    *url_candidates: object,
) -> OpenGitButton | None:
    """Inline Open-in-Git icon (~20px art / ~22px hit), same plate as AddonRow."""
    from ichalaunch.ui.widgets.common import (
        OpenGitButton,
        apply_open_git_visibility,
        git_repo_browse_url,
    )

    url = git_repo_browse_url(*url_candidates)
    if not url:
        return None
    btn = OpenGitButton(parent, plate="inline")
    btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
    apply_open_git_visibility(btn, url, owner)
    return btn


def _addon_dialog_title_row(
    title: str,
    open_git_btn: QWidget | None,
) -> tuple[QHBoxLayout, QLabel]:
    """Name → (optional) Open-in-Git with AddonRow spacing (~6px)."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    title_lbl = QLabel(title)
    title_lbl.setObjectName("ThemedDialogTitle")
    title_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    row.addWidget(title_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    if open_git_btn is not None:
        row.addWidget(open_git_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    return row, title_lbl


class ThemedDialog(QDialog):
    """Frameless dark card dialog with gold title and themed buttons."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        buttons: list[tuple[str, DialogResult]] | None = None,
        kind: str = "info",
    ):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setMaximumWidth(620)
        self._result = DialogResult.Cancel

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(14)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        msg = QLabel(text)
        msg.setObjectName("ThemedDialogBody")
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(msg)

        if buttons is None:
            buttons = [("OK", DialogResult.Ok)]

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        btn_widths = 0
        for label, result in buttons:
            btn = _dialog_glue_button(
                label, card, primary=result in _PRIMARY_RESULTS
            )
            btn.clicked.connect(lambda _checked=False, r=result: self._finish(r))
            row.addWidget(btn)
            btn_widths += btn.width()
        if len(buttons) >= 3:
            needed = 44 + 10 * (len(buttons) - 1) + btn_widths + 24
            self.setMinimumWidth(max(460, needed))
            if needed > 620:
                self.setMaximumWidth(needed + 40)
        body.addLayout(row)

        root.addWidget(card)

        # Soft accent tint by kind (info / warning / error / question)
        accents = {
            "info": "#7c5cc4",
            "warning": "#F1C22D",
            "error": "#c62828",
            "question": "#F1C22D",
        }
        accent = accents.get(kind, "#7c5cc4")
        self.setStyleSheet(
            f"QDialog#ThemedDialog {{ background: transparent; }}"
            f"QWidget#ThemedDialogCard {{"
            f"  background-color: #100d0c;"
            f"  border: 1px solid rgba(150, 131, 158, 0.22);"
            f"  border-top: 3px solid {accent};"
            f"  border-radius: 10px;"
            f"}}"
        )

    def _finish(self, result: DialogResult) -> None:
        self._result = result
        if result in (DialogResult.Yes, DialogResult.Ok, DialogResult.Browse):
            self.accept()
        else:
            self.reject()

    @property
    def result_value(self) -> DialogResult:
        return self._result


class DllSecurityExclusionDialog(QDialog):
    """First-time hint: add the WoW folder to Windows Security exclusions before DLL mods."""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(480, 360)
        self.resize(560, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_content.setObjectName("ThemedDialogScrollBody")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 4, 0)
        msg = QLabel(text)
        msg.setObjectName("ThemedDialogBody")
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll_layout.addWidget(msg)
        scroll.setWidget(scroll_content)
        body.addWidget(scroll, 1)

        self._dont_show = ThemeCheckBox("Don't show this again", card)
        self._dont_show.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dont_show.setMinimumHeight(28)
        self._dont_show.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        body.addWidget(self._dont_show)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        ok_btn = _dialog_glue_button("OK", card, primary=True)
        ok_btn.clicked.connect(self.accept)
        row.addWidget(ok_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
        )

    def dismissed_permanently(self) -> bool:
        return self._dont_show.isChecked()


class MpqPatchWarningDialog(QDialog):
    """Warning when enabling HD / patch-*.mpq client mods."""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(420, 220)
        self.resize(480, 240)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        msg = QLabel(text)
        msg.setObjectName("ThemedDialogBody")
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(msg, 1)

        self._dont_show = ThemeCheckBox("Don't show again", card)
        self._dont_show.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dont_show.setMinimumHeight(28)
        self._dont_show.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        body.addWidget(self._dont_show)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        close_btn = _dialog_glue_button("Close", card, primary=True)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
        )

    def dismissed_permanently(self) -> bool:
        return self._dont_show.isChecked()


def mpq_patch_warning_dialog(parent: QWidget | None) -> bool:
    """Blocking HD/MPQ patch warning. Returns True if user checked Don't show again."""
    from ichalaunch.mods.client_mod_hints import MPQ_PATCH_WARNING_TEXT

    dlg = MpqPatchWarningDialog(parent, "MPQ patch warning", MPQ_PATCH_WARNING_TEXT)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    return dlg.dismissed_permanently()


def dll_security_exclusion_dialog(
    parent: QWidget | None,
    game_folder: str,
) -> bool:
    """Blocking hint before first DLL-mod enable. Returns True if user checked Don't show again."""
    from ichalaunch.mods.client_mod_hints import dll_security_exclusion_message

    dlg = DllSecurityExclusionDialog(
        parent,
        "Add game folder to Windows Security",
        dll_security_exclusion_message(game_folder),
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    return dlg.dismissed_permanently()


def crash_reporting_opt_in_dialog(parent: QWidget | None) -> DialogResult:
    """One-shot crash-reporting opt-in.

    Returns:
      - ``DialogResult.Yes`` — Enable
      - ``DialogResult.No`` — Not now (still marks the prompt as shown)
      - ``DialogResult.Cancel`` — Don't show again (same persistence as Not now)
    """
    from ichalaunch.core.crash_report import (
        CRASH_REPORTING_OPT_IN_TEXT,
        CRASH_REPORTING_OPT_IN_TITLE,
    )

    return choice(
        parent,
        CRASH_REPORTING_OPT_IN_TITLE,
        CRASH_REPORTING_OPT_IN_TEXT,
        [
            ("Don't show again", DialogResult.Cancel),
            ("Not now", DialogResult.No),
            ("Enable", DialogResult.Yes),
        ],
        kind="question",
    )


class _PreviewFetchThread(QThread):
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, kind: str, url: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._url = url

    def run(self) -> None:
        try:
            if self._kind == "dll":
                from ichalaunch.mods.installer import preview_github_dll_mod

                self.ok.emit(preview_github_dll_mod(self._url))
            else:
                from ichalaunch.addons.github import preview_addon_repo

                self.ok.emit(preview_addon_repo(self._url))
        except Exception as exc:
            self.err.emit(str(exc) or exc.__class__.__name__)


class GitHubImportDialog(QDialog):
    """Paste a GitHub URL → preview auto-loads → confirm Add/Install."""

    _COMPACT_W = 480
    _COMPACT_H = 210
    _PREVIEW_W = 740
    _PREVIEW_H = 640
    _PREVIEW_MIN = (600, 520)

    def __init__(
        self,
        parent: QWidget | None,
        *,
        kind: str = "addon",
        title: str = "Add from GitHub",
        hint: str = "Paste a GitHub repository URL:",
        placeholder: str = "https://github.com/owner/repo",
        accept_text: str = "Add",
        view_only: bool = False,
        initial_url: str = "",
    ):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self._kind = kind
        self._result = DialogResult.Cancel
        self._info: dict | None = None
        self._cache_dir = ""
        self._fetch_gen = 0
        self._preview_mode = False
        self._view_only = bool(view_only)
        self._worker: _PreviewFetchThread | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self._start_fetch)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        body.addWidget(title_lbl)

        self._hint_lbl = QLabel(hint)
        self._hint_lbl.setObjectName("Muted")
        self._hint_lbl.setWordWrap(True)
        body.addWidget(self._hint_lbl)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(placeholder)
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(self._on_url_changed)
        body.addWidget(self.url_edit)

        self.status_lbl = QLabel("Paste a GitHub link to load the preview.")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(True)
        body.addWidget(self.status_lbl)

        self.meta_host = QWidget()
        self.meta_host.setObjectName("GitHubPreviewMeta")
        self.meta_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.meta_l = QVBoxLayout(self.meta_host)
        self.meta_l.setContentsMargins(12, 10, 12, 10)
        self.meta_l.setSpacing(6)
        self.meta_host.setVisible(False)
        body.addWidget(self.meta_host)

        self.browser = QTextBrowser()
        self.browser.setObjectName("ThemedPreviewBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        self.browser.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.browser.setVisible(False)
        body.addWidget(self.browser, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel_btn = _dialog_glue_button("Cancel", card, primary=False)
        cancel_btn.clicked.connect(lambda: self._finish(DialogResult.Cancel))
        cancel_btn.setVisible(not self._view_only)
        self.accept_btn = _dialog_glue_button(accept_text, card, primary=True)
        self.accept_btn.setEnabled(bool(self._view_only))
        self.accept_btn.clicked.connect(lambda: self._finish(DialogResult.Yes))
        self._default_accept = accept_text
        row.addWidget(cancel_btn)
        row.addWidget(self.accept_btn)
        body.addLayout(row)

        root.addWidget(card)
        if initial_url.strip():
            self.url_edit.blockSignals(True)
            self.url_edit.setText(initial_url.strip())
            self.url_edit.blockSignals(False)
        self._enter_compact()
        if self._view_only and initial_url.strip():
            QTimer.singleShot(0, self._start_fetch)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
            "QWidget#GitHubPreviewMeta {"
            "  background-color: rgba(120, 100, 150, 0.10);"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-radius: 8px;"
            "}"
            "QLabel#PreviewRepoName {"
            "  color: #F1C22D;"
            "  font-size: 15px;"
            "  font-weight: 700;"
            "}"
            "QLabel#PreviewDesc {"
            "  color: #e6e0ee;"
            "  font-size: 13px;"
            "}"
            "QLabel#PreviewMetaLine {"
            "  color: #a89bb0;"
            "  font-size: 12px;"
            "}"
            "QLabel#PreviewPill {"
            "  color: #e6e0ee;"
            "  background-color: rgba(74, 47, 122, 0.45);"
            "  border: 1px solid rgba(241, 194, 45, 0.35);"
            "  border-radius: 6px;"
            "  padding: 4px 8px;"
            "  font-size: 12px;"
            "}"
            "QLabel#PreviewWarn {"
            "  color: #F1C22D;"
            "  font-size: 12px;"
            "}"
            "QTextBrowser#ThemedPreviewBrowser {"
            "  background-color: #181412;"
            "  color: #e6e0ee;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-radius: 8px;"
            "  padding: 10px;"
            "  selection-background-color: #4a2f7a;"
            "  selection-color: #ffffff;"
            "}"
        )

    def _clear_meta(self) -> None:
        while self.meta_l.count():
            item = self.meta_l.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    child = lay.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def _set_url_bar_visible(self, visible: bool) -> None:
        self._hint_lbl.setVisible(visible)
        self.url_edit.setVisible(visible)

    def _enter_compact(self) -> None:
        """Fit title / hint / URL / status / buttons only (no preview chrome)."""
        self._preview_mode = False
        self._set_url_bar_visible(not self._view_only)
        self.setMinimumSize(420, 160)
        self.setMaximumHeight(280)
        self.resize(self._COMPACT_W, self._COMPACT_H)
        self.adjustSize()
        hint = self.sizeHint()
        self.resize(
            max(self._COMPACT_W, hint.width()),
            max(self._COMPACT_H, min(hint.height(), 280)),
        )

    def _enter_preview(self) -> None:
        """Grow for meta + README; hide the URL add bar to free vertical space."""
        self._preview_mode = True
        self._set_url_bar_visible(False)
        self.setMaximumHeight(16777215)
        self.setMinimumSize(*self._PREVIEW_MIN)
        self.resize(self._PREVIEW_W, self._PREVIEW_H)

    def _on_url_changed(self, _text: str = "") -> None:
        self._debounce.start()

    def _start_fetch(self) -> None:
        from ichalaunch.addons.github import cleanup_readme_cache, parse_github_url
        from ichalaunch.addons.gitlab import parse_gitlab_url

        raw = self.url_edit.text().strip()
        if not raw:
            self._reset_preview("Paste a GitHub or GitLab link to load the preview.")
            return
        if self._kind != "dll" and parse_gitlab_url(raw):
            pass
        elif not parse_github_url(raw):
            if self._kind == "dll":
                self._reset_preview("Enter a valid GitHub repository URL (github.com/owner/repo).")
            else:
                self._reset_preview(
                    "Enter a valid GitHub or GitLab repository URL "
                    "(github.com/owner/repo or gitlab.com/owner/repo)."
                )
            return

        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        self._info = None
        if not self._view_only:
            self.accept_btn.setEnabled(False)
        self.status_lbl.setText("Loading preview…")
        self.meta_host.setVisible(False)
        self.browser.setVisible(False)
        self._clear_meta()
        # Keep compact + URL bar while loading so the dialog doesn't sit empty-tall.
        if self._preview_mode:
            self._enter_compact()

        self._fetch_gen += 1
        gen = self._fetch_gen
        if self._worker is not None and self._worker.isRunning():
            # Leave old worker; ignore its result via gen check
            pass
        worker = _PreviewFetchThread(self._kind, raw, self)
        self._worker = worker

        def on_ok(info: object) -> None:
            if gen != self._fetch_gen:
                if isinstance(info, dict):
                    cleanup_readme_cache(info.get("readme_cache_dir"))
                return
            if not isinstance(info, dict):
                self.status_lbl.setText("Preview failed.")
                return
            self._apply_info(info)

        def on_err(msg: str) -> None:
            if gen != self._fetch_gen:
                return
            self.status_lbl.setText(msg or "Preview failed.")
            self.accept_btn.setEnabled(self._view_only)
            if self._preview_mode:
                self._enter_compact()

        worker.ok.connect(on_ok)
        worker.err.connect(on_err)
        worker.start()

    def _reset_preview(self, status: str) -> None:
        from ichalaunch.addons.github import cleanup_readme_cache

        self._fetch_gen += 1
        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        self._info = None
        self.status_lbl.setText(status)
        self.meta_host.setVisible(False)
        self.browser.setVisible(False)
        self._clear_meta()
        self.accept_btn.setEnabled(self._view_only)
        self.accept_btn.setText(self._default_accept)
        self._enter_compact()

    def _apply_info(self, info: dict) -> None:
        self._info = info
        self._cache_dir = str(info.get("readme_cache_dir") or "")
        self._clear_meta()

        if info.get("kind") == "dll":
            name = QLabel(str(info.get("name") or "DLL"))
            name.setObjectName("PreviewRepoName")
            name.setWordWrap(True)
            self.meta_l.addWidget(name)

            desc = QLabel(str(info.get("description") or ""))
            desc.setObjectName("PreviewDesc")
            desc.setWordWrap(True)
            self.meta_l.addWidget(desc)

            meta = QLabel(
                f"{info.get('url')}\n"
                f"Category · {info.get('category') or 'Custom'}"
                + (f"  ·  Asset · {info.get('asset')}" if info.get("asset") else "")
            )
            meta.setObjectName("PreviewMetaLine")
            meta.setWordWrap(True)
            meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.meta_l.addWidget(meta)

            pills = QHBoxLayout()
            pills.setSpacing(8)
            if info.get("matched_existing"):
                pill = QLabel("Catalog match — will enable")
                pill.setObjectName("PreviewPill")
                pills.addWidget(pill)
            else:
                pill = QLabel("New Custom entry")
                pill.setObjectName("PreviewPill")
                pills.addWidget(pill)
            if info.get("has_companion_addon"):
                pill2 = QLabel("Companion addon included")
                pill2.setObjectName("PreviewPill")
                pills.addWidget(pill2)
            pills.addStretch(1)
            self.meta_l.addLayout(pills)

            self.accept_btn.setText("Enable" if info.get("matched_existing") else "Install")
        else:
            name = QLabel(str(info.get("full_name") or info.get("url") or "Repository"))
            name.setObjectName("PreviewRepoName")
            name.setWordWrap(True)
            self.meta_l.addWidget(name)

            desc = QLabel(str(info.get("description") or ""))
            desc.setObjectName("PreviewDesc")
            desc.setWordWrap(True)
            self.meta_l.addWidget(desc)

            stats = QHBoxLayout()
            stats.setSpacing(14)
            stars = QLabel(f"★  {info.get('stars', 0)}")
            stars.setObjectName("PreviewMetaLine")
            stats.addWidget(stars)
            tag = str(info.get("tag") or "").strip()
            if tag:
                branch = QLabel(
                    f"Tag  {tag}  @  {info.get('commit_sha') or '?'}"
                    + (f"  ·  {info.get('commit_date')}" if info.get("commit_date") else "")
                )
            else:
                branch = QLabel(
                    f"Branch  {info.get('default_branch') or '?'}  @  "
                    f"{info.get('commit_sha') or '?'}"
                    + (f"  ·  {info.get('commit_date')}" if info.get("commit_date") else "")
                )
            branch.setObjectName("PreviewMetaLine")
            branch.setWordWrap(True)
            stats.addWidget(branch, 1)
            self.meta_l.addLayout(stats)

            if info.get("commit_message"):
                latest = QLabel(f"Latest  ·  {info.get('commit_message')}")
                latest.setObjectName("PreviewMetaLine")
                latest.setWordWrap(True)
                self.meta_l.addWidget(latest)

            url_lbl = QLabel(str(info.get("url") or ""))
            url_lbl.setObjectName("PreviewMetaLine")
            url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.meta_l.addWidget(url_lbl)

            pills = QHBoxLayout()
            pills.setSpacing(8)
            if info.get("catalog_name"):
                pill = QLabel(f"Catalog · {info.get('catalog_name')}")
                pill.setObjectName("PreviewPill")
                pills.addWidget(pill)
            if info.get("already_installed"):
                warn = QLabel(
                    f"Already installed as {info.get('installed_folder') or '(tracked)'}"
                )
                warn.setObjectName("PreviewWarn")
                pills.addWidget(warn)
            pills.addStretch(1)
            self.meta_l.addLayout(pills)

            self.accept_btn.setText(self._default_accept)

        self.meta_host.setVisible(True)
        md = str(info.get("readme_markdown") or "").strip()
        base = str(info.get("readme_base_url") or "")
        if base:
            self.browser.document().setBaseUrl(QUrl(base))
        if md:
            self.browser.setMarkdown(md)
        else:
            self.browser.setPlainText("(No README found for this repository.)")
        self.browser.setVisible(True)
        self.status_lbl.setText(
            "Preview ready." if self._view_only else "Preview ready — confirm to continue."
        )
        self.accept_btn.setEnabled(True)
        self._enter_preview()

    def _open_link(self, url: QUrl) -> None:
        if url.isValid():
            QDesktopServices.openUrl(url)

    def selected_url(self) -> str:
        if self._info and self._info.get("url"):
            return str(self._info["url"])
        return self.url_edit.text().strip()

    def preview_info(self) -> dict | None:
        return self._info

    def _finish(self, result: DialogResult) -> None:
        self._result = result
        if result in (DialogResult.Yes, DialogResult.Ok):
            self.accept()
        else:
            self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        from ichalaunch.addons.github import cleanup_readme_cache

        self._fetch_gen += 1
        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        if self._info and self._info.get("readme_cache_dir"):
            cleanup_readme_cache(self._info.get("readme_cache_dir"))
        super().closeEvent(event)

    @property
    def result_value(self) -> DialogResult:
        return self._result


class ThemedPreviewDialog(QDialog):
    """Confirm dialog with structured summary + scrollable README (legacy entry)."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        summary: str,
        *,
        info: dict | None = None,
        readme_markdown: str = "",
        readme_base_url: str = "",
        readme_cache_dir: str = "",
        accept_text: str = "Add",
        cancel_text: str = "Cancel",
    ):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(560, 480)
        self.resize(720, 620)
        self._result = DialogResult.Cancel
        self._cache_dir = readme_cache_dir

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        # Prefer structured meta from info; fall back to plain summary text.
        if info:
            meta = QWidget()
            meta.setObjectName("GitHubPreviewMeta")
            meta.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            ml = QVBoxLayout(meta)
            ml.setContentsMargins(12, 10, 12, 10)
            ml.setSpacing(6)
            if info.get("kind") == "dll":
                n = QLabel(str(info.get("name") or ""))
                n.setObjectName("PreviewRepoName")
                ml.addWidget(n)
            else:
                n = QLabel(str(info.get("full_name") or ""))
                n.setObjectName("PreviewRepoName")
                ml.addWidget(n)
            d = QLabel(str(info.get("description") or summary))
            d.setObjectName("PreviewDesc")
            d.setWordWrap(True)
            ml.addWidget(d)
            body.addWidget(meta)
        else:
            summary_lbl = QLabel(summary)
            summary_lbl.setObjectName("ThemedDialogBody")
            summary_lbl.setWordWrap(True)
            summary_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.addWidget(summary_lbl)

        browser = QTextBrowser()
        browser.setObjectName("ThemedPreviewBrowser")
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(self._open_link)
        browser.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if readme_base_url:
            browser.document().setBaseUrl(QUrl(readme_base_url))
        md = (readme_markdown or "").strip()
        if md:
            browser.setMarkdown(md)
        else:
            browser.setPlainText("(No README found for this repository.)")
        body.addWidget(browser, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel_btn = _dialog_glue_button(cancel_text, card, primary=False)
        cancel_btn.clicked.connect(lambda: self._finish(DialogResult.Cancel))
        ok_btn = _dialog_glue_button(accept_text, card, primary=True)
        ok_btn.clicked.connect(lambda: self._finish(DialogResult.Yes))
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
            "QWidget#GitHubPreviewMeta {"
            "  background-color: rgba(120, 100, 150, 0.10);"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-radius: 8px;"
            "}"
            "QLabel#PreviewRepoName {"
            "  color: #F1C22D; font-size: 15px; font-weight: 700;"
            "}"
            "QLabel#PreviewDesc { color: #e6e0ee; font-size: 13px; }"
            "QTextBrowser#ThemedPreviewBrowser {"
            "  background-color: #181412;"
            "  color: #e6e0ee;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-radius: 8px;"
            "  padding: 10px;"
            "}"
        )

    def _open_link(self, url: QUrl) -> None:
        if url.isValid():
            QDesktopServices.openUrl(url)

    def _finish(self, result: DialogResult) -> None:
        from ichalaunch.addons.github import cleanup_readme_cache

        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        self._result = result
        if result in (DialogResult.Yes, DialogResult.Ok):
            self.accept()
        else:
            self.reject()

    @property
    def result_value(self) -> DialogResult:
        return self._result


class ThemedInputDialog(QDialog):
    """Frameless prompt with a single line edit (e.g. GitHub URL)."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        label: str,
        *,
        placeholder: str = "",
        accept_text: str = "OK",
        cancel_text: str = "Cancel",
    ):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ThemedDialogTitle")
        body.addWidget(title_lbl)

        hint = QLabel(label)
        hint.setObjectName("ThemedDialogBody")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.returnPressed.connect(self.accept)
        body.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel_btn = _dialog_glue_button(cancel_text, card, primary=False)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = _dialog_glue_button(accept_text, card, primary=True)
        ok_btn.clicked.connect(self.accept)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        body.addLayout(row)

        root.addWidget(card)

    def text_value(self) -> str:
        return self.edit.text().strip()


class GitHubTokenPromptDialog(QDialog):
    """Prompt for a GitHub PAT before manual addon update checks."""

    def __init__(self, parent: QWidget | None):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(580)
        self._saved_token: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        title_lbl = QLabel("GitHub token required")
        title_lbl.setObjectName("ThemedDialogTitle")
        body.addWidget(title_lbl)

        hint = QLabel(
            "Addon update checks use the GitHub API. Create a personal access token "
            "at github.com/settings/tokens with the public_repo scope, then paste it "
            "below. You can also add a token later in Settings → GitHub API."
        )
        hint.setObjectName("ThemedDialogBody")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("ghp_…")
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.returnPressed.connect(self._save_and_accept)
        body.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        later_btn = _dialog_glue_button("Later", card, primary=False)
        later_btn.clicked.connect(self.reject)
        save_btn = _dialog_glue_button("Save & Check", card, primary=True)
        save_btn.clicked.connect(self._save_and_accept)
        row.addWidget(later_btn)
        row.addWidget(save_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
        )

    def _save_and_accept(self) -> None:
        token = self.edit.text().strip()
        if not token:
            return
        self._saved_token = token
        self.accept()

    def saved_token(self) -> str | None:
        return self._saved_token


def github_token_prompt_dialog(parent: QWidget | None) -> str | None:
    """Blocking PAT prompt for manual addon checks.

    Returns the token string if saved via Save & Check, or None if dismissed.
    """
    dlg = GitHubTokenPromptDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    token = dlg.saved_token()
    if not token:
        return None
    from ichalaunch.config.settings import settings

    settings.set("github_token", token)
    try:
        from ichalaunch.addons.github import clear_addon_scan_queue

        clear_addon_scan_queue()
    except Exception:  # noqa: BLE001
        pass
    return token


class _AddonBrowseFetchThread(QThread):
  ok = Signal(object, object)
  err = Signal(str)

  def __init__(
    self,
    owner: str,
    repo: str,
    parent: QWidget | None = None,
    *,
    host: str = "github",
  ):
    super().__init__(parent)
    self._owner = owner
    self._repo = repo
    self._host = (host or "github").strip().lower() or "github"

  def run(self) -> None:
    try:
      if self._host == "gitlab":
        from ichalaunch.addons.github import fork_entry_from_repo_url
        from ichalaunch.addons.gitlab import gitlab_browse_url, list_gitlab_repo_tags

        forks = [fork_entry_from_repo_url(gitlab_browse_url(self._owner, self._repo))]
        versions = list_gitlab_repo_tags(self._owner, self._repo)
        self.ok.emit(forks, versions)
        return
      from ichalaunch.addons.github import list_repo_forks, list_repo_versions

      forks = list_repo_forks(self._owner, self._repo)
      versions = list_repo_versions(self._owner, self._repo)
      self.ok.emit(forks, versions)
    except Exception as exc:  # noqa: BLE001
      self.err.emit(str(exc) or exc.__class__.__name__)


class AddonInstallPickerDialog(QDialog):
  """Themed fork + version picker with README preview before catalog addon install."""

  _PREVIEW_MIN = (560, 480)

  def __init__(self, parent: QWidget | None, entry: dict):
    from ichalaunch.addons.github import (
      catalog_fork_entries,
      catalog_pin_tag,
      parse_entry_owner_repo,
    )
    from ichalaunch.addons.gitlab import parse_entry_gitlab
    from ichalaunch.ui.widgets.common import addon_fork_label, addon_version_label, fork_combo_label
    from ichalaunch.ui.widgets.glue_combo import GlueComboBox

    super().__init__(parent)
    self.setObjectName("ThemedDialog")
    self.setWindowFlags(_themed_dialog_flags())
    self.setModal(True)
    self.setMinimumSize(*self._PREVIEW_MIN)
    self.resize(680, 560)
    self._entry = dict(entry)
    self._result: dict | None = None
    self._worker: _AddonBrowseFetchThread | None = None
    self._preview_worker: _PreviewFetchThread | None = None
    self._preview_gen = 0
    self._cache_dir = ""
    self._browse_owner = ""
    self._browse_repo = ""
    self._browse_fetch_gen = 0
    self._preview_url_loaded = ""
    self._preview_url_loading = ""
    self._preview_pending = True
    self._forks_fetch_done = False
    self._open_git_btn = None
    self._title_lbl = None

    name = str(entry.get("name") or entry.get("folder") or "addon")
    version_text = addon_version_label(entry)

    root = QVBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)

    card = QWidget()
    card.setObjectName("ThemedDialogCard")
    body = QVBoxLayout(card)
    body.setContentsMargins(22, 18, 22, 18)
    body.setSpacing(10)

    self._open_git_btn = _addon_open_git_button(
      card,
      self,
      entry.get("repo"),
      entry.get("url"),
      entry.get("repository"),
    )
    title_row, self._title_lbl = _addon_dialog_title_row(
      f"Install {name}",
      self._open_git_btn,
    )
    body.addLayout(title_row)

    hint = QLabel(
      "Choose fork and release, or install now with the defaults below. "
      "Forks, versions, and the preview load in the background."
    )
    hint.setObjectName("ThemedDialogBody")
    hint.setWordWrap(True)
    body.addWidget(hint)

    self.fork_combo = GlueComboBox(card, min_width=GLUE_BTN_W)
    catalog_forks = catalog_fork_entries(entry)
    for fe in catalog_forks:
      label = fork_combo_label(fe)
      self.fork_combo.addItem(label, fe)
    current_repo = str(entry.get("repo") or "").strip()
    for i in range(self.fork_combo.count()):
      fd = self.fork_combo.itemData(i)
      if isinstance(fd, dict) and str(fd.get("repo") or "") == current_repo:
        self.fork_combo.setCurrentIndex(i)
        break
    pin = str(entry.get("pin_release") or catalog_pin_tag(entry) or "").strip()
    ver_label = version_text or (f"v{pin}" if pin else "Latest (branch tip)")
    self.version_combo = GlueComboBox(card, min_width=GLUE_BTN_W)
    self.version_combo.addItem(ver_label, pin)
    self.fork_combo.currentIndexChanged.connect(self._on_fork_changed)
    self.version_combo.currentIndexChanged.connect(self._on_version_changed)
    body.addLayout(
      _addon_fork_version_row(
        card,
        fork_combo=self.fork_combo,
        version_combo=self.version_combo,
      )
    )
    self._sync_browse_combos()
    self.status_lbl = QLabel("")
    self.status_lbl.setObjectName("Muted")
    self.status_lbl.setWordWrap(True)
    body.addWidget(self.status_lbl)

    self.browser = QTextBrowser()
    self.browser.setObjectName("ThemedPreviewBrowser")
    self.browser.setOpenExternalLinks(False)
    self.browser.setOpenLinks(False)
    self.browser.anchorClicked.connect(self._open_preview_link)
    body.addWidget(self.browser, 1)

    row = QHBoxLayout()
    row.setSpacing(10)
    row.addStretch(1)
    cancel_btn = _dialog_glue_button("Cancel", card, primary=False)
    cancel_btn.clicked.connect(self.reject)
    self.install_btn = _dialog_glue_button("Install", card, primary=True)
    self.install_btn.clicked.connect(self._accept_install)
    row.addWidget(cancel_btn)
    row.addWidget(self.install_btn)
    body.addLayout(row)

    root.addWidget(card)
    self.setStyleSheet(
      "QDialog#ThemedDialog { background: transparent; }"
      "QWidget#ThemedDialogCard {"
      "  background-color: #100d0c;"
      "  border: 1px solid rgba(150, 131, 158, 0.22);"
      "  border-top: 3px solid #F1C22D;"
      "  border-radius: 10px;"
      "}"
      "QTextBrowser#ThemedPreviewBrowser {"
      "  background-color: #181412;"
      "  color: #e6e0ee;"
      "  border: 1px solid rgba(150, 131, 158, 0.22);"
      "  border-radius: 8px;"
      "  padding: 10px;"
      "}"
    )

    pair = parse_entry_owner_repo(entry)
    gl = parse_entry_gitlab(entry)
    can_install = bool(self._preview_url())
    self.install_btn.setEnabled(can_install)
    if gl:
      self._browse_owner, self._browse_repo = gl.owner, gl.repo
      self._browse_fetch_gen += 1
      fetch_gen = self._browse_fetch_gen
      self._worker = _AddonBrowseFetchThread(gl.owner, gl.repo, self, host="gitlab")
      self._worker.ok.connect(
        lambda forks, versions, g=fetch_gen: self._on_fetch_ok(forks, versions, g)
      )
      self._worker.err.connect(lambda msg, g=fetch_gen: self._on_fetch_err(msg, g))
      self._worker.start()
      self.status_lbl.setText("Loading versions from GitLab…")
    elif pair:
      owner, repo = pair
      self._browse_owner, self._browse_repo = owner, repo
      self._browse_fetch_gen += 1
      fetch_gen = self._browse_fetch_gen
      self._worker = _AddonBrowseFetchThread(owner, repo, self)
      self._worker.ok.connect(
        lambda forks, versions, g=fetch_gen: self._on_fetch_ok(forks, versions, g)
      )
      self._worker.err.connect(lambda msg, g=fetch_gen: self._on_fetch_err(msg, g))
      self._worker.start()
      self.status_lbl.setText("Loading forks and versions from GitHub…")
    else:
      self._forks_fetch_done = True
      self.status_lbl.setText("Could not resolve a GitHub or GitLab repository for this addon.")

    QTimer.singleShot(0, self._load_preview)

  def _set_browse_combos_enabled(
    self,
    enabled: bool,
    *,
    loading: bool = False,
    tip: str | None = None,
  ) -> None:
    for combo in (self.fork_combo, self.version_combo):
      # Only force-close when locking; enabling must not dismiss an open list.
      if not enabled:
        try:
          combo.hidePopup()
        except RuntimeError:
          pass
      combo.setEnabled(bool(enabled))
    if enabled:
      self.fork_combo.setToolTip("")
      self.version_combo.setToolTip("")
    elif tip:
      self.fork_combo.setToolTip(tip)
      self.version_combo.setToolTip(tip)
    elif loading:
      self.fork_combo.setToolTip(_LOADING_FORKS_TIP)
      self.version_combo.setToolTip(_LOADING_VERSIONS_TIP)

  def _sync_browse_combos(self) -> None:
    """Keep fork/version locked until preview finishes (and forks finish if loading)."""
    if self._preview_pending:
      self._set_browse_combos_enabled(False, tip=_LOADING_PREVIEW_TIP)
      return
    if not self._forks_fetch_done or (self._worker and self._worker.isRunning()):
      self._set_browse_combos_enabled(False, loading=True)
      return
    self._set_browse_combos_enabled(True)

  def _open_preview_link(self, url: QUrl) -> None:
    QDesktopServices.openUrl(url)

  def _version_tag(self) -> str:
    return str(self.version_combo.currentData() or "").strip()

  def _preview_url(self) -> str:
    from ichalaunch.addons.github import addon_browse_url, addon_install_url_for_choice, fork_git_host
    from ichalaunch.ui.widgets.common import git_repo_browse_url

    fork = self._current_fork_data()
    tag = self._version_tag()
    install = addon_install_url_for_choice(fork, tag or None)
    if install:
      return install
    owner = str(fork.get("owner") or self._browse_owner or "").strip()
    repo = str(fork.get("repo_name") or self._browse_repo or "").strip()
    if owner and repo:
      return addon_browse_url(owner, repo, host=fork_git_host(fork))
    return git_repo_browse_url(
      self._entry.get("repo"),
      self._entry.get("url"),
      self._entry.get("repository"),
    ) or ""

  def _load_preview(self) -> None:
    from ichalaunch.addons.github import cleanup_readme_cache

    url = self._preview_url()
    if not url:
      self.browser.clear()
      self._preview_url_loaded = ""
      self._preview_url_loading = ""
      self._preview_pending = False
      self._sync_browse_combos()
      return
    if url == self._preview_url_loaded and self.browser.toPlainText().strip():
      self._preview_pending = False
      self._sync_browse_combos()
      return
    if url == self._preview_url_loading:
      return
    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""
    self._preview_url_loading = url
    self._preview_pending = True
    self._sync_browse_combos()
    if not (self._worker and self._worker.isRunning()):
      self.status_lbl.setText("Loading preview…")
    self.browser.clear()
    self._preview_gen += 1
    gen = self._preview_gen
    worker = _PreviewFetchThread("addon", url, self)
    self._preview_worker = worker

    def on_ok(info: object) -> None:
      if gen != self._preview_gen:
        if isinstance(info, dict):
          cleanup_readme_cache(info.get("readme_cache_dir"))
        return
      self._preview_url_loading = ""
      self._preview_pending = False
      if not isinstance(info, dict):
        if not (self._worker and self._worker.isRunning()):
          self.status_lbl.setText("Preview failed.")
        self._sync_browse_combos()
        return
      self._cache_dir = str(info.get("readme_cache_dir") or "")
      self._preview_url_loaded = url
      md = str(info.get("readme_markdown") or "").strip()
      base = str(info.get("readme_base_url") or "")
      if base:
        self.browser.document().setBaseUrl(QUrl(base))
      if md:
        self.browser.setMarkdown(md)
      else:
        self.browser.setPlainText(str(info.get("description") or "No README found."))
      if not (self._worker and self._worker.isRunning()):
        self.status_lbl.setText("")
      self._sync_browse_combos()

    def on_err(msg: str) -> None:
      if gen != self._preview_gen:
        return
      if self._preview_url_loading == url:
        self._preview_url_loading = ""
      self._preview_pending = False
      if not (self._worker and self._worker.isRunning()):
        self.status_lbl.setText(msg or "Preview failed.")
      self._sync_browse_combos()

    worker.ok.connect(on_ok)
    worker.err.connect(on_err)
    worker.start()

  def _on_fork_changed(self, _index: int) -> None:
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or "").strip()
    repo = str(fork.get("repo_name") or "").strip()
    if owner and repo:
      self._browse_owner = owner
      self._browse_repo = repo
    self.install_btn.setEnabled(bool(self._preview_url()))
    self._load_preview()

  def _on_version_changed(self, _index: int) -> None:
    self.install_btn.setEnabled(bool(self._preview_url()))
    self._load_preview()

  def _on_fetch_ok(self, forks: object, versions: object, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    before_url = self._preview_url()
    fork_list = forks if isinstance(forks, list) else []
    version_list = versions if isinstance(versions, list) else []
    current = self._current_fork_data()
    current_repo = str(current.get("repo") or "").strip().lower()
    self.fork_combo.hidePopup()
    self.version_combo.hidePopup()
    self.fork_combo.blockSignals(True)
    self.fork_combo.clear()
    seen: set[str] = set()
    selected = 0
    for fe in fork_list:
      if not isinstance(fe, dict):
        continue
      repo = str(fe.get("repo") or "").strip()
      key = repo.lower()
      if not key or key in seen:
        continue
      seen.add(key)
      from ichalaunch.ui.widgets.common import fork_combo_label

      label = fork_combo_label(fe)
      idx = self.fork_combo.count()
      self.fork_combo.addItem(label, fe)
      if key == current_repo:
        selected = idx
    if self.fork_combo.count() == 0 and current_repo:
      from ichalaunch.ui.widgets.common import fork_combo_label

      self.fork_combo.addItem(
        fork_combo_label(current),
        current,
      )
    else:
      self.fork_combo.setCurrentIndex(min(selected, max(0, self.fork_combo.count() - 1)))
    self.fork_combo.blockSignals(False)

    pin = self._version_tag()
    if not pin:
      pin = str(self._entry.get("pin_release") or "").strip()
    self.version_combo.blockSignals(True)
    self.version_combo.clear()
    self.version_combo.addItem("Latest (branch tip)", "")
    selected_v = 0
    for tag in version_list:
      t = str(tag or "").strip()
      if not t:
        continue
      label = t if t.lower().startswith("v") else f"v{t}"
      idx = self.version_combo.count()
      self.version_combo.addItem(label, t)
      if pin and t.lower() == pin.lower():
        selected_v = idx
    if self.version_combo.count() == 1 and pin:
      label = pin if pin.lower().startswith("v") else f"v{pin}"
      self.version_combo.addItem(label, pin)
      selected_v = 1
    self.version_combo.setCurrentIndex(
      min(selected_v, max(0, self.version_combo.count() - 1))
    )
    self.version_combo.blockSignals(False)
    self._forks_fetch_done = True
    self._sync_browse_combos()
    self.status_lbl.setText("")
    self.install_btn.setEnabled(bool(self._preview_url()))
    after_url = self._preview_url()
    if after_url != before_url:
      self._load_preview()

  def _on_fetch_err(self, message: str, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._forks_fetch_done = True
    self._sync_browse_combos()
    if not self.browser.toPlainText().strip():
      self.status_lbl.setText(message or "GitHub request failed.")
    self.install_btn.setEnabled(bool(self._preview_url()))

  def _current_fork_data(self) -> dict:
    idx = self.fork_combo.currentIndex()
    if idx < 0:
      return {}
    data = self.fork_combo.itemData(idx)
    return dict(data) if isinstance(data, dict) else {}

  def _build_result_entry(self) -> dict:
    from ichalaunch.addons.github import addon_install_url_for_choice

    fork = self._current_fork_data()
    tag = self._version_tag()
    url = addon_install_url_for_choice(fork, tag or None)
    if not url:
      return {}
    out = dict(self._entry)
    out["repo"] = url
    if tag:
      out["pin_release"] = tag
      out["tag"] = tag
    else:
      out.pop("pin_release", None)
      out.pop("tag", None)
    if fork.get("folder"):
      out["folder"] = fork.get("folder")
    owner = str(fork.get("owner") or "").strip()
    repo_name = str(fork.get("repo_name") or "").strip()
    if owner and repo_name:
      out["repository"] = f"{owner}/{repo_name}"
    return out

  def _cleanup_preview_cache(self) -> None:
    from ichalaunch.addons.github import cleanup_readme_cache

    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""

  def _queue_selected_fork_review(self) -> None:
    from ichalaunch.addons.submit import queue_selected_fork_if_uncatalogued

    fork = self._current_fork_data()
    queue_selected_fork_if_uncatalogued(
      fork,
      name=str(self._entry.get("name") or ""),
      folder=str(self._entry.get("folder") or ""),
      category=str(self._entry.get("category") or "General"),
    )

  def _accept_install(self) -> None:
    out = self._build_result_entry()
    if not out:
      return
    self._queue_selected_fork_review()
    self._result = out
    self.accept()

  def accept(self) -> None:
    self._browse_fetch_gen += 1
    self._preview_gen += 1
    self._set_browse_combos_enabled(False)
    self._cleanup_preview_cache()
    super().accept()

  def reject(self) -> None:
    self._browse_fetch_gen += 1
    self._preview_gen += 1
    self._set_browse_combos_enabled(False)
    self._cleanup_preview_cache()
    super().reject()

  def closeEvent(self, event) -> None:  # noqa: N802
    self._browse_fetch_gen += 1
    self._preview_gen += 1
    self._set_browse_combos_enabled(False)
    self._cleanup_preview_cache()
    super().closeEvent(event)

  def result_data(self) -> dict | None:
    return self._result


def addon_install_picker_dialog(parent: QWidget | None, entry: dict) -> dict | None:
  """Blocking install picker. Returns updated entry dict or None if cancelled."""
  dlg = AddonInstallPickerDialog(parent, entry)
  if dlg.exec() != QDialog.DialogCode.Accepted:
    return None
  return dlg.result_data()


class AddonSettingsDialog(QDialog):
  """Installed-addon settings: fork, version, and README preview."""

  _PREVIEW_MIN = (560, 480)

  def __init__(
    self,
    parent: QWidget | None,
    entry: dict,
    *,
    meta: dict | None = None,
  ):
    from ichalaunch.addons.github import (
      NO_TOKEN_FORK_TIP,
      catalog_fork_entries,
      catalog_pin_tag,
      has_github_token,
      parse_entry_owner_repo,
    )
    from ichalaunch.addons.gitlab import parse_entry_gitlab
    from ichalaunch.ui.widgets.common import (
      addon_fork_label,
      fork_combo_label,
      addon_version_label,
    )
    from ichalaunch.ui.widgets.glue_combo import GlueComboBox

    super().__init__(parent)
    self.setObjectName("ThemedDialog")
    self.setWindowFlags(_themed_dialog_flags())
    self.setModal(True)
    self.setMinimumSize(*self._PREVIEW_MIN)
    self.resize(680, 560)
    self._entry = dict(entry)
    self._meta = dict(meta) if isinstance(meta, dict) else {}
    self._result: dict | None = None
    self._browse_worker: _AddonBrowseFetchThread | None = None
    self._preview_worker: _PreviewFetchThread | None = None
    self._preview_gen = 0
    self._cache_dir = ""
    self._forks_loaded = False
    self._versions_loaded = False
    self._token_ok = bool(has_github_token())
    self._browse_fetch_gen = 0
    self._fork_fetch_pending = False
    self._version_fetch_pending = False
    self._preview_pending = False
    self._open_git_btn = None
    self._title_lbl = None
    self._reinstall_btn = None
    self._never_update_cb = None
    self._catalog_never_locked = False
    self._baseline_repo = ""
    self._baseline_tag = ""
    self._git_host = "github"

    name = str(entry.get("name") or entry.get("folder") or "addon")
    root = QVBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)

    card = QWidget()
    card.setObjectName("ThemedDialogCard")
    body = QVBoxLayout(card)
    body.setContentsMargins(22, 18, 22, 18)
    body.setSpacing(10)

    self._open_git_btn = _addon_open_git_button(
      card,
      self,
      entry.get("repo"),
      entry.get("url"),
      entry.get("repository"),
    )
    title_row, self._title_lbl = _addon_dialog_title_row(name, self._open_git_btn)
    body.addLayout(title_row)

    fork_text = addon_fork_label(entry)
    version_text = addon_version_label(entry, self._meta)
    self._fork_combo = None
    self._version_combo = None

    if self._token_ok:
      from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_W

      self._fork_combo = GlueComboBox(card, min_width=GLUE_BTN_W)
      for fe in catalog_fork_entries(entry):
        label = fork_combo_label(fe)
        self._fork_combo.addItem(label, fe)
      current_repo = str(entry.get("repo") or "").strip()
      for i in range(self._fork_combo.count()):
        fd = self._fork_combo.itemData(i)
        if isinstance(fd, dict) and str(fd.get("repo") or "") == current_repo:
          self._fork_combo.setCurrentIndex(i)
          break
      self._fork_combo.currentIndexChanged.connect(self._on_fork_changed)

      pin = str(
        self._meta.get("tag")
        or self._meta.get("version")
        or entry.get("pin_release")
        or entry.get("tag")
        or catalog_pin_tag(entry)
        or ""
      ).strip()
      ver_label = version_text or (f"v{pin}" if pin else "Latest (branch tip)")
      self._version_combo = GlueComboBox(card, min_width=GLUE_BTN_W)
      self._version_combo.addItem(ver_label, pin)
      self._version_combo.currentIndexChanged.connect(self._on_version_changed)
      self._version_combo.popupShown.connect(self._lazy_fetch_versions)
      body.addLayout(
        _addon_fork_version_row(
          card,
          fork_combo=self._fork_combo,
          version_combo=self._version_combo,
        )
      )
      self._preview_pending = True
      self._sync_combo_interactivity()
    else:
      meta_row = QHBoxLayout()
      meta_row.setSpacing(10)
      meta_line = " · ".join(x for x in (fork_text, version_text) if x)
      if meta_line:
        static = QLabel(meta_line)
        static.setObjectName("Muted")
        static.setToolTip(NO_TOKEN_FORK_TIP)
        meta_row.addWidget(static, 0, Qt.AlignmentFlag.AlignVCenter)
      meta_row.addStretch(1)
      body.addLayout(meta_row)

    self.status_lbl = QLabel("")
    self.status_lbl.setObjectName("Muted")
    self.status_lbl.setWordWrap(True)
    body.addWidget(self.status_lbl)

    self.browser = QTextBrowser()
    self.browser.setObjectName("ThemedPreviewBrowser")
    self.browser.setOpenExternalLinks(False)
    self.browser.setOpenLinks(False)
    self.browser.anchorClicked.connect(self._open_preview_link)
    body.addWidget(self.browser, 1)

    row = QHBoxLayout()
    row.setSpacing(10)
    folder_key = str(entry.get("folder") or entry.get("name") or "").strip()
    self._never_update_cb = ThemeCheckBox("Never update", card)
    self._never_update_cb.setCursor(Qt.CursorShape.PointingHandCursor)
    self._never_update_cb.setMinimumHeight(28)
    self._never_update_cb.setToolTip(
      "Skip update checks and Update All for this addon. "
      "Clear by unchecking and Save, or via Reinstall."
    )
    from ichalaunch.addons.github import catalog_locks_updates
    from ichalaunch.config.settings import settings as _settings
    from ichalaunch.core.detect import resolve_catalog_entry

    # Lock only for true catalog pins (Bagshui updates:false / catalog pin_release).
    # Do NOT use addon_ignores_updates(entry, …): after Save the row entry often
    # carries user pin_release / never_update, which would permanently disable
    # this checkbox for normal addons.
    self._catalog_never_locked = False
    if folder_key:
      cat, kind = resolve_catalog_entry(folder_key, include_mods=False)
      if kind == "exact" and isinstance(cat, dict):
        self._catalog_never_locked = bool(catalog_locks_updates(cat))
      elif entry.get("updates") is False:
        self._catalog_never_locked = True
    elif entry.get("updates") is False:
      self._catalog_never_locked = True
    never_on = bool(self._meta.get("never_update")) or (
      bool(folder_key) and _settings.is_addon_never_update(folder_key)
    )
    if self._catalog_never_locked:
      never_on = True
      self._never_update_cb.setEnabled(False)
      self._never_update_cb.setToolTip(
        "This addon is pinned in the catalog (updates disabled)."
      )
    self._never_update_cb.setChecked(never_on)
    row.addWidget(self._never_update_cb, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    close_btn = _dialog_glue_button("Close", card, primary=False)
    close_btn.clicked.connect(self.reject)
    row.addWidget(close_btn)
    if self._token_ok:
      self._reinstall_btn = _dialog_glue_button("Reinstall", card, primary=False)
      self._reinstall_btn.setToolTip(
        "Replace the installed addon with the selected fork/version"
      )
      self._reinstall_btn.clicked.connect(self._accept_reinstall)
      row.addWidget(self._reinstall_btn)
    save_btn = _dialog_glue_button("Save", card, primary=True)
    save_btn.clicked.connect(self._accept_save)
    row.addWidget(save_btn)
    body.addLayout(row)

    root.addWidget(card)
    self.setStyleSheet(
      "QDialog#ThemedDialog { background: transparent; }"
      "QWidget#ThemedDialogCard {"
      "  background-color: #100d0c;"
      "  border: 1px solid rgba(150, 131, 158, 0.22);"
      "  border-top: 3px solid #F1C22D;"
      "  border-radius: 10px;"
      "}"
      "QTextBrowser#ThemedPreviewBrowser {"
      "  background-color: #181412;"
      "  color: #e6e0ee;"
      "  border: 1px solid rgba(150, 131, 158, 0.22);"
      "  border-radius: 8px;"
      "  padding: 10px;"
      "}"
    )

    self._browse_owner = ""
    self._browse_repo = ""
    self._git_host = "github"
    gl = parse_entry_gitlab(entry, self._meta)
    pair = parse_entry_owner_repo(entry, self._meta)
    if gl:
      self._browse_owner, self._browse_repo = gl.owner, gl.repo
      self._baseline_repo = f"{gl.owner}/{gl.repo}".lower()
      self._git_host = "gitlab"
    elif pair:
      self._browse_owner, self._browse_repo = pair
      self._baseline_repo = f"{pair[0]}/{pair[1]}".lower()
    self._baseline_tag = str(
      self._meta.get("tag")
      or self._meta.get("version")
      or entry.get("pin_release")
      or entry.get("tag")
      or ""
    ).strip().lower()
    if self._token_ok:
      self._sync_reinstall_button()
    QTimer.singleShot(0, self._load_preview)
    if self._token_ok and self._fork_combo is not None:
      QTimer.singleShot(0, self._prefetch_forks)

  def _set_fork_combo_interactive(
    self,
    enabled: bool,
    *,
    loading: bool = False,
    tip: str | None = None,
  ) -> None:
    if self._fork_combo is None:
      return
    # Only force-close when locking; enabling must not dismiss an open list.
    if not enabled:
      try:
        self._fork_combo.hidePopup()
      except RuntimeError:
        pass
    self._fork_combo.setEnabled(bool(enabled))
    if enabled:
      self._fork_combo.setToolTip("")
    elif tip:
      self._fork_combo.setToolTip(tip)
    elif loading:
      self._fork_combo.setToolTip(_LOADING_FORKS_TIP)

  def _set_version_combo_interactive(
    self,
    enabled: bool,
    *,
    loading: bool = False,
    tip: str | None = None,
  ) -> None:
    if self._version_combo is None:
      return
    if not enabled:
      try:
        self._version_combo.hidePopup()
      except RuntimeError:
        pass
    self._version_combo.setEnabled(bool(enabled))
    if enabled:
      self._version_combo.setToolTip("")
    elif tip:
      self._version_combo.setToolTip(tip)
    elif loading:
      self._version_combo.setToolTip(_LOADING_VERSIONS_TIP)

  def _sync_combo_interactivity(self) -> None:
    """Lock fork/version while preview loads; unlock on success or failure."""
    if self._fork_combo is None:
      return
    if self._preview_pending:
      self._set_fork_combo_interactive(False, tip=_LOADING_PREVIEW_TIP)
      self._set_version_combo_interactive(False, tip=_LOADING_PREVIEW_TIP)
      self._sync_reinstall_button()
      return
    if self._fork_fetch_pending:
      self._set_fork_combo_interactive(False, loading=True)
      self._set_version_combo_interactive(False)
      self._sync_reinstall_button()
      return
    self._set_fork_combo_interactive(True)
    # Version fetch uses status_lbl only — disabling here while the list is open
    # used to desync GlueCombo (popupShown → hide → native show still ran).
    self._set_version_combo_interactive(True)
    self._sync_reinstall_button()

  def _selected_repo_key(self) -> str:
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or "").strip()
    repo = str(fork.get("repo_name") or "").strip()
    if owner and repo:
      return f"{owner}/{repo}".lower()
    raw = str(fork.get("repo") or "").strip()
    if not raw:
      return ""
    from ichalaunch.addons.github import parse_github_url
    from ichalaunch.addons.gitlab import parse_gitlab_url

    gl = parse_gitlab_url(raw)
    if gl:
      return f"{gl.owner}/{gl.repo}".lower()
    parsed = parse_github_url(raw)
    if parsed:
      return f"{parsed.owner}/{parsed.repo}".lower()
    if raw.count("/") == 1 and "://" not in raw:
      return raw.lower()
    return ""

  def _selected_tag_key(self) -> str:
    if self._version_combo is None:
      return self._baseline_tag
    return str(self._version_combo.currentData() or "").strip().lower()

  def _selection_differs_from_install(self) -> bool:
    """True when fork and/or version differ from the currently installed origin/tag."""
    sel_repo = self._selected_repo_key()
    if sel_repo and self._baseline_repo and sel_repo != self._baseline_repo:
      return True
    if self._selected_tag_key() != self._baseline_tag:
      return True
    return False

  def _sync_reinstall_button(self) -> None:
    if self._reinstall_btn is None:
      return
    busy = bool(self._preview_pending or self._fork_fetch_pending)
    differs = self._selection_differs_from_install()
    can = (not busy) and differs
    self._reinstall_btn.setEnabled(can)
    if busy:
      tip = (
        _LOADING_PREVIEW_TIP
        if self._preview_pending
        else _LOADING_FORKS_TIP
      )
    elif not differs:
      tip = "Already on this fork/version — pick a different one to reinstall"
    else:
      tip = "Replace the installed addon with the selected fork/version"
    self._reinstall_btn.setToolTip(tip)

  def _open_preview_link(self, url: QUrl) -> None:
    QDesktopServices.openUrl(url)

  def _preview_url(self) -> str:
    from ichalaunch.addons.github import addon_browse_url, addon_install_url_for_choice, fork_git_host
    from ichalaunch.ui.widgets.common import git_repo_browse_url

    fork = self._current_fork_data()
    tag = ""
    if self._version_combo is not None:
      tag = str(self._version_combo.currentData() or "").strip()
    install = addon_install_url_for_choice(fork, tag or None)
    if install:
      return install
    owner = str(fork.get("owner") or self._browse_owner or "").strip()
    repo = str(fork.get("repo_name") or self._browse_repo or "").strip()
    if owner and repo:
      return addon_browse_url(owner, repo, host=fork_git_host(fork) or self._git_host)
    return git_repo_browse_url(
      self._entry.get("repo"),
      self._entry.get("url"),
      self._entry.get("repository"),
      self._meta.get("url"),
    ) or ""

  def _load_preview(self) -> None:
    from ichalaunch.addons.github import cleanup_readme_cache

    url = self._preview_url()
    if not url:
      self.status_lbl.setText("No GitHub or GitLab repository URL for preview.")
      self.browser.clear()
      self._preview_pending = False
      self._sync_combo_interactivity()
      return
    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""
    self._preview_pending = True
    self._sync_combo_interactivity()
    self.status_lbl.setText("Loading preview…")
    self.browser.clear()
    self._preview_gen += 1
    gen = self._preview_gen
    worker = _PreviewFetchThread("addon", url, self)
    self._preview_worker = worker

    def on_ok(info: object) -> None:
      if gen != self._preview_gen:
        if isinstance(info, dict):
          cleanup_readme_cache(info.get("readme_cache_dir"))
        return
      self._preview_pending = False
      if not isinstance(info, dict):
        self.status_lbl.setText("Preview failed.")
        self._sync_combo_interactivity()
        return
      self._cache_dir = str(info.get("readme_cache_dir") or "")
      md = str(info.get("readme_markdown") or "").strip()
      base = str(info.get("readme_base_url") or "")
      if base:
        self.browser.document().setBaseUrl(QUrl(base))
      if md:
        self.browser.setMarkdown(md)
        self.status_lbl.setText("")
      else:
        self.browser.setPlainText(str(info.get("description") or "No README found."))
        self.status_lbl.setText("")
      self._sync_combo_interactivity()

    def on_err(msg: str) -> None:
      if gen != self._preview_gen:
        return
      self._preview_pending = False
      self.status_lbl.setText(msg or "Preview failed.")
      self._sync_combo_interactivity()

    worker.ok.connect(on_ok)
    worker.err.connect(on_err)
    worker.start()

  def _current_fork_data(self) -> dict:
    if self._fork_combo is None:
      from ichalaunch.addons.github import fork_entry_from_repo_url

      return fork_entry_from_repo_url(
        str(self._entry.get("repo") or self._entry.get("url") or "")
      )
    idx = self._fork_combo.currentIndex()
    if idx < 0:
      return {}
    data = self._fork_combo.itemData(idx)
    return dict(data) if isinstance(data, dict) else {}

  def _prefetch_forks(self) -> None:
    if not self._token_ok or self._fork_combo is None or self._forks_loaded:
      return
    from ichalaunch.addons.github import get_cached_repo_forks

    owner, repo = self._browse_owner, self._browse_repo
    if not owner or not repo:
      self._sync_combo_interactivity()
      return
    if self._fork_fetch_pending:
      return
    cached = get_cached_repo_forks(owner, repo)
    if cached:
      self._populate_forks(cached)
      self._forks_loaded = True
      self._sync_combo_interactivity()
      # Forks-from-cache skips the browse thread — still warm the version list
      # so the first Version click is already populated.
      self._prefetch_versions()
      return
    self._fork_fetch_pending = True
    self._browse_fetch_gen += 1
    fetch_gen = self._browse_fetch_gen
    self._sync_combo_interactivity()
    self.status_lbl.setText(
      "Loading versions from GitLab…" if self._git_host == "gitlab"
      else "Loading forks from GitHub…"
    )
    self._browse_worker = _AddonBrowseFetchThread(
      owner, repo, self, host=self._git_host
    )
    self._browse_worker.ok.connect(
      lambda forks, versions, g=fetch_gen: self._on_forks_fetched(forks, versions, g)
    )
    self._browse_worker.err.connect(
      lambda msg, g=fetch_gen: self._on_browse_err(msg, g, kind="forks")
    )
    self._browse_worker.start()

  def _version_owner_repo(self) -> tuple[str, str]:
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or self._browse_owner or "").strip()
    repo = str(fork.get("repo_name") or self._browse_repo or "").strip()
    return owner, repo

  def _show_versions_loading_placeholder(self) -> None:
    """Keep an open Version list usable while a fetch is in flight."""
    if self._version_combo is None or self._versions_loaded:
      return
    combo = self._version_combo
    for i in range(combo.count()):
      if str(combo.itemData(i) or "") == _VERSIONS_LOADING_DATA:
        return
    pin = str(combo.currentData() or "").strip()
    pin_text = str(combo.currentText() or "").strip() or (
      f"v{pin}" if pin else "Latest (branch tip)"
    )
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(pin_text, pin)
    combo.addItem(_VERSIONS_LOADING_LABEL, _VERSIONS_LOADING_DATA)
    combo.setCurrentIndex(0)
    combo.blockSignals(False)

  def _prefetch_versions(self) -> None:
    """Warm the Version combo before the user opens it (first click shows tags)."""
    if not self._token_ok or self._version_combo is None or self._versions_loaded:
      return
    if self._version_fetch_pending or self._fork_fetch_pending:
      return
    owner, repo = self._version_owner_repo()
    if not owner or not repo:
      return
    from ichalaunch.addons.github import get_cached_repo_versions

    cached = get_cached_repo_versions(owner, repo)
    if cached is not None:
      self._populate_versions(cached, close_popup=False)
      self._versions_loaded = True
      return
    self._start_version_fetch(owner, repo, show_status=not self._preview_pending)

  def _start_version_fetch(
    self,
    owner: str,
    repo: str,
    *,
    show_status: bool = True,
  ) -> None:
    if self._version_fetch_pending:
      return
    self._version_fetch_pending = True
    self._browse_fetch_gen += 1
    fetch_gen = self._browse_fetch_gen
    # Do not disable/hide while the dropdown is open — that desynced GlueCombo.
    if show_status:
      self.status_lbl.setText(
        "Loading versions from GitLab…" if self._git_host == "gitlab"
        else "Loading versions from GitHub…"
      )
    self._browse_worker = _AddonBrowseFetchThread(
      owner, repo, self, host=self._git_host
    )
    self._browse_worker.ok.connect(
      lambda forks, versions, g=fetch_gen: self._on_versions_fetched(forks, versions, g)
    )
    self._browse_worker.err.connect(
      lambda msg, g=fetch_gen: self._on_browse_err(msg, g, kind="versions")
    )
    self._browse_worker.start()

  def _lazy_fetch_versions(self) -> None:
    if not self._token_ok or self._version_combo is None or self._versions_loaded:
      return
    if self._preview_pending:
      return
    owner, repo = self._version_owner_repo()
    if not owner or not repo:
      return
    from ichalaunch.addons.github import get_cached_repo_versions

    cached = get_cached_repo_versions(owner, repo)
    if cached is not None:
      # Keep the open list; closing here made the first open feel stuck/empty.
      self._populate_versions(cached, close_popup=False)
      self._versions_loaded = True
      return
    if self._version_fetch_pending:
      self._show_versions_loading_placeholder()
      return
    self._show_versions_loading_placeholder()
    self._start_version_fetch(owner, repo)

  def _on_forks_fetched(self, forks: object, versions: object, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._fork_fetch_pending = False
    if isinstance(forks, list):
      self._populate_forks(forks)
      self._forks_loaded = True
    # Browse thread already fetched tags — fill Version now so first open is full.
    if isinstance(versions, list) and not self._versions_loaded:
      self._populate_versions(versions, close_popup=False)
      self._versions_loaded = True
    self._sync_combo_interactivity()
    if not self._preview_pending:
      self.status_lbl.setText("")
    if not self._versions_loaded:
      self._prefetch_versions()

  def _on_versions_fetched(self, _forks: object, versions: object, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._version_fetch_pending = False
    if isinstance(versions, list):
      # Never hidePopup here — first-open lazy fetch must repopulate in place.
      self._populate_versions(versions, close_popup=False)
      self._versions_loaded = True
    self._sync_combo_interactivity()
    if not self._preview_pending:
      self.status_lbl.setText("")

  def _on_browse_err(self, message: str, fetch_gen: int, *, kind: str = "") -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    if kind == "forks":
      self._fork_fetch_pending = False
    elif kind == "versions":
      self._version_fetch_pending = False
    self._sync_combo_interactivity()
    self.status_lbl.setText(message or "GitHub request failed.")

  def _populate_forks(self, forks: list) -> None:
    from ichalaunch.ui.widgets.common import fork_combo_label

    if self._fork_combo is None:
      return
    self._fork_combo.hidePopup()
    current = self._current_fork_data()
    current_repo = str(current.get("repo") or "").strip().lower()
    self._fork_combo.blockSignals(True)
    self._fork_combo.clear()
    seen: set[str] = set()
    selected = 0
    for fe in forks:
      if not isinstance(fe, dict):
        continue
      repo = str(fe.get("repo") or "").strip()
      key = repo.lower()
      if not key or key in seen:
        continue
      seen.add(key)
      label = fork_combo_label(fe)
      idx = self._fork_combo.count()
      self._fork_combo.addItem(label, fe)
      if key == current_repo:
        selected = idx
    if self._fork_combo.count() == 0 and current_repo:
      self._fork_combo.addItem(
        fork_combo_label(current),
        current,
      )
    else:
      self._fork_combo.setCurrentIndex(min(selected, max(0, self._fork_combo.count() - 1)))
    self._fork_combo.blockSignals(False)
    self._sync_reinstall_button()

  def _populate_versions(self, versions: list, *, close_popup: bool = True) -> None:
    if self._version_combo is None:
      return
    popup_open = False
    try:
      popup_open = bool(
        self._version_combo._popup_open and not self._version_combo._hiding_popup
      )
    except RuntimeError:
      popup_open = False
    # Never dismiss an open list — hide+reopen forced a second click to see tags.
    if close_popup and not popup_open:
      try:
        self._version_combo.hidePopup()
      except RuntimeError:
        pass
    pin = str(self._version_combo.currentData() or "").strip()
    if pin == _VERSIONS_LOADING_DATA:
      pin = ""
    if not pin:
      pin = str(
        self._meta.get("tag")
        or self._meta.get("version")
        or self._entry.get("pin_release")
        or self._entry.get("tag")
        or ""
      ).strip()
    self._version_combo.blockSignals(True)
    self._version_combo.clear()
    self._version_combo.addItem("Latest (branch tip)", "")
    selected = 0
    for tag in versions:
      t = str(tag or "").strip()
      if not t:
        continue
      label = t if t.lower().startswith("v") else f"v{t}"
      idx = self._version_combo.count()
      self._version_combo.addItem(label, t)
      if pin and t.lower() == pin.lower():
        selected = idx
    self._version_combo.setCurrentIndex(min(selected, max(0, self._version_combo.count() - 1)))
    self._version_combo.blockSignals(False)
    if popup_open:
      try:
        view = self._version_combo.view()
        if view is not None:
          view.reset()
          view.updateGeometry()
          container = view.parentWidget()
          if container is not None:
            container.adjustSize()
      except RuntimeError:
        pass
    self._sync_reinstall_button()

  def _on_fork_changed(self, _index: int) -> None:
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or "").strip()
    repo = str(fork.get("repo_name") or "").strip()
    if owner and repo:
      self._browse_owner = owner
      self._browse_repo = repo
    # Cancel any in-flight version fetch for the previous fork.
    self._browse_fetch_gen += 1
    self._versions_loaded = False
    self._version_fetch_pending = False
    if self._version_combo is not None:
      self._version_combo.blockSignals(True)
      self._version_combo.clear()
      self._version_combo.addItem("Latest (branch tip)", "")
      self._version_combo.blockSignals(False)
    self._sync_reinstall_button()
    self._prefetch_versions()
    self._load_preview()

  def _on_version_changed(self, _index: int) -> None:
    self._sync_reinstall_button()
    self._load_preview()

  def _build_result_entry(self) -> dict:
    from ichalaunch.addons.github import addon_install_url_for_choice

    fork = self._current_fork_data()
    tag = ""
    if self._version_combo is not None:
      tag = str(self._version_combo.currentData() or "").strip()
    url = addon_install_url_for_choice(fork, tag or None)
    out = dict(self._entry)
    if url:
      out["repo"] = url
    if tag:
      out["pin_release"] = tag
      out["tag"] = tag
    else:
      out.pop("pin_release", None)
      out.pop("tag", None)
    if fork.get("folder"):
      out["folder"] = fork.get("folder")
    owner = str(fork.get("owner") or "").strip()
    repo_name = str(fork.get("repo_name") or "").strip()
    if owner and repo_name:
      out["repository"] = f"{owner}/{repo_name}"
    if self._never_update_cb is not None:
      if self._catalog_never_locked:
        out["never_update"] = True
      else:
        out["never_update"] = bool(self._never_update_cb.isChecked())
    return out

  def _queue_selected_fork_review(self) -> None:
    from ichalaunch.addons.submit import queue_selected_fork_if_uncatalogued

    fork = self._current_fork_data()
    queue_selected_fork_if_uncatalogued(
      fork,
      name=str(self._entry.get("name") or ""),
      folder=str(self._entry.get("folder") or ""),
      category=str(self._entry.get("category") or "General"),
    )

  def _accept_save(self) -> None:
    self._queue_selected_fork_review()
    self._result = self._build_result_entry()
    self.accept()

  def _accept_reinstall(self) -> None:
    if self._reinstall_btn is None or not self._reinstall_btn.isEnabled():
      return
    if self._preview_pending or self._fork_fetch_pending:
      return
    if not self._selection_differs_from_install():
      return
    self._queue_selected_fork_review()
    out = self._build_result_entry()
    orig_folder = str(self._entry.get("folder") or self._entry.get("name") or "").strip()
    if orig_folder:
      out["folder"] = orig_folder
    if not out.get("repo") and not out.get("repository"):
      return
    out["_action"] = "reinstall"
    out["_prefer_selection"] = True
    self._result = out
    self.accept()

  def closeEvent(self, event) -> None:  # noqa: N802
    from ichalaunch.addons.github import cleanup_readme_cache

    self._browse_fetch_gen += 1
    self._preview_gen += 1
    self._fork_fetch_pending = False
    self._version_fetch_pending = False
    if self._fork_combo is not None:
      try:
        self._fork_combo.hidePopup()
      except RuntimeError:
        pass
    if self._version_combo is not None:
      try:
        self._version_combo.hidePopup()
      except RuntimeError:
        pass
    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""
    self._preview_pending = False
    super().closeEvent(event)

  def result_data(self) -> dict | None:
    return self._result


def addon_settings_dialog(
  parent: QWidget | None,
  entry: dict,
  *,
  meta: dict | None = None,
) -> dict | None:
  """Blocking settings dialog. Returns updated entry or None if dismissed without save."""
  dlg = AddonSettingsDialog(parent, entry, meta=meta)
  if dlg.exec() != QDialog.DialogCode.Accepted:
    return None
  return dlg.result_data()


class VanillaTweaksSettingsDialog(QDialog):
    """tubtubs/vanilla-tweaks patch options — themed card matching other modals."""

    _GIT = "https://github.com/tubtubs/vanilla-tweaks"
    _TITLE = "Vanilla Tweaks V2"
    _MOD_ID = "vanilla_tweaks"
    _DEFAULTS_TIP = "Restore tubtubs V2 defaults"
    _BLURB = (
        "These flags are applied by tubtubs/vanilla-tweaks when it patches "
        "WoW.exe. Saving while Tweaks is installed re-patches from "
        "WoW-OriginalBackup.exe so changes do not stack."
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(860, 500)
        self.resize(900, 540)
        self._result: dict | None = None
        self._defaults, self._initial = self._load_option_state()
        self._installed = self._tweaks_are_installed()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        open_git = _addon_open_git_button(card, self, self._GIT)
        title_row, _title = _addon_dialog_title_row(self._TITLE, open_git)
        body.addLayout(title_row)

        blurb = QLabel(self._BLURB)
        blurb.setObjectName("ThemedDialogBody")
        blurb.setWordWrap(True)
        body.addWidget(blurb)

        self._checks: dict[str, ThemeCheckBox] = {}
        self._spins: dict[str, QSpinBox | QDoubleSpinBox] = {}
        self._sliders: dict[str, QSlider] = {}
        self._slider_value_lbls: dict[str, QLabel] = {}
        self._combos: dict[str, object] = {}
        self._range_hints: dict[str, QLabel] = {}
        self._spin_for_check: dict[str, str] = {}
        self._slider_for_check: dict[str, str] = {}
        self._combo_for_check: dict[str, str] = {}
        self._extras_for_check: dict[str, tuple] = {}
        self._check_tips: dict[str, str] = {}
        self._superwow_locks_optional = False
        # V2-only override checkbox; the Old dialog never builds it.
        self._superwow_override_cb: ThemeCheckBox | None = None

        cols = QHBoxLayout()
        cols.setSpacing(22)
        left_host = QWidget(card)
        left = QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        right_host = QWidget(card)
        right = QVBoxLayout(right_host)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        self._build_option_rows(left, right, left_host, right_host)
        left.addStretch(1)
        right.addStretch(1)
        cols.addWidget(left_host, 1)
        cols.addWidget(right_host, 1)
        body.addLayout(cols)

        if self._installed:
            note = QLabel("Tweaks is installed — Save will re-patch WoW.exe.")
        else:
            note = QLabel("Options are saved for the next Vanilla Tweaks install.")
        note.setObjectName("ThemedDialogHint")
        note.setWordWrap(True)
        body.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(10)
        defaults_btn = _dialog_glue_button("Defaults", card, primary=False)
        defaults_btn.setToolTip(self._DEFAULTS_TIP)
        defaults_btn.clicked.connect(self._reset_defaults)
        row.addWidget(defaults_btn)
        # Wider than _dialog_glue_button allows — the 21-char label needs room.
        regen_btn = GluePanelButton(
            "Regenerate Config.wtf",
            card,
            role="standard",
            width=170,
            height=GLUE_BTN_H,
        )
        regen_btn.setToolTip(
            "Move WTF/Config.wtf aside so the client rebuilds default "
            "in-game settings on the next launch."
        )
        regen_btn.clicked.connect(self._regenerate_config_wtf)
        row.addWidget(regen_btn)
        from ichalaunch.ui.widgets.glue_combo import GlueComboBox

        restore_combo = GlueComboBox(card, min_width=150)
        restore_combo.setToolTip(
            "Restore a previous Config.wtf from the WTF/Backup folder."
        )
        # activated fires on user picks only, so reloads cannot re-trigger it.
        restore_combo.activated.connect(self._on_restore_backup_activated)
        row.addWidget(restore_combo)
        self._defaults_btn = defaults_btn
        self._regen_config_btn = regen_btn
        self._restore_combo = restore_combo
        self._reload_config_backups()
        row.addStretch(1)
        cancel_btn = _dialog_glue_button("Cancel", card, primary=False)
        cancel_btn.clicked.connect(self.reject)
        save_btn = _dialog_glue_button("Save", card, primary=True)
        save_btn.clicked.connect(self._accept_save)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
        )
        self._apply_options(self._initial)

    def _load_option_state(self) -> tuple[dict, dict]:
        from ichalaunch.config.settings import settings
        from ichalaunch.mods.vanilla_tweaks import (
            VANILLA_TWEAKS_DEFAULTS,
            normalize_vanilla_tweaks_options,
        )

        return (
            dict(VANILLA_TWEAKS_DEFAULTS),
            normalize_vanilla_tweaks_options(settings.vanilla_tweaks_options),
        )

    def _build_option_rows(
        self,
        left: QVBoxLayout,
        right: QVBoxLayout,
        left_host: QWidget,
        right_host: QWidget,
    ) -> None:
        from ichalaunch.mods.vanilla_tweaks import SOUND_CHANNEL_CHOICES

        left.addWidget(self._section("Applied by default"))
        self._add_toggle(
            left,
            left_host,
            "farclip",
            "Farclip (terrain distance)",
            "Stock maximum is 777. The patcher can raise the cap (up to 10000), "
            "but Config.wtf values above 777 can hide world geometry.",
            spin_key="farclip_value",
            spin_kind="float",
            decimals=0,
            lo=100,
            hi=10000,
            step=100,
        )
        self._add_toggle(
            left,
            left_host,
            "frilldistance",
            "Grass / frill distance",
            "Grass render distance (game default 70, launcher default 300). "
            "Density is still /console frilldensity.",
            spin_key="frilldistance_value",
            spin_kind="float",
            decimals=0,
            lo=1,
            hi=2000,
            step=10,
        )
        self._add_toggle(
            left,
            left_host,
            "nameplatedistance",
            "Nameplate range",
            "Nameplate distance in yards (game default 20, cap 41).",
            slider_key="nameplatedistance_value",
            lo=1,
            hi=41,
        )
        self._add_toggle(
            left,
            left_host,
            "largeaddressaware",
            "Large Address Aware (4 GB)",
            "Lets the 32-bit client use more than 2 GB of RAM. Leave on "
            "unless the machine has under 3 GB.",
        )
        self._add_toggle(
            left,
            left_host,
            "cameraskipfix",
            "Camera skip glitch fix",
            "Stops the camera from jumping to a random direction when rotated.",
        )
        self._add_toggle(
            left,
            left_host,
            "customglues",
            "Custom glues (frames / XML)",
            "Allows custom frames and XML. On by default in V2.",
        )
        self._add_toggle(
            left,
            left_host,
            "bluemoon",
            "Blue moon",
            "Shows the blue moon around 1am every other day or so. On by default in V2.",
        )

        right.addWidget(self._section("Optional (off in V2)"))
        extra = QLabel(
            "These are covered by SuperWoW for many players, so V2 leaves them off."
        )
        extra.setObjectName("ThemedDialogHint")
        extra.setWordWrap(True)
        right.addWidget(extra)
        self._add_toggle(
            right,
            right_host,
            "superwow_override",
            "Enable anyway (SuperWoW override)",
            "Unlock these patches even though SuperWoW is enabled. "
            "They overlap with SuperWoW and can cause conflicts or crashes.",
        )
        self._superwow_override_cb = self._checks["superwow_override"]
        self._superwow_override_cb.toggled.connect(
            self._on_superwow_override_toggled
        )
        self._add_toggle(
            right,
            right_host,
            "fov_patch",
            "Widescreen FoV",
            "Game default is 1.5708 radians. V2 suggested widescreen value is 1.925.",
            spin_key="fov",
            spin_kind="float",
            decimals=4,
            lo=0.5,
            hi=3.0,
            step=0.025,
        )
        self._add_toggle(
            right,
            right_host,
            "sound_in_background",
            "Sound in background",
            "Keep game audio playing when the client is not focused.",
        )
        self._add_toggle(
            right,
            right_host,
            "soundchannels_patch",
            "Sound channel count",
            "Persists /console SoundSoftwareChannels. Vanilla 12, TBC 32, "
            "or modern 64. Those are the only values offered.",
            choice_key="soundchannels",
            choices=SOUND_CHANNEL_CHOICES,
            choice_labels=("12 — Vanilla", "32 — TBC", "64 — Modern"),
        )
        self._add_toggle(
            right,
            right_host,
            "quickloot",
            "Quickloot reverse (hold Shift)",
            "Loot automatically; hold Shift for the loot window.",
        )
        self._add_toggle(
            right,
            right_host,
            "crossfactionresfix",
            "Cross-faction resurrect fix",
            "Lets you resurrect released opposite-faction players. Can apply "
            "even when they are not in your party.",
        )
        self._add_toggle(
            right,
            right_host,
            "maxcameradistance_patch",
            "Camera distance limit",
            "Stock maximum is 50. After patching, use /console CameraDistanceMax.",
            slider_key="maxcameradistance",
            lo=1,
            hi=50,
        )

    def _tweaks_are_installed(self) -> bool:
        from ichalaunch.config.settings import settings
        from ichalaunch.game.launcher import detect_game
        from ichalaunch.mods.installer import detect_actual_state

        mid = self._MOD_ID
        game = detect_game()
        if game:
            try:
                if detect_actual_state(game).get(mid):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return bool(
            settings.installed_mods.get(mid) and settings.desired_mods.get(mid)
        )

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("ThemedDialogSection")
        return lbl

    def _add_toggle(
        self,
        form: QVBoxLayout,
        parent: QWidget,
        key: str,
        label: str,
        tip: str,
        *,
        spin_key: str | None = None,
        spin_kind: str = "float",
        decimals: int = 0,
        lo: float = 0,
        hi: float = 100,
        step: float = 1,
        slider_key: str | None = None,
        choice_key: str | None = None,
        choices: tuple[int, ...] = (),
        choice_labels: tuple[str, ...] = (),
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        cb = ThemeCheckBox(label, parent)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.setMinimumHeight(28)
        cb.setToolTip(tip)
        cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(cb, 1)
        self._checks[key] = cb
        extra: list[QWidget] = []
        value_key = slider_key or spin_key or choice_key
        self._check_tips[key] = tip
        if value_key:
            hint = QLabel(
                self._range_hint(lo, hi, decimals=decimals, choices=choices),
                parent,
            )
            hint.setObjectName("ThemedDialogHint")
            hint.setToolTip(tip)
            row.addWidget(hint, 0, Qt.AlignmentFlag.AlignVCenter)
            self._range_hints[value_key] = hint
        if choice_key and choices:
            from ichalaunch.ui.widgets.glue_combo import GlueComboBox

            combo = GlueComboBox(parent, min_width=168)
            labels = choice_labels or tuple(str(v) for v in choices)
            for value, item_label in zip(choices, labels, strict=False):
                combo.addItem(item_label, int(value))
            combo.setToolTip(tip)
            row.addWidget(combo, 0)
            self._combos[choice_key] = combo
            self._combo_for_check[key] = choice_key
            extra.append(combo)
        elif slider_key:
            slider = QSlider(Qt.Orientation.Horizontal, parent)
            slider.setRange(int(lo), int(hi))
            slider.setSingleStep(1)
            slider.setPageStep(max(1, int((hi - lo) / 10)))
            slider.setMinimumWidth(120)
            slider.setMaximumWidth(180)
            slider.setFixedHeight(22)
            slider.setToolTip(tip)
            value_lbl = QLabel(str(int(hi)), parent)
            value_lbl.setObjectName("ThemedDialogHint")
            value_lbl.setFixedWidth(28)
            value_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            slider.valueChanged.connect(lambda v, lbl=value_lbl: lbl.setText(str(v)))
            row.addWidget(slider, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(value_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            self._sliders[slider_key] = slider
            self._slider_value_lbls[slider_key] = value_lbl
            self._slider_for_check[key] = slider_key
            extra.extend((slider, value_lbl))
        elif spin_key:
            if spin_kind == "int":
                spin: QSpinBox | QDoubleSpinBox = QSpinBox(parent)
                spin.setRange(int(lo), int(hi))
                spin.setSingleStep(int(step) or 1)
            else:
                spin = QDoubleSpinBox(parent)
                spin.setDecimals(decimals)
                spin.setRange(float(lo), float(hi))
                spin.setSingleStep(float(step) or 1.0)
            spin.setFixedWidth(104)
            spin.setFixedHeight(GLUE_BTN_H)
            spin.setToolTip(tip)
            spin.setKeyboardTracking(False)
            row.addWidget(spin, 0)
            self._spins[spin_key] = spin
            self._spin_for_check[key] = spin_key
            extra.append(spin)
        extras = tuple(extra)
        self._extras_for_check[key] = extras
        if extras:
            cb.toggled.connect(lambda on, widgets=extras: self._set_enabled(widgets, on))
        form.addLayout(row)

    @staticmethod
    def _range_hint(
        lo: float,
        hi: float,
        *,
        decimals: int = 0,
        choices: tuple[int, ...] = (),
    ) -> str:
        if choices:
            return f"({choices[0]}-{choices[-1]})"
        if decimals > 0:
            return f"({lo:g}-{hi:g})"
        return f"({int(lo)}-{int(hi)})"

    @staticmethod
    def _set_enabled(widgets: tuple[QWidget, ...], enabled: bool) -> None:
        for widget in widgets:
            widget.setEnabled(enabled)

    _SUPERWOW_OPTIONAL_TIP = (
        "SuperWoW already covers these, so they stay off."
    )

    def _superwow_detected(self) -> bool:
        from ichalaunch.mods.vanilla_tweaks import superwow_is_active

        return superwow_is_active()

    def _on_superwow_override_toggled(self, on: bool) -> None:
        cb = self._superwow_override_cb
        if cb is None:
            return
        if on and self._superwow_locks_optional:
            # Cancel is listed first so Enter cannot confirm the override;
            # Enable anyway still renders as the primary button.
            result = choice(
                self,
                "SuperWoW override",
                "SuperWoW already provides these features. Patching them "
                "into WoW.exe as well can cause conflicts or crashes in "
                "game.\n\nEnable them anyway?",
                buttons=[
                    ("Cancel", DialogResult.Cancel),
                    ("Enable anyway", DialogResult.Yes),
                ],
                kind="warning",
            )
            if result != DialogResult.Yes:
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
                return
        self._apply_superwow_optional_lock()

    def _apply_superwow_optional_lock(self) -> None:
        from ichalaunch.mods.vanilla_tweaks import VANILLA_TWEAKS_OPTIONAL_KEYS

        locked = self._superwow_detected()
        self._superwow_locks_optional = locked
        override_cb = self._superwow_override_cb
        if override_cb is not None:
            # The override is meaningless without SuperWoW — hide it then.
            override_cb.setVisible(locked)
        overridden = bool(override_cb is not None and override_cb.isChecked())
        grey = locked and not overridden
        for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
            cb = self._checks.get(key)
            if cb is None:
                continue
            extras = self._extras_for_check.get(key, ())
            if grey:
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
                cb.setEnabled(False)
                cb.setToolTip(self._SUPERWOW_OPTIONAL_TIP)
                for widget in extras:
                    widget.setEnabled(False)
                    widget.setToolTip(self._SUPERWOW_OPTIONAL_TIP)
            else:
                cb.setEnabled(True)
                cb.setToolTip(self._check_tips.get(key) or cb.toolTip())
                on = cb.isChecked()
                tip = self._check_tips.get(key) or ""
                for widget in extras:
                    widget.setEnabled(on)
                    if tip:
                        widget.setToolTip(tip)

    def _apply_options(self, opts: dict) -> None:
        for key, cb in self._checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(opts.get(key)))
            cb.blockSignals(False)
        for key, spin in self._spins.items():
            value = opts.get(key)
            spin.blockSignals(True)
            if isinstance(spin, QSpinBox):
                spin.setValue(int(value))
            else:
                spin.setValue(float(value))
            spin.blockSignals(False)
        for key, combo in self._combos.items():
            target = int(opts.get(key) or 0)
            combo.blockSignals(True)
            idx = combo.findData(target)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        for key, slider in self._sliders.items():
            try:
                parsed = int(round(float(opts.get(key) or slider.minimum())))
            except (TypeError, ValueError):
                parsed = slider.minimum()
            parsed = max(slider.minimum(), min(slider.maximum(), parsed))
            slider.blockSignals(True)
            slider.setValue(parsed)
            slider.blockSignals(False)
            lbl = self._slider_value_lbls.get(key)
            if lbl is not None:
                lbl.setText(str(parsed))
        for key, spin_key in self._spin_for_check.items():
            self._spins[spin_key].setEnabled(self._checks[key].isChecked())
        for key, slider_key in self._slider_for_check.items():
            on = self._checks[key].isChecked()
            self._sliders[slider_key].setEnabled(on)
            self._slider_value_lbls[slider_key].setEnabled(on)
        for key, choice_key in self._combo_for_check.items():
            self._combos[choice_key].setEnabled(self._checks[key].isChecked())
        self._apply_superwow_optional_lock()

    def _reset_defaults(self) -> None:
        self._apply_options(self._defaults)

    def _regenerate_config_wtf(self) -> None:
        from ichalaunch.core.process import wow_exe_running
        from ichalaunch.game.config_wtf import backup_and_remove_config
        from ichalaunch.game.launcher import detect_game

        title = "Regenerate Config.wtf"
        game = detect_game()
        if not game:
            warning(self, title, "No game folder detected.")
            return
        if wow_exe_running(game):
            # The client rewrites Config.wtf on exit, so removing it now
            # would be silently undone.
            warning(
                self,
                title,
                "Close the game first. WoW rewrites Config.wtf when it "
                "exits, so removing the file while the client is running "
                "has no effect.",
            )
            return
        # Cancel is listed first so Enter cannot trigger the destructive
        # action; Regenerate still renders as the primary button.
        result = choice(
            self,
            title,
            "This resets ALL in-game settings stored in Config.wtf — video, "
            "sound, and the keybinds kept there. The client writes a fresh "
            "Config.wtf on the next launch.\n\n"
            "The current file is saved into the WTF/Backup folder first.",
            buttons=[
                ("Cancel", DialogResult.Cancel),
                ("Regenerate", DialogResult.Yes),
            ],
            kind="warning",
        )
        if result != DialogResult.Yes:
            return
        try:
            backup = backup_and_remove_config(game)
        except OSError as exc:
            error(self, title, f"Could not move Config.wtf aside: {exc}")
            return
        if backup is None:
            info(
                self,
                title,
                "No Config.wtf found — the client will already write a "
                "fresh one on the next launch.",
            )
            return
        self._reload_config_backups()
        info(
            self,
            title,
            f"Done. The old file was saved to WTF/Backup/{backup.name}; "
            "a fresh Config.wtf will be created on the next launch.",
        )

    def _reload_config_backups(self) -> None:
        from ichalaunch.game.config_wtf import list_config_backups
        from ichalaunch.game.launcher import detect_game

        combo = self._restore_combo
        game = detect_game()
        backups = list_config_backups(game) if game else []
        combo.blockSignals(True)
        combo.clear()
        if backups:
            combo.addItem("Restore backup…", None)
            for entry in backups:
                combo.addItem(entry.label, str(entry.path))
            combo.setEnabled(True)
        else:
            combo.addItem("No backups", None)
            combo.setEnabled(False)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _on_restore_backup_activated(self, index: int) -> None:
        from ichalaunch.core.process import wow_exe_running
        from ichalaunch.game.config_wtf import restore_config_backup
        from ichalaunch.game.launcher import detect_game

        combo = self._restore_combo
        backup_path = combo.itemData(index)
        label = combo.itemText(index)
        combo.setCurrentIndex(0)
        if not backup_path:
            return  # "Restore backup…" placeholder row
        title = "Restore Config.wtf"
        game = detect_game()
        if not game:
            warning(self, title, "No game folder detected.")
            return
        if wow_exe_running(game):
            warning(
                self,
                title,
                "Close the game first. WoW rewrites Config.wtf when it "
                "exits, so a restore while the client is running would be "
                "overwritten.",
            )
            return
        # Cancel first so Enter cannot trigger the overwrite.
        result = choice(
            self,
            title,
            f"This replaces the current Config.wtf with the backup from "
            f"{label}. In-game settings stored there (video, sound, "
            "keybinds) revert to that snapshot.\n\n"
            "The current file is saved into the WTF/Backup folder first.",
            buttons=[
                ("Cancel", DialogResult.Cancel),
                ("Restore", DialogResult.Yes),
            ],
            kind="warning",
        )
        if result != DialogResult.Yes:
            return
        try:
            restore_config_backup(game, backup_path)
        except FileNotFoundError:
            self._reload_config_backups()
            warning(
                self,
                title,
                "That backup no longer exists — the list has been refreshed.",
            )
            return
        except OSError as exc:
            error(self, title, f"Could not restore Config.wtf: {exc}")
            return
        self._reload_config_backups()
        info(
            self,
            title,
            f"Restored the Config.wtf backup from {label}. The previous "
            "file was saved into the WTF/Backup folder.",
        )

    def _normalize_collected(self, raw: dict) -> dict:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_options

        return normalize_vanilla_tweaks_options(raw)

    def collect_options(self) -> dict:
        raw = dict(self._defaults)
        for key, cb in self._checks.items():
            raw[key] = cb.isChecked()
        for key, spin in self._spins.items():
            raw[key] = spin.value()
        for key, slider in self._sliders.items():
            raw[key] = slider.value()
        for key, combo in self._combos.items():
            data = combo.currentData()
            raw[key] = int(data) if data is not None else raw.get(key)
        return self._normalize_collected(raw)

    def _persist_options(self, options: dict) -> None:
        from ichalaunch.config.settings import settings

        settings.set_vanilla_tweaks_options(options)

    def _options_equal(self, left: dict, right: dict) -> bool:
        from ichalaunch.mods.vanilla_tweaks import options_equal

        return options_equal(left, right)

    def _accept_save(self) -> None:
        options = self.collect_options()
        changed = not self._options_equal(options, self._initial)
        self._persist_options(options)
        self._result = {"options": options, "repatch": bool(changed and self._installed)}
        self.accept()

    def result_data(self) -> dict | None:
        return self._result


def vanilla_tweaks_settings_dialog(parent: QWidget | None) -> dict | None:
    """Blocking Vanilla Tweaks V2 options modal. None if cancelled."""
    dlg = VanillaTweaksSettingsDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.result_data()


class VanillaTweaksOldSettingsDialog(VanillaTweaksSettingsDialog):
    """brndd/vanilla-tweaks 1.6.0 options — same chrome as V2, Old schema only."""

    _GIT = "https://github.com/brndd/vanilla-tweaks"
    _TITLE = "Vanilla Tweaks (Old)"
    _MOD_ID = "vanilla_tweaks_old"
    _DEFAULTS_TIP = "Restore brndd 1.6.0 defaults"
    _BLURB = (
        "These flags are applied by brndd/vanilla-tweaks 1.6.0 when it patches "
        "WoW.exe. Saving while Tweaks is installed re-patches from "
        "WoW-OriginalBackup.exe so changes do not stack."
    )

    def _load_option_state(self) -> tuple[dict, dict]:
        from ichalaunch.config.settings import settings
        from ichalaunch.mods.vanilla_tweaks import (
            VANILLA_TWEAKS_OLD_DEFAULTS,
            normalize_vanilla_tweaks_old_options,
        )

        return (
            dict(VANILLA_TWEAKS_OLD_DEFAULTS),
            normalize_vanilla_tweaks_old_options(settings.vanilla_tweaks_old_options),
        )

    def _build_option_rows(
        self,
        left: QVBoxLayout,
        right: QVBoxLayout,
        left_host: QWidget,
        right_host: QWidget,
    ) -> None:
        from ichalaunch.mods.vanilla_tweaks import SOUND_CHANNEL_CHOICES

        left.addWidget(self._section("Applied by default"))
        self._add_toggle(
            left,
            left_host,
            "farclip",
            "Farclip (terrain distance)",
            "Stock maximum is 777. The patcher can raise the cap (up to 10000), "
            "but Config.wtf values above 777 can hide world geometry.",
            spin_key="farclip_value",
            spin_kind="float",
            decimals=0,
            lo=100,
            hi=10000,
            step=100,
        )
        self._add_toggle(
            left,
            left_host,
            "frilldistance",
            "Grass / frill distance",
            "Grass render distance (game default 70, brndd default 300). "
            "Density is still /console frilldensity.",
            spin_key="frilldistance_value",
            spin_kind="float",
            decimals=0,
            lo=1,
            hi=2000,
            step=10,
        )
        self._add_toggle(
            left,
            left_host,
            "nameplatedistance",
            "Nameplate range",
            "Nameplate distance in yards (game default 20, cap 41).",
            slider_key="nameplatedistance_value",
            lo=1,
            hi=41,
        )
        self._add_toggle(
            left,
            left_host,
            "largeaddressaware",
            "Large Address Aware (4 GB)",
            "Lets the 32-bit client use more than 2 GB of RAM. Leave on "
            "unless the machine has under 3 GB.",
        )
        self._add_toggle(
            left,
            left_host,
            "cameraskipfix",
            "Camera skip glitch fix",
            "Stops the camera from jumping to a random direction when rotated.",
        )
        self._add_toggle(
            left,
            left_host,
            "quickloot",
            "Quickloot reverse (hold Shift)",
            "Loot automatically; hold Shift for the loot window. On by default in 1.6.0.",
        )

        right.addWidget(self._section("Also applied by default"))
        extra = QLabel(
            "brndd 1.6.0 enables FoV, background sound, and extra channels by default."
        )
        extra.setObjectName("ThemedDialogHint")
        extra.setWordWrap(True)
        right.addWidget(extra)
        self._add_toggle(
            right,
            right_host,
            "fov_patch",
            "Widescreen FoV",
            "Game default is 1.5708 radians. brndd default widescreen value is 1.925.",
            spin_key="fov",
            spin_kind="float",
            decimals=4,
            lo=0.5,
            hi=3.0,
            step=0.025,
        )
        self._add_toggle(
            right,
            right_host,
            "sound_in_background",
            "Sound in background",
            "Keep game audio playing when the client is not focused.",
        )
        self._add_toggle(
            right,
            right_host,
            "soundchannels_patch",
            "Sound channel count",
            "Persists /console SoundSoftwareChannels. Vanilla 12, TBC 32, "
            "or modern 64. Those are the only values offered.",
            choice_key="soundchannels",
            choices=SOUND_CHANNEL_CHOICES,
            choice_labels=("12 — Vanilla", "32 — TBC", "64 — Modern"),
        )
        right.addWidget(self._section("Optional (off by default)"))
        self._add_toggle(
            right,
            right_host,
            "maxcameradistance_patch",
            "Camera distance limit",
            "Stock maximum is 50. After patching, use /console CameraDistanceMax.",
            slider_key="maxcameradistance",
            lo=1,
            hi=50,
        )

    def _apply_superwow_optional_lock(self) -> None:
        # SuperWoW warning is the enable popup — do not grey Old options.
        self._superwow_locks_optional = False

    def _normalize_collected(self, raw: dict) -> dict:
        from ichalaunch.mods.vanilla_tweaks import normalize_vanilla_tweaks_old_options

        return normalize_vanilla_tweaks_old_options(raw)

    def _persist_options(self, options: dict) -> None:
        from ichalaunch.config.settings import settings

        settings.set_vanilla_tweaks_old_options(options)

    def _options_equal(self, left: dict, right: dict) -> bool:
        from ichalaunch.mods.vanilla_tweaks import old_options_equal

        return old_options_equal(left, right)


def vanilla_tweaks_old_settings_dialog(parent: QWidget | None) -> dict | None:
    """Blocking Vanilla Tweaks (Old) options modal. None if cancelled."""
    dlg = VanillaTweaksOldSettingsDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.result_data()


def confirm_vanilla_tweaks_old(parent: QWidget | None) -> bool:
    """Warn that Old may conflict with SuperWoW. False leaves Old unchecked."""
    return confirm(
        parent,
        "Vanilla Tweaks (Old)",
        "Vanilla Tweaks (Old) may conflict with SuperWoW.\n\n"
        "Only use this version if Vanilla Tweaks V2 causes poor performance.\n\n"
        "Continue?",
    )


def _run(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    kind: str,
    buttons: list[tuple[str, DialogResult]],
) -> DialogResult:
    dlg = ThemedDialog(parent, title, text, buttons=buttons, kind=kind)
    dlg.exec()
    return dlg.result_value


def info(parent: QWidget | None, title: str, text: str) -> None:
    _run(parent, title, text, kind="info", buttons=[("OK", DialogResult.Ok)])


def warning(parent: QWidget | None, title: str, text: str) -> None:
    _run(parent, title, text, kind="warning", buttons=[("OK", DialogResult.Ok)])


def error(parent: QWidget | None, title: str, text: str) -> None:
    _run(parent, title, text, kind="error", buttons=[("OK", DialogResult.Ok)])


def question(parent: QWidget | None, title: str, text: str) -> bool:
    """Blocking Yes/No prompt. Returns True for Yes."""
    result = _run(
        parent,
        title,
        text,
        kind="question",
        buttons=[("No", DialogResult.No), ("Yes", DialogResult.Yes)],
    )
    return result == DialogResult.Yes


def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    """Blocking Yes/No confirm. Returns True for Yes."""
    return question(parent, title, text)


def confirm_addon_toc_rename(
    parent: QWidget | None,
    current_name: str,
    toc_name: str,
) -> bool:
    """Ask whether to rename the addon folder to the ``.toc`` stem."""
    from ichalaunch.core.filesystem import toc_mismatch_prompt_text

    return question(
        parent,
        "Addon folder name mismatch",
        toc_mismatch_prompt_text(current_name, toc_name),
    )


def choice(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: list[tuple[str, DialogResult]],
    *,
    kind: str = "question",
) -> DialogResult:
    """Blocking multi-button prompt. Returns the clicked ``DialogResult``."""
    return _run(parent, title, text, kind=kind, buttons=buttons)


def prompt_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    placeholder: str = "",
    accept_text: str = "OK",
) -> str | None:
    """Blocking text prompt. Returns stripped text, or None if cancelled."""
    dlg = ThemedInputDialog(
        parent, title, label, placeholder=placeholder, accept_text=accept_text
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    text = dlg.text_value()
    return text or None


def confirm_preview(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    info: dict | None = None,
    readme_markdown: str = "",
    readme_base_url: str = "",
    readme_cache_dir: str = "",
    accept_text: str = "Add",
    cancel_text: str = "Cancel",
) -> bool:
    """Show a preview (summary + optional README) and confirm add/install."""
    dlg = ThemedPreviewDialog(
        parent,
        title,
        text,
        info=info,
        readme_markdown=readme_markdown,
        readme_base_url=readme_base_url,
        readme_cache_dir=readme_cache_dir,
        accept_text=accept_text,
        cancel_text=cancel_text,
    )
    dlg.exec()
    return dlg.result_value == DialogResult.Yes


class _CatalogSubmitThread(QThread):
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self._payload = payload

    def run(self) -> None:
        try:
            from ichalaunch.addons.submit import submit_catalog_suggestion

            self.ok.emit(submit_catalog_suggestion(self._payload))
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc) or exc.__class__.__name__)


class CatalogSuggestDialog(QDialog):
    """Suggest a GitHub addon for the shared Available catalog (no credentials)."""

    _DUPLICATE_MSG = (
        "This repository is already in the Available catalog. "
        "You do not need to suggest it again."
    )

    def __init__(
        self,
        parent: QWidget | None,
        *,
        categories: list[str] | None = None,
        catalog_entries: list | None = None,
        initial_repo: str = "",
    ):
        del categories  # category UI removed; suggestions default to General
        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(440, 220)
        self.resize(480, 240)
        self._worker: _CatalogSubmitThread | None = None
        self._duplicate = False
        self._result_ok = False
        self._success_message = ""
        self._catalog_entries = catalog_entries

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ThemedDialogCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(10)

        title_lbl = QLabel("Suggest for catalog")
        title_lbl.setObjectName("ThemedDialogTitle")
        body.addWidget(title_lbl)

        hint = QLabel(
            "Paste a public GitHub addon URL to propose it for the shared "
            "Available list. No GitHub login or token is used."
        )
        hint.setObjectName("ThemedDialogBody")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText("https://github.com/owner/repo")
        self.repo_edit.setClearButtonEnabled(True)
        if initial_repo.strip():
            self.repo_edit.setText(initial_repo.strip())
        self.repo_edit.textChanged.connect(self._refresh_repo_state)
        body.addWidget(self.repo_edit)

        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("SuggestStatusBanner")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.addWidget(self.status_lbl)
        self._set_status("Paste a GitHub repository URL.", kind="info")

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.cancel_btn = _dialog_glue_button("Cancel", card, primary=False)
        self.cancel_btn.clicked.connect(self.reject)
        self.suggest_btn = _dialog_glue_button("Suggest", card, primary=True)
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.clicked.connect(self._on_submit)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.suggest_btn)
        body.addLayout(row)

        root.addWidget(card)
        self.setStyleSheet(
            "QDialog#ThemedDialog { background: transparent; }"
            "QWidget#ThemedDialogCard {"
            "  background-color: #100d0c;"
            "  border: 1px solid rgba(150, 131, 158, 0.22);"
            "  border-top: 3px solid #F1C22D;"
            "  border-radius: 10px;"
            "}"
        )
        if initial_repo.strip():
            self._refresh_repo_state()

    def _set_status(self, text: str, *, kind: str = "info") -> None:
        """Loud banner status — warning gold / error red / ready green / info."""
        self.status_lbl.setText(text)
        styles = {
            "warning": (
                "color: #F1C22D;"
                "background-color: rgba(241, 194, 45, 0.16);"
                "border: 1px solid rgba(241, 194, 45, 0.55);"
            ),
            "error": (
                "color: #ff8a80;"
                "background-color: rgba(198, 40, 40, 0.22);"
                "border: 1px solid rgba(229, 115, 115, 0.65);"
            ),
            "ready": (
                "color: #8fd99a;"
                "background-color: rgba(76, 175, 80, 0.16);"
                "border: 1px solid rgba(129, 199, 132, 0.55);"
            ),
            "info": (
                "color: #e6e0ee;"
                "background-color: rgba(150, 131, 158, 0.14);"
                "border: 1px solid rgba(150, 131, 158, 0.35);"
            ),
        }
        tone = styles.get(kind, styles["info"])
        self.status_lbl.setStyleSheet(
            "QLabel#SuggestStatusBanner {"
            f"  {tone}"
            "  font-size: 14px;"
            "  font-weight: 700;"
            "  padding: 10px 12px;"
            "  border-radius: 8px;"
            "}"
        )

    def _check_duplicate(self, repo_text: str) -> bool:
        from ichalaunch.addons.submit import repo_in_catalog

        return repo_in_catalog(repo_text, self._catalog_entries)

    def _refresh_repo_state(self, _text: str = "") -> None:
        from ichalaunch.addons.github import parse_github_url
        from ichalaunch.addons.submit import normalize_repo_url

        raw = self.repo_edit.text().strip()
        if not raw:
            self._duplicate = False
            self._set_status("Paste a GitHub repository URL.", kind="info")
            self.suggest_btn.setEnabled(False)
            return
        if not parse_github_url(raw) or not normalize_repo_url(raw):
            self._duplicate = False
            self._set_status(
                "Enter a valid GitHub repository URL (github.com/owner/repo).",
                kind="warning",
            )
            self.suggest_btn.setEnabled(False)
            return

        if self._check_duplicate(raw):
            self._duplicate = True
            self._set_status(self._DUPLICATE_MSG, kind="warning")
            self.suggest_btn.setEnabled(False)
            return

        self._duplicate = False
        self._set_status("Ready to suggest.", kind="ready")
        self.suggest_btn.setEnabled(True)

    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.setEnabled(not busy)
        self.repo_edit.setEnabled(not busy)
        if busy:
            self.suggest_btn.setEnabled(False)
            return
        from ichalaunch.addons.github import parse_github_url
        from ichalaunch.addons.submit import normalize_repo_url

        raw = self.repo_edit.text().strip()
        can_suggest = (
            bool(raw)
            and not self._duplicate
            and bool(parse_github_url(raw))
            and bool(normalize_repo_url(raw))
        )
        self.suggest_btn.setEnabled(can_suggest)

    def _on_submit(self) -> None:
        from ichalaunch.addons.submit import build_submit_payload

        raw = self.repo_edit.text().strip()
        if self._check_duplicate(raw):
            self._duplicate = True
            self._set_status(self._DUPLICATE_MSG, kind="warning")
            self.suggest_btn.setEnabled(False)
            return

        payload, err = build_submit_payload(
            repo=raw,
            category="General",
            description="",
        )
        if err or payload is None:
            self._set_status(err or "Invalid suggestion.", kind="error")
            return

        self._set_status("Submitting…", kind="info")
        self._set_busy(True)
        worker = _CatalogSubmitThread(payload, self)
        self._worker = worker

        def on_ok(result: object) -> None:
            self._set_busy(False)
            ok = bool(getattr(result, "ok", False))
            msg = str(getattr(result, "message", "") or "")
            issue = str(getattr(result, "issue_url", "") or "").strip()
            if ok:
                self._result_ok = True
                text = msg or "Suggestion submitted."
                if issue:
                    text = f"{text}\n{issue}"
                self._success_message = text
                self.accept()
            else:
                self._set_status(msg or "Suggestion failed.", kind="error")

        def on_err(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg or "Suggestion failed.", kind="error")

        worker.ok.connect(on_ok)
        worker.err.connect(on_err)
        worker.start()

    @property
    def submitted(self) -> bool:
        return self._result_ok

    @property
    def success_message(self) -> str:
        return self._success_message


def catalog_suggest_dialog(
    parent: QWidget | None,
    *,
    categories: list[str] | None = None,
    catalog_entries: list | None = None,
    initial_repo: str = "",
    initial_name: str = "",  # retained for call-site compatibility; unused
) -> bool:
    """Open catalog suggestion dialog. Returns True if a suggestion was accepted."""
    del initial_name  # display name removed; name is derived from the repo slug
    dlg = CatalogSuggestDialog(
        parent,
        categories=categories,
        catalog_entries=catalog_entries,
        initial_repo=initial_repo,
    )
    dlg.exec()
    if dlg.submitted:
        info(
            parent,
            "Suggestion sent",
            dlg.success_message or "Suggestion submitted. Maintainers will review it.",
        )
        return True
    return False


def github_import_dialog(
    parent: QWidget | None,
    *,
    kind: str = "addon",
) -> str | None:
    """Open auto-preview GitHub import dialog. Returns confirmed URL, or None."""
    if kind == "dll":
        dlg = GitHubImportDialog(
            parent,
            kind="dll",
            title="Add DLL from GitHub",
            hint="Paste a GitHub repository URL that publishes a .dll or .zip release. "
            "The README loads automatically.",
            placeholder="https://github.com/owner/mod-repo",
            accept_text="Install",
        )
    else:
        dlg = GitHubImportDialog(
            parent,
            kind="addon",
            title="Add from GitHub",
            hint="Paste a GitHub repository URL (or a releases/tag link). "
            "The README loads automatically.",
            placeholder="https://github.com/owner/addon-repo or …/releases/tag/1.2.3",
            accept_text="Add",
        )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    url = dlg.selected_url()
    return url or None


def github_preview_dialog(parent: QWidget | None, url: str) -> None:
    """Show the GitHub README preview for *url* (no URL prompt, no install)."""
    text = (url or "").strip()
    if not text:
        return
    dlg = GitHubImportDialog(
        parent,
        kind="addon",
        title="Addon preview",
        hint="",
        placeholder="",
        accept_text="Close",
        view_only=True,
        initial_url=text,
    )
    dlg.exec()
