"""Themed modal dialogs matching the RavenCraft launcher look."""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
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


def _addon_fork_version_row(
    parent: QWidget,
    *,
    fork_combo,
    version_combo,
    trailing_widget: QWidget | None = None,
) -> QHBoxLayout:
    """Fork + version combos; optional trailing control sits right of Version."""
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
    if trailing_widget is not None:
        row.addWidget(trailing_widget, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    return row


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

        raw = self.url_edit.text().strip()
        if not raw:
            self._reset_preview("Paste a GitHub link to load the preview.")
            return
        if not parse_github_url(raw):
            self._reset_preview("Enter a valid GitHub repository URL (github.com/owner/repo).")
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

  def __init__(self, owner: str, repo: str, parent: QWidget | None = None):
    super().__init__(parent)
    self._owner = owner
    self._repo = repo

  def run(self) -> None:
    try:
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

    name = str(entry.get("name") or entry.get("folder") or "addon")
    version_text = addon_version_label(entry)

    root = QVBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)

    card = QWidget()
    card.setObjectName("ThemedDialogCard")
    body = QVBoxLayout(card)
    body.setContentsMargins(22, 18, 22, 18)
    body.setSpacing(10)

    title_lbl = QLabel(f"Install {name}")
    title_lbl.setObjectName("ThemedDialogTitle")
    body.addWidget(title_lbl)

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
    body.addLayout(_addon_fork_version_row(card, fork_combo=self.fork_combo, version_combo=self.version_combo))
    self._set_browse_combos_enabled(False, loading=True)

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
    can_install = bool(self._preview_url())
    self.install_btn.setEnabled(can_install)
    if pair:
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
      self.status_lbl.setText("Could not resolve a GitHub repository for this addon.")

    QTimer.singleShot(0, self._load_preview)

  def _set_browse_combos_enabled(self, enabled: bool, *, loading: bool = False) -> None:
    for combo in (self.fork_combo, self.version_combo):
      try:
        combo.hidePopup()
      except RuntimeError:
        pass
      combo.setEnabled(bool(enabled))
    if enabled:
      self.fork_combo.setToolTip("")
      self.version_combo.setToolTip("")
    elif loading:
      self.fork_combo.setToolTip(_LOADING_FORKS_TIP)
      self.version_combo.setToolTip(_LOADING_VERSIONS_TIP)

  def _open_preview_link(self, url: QUrl) -> None:
    QDesktopServices.openUrl(url)

  def _version_tag(self) -> str:
    return str(self.version_combo.currentData() or "").strip()

  def _preview_url(self) -> str:
    from ichalaunch.addons.github import addon_install_url_for_choice, github_browse_url
    from ichalaunch.ui.widgets.common import github_repo_browse_url

    fork = self._current_fork_data()
    tag = self._version_tag()
    install = addon_install_url_for_choice(fork, tag or None)
    if install:
      return install
    owner = str(fork.get("owner") or self._browse_owner or "").strip()
    repo = str(fork.get("repo_name") or self._browse_repo or "").strip()
    if owner and repo:
      return github_browse_url(owner, repo)
    return github_repo_browse_url(
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
      return
    if url == self._preview_url_loaded and self.browser.toPlainText().strip():
      return
    if url == self._preview_url_loading:
      return
    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""
    self._preview_url_loading = url
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
      if not isinstance(info, dict):
        if not (self._worker and self._worker.isRunning()):
          self.status_lbl.setText("Preview failed.")
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

    def on_err(msg: str) -> None:
      if gen != self._preview_gen:
        return
      if self._preview_url_loading == url:
        self._preview_url_loading = ""
      if not (self._worker and self._worker.isRunning()):
        self.status_lbl.setText(msg or "Preview failed.")

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
    self._set_browse_combos_enabled(True)
    self.status_lbl.setText("")
    self.install_btn.setEnabled(bool(self._preview_url()))
    after_url = self._preview_url()
    if after_url != before_url:
      self._load_preview()

  def _on_fetch_err(self, message: str, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._set_browse_combos_enabled(True)
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

  def _accept_install(self) -> None:
    out = self._build_result_entry()
    if not out:
      return
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
    from ichalaunch.ui.widgets.common import (
      addon_fork_label,
      fork_combo_label,
      addon_version_label,
      github_repo_browse_url,
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

    name = str(entry.get("name") or entry.get("folder") or "addon")
    root = QVBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)

    card = QWidget()
    card.setObjectName("ThemedDialogCard")
    body = QVBoxLayout(card)
    body.setContentsMargins(22, 18, 22, 18)
    body.setSpacing(10)

    title_lbl = QLabel(name)
    title_lbl.setObjectName("ThemedDialogTitle")
    body.addWidget(title_lbl)

    fork_text = addon_fork_label(entry)
    version_text = addon_version_label(entry, self._meta)
    self._fork_combo = None
    self._version_combo = None
    self._open_git_btn = None

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
      self._open_git_btn = self._make_open_git_button(card)
      body.addLayout(
        _addon_fork_version_row(
          card,
          fork_combo=self._fork_combo,
          version_combo=self._version_combo,
          trailing_widget=self._open_git_btn,
        )
      )
      self._set_fork_combo_interactive(False, loading=True)
      self._set_version_combo_interactive(False)
    else:
      meta_row = QHBoxLayout()
      meta_row.setSpacing(10)
      meta_line = " · ".join(x for x in (fork_text, version_text) if x)
      if meta_line:
        static = QLabel(meta_line)
        static.setObjectName("Muted")
        static.setToolTip(NO_TOKEN_FORK_TIP)
        meta_row.addWidget(static, 0, Qt.AlignmentFlag.AlignVCenter)
      self._open_git_btn = self._make_open_git_button(card)
      if self._open_git_btn is not None:
        meta_row.addWidget(self._open_git_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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
    row.addStretch(1)
    close_btn = _dialog_glue_button("Close", card, primary=False)
    close_btn.clicked.connect(self.reject)
    row.addWidget(close_btn)
    if self._token_ok:
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
    pair = parse_entry_owner_repo(entry, self._meta)
    if pair:
      self._browse_owner, self._browse_repo = pair
    QTimer.singleShot(0, self._load_preview)
    if self._token_ok and self._fork_combo is not None:
      QTimer.singleShot(0, self._prefetch_forks)

  def _make_open_git_button(self, parent: QWidget):
    """Open in Git control placed to the right of Version."""
    from ichalaunch.ui.widgets.common import (
      apply_open_git_visibility,
      github_repo_browse_url,
    )
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton

    url = github_repo_browse_url(
      self._entry.get("repo"),
      self._entry.get("url"),
      self._entry.get("repository"),
    )
    if not url:
      return None
    btn = GluePanelButton("Open in Git", parent, width=128, height=GLUE_BTN_H)
    btn.setToolTip("Open the repository in your browser")
    btn.clicked.connect(lambda _=False, u=url: self._open_git_url(u))
    apply_open_git_visibility(btn, url, self, defer=True)
    return btn

  def _open_git_url(self, url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    if url:
      QDesktopServices.openUrl(QUrl(url))


  def _set_fork_combo_interactive(self, enabled: bool, *, loading: bool = False) -> None:
    if self._fork_combo is None:
      return
    try:
      self._fork_combo.hidePopup()
    except RuntimeError:
      pass
    self._fork_combo.setEnabled(bool(enabled))
    if enabled:
      self._fork_combo.setToolTip("")
    elif loading:
      self._fork_combo.setToolTip(_LOADING_FORKS_TIP)

  def _set_version_combo_interactive(self, enabled: bool, *, loading: bool = False) -> None:
    if self._version_combo is None:
      return
    try:
      self._version_combo.hidePopup()
    except RuntimeError:
      pass
    self._version_combo.setEnabled(bool(enabled))
    if enabled:
      self._version_combo.setToolTip("")
    elif loading:
      self._version_combo.setToolTip(_LOADING_VERSIONS_TIP)

  def _open_preview_link(self, url: QUrl) -> None:
    QDesktopServices.openUrl(url)

  def _preview_url(self) -> str:
    from ichalaunch.addons.github import addon_install_url_for_choice, github_browse_url
    from ichalaunch.ui.widgets.common import github_repo_browse_url

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
      return github_browse_url(owner, repo)
    return github_repo_browse_url(
      self._entry.get("repo"),
      self._entry.get("url"),
      self._entry.get("repository"),
    ) or ""

  def _load_preview(self) -> None:
    from ichalaunch.addons.github import cleanup_readme_cache

    url = self._preview_url()
    if not url:
      self.status_lbl.setText("No GitHub repository URL for preview.")
      self.browser.clear()
      return
    cleanup_readme_cache(self._cache_dir)
    self._cache_dir = ""
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
      if not isinstance(info, dict):
        self.status_lbl.setText("Preview failed.")
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

    def on_err(msg: str) -> None:
      if gen != self._preview_gen:
        return
      self.status_lbl.setText(msg or "Preview failed.")

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
      self._set_fork_combo_interactive(True)
      self._set_version_combo_interactive(True)
      return
    if self._fork_fetch_pending:
      return
    cached = get_cached_repo_forks(owner, repo)
    if cached:
      self._populate_forks(cached)
      self._forks_loaded = True
      self._set_fork_combo_interactive(True)
      self._set_version_combo_interactive(True)
      return
    self._fork_fetch_pending = True
    self._browse_fetch_gen += 1
    fetch_gen = self._browse_fetch_gen
    self._set_fork_combo_interactive(False, loading=True)
    self._set_version_combo_interactive(False)
    self.status_lbl.setText("Loading forks from GitHub…")
    self._browse_worker = _AddonBrowseFetchThread(owner, repo, self)
    self._browse_worker.ok.connect(
      lambda forks, versions, g=fetch_gen: self._on_forks_fetched(forks, versions, g)
    )
    self._browse_worker.err.connect(
      lambda msg, g=fetch_gen: self._on_browse_err(msg, g, kind="forks")
    )
    self._browse_worker.start()

  def _lazy_fetch_versions(self) -> None:
    if not self._token_ok or self._version_combo is None or self._versions_loaded:
      return
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or self._browse_owner or "").strip()
    repo = str(fork.get("repo_name") or self._browse_repo or "").strip()
    if not owner or not repo:
      return
    if self._version_fetch_pending:
      return
    from ichalaunch.addons.github import get_cached_repo_versions

    cached = get_cached_repo_versions(owner, repo)
    if cached:
      self._populate_versions(cached)
      self._versions_loaded = True
      self._set_version_combo_interactive(True)
      return
    self._version_fetch_pending = True
    self._browse_fetch_gen += 1
    fetch_gen = self._browse_fetch_gen
    self._set_version_combo_interactive(False, loading=True)
    self.status_lbl.setText("Loading versions from GitHub…")
    self._browse_worker = _AddonBrowseFetchThread(owner, repo, self)
    self._browse_worker.ok.connect(
      lambda forks, versions, g=fetch_gen: self._on_versions_fetched(forks, versions, g)
    )
    self._browse_worker.err.connect(
      lambda msg, g=fetch_gen: self._on_browse_err(msg, g, kind="versions")
    )
    self._browse_worker.start()

  def _on_forks_fetched(self, forks: object, _versions: object, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._fork_fetch_pending = False
    if isinstance(forks, list):
      self._populate_forks(forks)
      self._forks_loaded = True
    self._set_fork_combo_interactive(True)
    if not self._versions_loaded:
      self._set_version_combo_interactive(True)
    self.status_lbl.setText("")

  def _on_versions_fetched(self, _forks: object, versions: object, fetch_gen: int) -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    self._version_fetch_pending = False
    if isinstance(versions, list):
      self._populate_versions(versions)
      self._versions_loaded = True
    self._set_version_combo_interactive(True)
    self.status_lbl.setText("")

  def _on_browse_err(self, message: str, fetch_gen: int, *, kind: str = "") -> None:
    if fetch_gen != self._browse_fetch_gen:
      return
    if kind == "forks":
      self._fork_fetch_pending = False
      self._set_fork_combo_interactive(True)
      if not self._versions_loaded:
        self._set_version_combo_interactive(True)
    elif kind == "versions":
      self._version_fetch_pending = False
      self._set_version_combo_interactive(True)
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

  def _populate_versions(self, versions: list) -> None:
    if self._version_combo is None:
      return
    self._version_combo.hidePopup()
    pin = str(self._version_combo.currentData() or "").strip()
    if not pin:
      pin = str(self._entry.get("pin_release") or self._entry.get("tag") or "").strip()
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

  def _on_fork_changed(self, _index: int) -> None:
    fork = self._current_fork_data()
    owner = str(fork.get("owner") or "").strip()
    repo = str(fork.get("repo_name") or "").strip()
    if owner and repo:
      self._browse_owner = owner
      self._browse_repo = repo
    self._versions_loaded = False
    self._set_version_combo_interactive(True)
    self._load_preview()

  def _on_version_changed(self, _index: int) -> None:
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
    return out

  def _accept_save(self) -> None:
    self._result = self._build_result_entry()
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
        from ichalaunch.ui.widgets.glue_combo import GlueComboBox

        super().__init__(parent)
        self.setObjectName("ThemedDialog")
        self.setWindowFlags(_themed_dialog_flags())
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self.resize(640, 560)
        self._worker: _CatalogSubmitThread | None = None
        self._preview_worker: _PreviewFetchThread | None = None
        self._preview_gen = 0
        self._cache_dir = ""
        self._preview_info: dict | None = None
        self._readme_for_submit = ""
        self._duplicate = False
        self._result_ok = False
        self._success_message = ""
        self._catalog_entries = catalog_entries

        cats = [c for c in (categories or []) if str(c).strip()]
        if not cats:
            cats = [
                "Bags",
                "Client",
                "Combat",
                "Economy",
                "General",
                "Hardcore",
                "Maps",
                "Professions",
                "PvP",
                "Questing",
                "Raiding",
                "Recommended",
                "Roleplay",
                "SuperWoW",
                "UI",
            ]
        if "General" not in cats:
            cats = ["General", *cats]

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
            "Propose a public GitHub addon for the shared Available list. "
            "The README is used as the suggestion description — no typing needed. "
            "No GitHub login or token is used."
        )
        hint.setObjectName("ThemedDialogBody")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText("https://github.com/owner/repo (required)")
        self.repo_edit.setClearButtonEnabled(True)
        if initial_repo.strip():
            self.repo_edit.setText(initial_repo.strip())
        self.repo_edit.textChanged.connect(self._on_repo_changed)
        self.repo_edit.editingFinished.connect(self._on_repo_blur)
        body.addWidget(self.repo_edit)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(10)
        cat_lbl = QLabel("Category")
        cat_lbl.setObjectName("Muted")
        self.cat_box = GlueComboBox(card, min_width=160)
        for c in cats:
            self.cat_box.addItem(c)
        idx = self.cat_box.findText("General")
        if idx >= 0:
            self.cat_box.setCurrentIndex(idx)
        cat_row.addWidget(cat_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        cat_row.addWidget(self.cat_box, 0, Qt.AlignmentFlag.AlignVCenter)
        cat_row.addStretch(1)
        body.addLayout(cat_row)

        self.browser = QTextBrowser()
        self.browser.setObjectName("ThemedPreviewBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        self.browser.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.addWidget(self.browser, 1)

        # Loud status banner above Submit (not muted subtitle).
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("SuggestStatusBanner")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.addWidget(self.status_lbl)
        self._set_status(
            "Paste a GitHub link to load the README preview.",
            kind="info",
        )

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.cancel_btn = _dialog_glue_button("Cancel", card, primary=False)
        self.cancel_btn.clicked.connect(self.reject)
        self.submit_btn = _dialog_glue_button("Submit", card, primary=True)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_submit)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.submit_btn)
        body.addLayout(row)

        root.addWidget(card)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self._refresh_repo_state)
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
            "  selection-background-color: #4a2f7a;"
            "  selection-color: #ffffff;"
            "}"
        )
        if initial_repo.strip():
            QTimer.singleShot(0, self._refresh_repo_state)

    def _open_link(self, url: QUrl) -> None:
        if url.isValid():
            QDesktopServices.openUrl(url)

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

    def _on_repo_changed(self, _text: str = "") -> None:
        self._debounce.start()

    def _on_repo_blur(self) -> None:
        self._debounce.stop()
        self._refresh_repo_state()

    def _clear_preview(self, status: str, *, kind: str = "info") -> None:
        from ichalaunch.addons.github import cleanup_readme_cache

        self._preview_gen += 1
        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        self._preview_info = None
        self._readme_for_submit = ""
        self.browser.clear()
        self._set_status(status, kind=kind)
        self.submit_btn.setEnabled(False)

    def _check_duplicate(self, repo_text: str) -> bool:
        from ichalaunch.addons.submit import repo_in_catalog

        return repo_in_catalog(repo_text, self._catalog_entries)

    def _refresh_repo_state(self) -> None:
        from ichalaunch.addons.github import cleanup_readme_cache, parse_github_url
        from ichalaunch.addons.submit import normalize_repo_url

        raw = self.repo_edit.text().strip()
        if not raw:
            self._duplicate = False
            self._clear_preview(
                "Paste a GitHub link to load the README preview.",
                kind="info",
            )
            return
        if not parse_github_url(raw) or not normalize_repo_url(raw):
            self._duplicate = False
            self._clear_preview(
                "Enter a valid GitHub repository URL (github.com/owner/repo).",
                kind="warning",
            )
            return

        if self._check_duplicate(raw):
            self._duplicate = True
            self._clear_preview(self._DUPLICATE_MSG, kind="warning")
            return

        self._duplicate = False
        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        self._preview_info = None
        self._readme_for_submit = ""
        self.submit_btn.setEnabled(False)
        self._set_status("Loading README preview…", kind="info")
        self.browser.clear()

        self._preview_gen += 1
        gen = self._preview_gen
        worker = _PreviewFetchThread("addon", raw, self)
        self._preview_worker = worker

        def on_ok(info: object) -> None:
            if gen != self._preview_gen:
                if isinstance(info, dict):
                    cleanup_readme_cache(info.get("readme_cache_dir"))
                return
            if not isinstance(info, dict):
                self._set_status("Preview failed.", kind="error")
                return
            # Re-check in case catalog changed; URL is the source of truth.
            if self._check_duplicate(raw):
                self._duplicate = True
                cleanup_readme_cache(info.get("readme_cache_dir"))
                self._clear_preview(self._DUPLICATE_MSG, kind="warning")
                return
            self._apply_preview(info)

        def on_err(msg: str) -> None:
            if gen != self._preview_gen:
                return
            self._preview_info = None
            self._readme_for_submit = ""
            self.browser.setPlainText("(Could not load README preview.)")
            self._set_status(msg or "Preview failed.", kind="error")
            # Allow submit with empty description if the repo URL itself is valid.
            self.submit_btn.setEnabled(not self._duplicate)

        worker.ok.connect(on_ok)
        worker.err.connect(on_err)
        worker.start()

    def _apply_preview(self, info: dict) -> None:
        self._preview_info = info
        self._cache_dir = str(info.get("readme_cache_dir") or "")
        raw_md = str(info.get("readme_raw") or "").strip()
        preview_md = str(info.get("readme_markdown") or "").strip()
        gh_desc = str(info.get("description") or "").strip()
        if gh_desc in {"(no description)", ""}:
            gh_desc = ""
        self._readme_for_submit = raw_md or gh_desc

        base = str(info.get("readme_base_url") or "")
        if base:
            self.browser.document().setBaseUrl(QUrl(base))
        if preview_md:
            self.browser.setMarkdown(preview_md)
            self._set_status(
                "README preview ready — submit to suggest this addon.",
                kind="ready",
            )
        elif gh_desc:
            self.browser.setPlainText(gh_desc)
            self._set_status(
                "No README found — GitHub description will be used.",
                kind="warning",
            )
        else:
            self.browser.setPlainText("(No README found for this repository.)")
            self._set_status(
                "No README found — you can still submit (description will be empty).",
                kind="warning",
            )
        self.submit_btn.setEnabled(True)

    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.setEnabled(not busy)
        self.repo_edit.setEnabled(not busy)
        self.cat_box.setEnabled(not busy)
        if busy:
            self.submit_btn.setEnabled(False)
        else:
            self.submit_btn.setEnabled(not self._duplicate and bool(self.repo_edit.text().strip()))

    def _on_submit(self) -> None:
        from ichalaunch.addons.submit import build_submit_payload

        raw = self.repo_edit.text().strip()
        if self._check_duplicate(raw):
            self._duplicate = True
            self._set_status(self._DUPLICATE_MSG, kind="warning")
            self.submit_btn.setEnabled(False)
            return

        payload, err = build_submit_payload(
            repo=raw,
            category=self.cat_box.currentText(),
            description=self._readme_for_submit,
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

    def closeEvent(self, event) -> None:  # noqa: N802
        from ichalaunch.addons.github import cleanup_readme_cache

        self._preview_gen += 1
        cleanup_readme_cache(self._cache_dir)
        self._cache_dir = ""
        if self._preview_info and self._preview_info.get("readme_cache_dir"):
            cleanup_readme_cache(self._preview_info.get("readme_cache_dir"))
        super().closeEvent(event)

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
