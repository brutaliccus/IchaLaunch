"""IchaLaunch application entrypoint."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ichalaunch.core.logging_setup import log
from ichalaunch.ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    from ichalaunch.core.paths import theme_file

    qss = theme_file("stylesheet.qss")
    if not qss.exists():
        return
    text = qss.read_text(encoding="utf-8")
    chevron = theme_file("chevron-down.svg")
    if chevron.exists():
        # Qt QSS urls need forward slashes
        text = text.replace("__CHEVRON_DOWN__", chevron.resolve().as_posix())
    app.setStyleSheet(text)


def main() -> int:
    log.info("Starting IchaLaunch")
    app = QApplication(sys.argv)
    app.setApplicationName("IchaLaunch")
    app.setOrganizationName("IchasArmory")
    load_stylesheet(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
