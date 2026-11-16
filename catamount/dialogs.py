# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Map/edit drive dialog, preferences and changelog."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from catamount import APP_NAME, __version__
from catamount import autostart, deps
from catamount.icons import TRAY_STYLES
from catamount.model import format_remote, new_drive_id, parse_address, suggest_mount_point
from catamount.theme import THEMES

SMB_VERSIONS = (("", "Automatic (recommended)"), ("3.1.1", "SMB 3.1.1"), ("3.02", "SMB 3.02"),
                ("3.0", "SMB 3.0"), ("2.1", "SMB 2.1"), ("2.0", "SMB 2.0"),
                ("1.0", "SMB 1.0 (insecure, legacy devices only)"))
NFS_VERSIONS = (("", "Automatic (recommended)"), ("4.2", "NFS 4.2"), ("4.1", "NFS 4.1"),
                ("4", "NFS 4.0"), ("3", "NFS 3"))

DEFAULTS = {
    "theme": "system",
    "mount_root": "/mnt",
    "refresh_seconds": 5,
    "default_automount": True,
    "default_idle_minutes": 0,
    "default_start_at_boot": True,
    "confirm_remove": True,
    "tray_enabled": True,
    "close_to_tray": True,
    "notify_failures": True,
    "tray_hint_shown": False,
    "tray_icon_style": "color",
}


def setting(settings, key):
    default = DEFAULTS[key]
    value = settings.value(key, default)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1")
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return str(value)


def _form():
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return form


def _combo(items, current=""):
    box = QComboBox()
    for value, label in items:
        box.addItem(label, value)
    idx = box.findData(current)
    box.setCurrentIndex(max(idx, 0))
    return box


