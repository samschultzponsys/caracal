# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Main window: list of managed drives plus a read-only fstab view."""

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QStyle, QTabWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from catamount import APP_NAME, __version__
from catamount.dialogs import ChangelogDialog, DriveDialog, PreferencesDialog, setting
from catamount.helper_client import HelperClient
from catamount.model import STATE_LABELS, load_drives, load_fstab, refresh_states
from catamount.theme import apply_theme


def _icon(widget, theme_name, fallback):
    return QIcon.fromTheme(theme_name, widget.style().standardIcon(fallback))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("catamount", "catamount")
        self.drives = []
        self.helper = HelperClient(self)
        self.helper.finished.connect(self._helper_done)
        self._pending = ""

        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(480)
        self.resize(820, 480)

        self._build_actions()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._apply_settings()
        self.refresh()

    # -- construction -----------------------------------------------------

    def _build_actions(self):
        st = QStyle.StandardPixmap
        self.act_add = QAction(_icon(self, "list-add", st.SP_FileDialogNewFolder),
                               "&Map network drive…", self)
        self.act_add.setShortcut(QKeySequence.New)
        self.act_add.triggered.connect(self.add_drive)
        self.act_connect = QAction(_icon(self, "network-connect", st.SP_DialogApplyButton),
                                   "&Connect", self)
        self.act_connect.triggered.connect(lambda: self._run_on_selected("connect"))
        self.act_disconnect = QAction(_icon(self, "network-disconnect", st.SP_DialogCancelButton),
                                      "&Disconnect", self)
        self.act_disconnect.triggered.connect(lambda: self._run_on_selected("disconnect"))
        self.act_edit = QAction(_icon(self, "document-properties", st.SP_FileDialogDetailedView),
                                "&Edit…", self)
        self.act_edit.triggered.connect(self.edit_drive)
        self.act_remove = QAction(_icon(self, "list-remove", st.SP_TrashIcon), "&Remove…", self)
        self.act_remove.setShortcut(QKeySequence.Delete)
        self.act_remove.triggered.connect(self.remove_drive)
        self.act_open = QAction(_icon(self, "folder-open", st.SP_DirOpenIcon), "&Open folder", self)
        self.act_open.triggered.connect(self.open_folder)
        self.act_refresh = QAction(_icon(self, "view-refresh", st.SP_BrowserReload), "Re&fresh", self)
        self.act_refresh.setShortcut(QKeySequence.Refresh)
        self.act_refresh.triggered.connect(self.refresh)
        self.act_prefs = QAction(_icon(self, "preferences-system", st.SP_FileDialogContentsView),
                                 "&Preferences…", self)
        self.act_prefs.setShortcut(QKeySequence.Preferences)
        self.act_prefs.triggered.connect(self.preferences)
        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.act_quit.triggered.connect(self.close)
        self.act_changelog = QAction("&Changelog", self)
        self.act_changelog.triggered.connect(self.show_changelog)
        self.act_about = QAction("&About %s" % APP_NAME, self)
        self.act_about.triggered.connect(self.about)
        self.drive_actions = (self.act_connect, self.act_disconnect, self.act_edit,
                              self.act_remove, self.act_open)

    def _build_ui(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_add)
        m.addSeparator()
        m.addAction(self.act_prefs)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Drive")
        for a in self.drive_actions:
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_refresh)
        m = mb.addMenu("&Help")
        m.addAction(self.act_changelog)
        m.addAction(self.act_about)

        tb = QToolBar("Main")
        tb.setObjectName("main-toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addAction(self.act_add)
        tb.addSeparator()
        tb.addAction(self.act_connect)
        tb.addAction(self.act_disconnect)
        tb.addAction(self.act_open)
        tb.addAction(self.act_edit)
        tb.addAction(self.act_remove)
        self.addToolBar(tb)

        # Managed drives
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Remote", "Local folder", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._update_actions)
        self.tree.itemDoubleClicked.connect(lambda *_: self._double_click())
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.resizeSection(0, 180)
        hdr.resizeSection(2, 160)

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        msg = QLabel("No network drives yet.\nMap an SMB or NFS share to see it here.")
        msg.setAlignment(Qt.AlignCenter)
        el.addWidget(msg)
        btn = QPushButton(self.act_add.icon(), "Map network drive…")
        btn.clicked.connect(self.add_drive)
        el.addWidget(btn, 0, Qt.AlignCenter)
        el.addStretch(2)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.tree)

        # fstab (read-only)
        fstab_page = QWidget()
        fl = QVBoxLayout(fstab_page)
        note = QLabel("Network mounts defined in /etc/fstab. Shown read-only — "
                      "CataMount never edits /etc/fstab.")
        note.setWordWrap(True)
        fl.addWidget(note)
        self.fstab_tree = QTreeWidget()
        self.fstab_tree.setHeaderLabels(["Remote", "Local folder", "Type", "Mounted", "Options"])
        self.fstab_tree.setRootIsDecorated(False)
        self.fstab_tree.setAlternatingRowColors(True)
        self.fstab_tree.setSelectionMode(QAbstractItemView.NoSelection)
        fl.addWidget(self.fstab_tree)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.stack, "Network drives")
        self.tabs.addTab(fstab_page, "System mounts (fstab)")
        self.setCentralWidget(self.tabs)

        self.version_label = QLabel()
        self.version_label.setTextFormat(Qt.RichText)
        self.version_label.setText('<a href="changelog">v%s</a>' % __version__)
        self.version_label.setToolTip("Show changelog")
        self.version_label.linkActivated.connect(lambda _l: self.show_changelog())
        self.statusBar().addPermanentWidget(self.version_label)

    def _apply_settings(self):
        apply_theme(setting(self.settings, "theme"))
        self.timer.start(setting(self.settings, "refresh_seconds") * 1000)

    # -- data -------------------------------------------------------------

    def selected(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        drive_id = items[0].data(0, Qt.UserRole)
        return next((d for d in self.drives if d.id == drive_id), None)

    def refresh(self):
        current = self.selected()
        current_id = current.id if current else None
        self.drives = load_drives()
        refresh_states(self.drives)

        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        failed = QBrush(QColor(200, 40, 40))
        for d in self.drives:
            item = QTreeWidgetItem([d.name, d.remote, d.mount_point,
                                    STATE_LABELS.get(d.state, d.state)])
            item.setData(0, Qt.UserRole, d.id)
            item.setToolTip(1, d.remote)
            item.setToolTip(3, "Reconnects at startup" if d.enabled
                            else "Does not reconnect at startup")
            if d.state == "failed":
                item.setForeground(3, failed)
            self.tree.addTopLevelItem(item)
            if d.id == current_id:
                item.setSelected(True)
        self.tree.setUpdatesEnabled(True)
        self.stack.setCurrentIndex(1 if self.drives else 0)

        self.fstab_tree.clear()
        for e in load_fstab():
            self.fstab_tree.addTopLevelItem(QTreeWidgetItem(
                [e.source, e.target, e.kind, "Yes" if e.mounted else "No", e.options]))
        self._update_actions()

    def _update_actions(self):
        d = self.selected()
        busy = self.helper.busy
        self.act_add.setEnabled(not busy)
        self.act_edit.setEnabled(bool(d) and not busy)
        self.act_remove.setEnabled(bool(d) and not busy)
        self.act_connect.setEnabled(bool(d) and not busy and d.state != "connected")
        self.act_disconnect.setEnabled(bool(d) and not busy
                                       and d.state in ("connected", "ready", "connecting", "failed"))
        self.act_open.setEnabled(bool(d) and d.state in ("connected", "ready"))

    # -- actions ----------------------------------------------------------

    def _run(self, command, payload, label):
        self._pending = label
        self.statusBar().showMessage(label + "…")
        self.helper.run(command, payload)
        self._update_actions()

    def _helper_done(self, ok, error, _reply):
        label, self._pending = self._pending, ""
        if ok:
            self.statusBar().showMessage(label + " — done.", 5000)
        else:
            self.statusBar().showMessage(label + " — failed.", 5000)
            box = QMessageBox(QMessageBox.Warning, APP_NAME, label + " failed.", parent=self)
            first, _, rest = error.partition("\n\n")
            box.setInformativeText(first)
            if rest:
                box.setDetailedText(rest)
            box.exec()
        self.refresh()

    def _taken(self):
        return [d.mount_point for d in self.drives]

    def add_drive(self):
        dlg = DriveDialog(self.settings, taken=self._taken(), parent=self)
        if dlg.exec():
            spec, password = dlg.request()
            self._run("apply", {"spec": spec, "password": password, "connect": True},
                      "Connecting %s" % spec["name"])

    def edit_drive(self):
        d = self.selected()
        if not d:
            return
        dlg = DriveDialog(self.settings, drive=d, taken=self._taken(), parent=self)
        if dlg.exec():
            spec, password = dlg.request()
            self._run("apply", {"spec": spec, "password": password,
                                "connect": d.state in ("connected", "ready", "connecting")},
                      "Saving %s" % spec["name"])

    def remove_drive(self):
        d = self.selected()
        if not d:
            return
        if setting(self.settings, "confirm_remove"):
            answer = QMessageBox.question(
                self, APP_NAME,
                "Disconnect and remove “%s”?\n\nThe share itself is not touched; only this "
                "computer's connection and saved password are removed." % d.name)
            if answer != QMessageBox.Yes:
                return
        self._run("remove", {"id": d.id}, "Removing %s" % d.name)

    def _run_on_selected(self, command):
        d = self.selected()
        if d:
            verb = "Connecting" if command == "connect" else "Disconnecting"
            self._run(command, {"id": d.id}, "%s %s" % (verb, d.name))

    def open_folder(self):
        d = self.selected()
        if d:
            QDesktopServices.openUrl(QUrl.fromLocalFile(d.mount_point))

    def _double_click(self):
        d = self.selected()
        if d and d.state in ("connected", "ready"):
            self.open_folder()
        else:
            self.edit_drive()

    def _context_menu(self, pos):
        if not self.tree.itemAt(pos):
            return
        menu = QMenu(self)
        for a in (self.act_open, self.act_connect, self.act_disconnect):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_edit)
        menu.addAction(self.act_remove)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def preferences(self):
        if PreferencesDialog(self.settings, self).exec():
            self._apply_settings()

    def show_changelog(self):
        ChangelogDialog(self).exec()

    def about(self):
        QMessageBox.about(
            self, "About %s" % APP_NAME,
            "<b>%s %s</b><br>Map SMB and NFS network drives as persistent systemd mounts."
            "<br><br>Copyright 2026 Spazcat Enterprises<br>Licensed under the Apache License 2.0"
            "<br><a href='https://github.com/samschultzponsys/catamount'>"
            "github.com/samschultzponsys/catamount</a>" % (APP_NAME, __version__))
