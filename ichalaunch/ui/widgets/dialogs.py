"""Themed modal dialogs matching the RavenCraft launcher look."""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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
        self.setMaximumWidth(560)
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
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
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
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(accept_text)
        ok_btn.setObjectName("ThemedDialogPrimary")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
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
