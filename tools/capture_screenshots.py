"""Grab MainWindow pages to docs/screenshots/ for README / release notes."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.app import load_app_icon, load_stylesheet
from ichalaunch.ui.main_window import MainWindow
from ichalaunch.ui.widgets.dialogs import ThemedDialog

OUT = ROOT / "docs" / "screenshots"
PAGES = ("home", "addons", "client", "settings")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("IchaLaunch")
    load_stylesheet(app)
    icon = load_app_icon(app)

    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)
    win.resize(1080, 720)
    win.show()
    app.processEvents()

    def finish() -> None:
        for idx, name in enumerate(PAGES):
            win._nav(idx)
            app.processEvents()
            # Let layout / countdown paint settle
            app.processEvents()
            pix = win.grab()
            dest = OUT / f"{name}.png"
            pix.save(str(dest), "PNG")
            print(f"Wrote {dest}")

        dlg = ThemedDialog(
            win,
            "Ready",
            "Client path saved.\nThis is the RavenCraft-themed dialog style.",
            kind="info",
        )
        dlg.show()
        app.processEvents()
        dlg.adjustSize()
        app.processEvents()
        dlg_pix = dlg.grab()
        dlg_path = OUT / "themed_dialog.png"
        dlg_pix.save(str(dlg_path), "PNG")
        print(f"Wrote {dlg_path}")
        dlg.close()

        win.close()
        app.quit()

    QTimer.singleShot(400, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
