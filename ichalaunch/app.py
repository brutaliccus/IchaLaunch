"""IchaLaunch application entrypoint."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import theme_file
from ichalaunch.ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    qss = theme_file("stylesheet.qss")
    if not qss.exists():
        return
    text = qss.read_text(encoding="utf-8")
    chevron = theme_file("chevron-down.svg")
    if chevron.exists():
        # Qt QSS urls need forward slashes
        text = text.replace("__CHEVRON_DOWN__", chevron.resolve().as_posix())
    app.setStyleSheet(text)


def load_app_icon(app: QApplication) -> QIcon | None:
    for name in ("ichalaunch.ico", "ichalaunch.png"):
        path = theme_file(name)
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                app.setWindowIcon(icon)
                return icon
    return None


def main() -> int:
    log.info("Starting IchaLaunch")
    app = QApplication(sys.argv)
    app.setApplicationName("IchaLaunch")
    app.setOrganizationName("IchasArmory")
    load_stylesheet(app)
    icon = load_app_icon(app)
    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
