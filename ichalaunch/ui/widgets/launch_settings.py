"""Launch-time checkboxes hosted on Client → Launch."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ichalaunch.config.settings import settings
from ichalaunch.game.cpu_topology import vcache_pin_enabled
from ichalaunch.game.display import frame_cap_enabled
from ichalaunch.game.proton import wow64_enabled
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox


class LaunchSettingsPanel(QWidget):
    """Same Launch keys as the old Settings card; saves immediately on toggle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LaunchSettingsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.cb_vf = ThemeCheckBox("Launch through VanillaFixes.exe when available")
        self.cb_vf.setChecked(bool(settings.get("vanillafixes_enabled", True)))
        self.cb_vf.toggled.connect(lambda v: settings.set("vanillafixes_enabled", v))
        self.cb_min = ThemeCheckBox("Minimize launcher when game starts")
        self.cb_min.setChecked(bool(settings.get("minimize_on_launch", False)))
        self.cb_min.toggled.connect(lambda v: settings.set("minimize_on_launch", v))
        self.cb_close = ThemeCheckBox("Close launcher when game starts")
        self.cb_close.setChecked(bool(settings.get("close_on_launch", False)))
        self.cb_close.toggled.connect(lambda v: settings.set("close_on_launch", v))
        self.cb_wow64 = ThemeCheckBox(
            "Run the client under new WoW64 where Proton supports it (Linux)"
        )
        self.cb_wow64.setChecked(wow64_enabled())
        self.cb_wow64.setToolTip(
            "Runs Wine's translation layer in a 64-bit host process, so its "
            "libraries stop sharing the 32-bit client's 4 GB of address space. "
            "The client stays 32-bit and its own ceiling does not move; what "
            "changes is how much of that ceiling is left for the game. The "
            "gain shows up with heavy texture packs.\n\n"
            "On by default, but only where it can be honoured: Proton builds "
            "differ, and the launcher checks yours before each launch. On a "
            "build without the 64-bit host it keeps the normal mode and says "
            "so in the log rather than failing the launch. Turn it off if a "
            "DLL-injecting client mod misbehaves under it."
        )
        self.cb_wow64.toggled.connect(lambda v: settings.set("linux_use_wow64", v))
        self.cb_vcache = ThemeCheckBox(
            "Pin the game to the 3D V-Cache cores (AMD X3D with two CCDs)"
        )
        self.cb_vcache.setChecked(vcache_pin_enabled())
        self.cb_vcache.setToolTip(
            "On a dual-CCD X3D CPU (7950X3D, 9950X3D, …) one die has the 3D "
            "V-Cache and the other does not. Vanilla WoW is cache-sensitive, "
            "so the launcher pins the client to the cache-rich die.\n\n"
            "On by default. Single-CCD X3D parts and every other CPU are left "
            "alone — detection reads the L3 layout, not the CPU name. Turn it "
            "off if you would rather the scheduler pick the cores."
        )
        self.cb_vcache.toggled.connect(lambda v: settings.set("pin_to_vcache_ccd", v))
        self.cb_frame_cap = ThemeCheckBox(
            "Cap DXVK frames a few below the monitor refresh rate"
        )
        self.cb_frame_cap.setChecked(frame_cap_enabled())
        self.cb_frame_cap.setToolTip(
            "Sets d3d9.maxFrameRate in dxvk.conf from the live display "
            "(refresh minus 3). Uses the fastest attached panel, not the "
            "Windows primary, so a 60 Hz desktop next to a 165 Hz game "
            "monitor does not lock the game at 57.\n\n"
            "Applied when DXVK is installed and again at PLAY if the file "
            "is already there. An unreadable display leaves the file alone. "
            "Turn it off to keep whatever cap you set by hand."
        )
        self.cb_frame_cap.toggled.connect(
            lambda v: settings.set("frame_cap_from_refresh", v)
        )
        launch_boxes = [self.cb_vf, self.cb_min, self.cb_close]
        # build_launch_command only consults linux_use_wow64 through the umu
        # path, and core/process.py imports that module inside its "not win32"
        # branch, so the setting cannot do anything on Windows. The widget is
        # still constructed there so refresh() needs no platform branch of its
        # own -- it is simply never added to the card.
        if sys.platform != "win32":
            launch_boxes.append(self.cb_wow64)
        launch_boxes.extend((self.cb_vcache, self.cb_frame_cap))
        for cb in launch_boxes:
            cb.setMinimumHeight(28)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(cb)

    def refresh(self) -> None:
        for cb, key in (
            (self.cb_vf, "vanillafixes_enabled"),
            (self.cb_min, "minimize_on_launch"),
            (self.cb_close, "close_on_launch"),
        ):
            cb.blockSignals(True)
            cb.setChecked(bool(settings.get(key, False)))
            cb.blockSignals(False)
        # Kept out of the loop above because that loop resolves a key to a bare
        # bool, and this one is tri-state: null means "unset", which is not the
        # same as off. wow64_enabled() is the single place that turns the stored
        # value into a launch decision, so asking it here is what keeps the box
        # and the launch path from ever disagreeing.
        self.cb_wow64.blockSignals(True)
        self.cb_wow64.setChecked(wow64_enabled())
        self.cb_wow64.blockSignals(False)
        self.cb_vcache.blockSignals(True)
        self.cb_vcache.setChecked(vcache_pin_enabled())
        self.cb_vcache.blockSignals(False)
        self.cb_frame_cap.blockSignals(True)
        self.cb_frame_cap.setChecked(frame_cap_enabled())
        self.cb_frame_cap.blockSignals(False)
