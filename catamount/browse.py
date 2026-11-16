# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Browse the network for SMB/NFS servers and their shares."""

import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from catamount import discovery
from catamount.background import run_async


def _resolves(host):
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


class BrowseDialog(QDialog):
    """Pick a share. result() -> (kind, server, share) after exec()."""

    def __init__(self, username="", password="", domain="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browse network")
        self.resize(620, 440)
        self.setMinimumWidth(440)
        self.tools = discovery.available()
        self._choice = None
        self._pending = 0
        self._current = None
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("Server name or address")
        self.server_edit.returnPressed.connect(self._list_typed)
        go = QPushButton("List shares")
        go.clicked.connect(self._list_typed)
        self.refresh_btn = QPushButton("Search again")
        self.refresh_btn.clicked.connect(self.discover)
        row.addWidget(self.server_edit, 1)
        row.addWidget(go)
        row.addWidget(self.refresh_btn)
        layout.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        self.servers = QListWidget()
        self.servers.currentItemChanged.connect(self._server_selected)
        self.shares = QTreeWidget()
        self.shares.setHeaderLabels(["Share", "Type", "Details"])
        self.shares.setRootIsDecorated(False)
        self.shares.setSelectionMode(QAbstractItemView.SingleSelection)
        self.shares.itemSelectionChanged.connect(self._update_ok)
        self.shares.itemDoubleClicked.connect(lambda *_: self._accept())
        self.shares.header().resizeSection(0, 160)
        split.addWidget(self.servers)
        split.addWidget(self.shares)
        split.setSizes([200, 400])
        layout.addWidget(split, 1)

        # Sign-in row, shown when a server won't list shares anonymously.
        self.login = QWidget()
        grid = QGridLayout(self.login)
        grid.setContentsMargins(0, 0, 0, 0)
        self.login_label = QLabel("This server needs a sign-in to list its shares.")
        self.login_label.setWordWrap(True)
        self.user = QLineEdit(username)
        self.user.setPlaceholderText("User name")
        self.pw = QLineEdit(password)
        self.pw.setPlaceholderText("Password")
        self.pw.setEchoMode(QLineEdit.Password)
        self.dom = QLineEdit(domain)
        self.dom.setPlaceholderText("Domain (optional)")
        sign = QPushButton("Sign in")
        sign.clicked.connect(lambda: self._list(self._current, force_smb=True))
        self.pw.returnPressed.connect(sign.click)
        grid.addWidget(self.login_label, 0, 0, 1, 4)
        grid.addWidget(self.user, 1, 0)
        grid.addWidget(self.pw, 1, 1)
        grid.addWidget(self.dom, 1, 2)
        grid.addWidget(sign, 1, 3)
        self.login.hide()
        layout.addWidget(self.login)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Use this share")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_ok()

        missing = []
        if not self.tools["avahi"]:
            missing.append("avahi-browse (automatic server discovery)")
        if not self.tools["smbclient"]:
            missing.append("smbclient (listing SMB shares)")
        if missing:
            self.status.setText("Not installed: %s. You can still type a server name."
                                % "; ".join(missing))
        self.discover()

    # -- discovery ----------------------------------------------------------

    def discover(self):
        if not self.tools["avahi"]:
            return
        self.refresh_btn.setEnabled(False)
        self.servers.clear()
        placeholder = QListWidgetItem("Searching the network…")
        placeholder.setFlags(Qt.NoItemFlags)
        self.servers.addItem(placeholder)
        run_async(self, discovery.discover_servers, self._discovered)

    def _discovered(self, servers, error):
        self.refresh_btn.setEnabled(True)
        self.servers.clear()
        for srv in servers or []:
            item = QListWidgetItem(srv.label)
            item.setData(Qt.UserRole, srv)
            item.setToolTip("%s  %s" % (srv.host, srv.address))
            self.servers.addItem(item)
        if not servers:
            item = QListWidgetItem("No servers found.")
            item.setFlags(Qt.NoItemFlags)
            self.servers.addItem(item)

    def _server_selected(self, item, _prev):
        srv = item.data(Qt.UserRole) if item else None
        if srv:
            host = srv.host if _resolves(srv.host) else (srv.address or srv.host)
            self.server_edit.setText(host)
            self._list(discovery.Server(srv.name, host, srv.address, set(srv.kinds)))

    def _list_typed(self):
        host = self.server_edit.text().strip().strip("\\/")
        if host:
            self._list(discovery.Server(host, host, kinds={"smb", "nfs"}))

    # -- listing ------------------------------------------------------------

    def _list(self, srv, force_smb=False):
        if srv is None:
            return
        self._current = srv
        self.shares.clear()
        self.login.hide()
        self.status.setText("Listing shares on %s…" % srv.host)
        self._errors = []
        self._pending = 0
        host = srv.host
        if ("smb" in srv.kinds or force_smb) and self.tools["smbclient"]:
            self._pending += 1
            user, pw, dom = self.user.text().strip(), self.pw.text(), self.dom.text().strip()
            run_async(self, lambda: discovery.list_smb_shares(host, user, pw, dom),
                      lambda r, e: self._smb_listed(host, r, e))
        if "nfs" in srv.kinds and not force_smb and self.tools["showmount"]:
            self._pending += 1
            run_async(self, lambda: discovery.list_nfs_exports(host),
                      lambda r, e: self._nfs_listed(host, r, e))
        if not self._pending:
            self.status.setText("No tool is installed that can list shares on this server.")

    def _add(self, host, kind, share, detail):
        item = QTreeWidgetItem([share, kind.upper(), detail])
        item.setData(0, Qt.UserRole, (kind, host, share))
        self.shares.addTopLevelItem(item)

    def _finish_one(self, host, error):
        self._pending -= 1
        if error:
            self._errors.append(error)
        if self._pending <= 0 and self._current and self._current.host == host:
            n = self.shares.topLevelItemCount()
            if n:
                self.status.setText("%d share%s on %s." % (n, "" if n == 1 else "s", host))
            else:
                self.status.setText(" ".join(self._errors) or "No shares found on %s." % host)

    def _smb_listed(self, host, result, exc):
        shares, err, login = result if result else ([], str(exc), False)
        for name, comment in shares:
            self._add(host, "smb", name, comment)
        if login:
            self.login.show()
            self.user.setFocus()
        self._finish_one(host, err)

    def _nfs_listed(self, host, result, exc):
        exports, err = result if result else ([], str(exc))
        for path, clients in exports:
            self._add(host, "nfs", path, clients)
        self._finish_one(host, err if not exports else "")

    # -- result -------------------------------------------------------------

    def _update_ok(self):
        self.ok.setEnabled(bool(self.shares.selectedItems()))

    def _accept(self):
        items = self.shares.selectedItems()
        if items:
            self._choice = items[0].data(0, Qt.UserRole)
            self.accept()

    def result_share(self):
        return self._choice

    def credentials(self):
        return self.user.text().strip(), self.pw.text(), self.dom.text().strip()
