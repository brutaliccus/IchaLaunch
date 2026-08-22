import sys

# Install crash hooks before other imports when launched via run.py.
import ichalaunch.core.logging_setup  # noqa: F401

if __name__ == "__main__":
    if "--qt-smoke" in sys.argv:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication([])
        print("OK PySide6.QtGui")
        raise SystemExit(0)

    from ichalaunch.app import main

    raise SystemExit(main())
