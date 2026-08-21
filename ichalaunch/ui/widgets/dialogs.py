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
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.ui.widgets.cursors import apply_open_hand


class DialogResult(Enum):
    Yes = auto()
    No = auto()
    Ok = auto()
    Cancel = auto()


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
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
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
        for label, result in buttons:
            btn = QPushButton(label)
            if result in (DialogResult.Yes, DialogResult.Ok):
                btn.setObjectName("ThemedDialogPrimary")
            else:
                btn.setObjectName("ThemedDialogSecondary")
            apply_open_hand(btn)
            btn.clicked.connect(lambda _checked=False, r=result: self._finish(r))
            row.addWidget(btn)
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
        if result in (DialogResult.Yes, DialogResult.Ok):
            self.accept()
        else:
            self.reject()

    @property
    def result_value(self) -> DialogResult:
        return self._result


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
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
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
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ThemedDialogSecondary")
        apply_open_hand(cancel_btn)
        cancel_btn.clicked.connect(lambda: self._finish(DialogResult.Cancel))
        cancel_btn.setVisible(not self._view_only)
        self.accept_btn = QPushButton(accept_text)
        self.accept_btn.setObjectName("ThemedDialogPrimary")
        apply_open_hand(self.accept_btn)
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
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
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
        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setObjectName("ThemedDialogSecondary")
        apply_open_hand(cancel_btn)
        cancel_btn.clicked.connect(lambda: self._finish(DialogResult.Cancel))
        ok_btn = QPushButton(accept_text)
        ok_btn.setObjectName("ThemedDialogPrimary")
        apply_open_hand(ok_btn)
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
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
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
        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setObjectName("ThemedDialogSecondary")
        apply_open_hand(cancel_btn)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(accept_text)
        ok_btn.setObjectName("ThemedDialogPrimary")
        apply_open_hand(ok_btn)
        ok_btn.clicked.connect(self.accept)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        body.addLayout(row)

        root.addWidget(card)

    def text_value(self) -> str:
        return self.edit.text().strip()


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
