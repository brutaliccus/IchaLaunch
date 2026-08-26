"""Launch-time checkboxes hosted on Client → Launch."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ichalaunch.config.settings import settings
from ichalaunch.game.cpu_topology import vcache_pin_enabled
from ichalaunch.game.display import frame_cap_enabled
from ichalaunch.game.nampower_encrypt import encrypt_enabled, set_encrypt_enabled
from ichalaunch.game.proton import wow64_enabled
from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox

_ENCRYPT_HINT_WIN = (
    "Enables Nampower's login Encrypt toggle via Windows DPAPI. "
    "Changing or clearing the key makes previously encrypted passwords unreadable."
)
_ENCRYPT_HINT_LINUX = (
    "Nampower password encryption uses Windows DPAPI and is not available on Linux."
)
REGEN_KEY_STATUS_OK = (
    "New key saved. Previously encrypted passwords will not work."
)
_REGEN_BTN_LABEL = "Regenerate key"
_REGEN_BTN_OK_LABEL = "Key replaced"
_REGEN_BTN_RESTORE_MS = 2000


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
        self.cb_nampower_encrypt = ThemeCheckBox(
            "Encrypt saved login passwords (Nampower)"
        )
        self.cb_nampower_encrypt.setChecked(encrypt_enabled())
        self.cb_nampower_encrypt.setToolTip(
            "Sets WOW_ENCRYPTION_KEY on the game process so Nampower can "
            "encrypt saved login passwords with Windows DPAPI. You do not "
            "need to remember the key. Regenerating or clearing it makes "
            "previously encrypted passwords unreadable."
        )
        self.cb_nampower_encrypt.toggled.connect(self._on_nampower_encrypt_toggled)
        self.nampower_encrypt_hint = QLabel(
            _ENCRYPT_HINT_WIN if sys.platform == "win32" else _ENCRYPT_HINT_LINUX
        )
        self.nampower_encrypt_hint.setObjectName("Muted")
        self.nampower_encrypt_hint.setWordWrap(True)
        self.nampower_encrypt_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.btn_regenerate_encrypt_key = GluePanelButton(
            _REGEN_BTN_LABEL, width=148, height=GLUE_BTN_H
        )
        self.btn_regenerate_encrypt_key.setToolTip(
            "Store a new encryption key. Previously encrypted saved login "
            "passwords will become unreadable."
        )
        self.btn_regenerate_encrypt_key.clicked.connect(self._on_regenerate_encrypt_key)
        self.encrypt_key_status = QLabel("")
        self.encrypt_key_status.setObjectName("CardTitle")
        self.encrypt_key_status.setWordWrap(True)
        self.encrypt_key_status.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._regen_btn_restore = QTimer(self)
        self._regen_btn_restore.setSingleShot(True)
        self._regen_btn_restore.setInterval(_REGEN_BTN_RESTORE_MS)
        self._regen_btn_restore.timeout.connect(self._restore_regenerate_button)
        if sys.platform != "win32":
            self.cb_nampower_encrypt.setEnabled(False)
            self.cb_nampower_encrypt.setToolTip(_ENCRYPT_HINT_LINUX)
        launch_boxes = [self.cb_min, self.cb_close]
        # build_launch_command only consults linux_use_wow64 through the umu
        # path, and core/process.py imports that module inside its "not win32"
        # branch, so the setting cannot do anything on Windows. The widget is
        # still constructed there so refresh() needs no platform branch of its
        # own -- it is simply never added to the card.
        if sys.platform != "win32":
            launch_boxes.append(self.cb_wow64)
        launch_boxes.extend((self.cb_vcache, self.cb_frame_cap, self.cb_nampower_encrypt))
        for cb in launch_boxes:
            cb.setMinimumHeight(28)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(cb)
        layout.addWidget(self.nampower_encrypt_hint)
        regen_row = QHBoxLayout()
        regen_row.setContentsMargins(0, 0, 0, 0)
        regen_row.setSpacing(10)
        regen_row.addWidget(
            self.btn_regenerate_encrypt_key, 0, Qt.AlignmentFlag.AlignTop
        )
        regen_row.addWidget(self.encrypt_key_status, 1)
        if sys.platform == "win32":
            layout.addLayout(regen_row)
        else:
            self.btn_regenerate_encrypt_key.hide()
            self.encrypt_key_status.hide()
        self._sync_nampower_encrypt_controls()

    def refresh(self) -> None:
        for cb, key in (
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
        self.cb_nampower_encrypt.blockSignals(True)
        self.cb_nampower_encrypt.setChecked(encrypt_enabled())
        self.cb_nampower_encrypt.blockSignals(False)
        self._sync_nampower_encrypt_controls()

    def _sync_nampower_encrypt_controls(self) -> None:
        on = encrypt_enabled() and sys.platform == "win32"
        if self._regen_btn_restore.isActive() and on:
            self.btn_regenerate_encrypt_key.setEnabled(False)
            return
        self._restore_regenerate_button()

    def _restore_regenerate_button(self) -> None:
        self._regen_btn_restore.stop()
        self.btn_regenerate_encrypt_key.setText(_REGEN_BTN_LABEL)
        on = encrypt_enabled() and sys.platform == "win32"
        self.btn_regenerate_encrypt_key.setEnabled(on)

    def _on_nampower_encrypt_toggled(self, checked: bool) -> None:
        set_encrypt_enabled(checked)
        self._sync_nampower_encrypt_controls()

    def _on_regenerate_encrypt_key(self) -> None:
        from ichalaunch.game.nampower_encrypt import regenerate_encryption_key
        from ichalaunch.ui.widgets.dialogs import confirm, error

        if not confirm(
            self,
            "Regenerate encryption key?",
            "A new key will be stored. Previously encrypted saved login "
            "passwords will become unreadable.",
        ):
            return
        try:
            regenerate_encryption_key()
        except Exception as exc:  # noqa: BLE001
            self.encrypt_key_status.clear()
            error(
                self,
                "Could not regenerate key",
                str(exc) or "The encryption key could not be saved.",
            )
            return
        self.encrypt_key_status.setText(REGEN_KEY_STATUS_OK)
        self.encrypt_key_status.show()
        self.btn_regenerate_encrypt_key.setText(_REGEN_BTN_OK_LABEL)
        self.btn_regenerate_encrypt_key.setEnabled(False)
        self._regen_btn_restore.start()
