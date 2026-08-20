"""Launch countdown matching ravencraft.io (Aug 22, 2026 18:00 UTC)."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# Same target as https://ravencraft.io/
LAUNCH_UTC = datetime(2026, 8, 22, 18, 0, 0, tzinfo=timezone.utc)


class _CountCell(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("CountCell")
        self.setMinimumWidth(78)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num = QLabel("00")
        self.num.setObjectName("CountNum")
        self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(label.upper())
        lbl.setObjectName("CountLabel")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.num)
        lay.addWidget(lbl)

    def set_value(self, n: int) -> None:
        self.num.setText(f"{max(0, n):02d}")


class LaunchCountdown(QWidget):
    """Days / Hours / Minutes / Seconds until Ravencraft launch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LaunchCountdown")
        root = QVBoxLayout(self)
        # Compact padding — countdown lives under the HOME logo, not above the banner.
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("LAUNCH COUNTDOWN")
        title.setObjectName("CountdownTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setSpacing(14)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.days = _CountCell("Days")
        self.hours = _CountCell("Hours")
        self.mins = _CountCell("Minutes")
        self.secs = _CountCell("Seconds")
        for c in (self.days, self.hours, self.mins, self.secs):
            row.addWidget(c)

        self.live_lbl = QLabel("RavenCraft is live")
        self.live_lbl.setObjectName("CountdownLive")
        self.live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_lbl.setVisible(False)

        root.addWidget(title)
        root.addLayout(row)
        root.addWidget(self.live_lbl)

        self._cells_row = row
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        diff = (LAUNCH_UTC - now).total_seconds()
        if diff <= 0:
            self.days.setVisible(False)
            self.hours.setVisible(False)
            self.mins.setVisible(False)
            self.secs.setVisible(False)
            self.live_lbl.setVisible(True)
            self._timer.stop()
            return
        sec = int(diff)
        self.days.set_value(sec // 86400)
        self.hours.set_value((sec % 86400) // 3600)
        self.mins.set_value((sec % 3600) // 60)
        self.secs.set_value(sec % 60)
