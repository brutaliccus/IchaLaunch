"""Grab MainWindow pages to docs/screenshots/ for README / release notes.

Captures with an alpha channel so tab gutters, rounded bottom corners, and the
RavenCraft crest overhang stay transparent against the README background.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.app import load_app_icon, load_stylesheet
from ichalaunch.ui import main_window as main_window_mod
from ichalaunch.ui.main_window import MainWindow
from ichalaunch.ui.widgets.dialogs import ThemedDialog

OUT = ROOT / "docs" / "screenshots"
# (filename stem, nav index, show mid-fill ThemeLoadingBar)
SHOTS: tuple[tuple[str, int, bool], ...] = (
    ("home", 0, False),
    ("home-loading", 0, True),
    ("addons", 1, False),
    ("client", 2, False),
    ("settings", 3, False),
)
# Unique fill used only if render() composites opaque; punched back to alpha 0.
_CHROMA = QColor(0, 255, 1)
_CHROMA_TOL = 10
# Allow talent art / overlays / countdown to settle before the first grab.
_SETTLE_MS = 1800
_PAGE_PAUSE_MS = 400
_FAILSAFE_MS = 90_000
_MIN_TRANSPARENT_FRAC = 0.012


def _new_argb(widget: QWidget, fill: QColor) -> QImage:
    dpr = max(1.0, float(widget.devicePixelRatioF()))
    w = max(1, int(round(widget.width() * dpr)))
    h = max(1, int(round(widget.height() * dpr)))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(fill)
    return img


def _render_children(widget: QWidget, img: QImage) -> None:
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # Omit DrawWindowBackground so our transparent/chroma fill stays in the gutters.
    widget.render(
        painter,
        QPoint(0, 0),
        QRegion(),
        QWidget.RenderFlag.DrawChildren,
    )
    painter.end()


def _alpha_stats(img: QImage) -> tuple[int, int, float]:
    """Return (transparent-ish pixels, total, fraction) for alpha < 250."""
    src = img.convertToFormat(QImage.Format.Format_ARGB32)
    total = src.width() * src.height()
    if total <= 0:
        return 0, 0, 0.0
    clear = 0
    for y in range(src.height()):
        for x in range(src.width()):
            if src.pixelColor(x, y).alpha() < 250:
                clear += 1
    return clear, total, clear / total


def _punch_chroma(img: QImage, chroma: QColor, tolerance: int) -> QImage:
    """Set near-chroma pixels to alpha 0 (Windows grab / opaque-composite fallback)."""
    out = img.convertToFormat(QImage.Format.Format_ARGB32)
    cr, cg, cb = chroma.red(), chroma.green(), chroma.blue()
    tol = int(tolerance)
    for y in range(out.height()):
        for x in range(out.width()):
            c = out.pixelColor(x, y)
            if (
                abs(c.red() - cr) <= tol
                and abs(c.green() - cg) <= tol
                and abs(c.blue() - cb) <= tol
            ):
                c.setAlpha(0)
                out.setPixelColor(x, y, c)
    return out


def _recover_alpha(over_black: QImage, over_white: QImage) -> QImage:
    """Rebuild alpha from SourceOver onto black vs white (artwork-safe)."""
    black = over_black.convertToFormat(QImage.Format.Format_ARGB32)
    white = over_white.convertToFormat(QImage.Format.Format_ARGB32)
    out = QImage(black.size(), QImage.Format.Format_ARGB32)
    out.setDevicePixelRatio(black.devicePixelRatio())
    for y in range(black.height()):
        for x in range(black.width()):
            pb = black.pixelColor(x, y)
            pw = white.pixelColor(x, y)
            inv = (pw.red() - pb.red() + pw.green() - pb.green() + pw.blue() - pb.blue()) / 3.0
            inv = max(0.0, min(255.0, inv))
            a = 255.0 - inv
            if a < 1.5:
                out.setPixelColor(x, y, QColor(0, 0, 0, 0))
            elif a >= 253.0:
                out.setPixelColor(x, y, QColor(pb.red(), pb.green(), pb.blue(), 255))
            else:
                s = a / 255.0
                out.setPixelColor(
                    x,
                    y,
                    QColor(
                        min(255, int(round(pb.red() / s))),
                        min(255, int(round(pb.green() / s))),
                        min(255, int(round(pb.blue() / s))),
                        int(round(a)),
                    ),
                )
    return out


def grab_widget_alpha(widget: QWidget) -> tuple[QImage, str]:
    """Capture *widget* to ARGB32, preserving gutter / rounded-corner holes.

    Tries a transparent render first. If Windows flattens alpha, recovers it
    via black/white composites, then chroma punch as a last resort.
    """
    transparent = Qt.GlobalColor.transparent
    img = _new_argb(widget, QColor(transparent))
    _render_children(widget, img)
    _clear, _total, frac = _alpha_stats(img)
    if frac >= _MIN_TRANSPARENT_FRAC:
        return img, f"render-argb ({frac:.1%} transparent)"

    # Dual composite: pixels that change between black and white were see-through.
    over_black = _new_argb(widget, QColor(0, 0, 0, 255))
    _render_children(widget, over_black)
    over_white = _new_argb(widget, QColor(255, 255, 255, 255))
    _render_children(widget, over_white)
    recovered = _recover_alpha(over_black, over_white)
    _clear, _total, frac = _alpha_stats(recovered)
    if frac >= _MIN_TRANSPARENT_FRAC:
        return recovered, f"black-white matte ({frac:.1%} transparent)"

    chroma_img = _new_argb(widget, _CHROMA)
    _render_children(widget, chroma_img)
    punched = _punch_chroma(chroma_img, _CHROMA, _CHROMA_TOL)
    _clear, _total, frac = _alpha_stats(punched)
    return punched, f"chroma punch ({frac:.1%} transparent)"


def _set_loading_bar(win: MainWindow, on: bool) -> None:
    bar = win.progress
    if on:
        bar.show()
        bar.setRange(0, 100)
        bar.setValue(55)
        bar.setFormat("%p%")
        win.status_lbl.setText("Checking addon updates…")
    else:
        bar.hide()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFormat("%p%")
        win.status_lbl.setText("Ready")


def _save_png(img: QImage, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(dest), "PNG"):
        raise RuntimeError(f"Failed to write {dest}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # Avoid mid-scan progress chrome except the dedicated loading-bar shot.
    main_window_mod._STARTUP_UPDATE_DELAY_MS = 24 * 60 * 60 * 1000
    app = QApplication(sys.argv)
    app.setApplicationName("IchaLaunch")
    load_stylesheet(app)
    icon = load_app_icon(app)

    # Capture must not block on first-run privacy dialogs.
    MainWindow._run_startup_opt_in_prompts = lambda self: None  # type: ignore[method-assign]
    MainWindow._flush_pending_toc_mismatch_prompt = lambda self: None  # type: ignore[method-assign]

    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.resize(1080, 720)
    win.show()
    app.processEvents()

    QTimer.singleShot(_FAILSAFE_MS, app.quit)

    def _freeze_featured_slide() -> None:
        """Hold the first Zaeya slide so Home shots match the 1.5 well."""
        bg = win.home.talent_bg
        fade = getattr(bg, "_fade", None)
        if fade is not None:
            fade.stop()
            bg._fade = None
        bg._timer.stop()
        if bg.slide_count() > 0 and bg.display_index() != 0:
            bg.go_to(0)
            bg._timer.stop()
        win.home._sync_brand_layout()

    def grab_pages(idx: int = 0) -> None:
        if idx >= len(SHOTS):
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
            dlg_img, how = grab_widget_alpha(dlg)
            dlg_path = OUT / "themed_dialog.png"
            _save_png(dlg_img, dlg_path)
            print(f"Wrote {dlg_path} via {how}")
            dlg.close()
            win.close()
            app.quit()
            return

        name, page, loading = SHOTS[idx]
        win._nav(page)
        _set_loading_bar(win, loading)
        if page == 0:
            _freeze_featured_slide()
        app.processEvents()
        win._position_frame_stroke()
        win._position_rc_logo()
        win._position_chrome_buttons()
        app.processEvents()

        def snap() -> None:
            app.processEvents()
            target = win.centralWidget() or win
            img, how = grab_widget_alpha(target)
            dest = OUT / f"{name}.png"
            _save_png(img, dest)
            print(f"Wrote {dest} via {how} ({img.width()}x{img.height()})")
            grab_pages(idx + 1)

        QTimer.singleShot(_PAGE_PAUSE_MS, snap)

    QTimer.singleShot(_SETTLE_MS, grab_pages)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
