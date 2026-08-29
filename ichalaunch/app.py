"""IchaLaunch application entrypoint."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import theme_file
from ichalaunch.ui.theme_fonts import body_family, chrome_family, label_family
from ichalaunch.ui.widgets.splash import SplashScreen, load_splash_pixmap


def load_stylesheet(app: QApplication) -> None:
    # Register the bundled face first: the sheet names it, and a family Qt has
    # not seen yet is silently skipped rather than applied on the next repaint.
    chrome_family()

    qss = theme_file("stylesheet.qss")
    if not qss.exists():
        return
    text = qss.read_text(encoding="utf-8")
    # Same substitution the chevron below uses. Without it the sheet pins a
    # family while the painted chrome follows the override, so naming a font
    # dresses the tabs and buttons and leaves every heading behind.
    text = text.replace("__CHROME_FAMILY__", chrome_family())
    text = text.replace("__BODY_FAMILY__", body_family())
    text = text.replace("__LABEL_FAMILY__", label_family())

    # Theme directory, so the sheet can reference bundled art by name instead of
    # every asset needing its own placeholder the way the chevron does.
    theme_dir = theme_file("chevron-down.svg").parent
    text = text.replace("__THEME__", theme_dir.resolve().as_posix())

    chevron = theme_file("chevron-down.svg")
    if chevron.exists():
        # Qt QSS urls need forward slashes
        text = text.replace("__CHEVRON_DOWN__", chevron.resolve().as_posix())
    app.setStyleSheet(text)


def load_app_icon(app: QApplication) -> QIcon | None:
    for name in ("ravencraft_icon.ico", "ravencraft_icon.png"):
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

    splash: SplashScreen | None = None
    try:
        # Show splash before importing / constructing the heavy main window.
        splash_pm = load_splash_pixmap()
        if not splash_pm.isNull():
            splash = SplashScreen(splash_pm)
            splash.show()
            app.processEvents()

        load_stylesheet(app)
        icon = load_app_icon(app)

        from ichalaunch.ui.main_window import MainWindow

        win = MainWindow()
        if icon is not None:
            win.setWindowIcon(icon)

        # Local list build under the splash — not network scans. Keeps
        # launch → Addons → Check Updates off the first-open rebuild race.
        try:
            win.prepare_addon_lists_before_show()
        except Exception:  # noqa: BLE001
            log.exception("Addon list preload before show failed")

        if splash is not None:
            splash.finish(win)
        else:
            win.show()
        return app.exec()
    except Exception:
        if splash is not None:
            splash.finish()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
