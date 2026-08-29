"""A display line with a smaller qualifier beneath it.

The shape every card on ravencraft.io uses: the name, then a quieter line
underneath in the same face saying which part of it you are looking at. The
launcher's headings were all single lines, so a heading and a plain label read
at the same weight even though one names a section and the other is content.

The second line is only ever a fact the caller already has. A lockup with
invented subtitle text would be decoration wearing the shape of information.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ichalaunch.ui.widgets.gradient_label import AnimatedLavaLabel


class TitleLockup(QWidget):
    """Two stacked labels; the subtitle is hidden when there is nothing to say."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        title_name: str = "SectionTitle",
        subtitle_name: str = "SectionSubtitle",
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Parent immediately. A parentless QLabel is a real HWND; setVisible(True)
        # before addWidget() flashes a mini top-level window on Home refresh.
        # The title carries the animated ramp; the subtitle stays plain, so a
        # lockup reads as one heading with a quiet qualifier under it rather
        # than as two things both asking for attention.
        self.title = AnimatedLavaLabel(title, self)
        self.title.setObjectName(title_name)
        layout.addWidget(self.title)

        self.subtitle = QLabel(subtitle, self)
        self.subtitle.setObjectName(subtitle_name)
        layout.addWidget(self.subtitle)
        self.subtitle.setVisible(bool(subtitle))

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))