class DriveDialog(QDialog):
    """Create or edit a network drive."""

    def __init__(self, settings, drive=None, taken=(), parent=None):
        super().__init__(parent)
        self.settings = settings
        self.drive = drive
        self.taken = set(taken)
        self.editing = drive is not None
        spec = dict(drive.spec) if drive else {}
        self.drive_id = spec.get("id") or new_drive_id()
        self._had_password = (self.editing and spec.get("type") == "smb"
                              and not spec.get("smb", {}).get("guest"))
        self._mp_touched = self.editing
        self._name_touched = self.editing

        self.setWindowTitle("Edit network drive" if self.editing else "Map network drive")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        intro = QLabel("Specify the shared folder you want to connect to.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = _form()
        self.kind = _combo((("smb", "SMB / Windows share"), ("nfs", "NFS export")),
                           spec.get("type", "smb"))
        self.address = QLineEdit()
        self.address.setPlaceholderText(r"\\server\share   or   server:/export")
        self.address.setClearButtonEnabled(True)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText)
        self.name = QLineEdit()
        self.mount_point = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        mp_row = QWidget()
        mp_layout = QHBoxLayout(mp_row)
        mp_layout.setContentsMargins(0, 0, 0, 0)
        mp_layout.addWidget(self.mount_point, 1)
        mp_layout.addWidget(browse)

        form.addRow("Folder:", self.address)
        form.addRow("", self.hint)
        form.addRow("Protocol:", self.kind)
        form.addRow("Name:", self.name)
        form.addRow("Local folder:", mp_row)
        layout.addLayout(form)

        # SMB
        smb = spec.get("smb", {})
        self.smb_box = QGroupBox("Sign in")
        sform = _form()
        self.guest = QCheckBox("Connect as guest (no password)")
        self.guest.setChecked(bool(smb.get("guest")))
        self.username = QLineEdit(smb.get("username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        if self._had_password:
            self.password.setPlaceholderText("(unchanged)")
        self.domain = QLineEdit(smb.get("domain", ""))
        self.domain.setPlaceholderText("optional, e.g. WORKGROUP")
        self.smb_version = _combo(SMB_VERSIONS, smb.get("version", ""))
        sform.addRow(self.guest)
        sform.addRow("User name:", self.username)
        sform.addRow("Password:", self.password)
        sform.addRow("Domain:", self.domain)
        sform.addRow("SMB version:", self.smb_version)
        self.smb_box.setLayout(sform)
        layout.addWidget(self.smb_box)

        # NFS
        self.nfs_box = QGroupBox("NFS")
        nform = _form()
        self.nfs_version = _combo(NFS_VERSIONS, spec.get("nfs", {}).get("version", ""))
        nform.addRow("NFS version:", self.nfs_version)
        self.nfs_box.setLayout(nform)
        layout.addWidget(self.nfs_box)

        # Options
        opt_box = QGroupBox("Options")
        oform = _form()
        self.start_at_boot = QCheckBox("Reconnect at startup")
        self.start_at_boot.setChecked(spec.get("start_at_boot",
                                               setting(settings, "default_start_at_boot")))
        self.automount = QCheckBox("Connect on first access (automount)")
        self.automount.setToolTip("The share is mounted when something opens the folder, so "
                                  "startup never waits for the network.")
        self.automount.setChecked(spec.get("automount", setting(settings, "default_automount")))
        self.idle = QSpinBox()
        self.idle.setRange(0, 10080)
        self.idle.setSuffix(" min")
        self.idle.setSpecialValueText("Never")
        idle_secs = spec.get("idle_timeout")
        self.idle.setValue(idle_secs // 60 if isinstance(idle_secs, int)
                           else setting(settings, "default_idle_minutes"))
        self.read_only = QCheckBox("Read-only")
        self.read_only.setChecked(bool(spec.get("read_only")))
        self.extra = QLineEdit(spec.get("extra_options", ""))
        self.extra.setPlaceholderText("advanced, comma-separated mount options")
        oform.addRow(self.start_at_boot)
        oform.addRow(self.automount)
        oform.addRow("Disconnect when idle:", self.idle)
        oform.addRow(self.read_only)
        oform.addRow("Extra options:", self.extra)
        opt_box.setLayout(oform)
        layout.addWidget(opt_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save" if self.editing else "Connect")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)

        if self.editing:
            self.address.setText(format_remote(spec.get("type"), spec.get("server", ""),
                                               spec.get("share", "")))
            self.name.setText(spec.get("name", ""))
            self.mount_point.setText(spec.get("mount_point", ""))
            self.taken.discard(spec.get("mount_point", ""))

        self.address.textChanged.connect(self._address_changed)
        self.kind.currentIndexChanged.connect(self._address_changed)
        self.mount_point.textEdited.connect(lambda _t: setattr(self, "_mp_touched", True))
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self.guest.toggled.connect(self._update_visibility)
        self.automount.toggled.connect(self._update_visibility)
        self._address_changed()
        self._update_visibility()
        self.address.setFocus()

    # -- helpers ----------------------------------------------------------

    def _parsed(self):
        return parse_address(self.address.text(), self.kind.currentData())

    def _address_changed(self, *_):
        addr = self._parsed()
        if addr.kind and addr.kind != self.kind.currentData():
            self.kind.blockSignals(True)
            self.kind.setCurrentIndex(self.kind.findData(addr.kind))
            self.kind.blockSignals(False)
        if not self.address.text().strip():
            self.hint.setText("Examples: \\\\nas\\media   smb://nas/media   nas:/srv/export")
        elif addr.errors:
            self.hint.setText("⚠ " + " ".join(addr.errors))
        elif addr.kind == "smb":
            self.hint.setText("SMB share “%s” on %s" % (addr.share, addr.server))
        else:
            self.hint.setText("NFS export %s on %s" % (addr.share, addr.server))
        if addr.username and not self.username.text():
            self.username.setText(addr.username)
        if addr.domain and not self.domain.text():
            self.domain.setText(addr.domain)
        if addr.valid:
            if not self._name_touched:
                leaf = [p for p in addr.share.split("/") if p]
                self.name.setText("%s on %s" % (leaf[-1] if leaf else addr.share, addr.server))
            if not self._mp_touched:
                self.mount_point.setText(suggest_mount_point(
                    setting(self.settings, "mount_root"), addr, self.taken))
        self._update_visibility()

    def _update_visibility(self, *_):
        is_smb = self.kind.currentData() == "smb"
        self.smb_box.setVisible(is_smb)
        self.nfs_box.setVisible(not is_smb)
        guest = self.guest.isChecked()
        for w in (self.username, self.password, self.domain):
            w.setEnabled(not guest)
        self.idle.setEnabled(self.automount.isChecked())
        self.adjustSize()

    def _browse(self):
        start = self.mount_point.text() or setting(self.settings, "mount_root")
        path = QFileDialog.getExistingDirectory(self, "Choose local folder",
                                                os.path.dirname(start.rstrip("/")) or "/")
        if path:
            self.mount_point.setText(path)
            self._mp_touched = True

    def _error(self, text, widget=None):
        QMessageBox.warning(self, self.windowTitle(), text)
        if widget is not None:
            widget.setFocus()

    def _accept(self):
        addr = self._parsed()
        if not addr.valid:
            self._error(" ".join(addr.errors) or "Enter the folder to connect to.", self.address)
            return
        if not self.name.text().strip():
            self._error("Enter a name for this drive.", self.name)
            return
        mp = self.mount_point.text().strip()
        if not mp.startswith("/"):
            self._error("The local folder must be an absolute path such as /mnt/share.",
                        self.mount_point)
            return
        if mp.rstrip("/") in self.taken:
            self._error("Another drive already uses %s." % mp, self.mount_point)
            return
        if addr.kind == "smb" and not self.guest.isChecked():
            if not self.username.text().strip():
                self._error("Enter a user name, or choose guest access.", self.username)
                return
            if not self.password.text() and not self._had_password:
                self._error("Enter the password for this share.", self.password)
                return
            if "\n" in self.password.text():
                self._error("The password cannot contain line breaks.", self.password)
                return
        self.accept()

    def request(self):
        """Return (spec, password-or-None) for the helper's apply command."""
        addr = self._parsed()
        spec = {
            "id": self.drive_id,
            "name": self.name.text().strip(),
            "type": addr.kind,
            "server": addr.server,
            "share": addr.share,
            "mount_point": "/" + self.mount_point.text().strip().strip("/"),
            "automount": self.automount.isChecked(),
            "idle_timeout": self.idle.value() * 60 if self.automount.isChecked() else 0,
            "start_at_boot": self.start_at_boot.isChecked(),
            "read_only": self.read_only.isChecked(),
            "extra_options": self.extra.text().strip(),
        }
        password = None
        if addr.kind == "smb":
            spec["smb"] = {
                "guest": self.guest.isChecked(),
                "username": self.username.text().strip(),
                "domain": self.domain.text().strip(),
                "version": self.smb_version.currentData(),
                "uid": os.getuid(),
                "gid": os.getgid(),
            }
            if not self.guest.isChecked() and self.password.text():
                password = self.password.text()
        else:
            spec["nfs"] = {"version": self.nfs_version.currentData()}
        return spec, password


class PreferencesDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        form = _form()
        self.theme = _combo(THEMES, setting(settings, "theme"))
        self.mount_root = QLineEdit(setting(settings, "mount_root"))
        self.refresh = QSpinBox()
        self.refresh.setRange(2, 300)
        self.refresh.setSuffix(" s")
        self.refresh.setValue(setting(settings, "refresh_seconds"))
        self.automount = QCheckBox("New drives connect on first access")
        self.automount.setChecked(setting(settings, "default_automount"))
        self.start_at_boot = QCheckBox("New drives reconnect at startup")
        self.start_at_boot.setChecked(setting(settings, "default_start_at_boot"))
        self.idle = QSpinBox()
        self.idle.setRange(0, 10080)
        self.idle.setSuffix(" min")
        self.idle.setSpecialValueText("Never")
        self.idle.setValue(setting(settings, "default_idle_minutes"))
        self.confirm = QCheckBox("Ask before removing a drive")
        self.confirm.setChecked(setting(settings, "confirm_remove"))
        form.addRow("Theme:", self.theme)
        form.addRow("Default folder for new drives:", self.mount_root)
        form.addRow("Status refresh:", self.refresh)
        form.addRow(self.automount)
        form.addRow(self.start_at_boot)
        form.addRow("Default idle disconnect:", self.idle)
        form.addRow(self.confirm)
        layout.addLayout(form)

        tray_box = QGroupBox("System tray")
        tform = _form()
        self.tray_enabled = QCheckBox("Show CataMount in the system tray")
        self.tray_enabled.setChecked(setting(settings, "tray_enabled"))
        self.close_to_tray = QCheckBox("Closing the window keeps CataMount running in the tray")
        self.close_to_tray.setChecked(setting(settings, "close_to_tray"))
        self.notify = QCheckBox("Notify me when a drive fails or disconnects")
        self.notify.setChecked(setting(settings, "notify_failures"))
        self.login = QCheckBox("Start CataMount in the tray when I log in")
        self.login.setChecked(autostart.is_enabled())
        self.tray_style = _combo(TRAY_STYLES, setting(settings, "tray_icon_style"))
        for w in (self.tray_enabled, self.close_to_tray, self.notify, self.login):
            tform.addRow(w)
        tform.addRow("Tray icon:", self.tray_style)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            status = deps.appindicator_status()
            text = "No system tray was found, so closing the window quits CataMount."
            if status == "missing":
                text += (" GNOME needs the AppIndicator extension for a tray. Install it with:\n"
                         + deps.appindicator_install_hint() + "\nthen log out and back in.")
            elif status == "disabled":
                text += " GNOME's AppIndicator extension is installed but turned off."
            note = QLabel(text)
            note.setWordWrap(True)
            note.setTextInteractionFlags(Qt.TextSelectableByMouse)
            tform.addRow(note)
            if status == "disabled":
                btn = QPushButton("Turn on the tray extension")
                btn.clicked.connect(lambda: self._enable_indicator(btn, note))
                tform.addRow(btn)
        self.tray_enabled.toggled.connect(self._tray_toggled)
        self._tray_toggled(self.tray_enabled.isChecked())
        tray_box.setLayout(tform)
        layout.addWidget(tray_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _enable_indicator(self, btn, note):
        if deps.enable_appindicator():
            btn.setEnabled(False)
            note.setText("Tray extension turned on. If the CataMount icon doesn't appear, "
                         "log out and back in.")
        else:
            QMessageBox.warning(self, "Preferences", "Could not turn on the extension. "
                                "Use the Extensions app instead.")

    def _tray_toggled(self, on):
        for w in (self.close_to_tray, self.notify, self.tray_style):
            w.setEnabled(on)

    def _save(self):
        root = self.mount_root.text().strip()
        if not root.startswith("/"):
            QMessageBox.warning(self, "Preferences", "The default folder must be an absolute path.")
            return
        s = self.settings
        s.setValue("theme", self.theme.currentData())
        s.setValue("mount_root", "/" + root.strip("/"))
        s.setValue("refresh_seconds", self.refresh.value())
        s.setValue("default_automount", self.automount.isChecked())
        s.setValue("default_start_at_boot", self.start_at_boot.isChecked())
        s.setValue("default_idle_minutes", self.idle.value())
        s.setValue("confirm_remove", self.confirm.isChecked())
        s.setValue("tray_enabled", self.tray_enabled.isChecked())
        s.setValue("close_to_tray", self.close_to_tray.isChecked())
        s.setValue("notify_failures", self.notify.isChecked())
        s.setValue("tray_icon_style", self.tray_style.currentData())
        s.sync()
        try:
            autostart.set_enabled(self.login.isChecked())
        except OSError as exc:
            QMessageBox.warning(self, "Preferences", "Could not change start at login: %s" % exc)
        self.accept()


def changelog_text():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", "CHANGELOG.md"),
                 "/usr/share/catamount/CHANGELOG.md"):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    return "# Changelog\n\nThe changelog file could not be found."


class ChangelogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("%s %s — Changelog" % (APP_NAME, __version__))
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(changelog_text())
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
