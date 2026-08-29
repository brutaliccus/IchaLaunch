"""Bundled display face for the launcher's chrome.

Lives outside main_window so painted widgets can reach it without importing the
window they sit in.

Why a bundled face at all
-------------------------
The stylesheet asked for "Segoe UI", "Candara", "Calibri" and four painted
widgets hardcoded the first of those. All three are Windows fonts, so every
Linux user has been reading the launcher in whatever fontconfig considers
generic sans while Windows users saw the intended one. Shipping the face makes
both platforms agree instead of leaving it to what the machine happens to have.

Why Cinzel
----------
SIL Open Font License 1.1 (OFL-Cinzel.txt sits beside the files), which permits
bundling inside an application, including one distributed commercially.

It is used for chrome only - tabs, buttons, section headings. Body copy, mod and
addon rows, tooltips and combo boxes stay in the sans they were, because an
inscriptional Roman face is excellent at HOME ADDONS CLIENT SETTINGS and a
readability tax on a list of thirty addon names at 12px. The IchaLaunch crest
caption is a separate LifeCraft face in main_window, not this family.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QRect
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

from ichalaunch.core.paths import theme_file

log = logging.getLogger(__name__)

_CHROME_FILES = ("Cinzel-Regular.ttf", "Cinzel-Bold.ttf")

# Point the chrome at a family the machine already has, without the launcher
# shipping it. This exists because the fonts that would suit best cannot be
# bundled: ravencraft.io sets its titles in Folkard, whose licence grants use to
# the purchaser and forbids including the file with other software. A user who
# holds that licence can install it and set this; we still ship nothing.
_FAMILY_ENV = "ICHALAUNCH_CHROME_FAMILY"

# What the launcher asked for before anything was bundled. Retained so a build
# with the font files stripped degrades to the old behaviour rather than to
# whatever QFont() defaults to.
FALLBACK_CHROME_FAMILY = "Segoe UI"

_chrome_family: str | None = None
_load_attempted = False


# Reading text. Lexend is drawn on reading-speed research: wide apertures and
# generous spacing, measurably easier for dyslexic readers and simply clearer at
# small sizes than a general UI face. The split is display-face for ornament,
# reading-face for content, which is a standard pairing rather than a mixture.
#
# Unlike the chrome face this is SIL Open Font Licence, so it can be bundled and
# shipped rather than depending on what the machine happens to have installed.
_BODY_FILES = ("Lexend-Regular.ttf", "Lexend-Medium.ttf", "Lexend-SemiBold.ttf")
FALLBACK_BODY_FAMILY = "Segoe UI"
_body_family: str | None = None
_body_loaded = False


# RavenCraft's own secondary face, and the answer to the one type note the project
# owner actually made: the display font is right for headers, too much for small
# text. ravencraft.io declares Fontin-Sans in more rules than any other family and
# keeps Folkard for display, so this is the site's pairing rather than an invention.
# Small caps is its normal weight there, which is why their labels read as they do.
_LABEL_FILES = ("Fontin-Sans-CR-SC.ttf", "Fontin-Sans-CR-Bold.ttf")
_label_family: str | None = None
_label_loaded = False


def label_family() -> str:
    """Small-caps label face, registered from the bundle on first use."""
    global _label_family, _label_loaded
    if _label_loaded:
        return _label_family or body_family()
    _label_loaded = True
    for name in _LABEL_FILES:
        path = theme_file("fonts", name)
        if not path.is_file() or path.stat().st_size <= 0:
            log.warning("Label font not bundled: %s", name)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            log.warning("Label font rejected by Qt: %s", name)
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families and _label_family is None:
            _label_family = families[0]
    if _label_family is None:
        return body_family()
    return _label_family


def body_family() -> str:
    """The reading face, registered from the bundle on first use."""
    global _body_family, _body_loaded
    if _body_loaded:
        return _body_family or FALLBACK_BODY_FAMILY
    _body_loaded = True
    for name in _BODY_FILES:
        path = theme_file("fonts", name)
        if not path.is_file() or path.stat().st_size <= 0:
            log.warning("Body font not bundled: %s", name)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            log.warning("Body font rejected by Qt: %s", name)
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families and _body_family is None:
            _body_family = families[0]
    if _body_family is None:
        log.warning("No body font registered; falling back to %s", FALLBACK_BODY_FAMILY)
    return _body_family or FALLBACK_BODY_FAMILY


def chrome_family() -> str:
    """Family for tabs, buttons and headings; the old stack if it is missing."""
    global _chrome_family, _load_attempted
    if _load_attempted:
        return _chrome_family or FALLBACK_CHROME_FAMILY
    _load_attempted = True

    override = os.environ.get(_FAMILY_ENV, "").strip()
    if override:
        if override in QFontDatabase.families():
            log.info("Chrome font set to %r by %s", override, _FAMILY_ENV)
            _chrome_family = override
            return _chrome_family
        # Naming a family Qt cannot see would silently substitute something
        # arbitrary, which looks like the setting worked. Say so and carry on.
        log.warning("%s names %r, which is not installed - ignoring", _FAMILY_ENV, override)

    for name in _CHROME_FILES:
        path = theme_file("fonts", name)
        if not path.is_file() or path.stat().st_size <= 0:
            log.warning("Chrome font not bundled: %s", name)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            log.warning("Chrome font rejected by Qt: %s", name)
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        # Both weights register under one family; the first to arrive names it.
        if families and _chrome_family is None:
            _chrome_family = families[0]

    if _chrome_family is None:
        log.warning("No chrome font registered; falling back to %s", FALLBACK_CHROME_FAMILY)
    return _chrome_family or FALLBACK_CHROME_FAMILY


# --- ink metrics -------------------------------------------------------------
#
# Qt lays text out on the line box: ascent plus descent, as the font declares
# them. Display faces declare a tall ascent to make room for flourishes their
# capitals never reach, so a label centred on the line box sits visibly high and
# one sized by the line box comes out small. Both are read as the font being
# wrong rather than the measurement being wrong. Measuring the ink instead makes
# the chrome behave the same whatever family it is pointed at.


def ink_height(font: QFont, text: str) -> int:
    """Height of the marks themselves, never less than one line box would need."""
    fm = QFontMetrics(font)
    tight = fm.tightBoundingRect(text)
    return max(1, tight.height())


def fit_pixel_size(font: QFont, text: str, box_w: int, box_h: int, lo: int, hi: int) -> int:
    """Largest size in [lo, hi] fitting box_w x box_h. Returns lo if none do."""
    best = lo
    for px in range(lo, hi + 1):
        font.setPixelSize(px)
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) > box_w:
            break
        # Both bounds matter and neither implies the other. Folkard's descenders
        # make its ink taller than its line box; Cinzel, all caps and no tails,
        # is the reverse by a factor of two. Checking one lets the other overrun.
        if max(fm.tightBoundingRect(text).height(), fm.height()) > box_h:
            break
        best = px
    font.setPixelSize(best)
    return best


def ink_centered_rect(rect: QRect, font: QFont, text: str) -> QRect:
    """Shift *rect* so AlignCenter lands the ink centre on the rect centre.

    Qt centres the line box, so a face whose capitals sit low inside a tall
    ascent renders high in its own button. This returns the rect to draw into so
    the marks are optically centred instead.
    """
    fm = QFontMetrics(font)
    tight = fm.tightBoundingRect(text)
    if tight.isEmpty():
        return rect
    baseline = rect.top() + (rect.height() - fm.height()) / 2.0 + fm.ascent()
    ink_center = baseline + tight.top() + tight.height() / 2.0
    return rect.translated(0, round(rect.center().y() - ink_center))
