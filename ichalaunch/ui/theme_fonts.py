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
bundling inside an application, including one distributed commercially. That
matters here: the face this replaces was donationware licensed for personal use,
which an installed launcher is not.

It is used for chrome only - tabs, buttons, section headings. Body copy, mod and
addon rows, tooltips and combo boxes stay in the sans they were, because an
inscriptional Roman face is excellent at HOME ADDONS CLIENT SETTINGS and a
readability tax on a list of thirty addon names at 12px.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QFontDatabase

from ichalaunch.core.paths import theme_file

log = logging.getLogger(__name__)

_CHROME_FILES = ("Cinzel-Regular.ttf", "Cinzel-Bold.ttf")

# What the launcher asked for before anything was bundled. Retained so a build
# with the font files stripped degrades to the old behaviour rather than to
# whatever QFont() defaults to.
FALLBACK_CHROME_FAMILY = "Segoe UI"

_chrome_family: str | None = None
_load_attempted = False


def chrome_family() -> str:
    """Family for tabs, buttons and headings; the old stack if it is missing."""
    global _chrome_family, _load_attempted
    if _load_attempted:
        return _chrome_family or FALLBACK_CHROME_FAMILY
    _load_attempted = True

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
