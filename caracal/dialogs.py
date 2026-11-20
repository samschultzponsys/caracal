# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Map/edit drive dialog, preferences and changelog."""

import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QSystemTrayIcon, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from caracal import APP_NAME, __version__
from caracal import autostart, deps, discovery
from caracal.background import run_async
from caracal.widgets import add_reveal_action
from caracal.icons import TRAY_STYLES
from caracal.model import format_remote, new_drive_id, parse_address, suggest_mount_point
from caracal.theme import THEMES

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
    "watch_retry_minutes": 5,
    "watch_on_fail": "lock",
    "watch_recover_dead": True,
    "tray_enabled": True,
    "close_to_tray": True,
    "notify_failures": True,
    "tray_hint_shown": False,
    "tray_icon_style": "color",
    "tray_show_status": True,
    "auto_check_updates": True,
    "default_bookmark": True,
    "explorer_mode": False,
    "tray_in_explorer_mode": False,
}
from caracal.index_manager import INDEX_DEFAULTS  # noqa: E402
DEFAULTS.update(INDEX_DEFAULTS)


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

    def __init__(self, settings, drive=None, taken=(), parent=None, template=None, title=None):
        super().__init__(parent)
        self.settings = settings
        self.drive = drive
        self.taken = set(taken)
        self.editing = drive is not None
        spec = dict(drive.spec) if drive else dict(template or {})
        self.drive_id = (spec.get("id") if self.editing else None) or new_drive_id()
        prefilled = bool(spec.get("server"))
        smb0 = spec.get("smb", {})
        self._had_password = (self.editing and spec.get("type") == "smb"
                              and not smb0.get("guest") and not smb0.get("kerberos"))
        # Only a path/name that was actually given counts as chosen; otherwise keep
        # suggesting /mnt/<share> (opening from Browse or "faster connection" used to leave
        # the local folder empty, so it had to be typed every time).
        self._mp_touched = bool(spec.get("mount_point"))
        self._name_touched = bool(spec.get("name"))

        self.setWindowTitle(title or ("Edit network drive" if self.editing
                                      else "Map network drive"))
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
        # "this server also shares it over NFS" tip, checked in the background
        self.fast_hint = QLabel()
        self.fast_hint.setWordWrap(True)
        self.fast_hint.setTextFormat(Qt.RichText)
        self.fast_hint.hide()
        self.fast_hint.linkActivated.connect(self._use_nfs)
        self._fast_timer = QTimer(self)
        self._fast_timer.setSingleShot(True)
        self._fast_timer.setInterval(900)
        self._fast_timer.timeout.connect(self._probe_nfs)
        self.name = QLineEdit()
        self.mount_point = QLineEdit()
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        net_browse = QPushButton("Browse…")
        net_browse.setToolTip("Find servers and shares on the network")
        net_browse.clicked.connect(self._browse_network)
        addr_row = QWidget()
        addr_layout = QHBoxLayout(addr_row)
        addr_layout.setContentsMargins(0, 0, 0, 0)
        addr_layout.addWidget(self.address, 1)
        addr_layout.addWidget(net_browse)
        mp_row = QWidget()
        mp_layout = QHBoxLayout(mp_row)
        mp_layout.setContentsMargins(0, 0, 0, 0)
        mp_layout.addWidget(self.mount_point, 1)
        mp_layout.addWidget(browse)

        form.addRow("Folder:", addr_row)
        form.addRow("", self.hint)
        form.addRow("", self.fast_hint)
        form.addRow("Protocol:", self.kind)
        form.addRow("Name:", self.name)
        form.addRow("Local folder:", mp_row)
        layout.addLayout(form)

        # SMB
        smb = spec.get("smb", {})
        self.smb_box = QGroupBox("Sign in")
        sform = _form()
        self.auth = _combo((("password", "User name and password"),
                            ("guest", "Guest (no password)"),
                            ("kerberos", "Kerberos ticket (domain sign-in)")),
                           "guest" if smb.get("guest") else
                           "kerberos" if smb.get("kerberos") else "password")
        self.username = QLineEdit(smb.get("username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        add_reveal_action(self.password)
        if self._had_password:
            self.password.setPlaceholderText("(unchanged)")
        self.domain = QLineEdit(smb.get("domain", ""))
        self.domain.setPlaceholderText("optional, e.g. WORKGROUP")
        self.smb_version = _combo(SMB_VERSIONS, smb.get("version", ""))
        self.seal = QCheckBox("Encrypt traffic (needs SMB 3)")
        self.seal.setChecked(bool(smb.get("seal")))
        self.all_users = QCheckBox("Every user on this computer can write to it")
        self.all_users.setToolTip("By default only you can write to the drive's files.")
        self.all_users.setChecked(bool(smb.get("all_users")))
        sform.addRow("Sign in with:", self.auth)
        sform.addRow("User name:", self.username)
        sform.addRow("Password:", self.password)
        sform.addRow("Domain:", self.domain)
        sform.addRow("SMB version:", self.smb_version)
        sform.addRow(self.seal)
        sform.addRow(self.all_users)
        self.smb_box.setLayout(sform)
        layout.addWidget(self.smb_box)

        # NFS
        self.nfs_box = QGroupBox("NFS")
        nform = _form()
        self.nfs_version = _combo(NFS_VERSIONS, spec.get("nfs", {}).get("version", ""))
        self.nfs_soft = QCheckBox("Don't freeze apps if the server goes away (soft mount)")
        self.nfs_soft.setToolTip("Apps get an error after about 45 seconds instead of waiting "
                                 "forever. Good for laptops; a write in progress can be lost.")
        self.nfs_soft.setChecked(bool(spec.get("nfs", {}).get("soft")))
        nform.addRow("NFS version:", self.nfs_version)
        nform.addRow(self.nfs_soft)
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
        self.bookmark = QCheckBox("Show in the file manager sidebar")
        self.bookmark.setChecked(spec.get("bookmark", setting(settings, "default_bookmark")))
        self.extra = QLineEdit(spec.get("extra_options", ""))
        self.extra.setPlaceholderText("advanced, comma-separated mount options")
        oform.addRow(self.start_at_boot)
        oform.addRow(self.automount)
        oform.addRow("Disconnect when idle:", self.idle)
        oform.addRow(self.read_only)
        oform.addRow(self.bookmark)
        oform.addRow("Extra options:", self.extra)
        opt_box.setLayout(oform)
        layout.addWidget(opt_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save" if self.editing else "Connect")
        self.test_btn = buttons.addButton("Test connection", QDialogButtonBox.ActionRole)
        self.test_btn.clicked.connect(self._test)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.tools = discovery.available()

        if prefilled:
            self.address.setText(format_remote(spec.get("type"), spec.get("server", ""),
                                               spec.get("share", "")))
            self.name.setText(spec.get("name", ""))
            self.mount_point.setText(spec.get("mount_point", ""))
            self.taken.discard(spec.get("mount_point", ""))

        self.address.textChanged.connect(self._address_changed)
        self.kind.currentIndexChanged.connect(self._address_changed)
        self.mount_point.textEdited.connect(lambda _t: setattr(self, "_mp_touched", True))
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self.auth.currentIndexChanged.connect(self._update_visibility)
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
        self.fast_hint.hide()
        if addr.valid and addr.kind == "smb":
            self._fast_timer.start()
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

    def _probe_nfs(self):
        from caracal import fastpath
        from caracal.background import run_async
        addr = self._parsed()
        if not (addr.valid and addr.kind == "smb"):
            return
        key = (addr.server, addr.share)

        def done(res, _err):
            now = self._parsed()
            if (now.server, now.share) != key or now.kind != "smb" or not res:
                return
            if res.get("export"):
                self._nfs_target = "%s:%s" % (addr.server, res["export"])
                self.fast_hint.setText(
                    "⚡ %s also shares this folder over NFS (<b>%s</b>), which is usually "
                    "faster on Linux. <a href='nfs'>Use NFS instead</a>"
                    % (addr.server, res["export"]))
            else:
                self.fast_hint.setText("⚡ %s also offers NFS, which is usually faster on Linux, "
                                       "but no export matching “%s” was found."
                                       % (addr.server, addr.share))
            self.fast_hint.show()
            self.adjustSize()
        run_async(self, lambda: fastpath.nfs_alternative(addr.server, addr.share), done)

    def _use_nfs(self, _link):
        target = getattr(self, "_nfs_target", "")
        if target:
            self.kind.setCurrentIndex(self.kind.findData("nfs"))
            self.address.setText(target)

    def _update_visibility(self, *_):
        is_smb = self.kind.currentData() == "smb"
        self.smb_box.setVisible(is_smb)
        self.nfs_box.setVisible(not is_smb)
        mode = self.auth.currentData()
        self.username.setEnabled(mode != "guest")
        self.password.setEnabled(mode == "password")
        self.domain.setEnabled(mode == "password")
        tool = "smbclient" if is_smb else "showmount"
        self.test_btn.setEnabled(self.tools.get(tool, False))
        self.test_btn.setToolTip("" if self.tools.get(tool) else
                                 "Install %s to test connections."
                                 % ("samba-client / smbclient" if is_smb else "nfs-utils"))
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
        mode = self.auth.currentData()
        if addr.kind == "smb" and mode == "password":
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

    def _browse_network(self):
        from caracal.browse import BrowseDialog
        dlg = BrowseDialog(self.username.text().strip(), self.password.text(),
                           self.domain.text().strip(), self)
        if not dlg.exec() or not dlg.result_share():
            return
        kind, server, share = dlg.result_share()
        self.kind.setCurrentIndex(self.kind.findData(kind))
        self.address.setText(format_remote(kind, server, share))
        user, pw, dom = dlg.credentials()
        if kind == "smb" and user and self.auth.currentData() == "password":
            self.username.setText(user)
            if pw:
                self.password.setText(pw)
            if dom:
                self.domain.setText(dom)

    def _test(self):
        addr = self._parsed()
        if not addr.valid:
            self._error(" ".join(addr.errors) or "Enter the folder to connect to.", self.address)
            return
        mode = self.auth.currentData()
        if addr.kind == "smb" and mode == "password" and not self.password.text():
            self._error("Enter the password to test the connection.", self.password)
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        if addr.kind == "smb":
            user, pw, dom = self.username.text().strip(), self.password.text(), \
                self.domain.text().strip()
            fn = lambda: discovery.test_smb(addr.server, addr.share, user, pw, dom,  # noqa: E731
                                            kerberos=mode == "kerberos", guest=mode == "guest")
        else:
            fn = lambda: discovery.test_nfs(addr.server, addr.share)  # noqa: E731
        run_async(self, fn, self._tested)

    def _tested(self, result, exc):
        self.test_btn.setText("Test connection")
        self._update_visibility()
        ok, msg = result if result else (False, str(exc))
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Test connection", msg)

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
            "bookmark": self.bookmark.isChecked(),
            "extra_options": self.extra.text().strip(),
        }
        password = None
        if addr.kind == "smb":
            mode = self.auth.currentData()
            spec["smb"] = {
                "guest": mode == "guest",
                "kerberos": mode == "kerberos",
                "seal": self.seal.isChecked(),
                "all_users": self.all_users.isChecked(),
                "username": self.username.text().strip(),
                "domain": self.domain.text().strip(),
                "version": self.smb_version.currentData(),
                "uid": os.getuid(),
                "gid": os.getgid(),
            }
            if mode == "password" and self.password.text():
                password = self.password.text()
        else:
            spec["nfs"] = {"version": self.nfs_version.currentData(),
                           "soft": self.nfs_soft.isChecked()}
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
        # When a drive can't connect (the root watchdog timer does the work)
        self.w_on_fail = _combo((("lock", "Lock the empty folder so nothing can write into it"),
                                 ("remove", "Remove the empty folder"),
                                 ("keep", "Leave the folder as it is")),
                                setting(settings, "watch_on_fail"))
        self.w_on_fail.setToolTip(
            "When a drive fails or is disconnected, its folder (e.g. /mnt/media) stays behind "
            "empty. Programs then see an empty share or write into it, filling your own disk. "
            "Locking it stops that; mounting still works normally.")
        self.w_retry = QSpinBox()
        self.w_retry.setRange(0, 1440)
        self.w_retry.setSuffix(" min")
        self.w_retry.setSpecialValueText("Never")
        self.w_retry.setValue(int(setting(settings, "watch_retry_minutes")))
        self.w_retry.setToolTip("Retry drives that should be connected but aren't, once the "
                                "server answers. Runs in the background even when Caracal "
                                "isn't open. Drives you disconnected stay disconnected.")
        self.w_dead = QCheckBox("Unmount shares that stop responding, then retry")
        self.w_dead.setChecked(bool(setting(settings, "watch_recover_dead")))
        self.w_dead.setToolTip("A share whose server went away can freeze every program that "
                               "touches it. Caracal checks connected shares every minute.")
        form.addRow("If a drive can't connect:", self.w_on_fail)
        form.addRow("Retry failed drives every:", self.w_retry)
        form.addRow(self.w_dead)
        self.auto_updates = QCheckBox("Check for updates automatically (once a day)")
        self.auto_updates.setChecked(setting(settings, "auto_check_updates"))
        form.addRow(self.auto_updates)
        tabs = QTabWidget()
        general = QWidget()
        general.setLayout(form)
        tabs.addTab(general, "General")
        layout.addWidget(tabs)

        tray_box = QWidget()
        tform = _form()
        self.tray_enabled = QCheckBox("Show Caracal in the system tray")
        self.tray_enabled.setChecked(setting(settings, "tray_enabled"))
        self.close_to_tray = QCheckBox("Closing the window keeps Caracal running in the tray")
        self.close_to_tray.setChecked(setting(settings, "close_to_tray"))
        self.notify = QCheckBox("Notify me when a drive fails or disconnects")
        self.notify.setChecked(setting(settings, "notify_failures"))
        self.login = QCheckBox("Start Caracal in the tray when I log in")
        self.login.setChecked(autostart.is_enabled())
        self.tray_style = _combo(TRAY_STYLES, setting(settings, "tray_icon_style"))
        self.tray_status = QCheckBox("Tray icon lights up when a drive is connected")
        self.tray_status.setChecked(setting(settings, "tray_show_status"))
        for w in (self.tray_enabled, self.close_to_tray, self.notify, self.login):
            tform.addRow(w)
        tform.addRow("Tray icon:", self.tray_style)
        tform.addRow(self.tray_status)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            status = deps.appindicator_status()
            text = "No system tray was found, so closing the window quits Caracal."
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
        tabs.addTab(tray_box, "System tray")

        idx_box = QWidget()
        iform = _form()
        self.rescan = QSpinBox()
        self.rescan.setRange(1, 1440)
        self.rescan.setSuffix(" min")
        self.rescan.setValue(setting(settings, "index/rescan_minutes"))
        self.rescan.setToolTip("How often to check indexed drives for changes made by other "
                               "computers (only changed folders are re-read).")
        self.full = QSpinBox()
        self.full.setRange(1, 24 * 30)
        self.full.setSuffix(" h")
        self.full.setValue(setting(settings, "index/full_hours"))
        self.full.setToolTip("How often to re-read every folder, which also catches files "
                             "edited in place on other computers.")
        self.max_content = QSpinBox()
        self.max_content.setRange(1, 1024)
        self.max_content.setSuffix(" MB")
        self.max_content.setValue(setting(settings, "index/max_content_mb"))
        self.max_content.setToolTip("Larger files are indexed by name only.")
        self.watch_local = QCheckBox("Update right away when I change files from this computer")
        self.watch_local.setChecked(setting(settings, "index/watch_local"))
        self.pause_battery = QCheckBox("Pause indexing on battery power")
        self.pause_battery.setChecked(setting(settings, "index/pause_on_battery"))
        self.mount_ready = QCheckBox("Connect on-access drives to index them")
        self.mount_ready.setToolTip("Drives that connect on first access are mounted when "
                                    "it's time to update their index.")
        self.mount_ready.setChecked(setting(settings, "index/mount_ready"))
        self.skip_system = QCheckBox("Skip recycle bins, snapshots and NAS system folders")
        self.skip_system.setChecked(setting(settings, "index/skip_system"))
        iform.addRow("Check for changes every:", self.rescan)
        iform.addRow("Full refresh every:", self.full)
        iform.addRow("Read contents of files up to:", self.max_content)
        for w in (self.watch_local, self.pause_battery, self.mount_ready, self.skip_system):
            iform.addRow(w)
        idx_box.setLayout(iform)
        tabs.addTab(idx_box, "Search")
        self._build_explorer_tabs(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _enable_indicator(self, btn, note):
        if deps.enable_appindicator():
            btn.setEnabled(False)
            note.setText("Tray extension turned on. If the Caracal icon doesn't appear, "
                         "log out and back in.")
        else:
            QMessageBox.warning(self, "Preferences", "Could not turn on the extension. "
                                "Use the Extensions app instead.")

    # -- Explorer -----------------------------------------------------------

    def _build_explorer_tabs(self, tabs):
        from caracal.explorer import fm1
        from caracal.explorer import theme as xtheme
        from caracal.explorer.navpane import LABEL_DEFAULTS
        s = self.settings

        def sv(key, default):
            v = s.value("explorer/" + key, default)
            if isinstance(default, bool):
                return v if isinstance(v, bool) else str(v).lower() in ("true", "1")
            return v

        page = QWidget()
        f = _form()
        self.x_mode = QCheckBox("Use Caracal as my file explorer")
        self.x_mode.setToolTip("Opening Caracal (and clicking the tray icon) opens the "
                               "Explorer window. Network drive management stays in the tray menu "
                               "and in Explorer's Network section.")
        self.x_mode.setChecked(setting(s, "explorer_mode"))
        self.x_default = QCheckBox("Make it the default file manager (open folders and "
                                   "“Show in folder” with it)")
        self.x_tray = QCheckBox("Keep the tray icon in explorer mode")
        self.x_tray.setToolTip("Caracal keeps running in the background either way (indexing, "
                               "drive notifications); File → Exit Caracal stops it.")
        self.x_tray.setChecked(setting(s, "tray_in_explorer_mode"))
        self.x_default.setChecked(fm1.is_default())
        self.x_layout = _combo((("win10", "Windows 10 (ribbon)"), ("win11", "Windows 11 (command bar)")),
                               sv("layout", "win10"))
        self.x_tabs = QCheckBox("Show tabs")
        self.x_tabs.setChecked(sv("tabs", sv("layout", "win10") != "win10"))
        self.x_start = _combo((("home", "Quick access / Home"), ("thispc", "This PC")),
                              sv("start", "home"))
        self.x_click = _combo((("double", "Double-click to open an item"),
                               ("single", "Single-click to open an item")),
                              "single" if sv("single_click", False) else "double")
        self.x_ext = QCheckBox("Show file name extensions")
        self.x_ext.setChecked(sv("show_ext", True))
        self.x_hidden = QCheckBox("Show hidden files and folders")
        self.x_hidden.setChecked(sv("show_hidden", False))
        self.x_confirm = QCheckBox("Ask before moving items to the Trash")
        self.x_confirm.setChecked(sv("confirm_delete", False))
        self.x_counts = QCheckBox("Show item counts in group headers")
        self.x_counts.setChecked(sv("group_counts", False))
        self.x_date = QLineEdit(s.value("explorer/date_format", "M/d/yyyy h:mm AP") or "M/d/yyyy h:mm AP")
        self.x_date.setToolTip("Qt date format, e.g. M/d/yyyy h:mm AP (US), dd/MM/yyyy HH:mm, yyyy-MM-dd HH:mm")
        self.x_size = _combo((("windows", "Always KB, like Windows (1,234 KB)"),
                              ("auto", "Automatic (1.2 MB)")), s.value("explorer/size_style", "windows"))
        self.x_7zip = _combo((("submenu", "In a “7-Zip” submenu (like Windows)"),
                              ("flat", "Directly in the right-click menu"),
                              ("off", "Don't show")), s.value("explorer/sevenzip_menu", "submenu"))
        self.x_terminal = QLineEdit(s.value("explorer/terminal", "") or "")
        self.x_terminal.setPlaceholderText("automatic (konsole, gnome-terminal, …)")
        for w in (self.x_mode, self.x_default, self.x_tray):
            f.addRow(w)
        f.addRow("Layout:", self.x_layout)
        f.addRow(self.x_tabs)
        f.addRow("Open new windows to:", self.x_start)
        f.addRow("Click items as follows:", self.x_click)
        for w in (self.x_ext, self.x_hidden, self.x_confirm, self.x_counts):
            f.addRow(w)
        f.addRow("Date format:", self.x_date)
        f.addRow("File sizes:", self.x_size)
        f.addRow("7-Zip commands:", self.x_7zip)
        f.addRow("Terminal app:", self.x_terminal)
        names = QGroupBox("Names in the navigation pane")
        nf = _form()
        self.x_labels = {}
        hints = {"quick": "Quick access", "home": "Home (Windows 11 layout)", "thispc": "This PC",
                 "network": "Network", "trash": "Trash (e.g. Recycle Bin)",
                 "homefolder": "Home folder"}
        for key, default in LABEL_DEFAULTS.items():
            e = QLineEdit(s.value("explorer/label_" + key, "") or "")
            e.setPlaceholderText(default)
            self.x_labels[key] = e
            nf.addRow(hints[key] + ":", e)
        names.setLayout(nf)
        f.addRow(names)
        page.setLayout(f)
        tabs.addTab(page, "Explorer")

        # Appearance
        t = xtheme.get()
        app = QWidget()
        af = _form()
        self.x_theme = _combo((("system", "Follow system"), ("light", "Light"), ("dark", "Dark")),
                              t.data.get("mode", "system"))
        self.x_icons = QComboBox()
        self._fill_icon_themes(t.data.get("icon_theme", ""))
        from PySide6.QtWidgets import QFontComboBox
        self.x_font = QFontComboBox()
        self.x_font_default = QCheckBox("Use the system font")
        self.x_font_default.setChecked(not t.data.get("font_family"))
        if t.data.get("font_family"):
            from PySide6.QtGui import QFont
            self.x_font.setCurrentFont(QFont(t.data["font_family"]))
        self.x_font_default.toggled.connect(lambda on: self.x_font.setEnabled(not on))
        self.x_font.setEnabled(not self.x_font_default.isChecked())
        self.x_font_size = QSpinBox()
        self.x_font_size.setRange(0, 24)
        self.x_font_size.setSpecialValueText("System size")
        self.x_font_size.setValue(int(t.data.get("font_size") or 0))
        self.x_row = QSpinBox()
        self.x_row.setRange(16, 48)
        self.x_row.setSuffix(" px")
        self.x_row.setValue(int(t.data.get("row_height", 24)))
        self.x_accent = QLineEdit(t.data.get("accent", "#0067C0"))
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._pick_accent)
        acc_row = QWidget()
        al = QHBoxLayout(acc_row)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.x_accent, 1)
        al.addWidget(pick)
        from caracal import icons as app_icons
        self.x_app_icon = QComboBox()
        self.x_app_icon.setIconSize(QSize(24, 24))
        self._app_icon_file = str(self.settings.value("app_icon_file", "") or "")
        for key, label in app_icons.APP_ICON_CHOICES:
            icon = app_icons.default_app_icon() if key == "caracal" else \
                app_icons.icon_for_choice(key, self._app_icon_file)
            self.x_app_icon.addItem(icon or QIcon(), label, key)
        self.x_app_icon.setCurrentIndex(max(0, self.x_app_icon.findData(
            app_icons.app_icon_choice(self.settings))))
        self.x_app_icon.setToolTip("Caracal's icon in the taskbar, app menu and window titles. "
                                   "\"From my theme\" uses the File Explorer icon from a theme "
                                   "pack you imported (or your icon theme's file manager icon).")
        self.x_app_icon.activated.connect(self._app_icon_picked)
        af.addRow("Theme:", self.x_theme)
        af.addRow("App icon:", self.x_app_icon)
        af.addRow("Icon theme:", self.x_icons)
        af.addRow(self.x_font_default)
        af.addRow("Font:", self.x_font)
        af.addRow("Font size:", self.x_font_size)
        af.addRow("Row height:", self.x_row)
        af.addRow("Accent color:", acc_row)
        info = QLabel("Custom icons, fonts and extra styling go in the theme folder: drop "
                      "files named after icon slots (this-pc.png, folder.ico, ext-pdf.svg, …) "
                      "into icons/, fonts into fonts/. README.txt there lists every slot.")
        info.setWordWrap(True)
        af.addRow(info)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        open_btn = QPushButton("Open theme folder")
        open_btn.clicked.connect(self._open_theme_folder)
        rl.addWidget(open_btn)
        import_btn = QPushButton("Import theme…")
        import_btn.setToolTip("Import a downloaded theme (.zip, .tar.gz, .tar.xz), a KDE color "
                              "scheme (.colors), or a folder: icon themes, color schemes, fonts "
                              "and icons are picked out of it")
        import_btn.clicked.connect(self._import_theme)
        rl.addWidget(import_btn)
        reload_btn = QPushButton("Reload theme")
        reload_btn.setToolTip("Pick up icons and fonts you just added to the theme folder")
        reload_btn.clicked.connect(self._reload_theme)
        rl.addWidget(reload_btn)
        rl.addStretch(1)
        af.addRow(row)
        app.setLayout(af)
        tabs.addTab(app, "Appearance")

    def _app_icon_picked(self, _i):
        if self.x_app_icon.currentData() != "custom":
            return
        p, _f = QFileDialog.getOpenFileName(
            self, "Choose an icon", os.path.dirname(self._app_icon_file) or os.path.expanduser("~"),
            "Images (*.svg *.png *.ico *.xpm *.jpg)")
        if p:
            from PySide6.QtGui import QIcon as _QIcon
            self._app_icon_file = p
            self.x_app_icon.setItemIcon(self.x_app_icon.currentIndex(), _QIcon(p))
        elif not self._app_icon_file:
            self.x_app_icon.setCurrentIndex(0)

    def _pick_accent(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.x_accent.text() or "#0067C0"), self, "Accent color")
        if c.isValid():
            self.x_accent.setText(c.name())

    def _fill_icon_themes(self, current):
        from caracal.explorer import theme as xtheme
        self.x_icons.clear()
        self.x_icons.addItem("Same as the desktop", "")
        for folder, name in xtheme.list_icon_themes():
            self.x_icons.addItem(name if name == folder else "%s (%s)" % (name, folder), folder)
        idx = self.x_icons.findData(current)
        self.x_icons.setCurrentIndex(max(0, idx))

    def _import_theme(self):
        from PySide6.QtWidgets import QFileDialog
        from caracal.explorer import theme as xtheme
        path, _ = QFileDialog.getOpenFileName(
            self, "Import theme", os.path.expanduser("~/Downloads"),
            "Themes (*.zip *.tar *.tar.gz *.tgz *.tar.xz *.txz *.tar.bz2 *.colors);;All files (*)")
        if not path:
            return
        try:
            summary = xtheme.import_theme(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import theme", "Couldn't import it: %s" % exc)
            return
        t = xtheme.reload()
        self._fill_icon_themes(t.data.get("icon_theme", ""))
        mode_idx = self.x_theme.findData(t.data.get("mode", "system"))
        self.x_theme.setCurrentIndex(max(0, mode_idx))
        self.x_accent.setText(t.data.get("accent", self.x_accent.text()))
        found = []
        if summary["icon_themes"]:
            found.append("icon theme: " + ", ".join(summary["icon_themes"]))
        if summary["colors"]:
            found.append("color scheme: " + ", ".join(summary["colors"]))
        if summary["fonts"]:
            found.append("%d font(s)" % summary["fonts"])
        if summary["icons"]:
            found.append("%d icon(s)" % summary["icons"])
        if not found:
            QMessageBox.information(self, "Import theme", "Nothing Caracal can use was found in "
                                    "that file (it looks for icon themes, .colors schemes, fonts "
                                    "and icons named after slots). Window decorations, Plasma "
                                    "styles and Kvantum themes don't apply to an app's contents.")
            return
        parent = self.parent()
        ctl = getattr(parent, "ctl", parent)
        if hasattr(ctl, "apply_explorer_settings"):
            ctl.apply_explorer_settings()
        QMessageBox.information(self, "Import theme", "Imported and applied:\n\n" +
                                "\n".join(found) + "\n\nChange anything here, then OK.")

    def _reload_theme(self):
        from caracal.explorer import theme as xtheme
        t = xtheme.reload()
        current = self.x_font.currentFont()
        self.x_font.setCurrentFont(current)   # the list now includes newly added fonts
        parent = self.parent()
        ctl = getattr(parent, "ctl", parent)
        if hasattr(ctl, "apply_explorer_settings"):
            ctl.apply_explorer_settings()
        added = ", ".join(t.fonts) if t.fonts else "none"
        QMessageBox.information(self, "Appearance", "Theme reloaded.\n\nFonts from the theme "
                                "folder: %s" % added)

    def _open_theme_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from caracal.explorer import theme as xtheme
        QDesktopServices.openUrl(QUrl.fromLocalFile(xtheme.ensure_theme_dir()))

    def _save_explorer(self):
        import json as _json
        from caracal.explorer import fm1
        from caracal.explorer import theme as xtheme
        s = self.settings
        s.setValue("explorer_mode", self.x_mode.isChecked())
        s.setValue("tray_in_explorer_mode", self.x_tray.isChecked())
        s.setValue("explorer/layout", self.x_layout.currentData())
        s.setValue("explorer/tabs", self.x_tabs.isChecked())
        s.setValue("explorer/start", self.x_start.currentData())
        s.setValue("explorer/single_click", self.x_click.currentData() == "single")
        s.setValue("explorer/show_ext", self.x_ext.isChecked())
        s.setValue("explorer/show_hidden", self.x_hidden.isChecked())
        s.setValue("explorer/confirm_delete", self.x_confirm.isChecked())
        s.setValue("explorer/group_counts", self.x_counts.isChecked())
        s.setValue("explorer/sevenzip_menu", self.x_7zip.currentData())
        s.setValue("explorer/date_format", self.x_date.text().strip() or "M/d/yyyy h:mm AP")
        s.setValue("explorer/size_style", self.x_size.currentData())
        s.setValue("explorer/terminal", self.x_terminal.text().strip())
        for key, e in self.x_labels.items():
            s.setValue("explorer/label_" + key, e.text().strip())
        if self.x_default.isChecked() != fm1.is_default():
            err = fm1.set_default(s, self.x_default.isChecked())
            if err:
                QMessageBox.warning(self, "Preferences", err)
        d = xtheme.ensure_theme_dir()
        path = os.path.join(d, "theme.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = _json.load(fh)
        except (OSError, ValueError):
            data = {}
        self.settings.setValue("app_icon", self.x_app_icon.currentData())
        self.settings.setValue("app_icon_file", self._app_icon_file)
        data.update({"mode": self.x_theme.currentData(),
                     "font_family": "" if self.x_font_default.isChecked() else self.x_font.currentFont().family(),
                     "font_size": self.x_font_size.value(), "row_height": self.x_row.value(),
                     "icon_theme": self.x_icons.currentData() or "",
                     "accent": self.x_accent.text().strip() or "#0067C0"})
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(data, fh, indent=2)

    def _tray_toggled(self, on):
        for w in (self.close_to_tray, self.notify, self.tray_style, self.tray_status):
            w.setEnabled(on)

    def _save(self):
        root = self.mount_root.text().strip()
        if not root.startswith("/"):
            QMessageBox.warning(self, "Preferences", "The default folder must be an absolute path.")
            return
        s = self.settings
        s.setValue("theme", self.theme.currentData())
        s.setValue("mount_root", "/" + root.strip("/"))
        s.setValue("watch_on_fail", self.w_on_fail.currentData())
        s.setValue("watch_retry_minutes", self.w_retry.value())
        s.setValue("watch_recover_dead", self.w_dead.isChecked())
        s.setValue("refresh_seconds", self.refresh.value())
        s.setValue("default_automount", self.automount.isChecked())
        s.setValue("default_start_at_boot", self.start_at_boot.isChecked())
        s.setValue("default_idle_minutes", self.idle.value())
        s.setValue("confirm_remove", self.confirm.isChecked())
        s.setValue("tray_enabled", self.tray_enabled.isChecked())
        s.setValue("close_to_tray", self.close_to_tray.isChecked())
        s.setValue("notify_failures", self.notify.isChecked())
        s.setValue("tray_icon_style", self.tray_style.currentData())
        s.setValue("tray_show_status", self.tray_status.isChecked())
        s.setValue("auto_check_updates", self.auto_updates.isChecked())
        s.setValue("index/rescan_minutes", self.rescan.value())
        s.setValue("index/full_hours", self.full.value())
        s.setValue("index/max_content_mb", self.max_content.value())
        s.setValue("index/watch_local", self.watch_local.isChecked())
        s.setValue("index/pause_on_battery", self.pause_battery.isChecked())
        s.setValue("index/mount_ready", self.mount_ready.isChecked())
        s.setValue("index/skip_system", self.skip_system.isChecked())
        self._save_explorer()
        s.sync()
        try:
            autostart.set_enabled(self.login.isChecked())
        except OSError as exc:
            QMessageBox.warning(self, "Preferences", "Could not change start at login: %s" % exc)
        self.accept()


def changelog_text():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", "CHANGELOG.md"),
                 "/usr/share/caracal/CHANGELOG.md"):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    return "# Changelog\n\nThe changelog file could not be found."


class ChangelogDialog(QDialog):
    """Local changelog. When an update is available its release notes go on top,
    with a button to install it (or open the download page)."""

    INSTALL = 2
    DOWNLOAD_PAGE = 3

    def __init__(self, parent=None, update=None, can_install=False, error=""):
        super().__init__(parent)
        self.setWindowTitle("%s %s — Changelog" % (APP_NAME, __version__))
        self.resize(580, 520)
        layout = QVBoxLayout(self)
        text = ""
        if update:
            text += "# Version %s is available\n\n%s\n\n---\n\n" % (
                update["version"], update.get("notes") or "_No release notes._")
        elif error:
            text += "_%s_\n\n---\n\n" % error
        text += changelog_text()
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        if update:
            if can_install:
                btn = buttons.addButton("Install %s" % update["version"],
                                        QDialogButtonBox.AcceptRole)
                btn.clicked.connect(lambda: self.done(self.INSTALL))
            else:
                btn = buttons.addButton("Open download page", QDialogButtonBox.AcceptRole)
                btn.clicked.connect(lambda: self.done(self.DOWNLOAD_PAGE))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
