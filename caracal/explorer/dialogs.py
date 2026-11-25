# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Explorer dialogs: operation progress, replace/skip, properties, network location,
open with."""

import grp
import os
import pwd
import stat
import time

from PySide6.QtCore import QFileInfo, QMimeDatabase, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from caracal import user_mounts
from caracal.background import run_async
from caracal.explorer import fileops, openwith, theme
from caracal.model import format_size
from caracal.widgets import add_reveal_action

VERBS = {"copy": "Copying", "move": "Moving", "trash": "Moving to Trash", "delete": "Deleting"}


class OperationDialog(QDialog):
    """Small non-modal progress window, like Explorer's copy dialog."""

    def __init__(self, op, dest_label="", parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        self.op = op
        self.setWindowTitle(VERBS.get(op.kind, "Working"))
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        n = len(op.sources)
        what = "%d item%s" % (n, "" if n == 1 else "s")
        self.head = QLabel("%s %s%s" % (VERBS.get(op.kind, ""), what,
                                        (" to " + dest_label) if dest_label else ""))
        f = self.head.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.head.setFont(f)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.detail = QLabel("Preparing…")
        self.detail.setWordWrap(True)
        lay.addWidget(self.head)
        lay.addWidget(self.bar)
        lay.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._cancel)
        lay.addWidget(buttons)
        op.progress.connect(self._progress)
        op.conflict.connect(self._conflict)
        op.done.connect(lambda _r: self.accept())

    def _progress(self, p):
        if p["total_bytes"]:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * p["bytes"] / p["total_bytes"]))
            left = (p["total_bytes"] - p["bytes"]) / p["rate"] if p["rate"] > 0 else 0
            self.detail.setText("%s · %s of %s · %s/s · about %s left" % (
                p["current"], format_size(p["bytes"]), format_size(p["total_bytes"]),
                format_size(int(p["rate"])), _duration(left)))
            self.head.setText(self.head.text().split(" — ")[0] + " — %d%% complete"
                              % (100 * p["bytes"] // max(1, p["total_bytes"])))
        elif p["total_files"]:
            self.bar.setRange(0, p["total_files"])
            self.bar.setValue(p["files"])
            self.detail.setText(p["current"])
        else:
            self.detail.setText(p["current"] or "Preparing…")

    def _conflict(self, info):
        dlg = ConflictDialog(info, self)
        choice, all_ = dlg.ask()
        self.op.answer(choice, all_)

    def _cancel(self):
        self.op.cancel()
        self.detail.setText("Cancelling…")

    def closeEvent(self, e):
        self.op.cancel()
        super().closeEvent(e)


def _adopt(dialog, parent):
    """Use the Explorer window's palette (its light/dark choice) for this dialog."""
    if parent is not None:
        dialog.setPalette(parent.palette())


def _duration(secs):
    secs = int(secs)
    if secs < 60:
        return "%d seconds" % max(secs, 1)
    if secs < 3600:
        return "%d minutes" % (secs // 60)
    return "%d h %d min" % (secs // 3600, (secs % 3600) // 60)


class ConflictDialog(QDialog):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        self.setWindowTitle("Replace or Skip Files")
        self.choice = fileops.SKIP
        lay = QVBoxLayout(self)
        name = info.get("name", "")
        lab = QLabel("The destination already has a file named “%s”" % name)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        if "src_size" in info:
            cmp_ = QLabel("New: %s, %s\nExisting: %s, %s" % (
                format_size(info["src_size"]), time.strftime("%Y-%m-%d %H:%M", time.localtime(info["src_mtime"])),
                format_size(info["dst_size"]), time.strftime("%Y-%m-%d %H:%M", time.localtime(info["dst_mtime"]))))
            cmp_.setStyleSheet("color: %s" % theme.get().colors()["subtle"])
            lay.addWidget(cmp_)
        for text, value in (("✓  Replace the file in the destination", fileops.REPLACE),
                            ("↷  Skip this file", fileops.SKIP),
                            ("⧉  Keep both (the new one gets a number)", fileops.KEEP_BOTH)):
            b = QPushButton(text)
            b.setStyleSheet("text-align: left; padding: 8px;")
            b.clicked.connect(lambda _c=False, v=value: self._pick(v))
            lay.addWidget(b)
        self.all = QCheckBox("Do this for all conflicts")
        lay.addWidget(self.all)
        cancel = QPushButton("Cancel the whole operation")
        cancel.setFlat(True)
        cancel.clicked.connect(lambda: self._pick(fileops.CANCEL))
        lay.addWidget(cancel)

    def _pick(self, v):
        self.choice = v
        self.accept()

    def ask(self):
        if not self.exec():
            return fileops.CANCEL, False
        return self.choice, self.all.isChecked()


class PropertiesDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        self.paths = paths
        first = QFileInfo(paths[0])
        self.setWindowTitle(("%s Properties" % first.fileName()) if len(paths) == 1
                            else "%d items Properties" % len(paths))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        gen = QWidget()
        form = QFormLayout(gen)
        icon = QLabel()
        icon.setPixmap((theme.get().icon("folder") if first.isDir()
                        else theme.get().file_icon(first)).pixmap(48, 48))
        name = QLabel(first.fileName() if len(paths) == 1 else "%d items" % len(paths))
        form.addRow(icon, name)
        mt = QMimeDatabase().mimeTypeForFile(paths[0], QMimeDatabase.MatchExtension)
        form.addRow("Type:", QLabel("File folder" if first.isDir() else (mt.comment() or mt.name())))
        form.addRow("Location:", _sel(os.path.dirname(paths[0])))
        self.size = QLabel("Calculating…")
        self.contains = QLabel("")
        form.addRow("Size:", self.size)
        form.addRow("Contains:", self.contains)
        if len(paths) == 1:
            st = os.lstat(paths[0])
            fmt = "%A, %B %d, %Y, %H:%M:%S"
            form.addRow("Modified:", QLabel(time.strftime(fmt, time.localtime(st.st_mtime))))
            form.addRow("Accessed:", QLabel(time.strftime(fmt, time.localtime(st.st_atime))))
            if hasattr(st, "st_birthtime"):
                form.addRow("Created:", QLabel(time.strftime(fmt, time.localtime(st.st_birthtime))))
        tabs.addTab(gen, "General")

        if len(paths) == 1:
            perm = QWidget()
            grid = QGridLayout(perm)
            st = os.stat(paths[0])
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            try:
                group = grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                group = str(st.st_gid)
            grid.addWidget(QLabel("Owner: %s    Group: %s" % (owner, group)), 0, 0, 1, 4)
            for c, h in enumerate(("", "Read", "Write", "Run / open")):
                grid.addWidget(QLabel(h), 1, c)
            self.bits = {}
            for r, (who, shift) in enumerate((("Owner", 6), ("Group", 3), ("Everyone", 0)), 2):
                grid.addWidget(QLabel(who), r, 0)
                for c, bit in enumerate((4, 2, 1), 1):
                    cb = QCheckBox()
                    cb.setChecked(bool(st.st_mode & (bit << shift)))
                    cb.setEnabled(os.access(paths[0], os.W_OK) or st.st_uid == os.getuid())
                    self.bits[(shift, bit)] = cb
                    grid.addWidget(cb, r, c)
            grid.setRowStretch(6, 1)
            tabs.addTab(perm, "Permissions")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        run_async(self, lambda: self._measure(paths), self._measured)

    @staticmethod
    def _measure(paths):
        files = dirs = size = 0
        for p in paths:
            if os.path.isdir(p) and not os.path.islink(p):
                for root, ds, fs in os.walk(p):
                    dirs += len(ds)
                    for f in fs:
                        files += 1
                        try:
                            size += os.lstat(os.path.join(root, f)).st_size
                        except OSError:
                            pass
            else:
                files += 1
                try:
                    size += os.lstat(p).st_size
                except OSError:
                    pass
        return files, dirs, size

    def _measured(self, result, error):
        if not result:
            self.size.setText("Unknown")
            return
        files, dirs, size = result
        self.size.setText("%s (%s bytes)" % (format_size(size), format(size, ",")))
        self.contains.setText("%s files, %s folders" % (format(files, ","), format(dirs, ",")))

    def _apply(self):
        if hasattr(self, "bits"):
            path = self.paths[0]
            mode = os.stat(path).st_mode
            new = stat.S_IMODE(mode)
            for (shift, bit), cb in self.bits.items():
                if cb.isChecked():
                    new |= bit << shift
                else:
                    new &= ~(bit << shift)
            if new != stat.S_IMODE(mode):
                try:
                    os.chmod(path, new)
                except OSError as exc:
                    QMessageBox.warning(self, "Properties", "Couldn't change permissions: %s"
                                        % exc.strerror)
                    return
        self.accept()


def _sel(text):
    lab = QLabel(text)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setWordWrap(True)
    return lab


class LocationDialog(QDialog):
    """Add or edit a cloud drive or an SFTP / FTP / FTPS / WebDAV location (per-user mount)."""

    def __init__(self, location=None, kind="sftp", parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        spec = dict(location.spec) if location else {"type": kind}
        self.loc_id = spec.get("id")
        self.editing = location is not None
        self.setWindowTitle("Edit network location" if self.editing else "Add network location")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.kind = QComboBox()
        self._spec0 = spec
        for k, label in user_mounts.KINDS.items():
            if k == "rclone" and spec.get("type") != "rclone":
                continue      # imported remotes come from "Import rclone mounts"
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(spec.get("type", kind))))
        self.name = QLineEdit(spec.get("name", ""))
        self.host = QLineEdit(spec.get("host", ""))
        self.host.setPlaceholderText("server.example.com or 192.168.1.10")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(spec.get("port") or user_mounts.DEFAULT_PORTS.get(spec.get("type", kind), 22))
        self.path = QLineEdit(spec.get("path", ""))
        self.path.setPlaceholderText("folder on the server (empty = your home folder)")
        self.url = QLineEdit(spec.get("url", ""))
        self.url.setPlaceholderText("https://cloud.example.com/remote.php/dav/files/me/")
        self.user = QLineEdit(spec.get("user", ""))
        # cloud drive
        from caracal import cloud
        self.server = QLineEdit(spec.get("server", ""))
        self.server.setPlaceholderText("https://cloud.example.com")
        self.product = QComboBox()
        for key, preset in cloud.PRESETS.items():
            self.product.addItem(preset["label"], key)
        self.product.setCurrentIndex(max(0, self.product.findData(spec.get("product", "nextcloud"))))
        detect = QPushButton("Detect")
        detect.setToolTip("Ask the server what it is (reads its status.php)")
        detect.clicked.connect(self._detect)
        prod_row = QWidget()
        pl = QHBoxLayout(prod_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.product, 1)
        pl.addWidget(detect)
        self.signin_btn = QPushButton("Sign in with browser…")
        self.signin_btn.clicked.connect(self._browser_sign_in)
        self.signin_note = QLabel()
        self.signin_note.setWordWrap(True)
        sign_row = QWidget()
        sl = QVBoxLayout(sign_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.signin_btn)
        sl.addWidget(self.signin_note)
        self.server.textChanged.connect(self._suggest)
        self.product.currentIndexChanged.connect(self._update)
        self._flow_cancel = False
        # files on demand (all rclone-based kinds)
        self.vfs = QCheckBox("Files on demand: list everything, download a file when it's "
                             "opened, keep it cached")
        self.vfs.setChecked(spec.get("vfs_full", user_mounts.VFS_DEFAULTS["vfs_full"]))
        self.vfs.setToolTip("Like Windows' Files On-Demand. Any program that reads a file gets "
                            "the real file (it downloads on first read); saves upload in the "
                            "background. Off: files are read straight from the server.")
        self.cache_gb = QSpinBox()
        self.cache_gb.setRange(1, 100000)
        self.cache_gb.setSuffix(" GB")
        self.cache_gb.setValue(spec.get("cache_gb", user_mounts.VFS_DEFAULTS["cache_gb"]))
        self.cache_gb.setToolTip("When the cache is full, the least recently used files are "
                                 "removed from this computer (they stay listed and download "
                                 "again when opened).")
        self.cache_days = QSpinBox()
        self.cache_days.setRange(1, 3650)
        self.cache_days.setSuffix(" days")
        self.cache_days.setValue(spec.get("cache_days", user_mounts.VFS_DEFAULTS["cache_days"]))
        self.cache_days.setToolTip("Files not opened for this long are removed from this "
                                   "computer (they stay listed).")
        cache_row = QWidget()
        cl = QHBoxLayout(cache_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.cache_gb)
        cl.addWidget(QLabel("keep files up to"))
        cl.addWidget(self.cache_days)
        cl.addStretch(1)
        self.vfs.toggled.connect(cache_row.setEnabled)
        cache_row.setEnabled(self.vfs.isChecked())
        self.auth = QComboBox()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        add_reveal_action(self.password)
        if self.editing:
            self.password.setPlaceholderText("(unchanged)")
        self.key = QLineEdit(spec.get("key_file", ""))
        self.key.setPlaceholderText("default SSH key / agent")
        kb = QPushButton("Choose…")
        kb.clicked.connect(self._pick_key)
        key_row = QWidget()
        kl = QHBoxLayout(key_row)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(self.key, 1)
        kl.addWidget(kb)
        self.mp = QLineEdit(spec.get("mount_point", ""))
        self.mp.textEdited.connect(lambda _t: setattr(self, "_mp_touched", True))
        self.reconnect = QCheckBox("Reconnect when I sign in")
        self.reconnect.setChecked(spec.get("start_at_login", True))
        self.ro = QCheckBox("Read-only")
        self.ro.setChecked(spec.get("read_only", False))
        self.bookmark = QCheckBox("Show in other file managers' sidebar (Dolphin, Files, …)")
        self.bookmark.setChecked(spec.get("bookmark", spec.get("type", kind) in ("cloud", "rclone")))
        self.rows = {}
        for label, w in (("Type:", self.kind), ("Server:", self.server),
                         ("Server type:", prod_row), ("Sign in:", sign_row),
                         ("Name:", self.name), ("Server:", self.host),
                         ("Port:", self.port), ("Address:", self.url), ("Folder:", self.path),
                         ("User name:", self.user), ("Sign in with:", self.auth),
                         ("Password:", self.password), ("SSH key:", key_row),
                         ("Local folder:", self.mp)):
            form.addRow(label, w)
            self.rows[w] = label
        form.addRow(self.reconnect)
        form.addRow(self.ro)
        form.addRow(self.bookmark)
        form.addRow(self.vfs)
        form.addRow("Cache:", cache_row)
        self._prod_row, self._sign_row, self._cache_row = prod_row, sign_row, cache_row
        self.form = form
        lay.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: %s" % theme.get().colors()["subtle"])
        lay.addWidget(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save" if self.editing else "Connect")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._auth_value = spec.get("auth")
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.auth.currentIndexChanged.connect(self._update)
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self.host.textChanged.connect(self._suggest)
        self._name_touched = self.editing
        self._kind_changed(initial=True)

    def _kind_changed(self, *_a, initial=False):
        k = self.kind.currentData()
        self.auth.blockSignals(True)
        self.auth.clear()
        if k == "sftp":
            self.auth.addItem("SSH key or agent (recommended)", "key")
            self.auth.addItem("Password", "password")
        elif k in ("cloud", "rclone"):
            self.auth.addItem("App password", "password")
        else:
            self.auth.addItem("Password", "password")
            self.auth.addItem("Anonymous / no password", "anonymous")
        idx = self.auth.findData(self._auth_value) if initial else 0
        self.auth.setCurrentIndex(max(0, idx))
        self.auth.blockSignals(False)
        if (not initial or not self.editing) and user_mounts.DEFAULT_PORTS.get(k):
            self.port.setValue(user_mounts.DEFAULT_PORTS[k])
        tools = user_mounts.tools_available()
        tool = user_mounts.tool_for(k)
        self.hint.setText(
            ("" if tools.get(tool) else "⚠ %s isn't installed. Install the %s package.\n"
             % (tool, "fuse-sshfs (Fedora) / sshfs (Debian)" if tool == "sshfs" else "rclone"))
            + ("SFTP also covers servers you'd reach with SCP. The server's key is trusted the "
               "first time you connect." if k == "sftp" else
               "Imported from your own rclone setup; its settings were copied as they were."
               if k == "rclone" else
               "Mounted with rclone; the password is stored obscured in a file only you can read."))
        self._update()
        if not initial and not self.editing:
            self._suggest()

    def _update(self, *_):
        k = self.kind.currentData()
        auth = self.auth.currentData()
        plain = k in ("sftp", "ftp", "ftps")
        show = {self.host: plain, self.port: plain, self.path: plain,
                self.url: k == "webdav", self.server: k == "cloud",
                self._prod_row: k == "cloud", self._sign_row: k == "cloud",
                self.auth: k not in ("cloud", "rclone"),
                self.password: auth == "password" and k != "rclone",
                self.key.parentWidget(): k == "sftp" and auth == "key",
                self.user: auth != "anonymous" and k != "rclone",
                self.vfs: k in user_mounts.RCLONE_KINDS,
                self._cache_row: k in user_mounts.RCLONE_KINDS}
        for w, vis in show.items():
            self.form.setRowVisible(w, vis)
        if k == "cloud":
            from caracal import cloud
            prod = self.product.currentData()
            self.signin_btn.setVisible(cloud.PRESETS[prod]["flow"])
            if not self.signin_note.property("signed"):
                self.signin_note.setText(cloud.SIGN_IN_HELP[prod])
            self.form.labelForField(self.password).setText(
                "App token:" if prod == "ocis" else "App password:")
        else:
            self.form.labelForField(self.password).setText("Password:")

    def _cloud_name(self):
        from urllib.parse import urlsplit
        from caracal import cloud
        host = urlsplit(cloud.normalize_server(self.server.text())).hostname or ""
        labels = [l for l in host.split(".") if l]
        if not host or ":" in host or all(l.isdigit() for l in labels):
            return "Cloud Drive"                  # an IP address says nothing useful
        if len(labels) < 2:
            return (labels[0].capitalize() + " Drive") if labels else ""
        main, first = labels[-2], labels[0]
        tail = first if first not in (main, "www", "cloud", "nextcloud", "owncloud", "files") \
            else "cloud"
        return "%s %s" % (main.capitalize(), tail.capitalize())

    def _suggest(self, *_):
        cloud_kind = self.kind.currentData() == "cloud"
        if not self._name_touched:
            self.name.setText(self._cloud_name() if cloud_kind else self.host.text().strip())
        if cloud_kind and not self.editing and not getattr(self, "_mp_touched", False):
            import re
            # space-free folder in your home, e.g. ~/SpazcatDrive
            leaf = re.sub(r"[^A-Za-z0-9]+", "", self.name.text().title()) or "CloudDrive"
            base = mp = os.path.join(os.path.expanduser("~"), leaf)
            n = 2
            while os.path.ismount(mp) or (os.path.isdir(mp) and os.listdir(mp)):
                mp = "%s%d" % (base, n)
                n += 1
            self.mp.setText(mp)
            return
        if not self.editing and not getattr(self, "_mp_touched", False):
            import re
            leaf = (self.name.text().strip() or self.host.text().strip() or "location")
            leaf = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip("-.") or "location"
            mp = "/mnt/" + leaf
            n = 2
            while os.path.ismount(mp) or (os.path.isdir(mp) and os.listdir(mp)):
                mp = "/mnt/%s-%d" % (leaf, n)
                n += 1
            self.mp.setText(mp)

    def _pick_key(self):
        p, _ = QFileDialog.getOpenFileName(self, "SSH private key", os.path.expanduser("~/.ssh"))
        if p:
            self.key.setText(p)

    def spec(self):
        k = self.kind.currentData()
        s = {"type": k, "name": self.name.text().strip(), "user": self.user.text().strip(),
             "auth": self.auth.currentData(), "mount_point": self.mp.text().strip(),
             "start_at_login": self.reconnect.isChecked(), "read_only": self.ro.isChecked(),
             "bookmark": self.bookmark.isChecked(),
             "key_file": self.key.text().strip()}
        if self.loc_id:
            s["id"] = self.loc_id
        if k == "webdav":
            s["url"] = self.url.text().strip()
            s["vendor"] = self._spec0.get("vendor", "other")
        elif k == "cloud":
            s.update(server=self.server.text().strip(), product=self.product.currentData(),
                     auth="password")
            if self._spec0.get("server") == s["server"] and self._spec0.get("user") == s["user"] \
                    and self._spec0.get("product") == s["product"]:
                s["url"] = self._spec0.get("url", "")
        elif k == "rclone":
            for key in ("remote_name", "rclone_type", "path", "imported_from"):
                s[key] = self._spec0.get(key, "")
            s["auth"] = "imported"
        else:
            s.update(host=self.host.text().strip(), port=self.port.value(),
                     path=self.path.text().strip())
        if k in user_mounts.RCLONE_KINDS:
            s.update(vfs_full=self.vfs.isChecked(), cache_gb=self.cache_gb.value(),
                     cache_days=self.cache_days.value())
        return s

    # -- cloud drive helpers ---------------------------------------------------------------

    def _detect(self):
        from caracal import cloud
        server = self.server.text().strip()
        if not server:
            return
        self.signin_note.setText("Asking the server…")

        def done(res, err):
            if err is not None:
                self.signin_note.setText("⚠ %s" % err)
                return
            product, status = res
            if product is None:
                self.signin_note.setText("⚠ Unknown server (%s). Choose the kind yourself."
                                         % (status.get("productname") or "no product name"))
                return
            self.product.setCurrentIndex(self.product.findData(product))
            ver = status.get("versionstring") or status.get("version") or ""
            self.signin_note.setText("✓ %s %s found. %s" % (cloud.PRESETS[product]["label"], ver,
                                                          cloud.SIGN_IN_HELP[product]))
        run_async(self, lambda: cloud.detect(server), done)

    def _browser_sign_in(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from caracal import cloud
        server = self.server.text().strip()
        if not server:
            QMessageBox.information(self, self.windowTitle(), "Enter the server address first.")
            return
        self.signin_btn.setEnabled(False)
        self.signin_note.setText("Opening the sign-in page…")
        self._flow_cancel = False

        def started(flow, err):
            if err is not None:
                self.signin_btn.setEnabled(True)
                self.signin_note.setText("⚠ %s" % err)
                return
            QDesktopServices.openUrl(QUrl(flow["login"]))
            self.signin_note.setText("Approve Caracal in your browser, then come back here. "
                                     "Waiting…")
            run_async(self, lambda: cloud.login_flow_poll(
                flow, should_stop=lambda: self._flow_cancel), finished)

        def finished(res, err):
            self.signin_btn.setEnabled(True)
            if err is not None:
                self.signin_note.setText("⚠ %s" % err)
                return
            self.user.setText(res["user"])
            self.password.setText(res["password"])
            self.signin_note.setProperty("signed", True)
            self.signin_note.setText("✓ Signed in as %s. An app password for Caracal was "
                                     "created (you can revoke it in your account's security "
                                     "settings)." % res["user"])
        run_async(self, lambda: cloud.login_flow_start(server), started)

    def done(self, result):
        self._flow_cancel = True        # stop waiting for a browser sign-in
        super().done(result)

    def password_value(self):
        if self.auth.currentData() != "password":
            return None
        return self.password.text() or None

    def _accept(self):
        if not self.mp.text().strip():
            self._suggest()
        try:
            user_mounts.validate(self.spec())
        except user_mounts.LocationError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        k = self.kind.currentData()
        if k != "rclone" and self.auth.currentData() == "password" and \
                not self.password.text() and not self.editing:
            QMessageBox.warning(self, self.windowTitle(),
                                "Sign in with the browser, or enter the app password."
                                if k == "cloud" else "Enter the password.")
            return
        if k == "cloud" and self.password.text():
            # check the credentials before saving (in the background: never freeze)
            from caracal import cloud
            spec = user_mounts.validate(self.spec())
            user, pw = spec["user"], self.password.text()
            self.signin_note.setText("Checking the sign-in…")

            def checked(_ok, err):
                if err is not None:
                    QMessageBox.warning(self, self.windowTitle(), str(err))
                    self.signin_note.setText("⚠ %s" % err)
                    return
                self.accept()
            run_async(self, lambda: cloud.check_login(spec["url"], user, pw), checked)
            return
        self.accept()


class OpenWithDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        self.setWindowTitle("Open with")
        self.setMinimumSize(380, 420)
        self.path = path
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("How do you want to open %s?" % os.path.basename(path)))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search apps")
        self.filter.textChanged.connect(self._fill)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list, 1)
        self.always = QCheckBox("Always use this app for this type of file")
        lay.addWidget(self.always)
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        lay.addWidget(b)
        mt = QMimeDatabase().mimeTypeForFile(path)
        self.mimetype = mt.name()
        self.suggested = openwith.apps_for([mt.name()] + mt.allAncestors())
        QTimer.singleShot(0, self._fill)

    def _fill(self, *_):
        text = self.filter.text().lower()
        self.list.clear()
        seen = set()
        for group in (self.suggested, openwith.all_apps()):
            for a in group:
                if a.desktop_id in seen or (text and text not in a.name.lower()):
                    continue
                seen.add(a.desktop_id)
                item = QListWidgetItem(theme.get().icon("file") if not a.icon else _app_icon(a.icon),
                                       a.name)
                item.setData(Qt.UserRole, a)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def chosen(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


def _app_icon(name):
    from PySide6.QtGui import QIcon
    if os.path.isabs(name):
        return QIcon(name)
    return QIcon.fromTheme(name, theme.get().icon("file"))


class ImportRcloneDialog(QDialog):
    """Rclone mounts set up outside Caracal (systemd user services): take them over."""

    def __init__(self, mounts, parent=None):
        super().__init__(parent)
        _adopt(self, parent)
        self.setWindowTitle("Import rclone mounts")
        self.mounts = mounts
        lay = QVBoxLayout(self)
        intro = QLabel("These rclone mounts run as your own systemd services. Caracal can take "
                       "them over: it copies the remote's settings, mounts it at the same folder "
                       "with files on demand, and turns your old service off (it isn't deleted; "
                       "right-click the location → Hand back to undo).")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.rows = []
        for m in mounts:
            box = QCheckBox("%s  (%s: → %s, %s)" % (m["unit"], m["remote"], m["mount_point"],
                                                   m["section"].get("type")))
            box.setChecked(True)
            name = QLineEdit(user_mounts.nice_name(m["mount_point"]))
            name.setToolTip("Name shown in the navigation pane")
            form.addRow(box)
            form.addRow("    Name:", name)
            note = QLabel("    Cache: %d GB, %d days%s" % (
                m["cache_gb"], m["cache_days"], "" if m["vfs_full"] else
                " · files on demand will be turned on"))
            note.setStyleSheet("color: %s" % theme.get().colors()["subtle"])
            form.addRow(note)
            self.rows.append((m, box, name))
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Take over")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(560)

    def chosen(self):
        out = []
        for m, box, name in self.rows:
            if box.isChecked():
                m = dict(m, vfs_full=True)
                out.append((m, name.text().strip() or user_mounts.nice_name(m["mount_point"])))
        return out
