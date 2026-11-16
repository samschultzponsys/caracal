# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Main window: list of managed drives plus a read-only fstab view."""

import json
import os
import sys

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHeaderView, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QStackedWidget, QStyle, QSystemTrayIcon, QTabWidget,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from catamount import APP_NAME, __version__
from catamount import bookmarks, deps
from catamount.dialogs import ChangelogDialog, DriveDialog, PreferencesDialog, setting
from catamount.helper_client import HelperClient
from catamount.icons import tray_icon
from catamount import updater
from catamount.update_client import UpdateClient
from catamount.model import STATE_LABELS, load_drives, load_fstab, refresh_states
from catamount.theme import apply_theme


def _icon(widget, theme_name, fallback):
    return QIcon.fromTheme(theme_name, widget.style().standardIcon(fallback))


class MainWindow(QMainWindow):
    def __init__(self, start_in_tray=False):
        super().__init__()
        self.settings = QSettings("catamount", "catamount")
        self.drives = []
        self.helper = HelperClient(self)
        self.helper.finished.connect(self._helper_done)
        self._pending = ""
        self._prev_states = None
        self._quitting = False
        self._after_helper = None
        self._import_queue = []
        self.tray = None
        self.updates = UpdateClient(self)
        self.updates.checked.connect(self._update_checked)
        self.updates.downloaded.connect(self._update_downloaded)

        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(480)
        self.resize(820, 480)

        self._build_actions()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._apply_settings()
        self.refresh()
        self._check_deps()
        self._refresh_version_label()
        # Update check: shortly after start, then hourly (it only hits the network once a day).
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._auto_check)
        self.update_timer.start(3600 * 1000)
        QTimer.singleShot(8000, self._auto_check)

        if start_in_tray:
            self._wait_for_tray(tries=15)
        else:
            self.show()

    # -- tray -------------------------------------------------------------

    def _wait_for_tray(self, tries):
        """At login the tray host may appear after us; wait for it, then fall back."""
        if self.tray is not None:
            return
        if tries <= 0:
            self.showMinimized()
            return
        if setting(self.settings, "tray_enabled") and QSystemTrayIcon.isSystemTrayAvailable():
            self._update_tray()
            return
        if not setting(self.settings, "tray_enabled"):
            self.showMinimized()
            return
        QTimer.singleShot(1000, lambda: self._wait_for_tray(tries - 1))

    def _update_tray(self):
        want = setting(self.settings, "tray_enabled") and QSystemTrayIcon.isSystemTrayAvailable()
        if want and self.tray is None:
            self.tray = QSystemTrayIcon(tray_icon(setting(self.settings, "tray_icon_style")), self)
            self.tray_menu = QMenu(self)
            self.tray_menu.aboutToShow.connect(self._build_tray_menu)
            self._build_tray_menu()
            self.tray.setContextMenu(self.tray_menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
            self._update_tray_status()
        elif not want and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
            if not self.isVisible():
                self.show_window()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
                self.hide()
            else:
                self.show_window()

    def _build_tray_menu(self):
        m = self.tray_menu
        m.clear()
        shown = self.isVisible() and not self.isMinimized()
        m.addAction("Hide %s" % APP_NAME if shown else "Show %s" % APP_NAME,
                    self.hide if shown else self.show_window)
        m.addSeparator()
        if not self.drives:
            a = m.addAction("No network drives")
            a.setEnabled(False)
        for d in self.drives:
            sub = m.addMenu("%s — %s" % (d.name, STATE_LABELS.get(d.state, d.state)))
            a = sub.addAction("Open folder", lambda d=d: self._open(d))
            a.setEnabled(d.state in ("connected", "ready"))
            a = sub.addAction("Connect", lambda d=d: self._run_on(d, "connect"))
            a.setEnabled(d.state != "connected" and not self.helper.busy)
            a = sub.addAction("Disconnect", lambda d=d: self._run_on(d, "disconnect"))
            a.setEnabled(d.state != "disconnected" and not self.helper.busy)
        m.addSeparator()
        if self.updates.update:
            m.addAction("Update to %s available…" % self.updates.update["version"],
                        lambda: (self.show_window(), self.show_changelog()))
        a = m.addAction("Map network drive…", lambda: (self.show_window(), self.add_drive()))
        a.setEnabled(not self.helper.busy)
        m.addSeparator()
        m.addAction("Quit", self.quit)

    def _update_tray_status(self):
        if self.tray is None:
            return
        connected = sum(1 for d in self.drives if d.state == "connected")
        failed = [d for d in self.drives if d.state == "failed"]
        tip = "%s — %d of %d drives connected" % (APP_NAME, connected, len(self.drives))
        if failed:
            tip += "\nFailed: " + ", ".join(d.name for d in failed)
        self.tray.setToolTip(tip)
        self.tray.setIcon(tray_icon(setting(self.settings, "tray_icon_style"),
                                     "failed" if failed else None))

    def _notify_changes(self):
        states = {d.id: (d.name, d.state) for d in self.drives}
        prev, self._prev_states = self._prev_states, states
        if prev is None or self.tray is None or not setting(self.settings, "notify_failures"):
            return
        for drive_id, (name, state) in states.items():
            old = prev.get(drive_id, (name, None))[1]
            if state == "failed" and old not in (None, "failed"):
                self.tray.showMessage(APP_NAME, "%s failed to connect." % name,
                                      QSystemTrayIcon.Warning, 8000)
            elif old == "connected" and state == "disconnected" and not self._pending:
                self.tray.showMessage(APP_NAME, "%s was disconnected." % name,
                                      QSystemTrayIcon.Information, 5000)

    def show_window(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if (not self._quitting and self.tray is not None
                and setting(self.settings, "close_to_tray")):
            event.ignore()
            self.hide()
            if not setting(self.settings, "tray_hint_shown"):
                self.tray.showMessage(APP_NAME, "CataMount is still running in the system tray. "
                                      "Use Quit in the tray menu to exit.",
                                      QSystemTrayIcon.Information, 6000)
                self.settings.setValue("tray_hint_shown", True)
            return
        event.accept()
        self.quit()

    def quit(self):
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def _check_deps(self):
        missing = deps.missing()
        if not missing:
            return
        lines = "\n".join("• %s — needed for %s" % (t, p) for t, p, _f, _d in missing)
        box = QMessageBox(QMessageBox.Warning, APP_NAME,
                          "Some system tools CataMount needs are missing.", parent=self)
        box.setInformativeText(lines + "\n\nInstall them with:\n" + deps.install_hint(missing))
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        QTimer.singleShot(0, box.exec)

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
        self.act_export = QAction("&Export drives…", self)
        self.act_export.triggered.connect(self.export_drives)
        self.act_import = QAction("&Import drives…", self)
        self.act_import.triggered.connect(self.import_drives)
        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.act_quit.triggered.connect(self.quit)
        self.act_changelog = QAction("&Changelog", self)
        self.act_changelog.triggered.connect(self.show_changelog)
        self.act_check_updates = QAction("Check for &updates…", self)
        self.act_check_updates.triggered.connect(lambda: self.updates.check(manual=True))
        self.act_about = QAction("&About %s" % APP_NAME, self)
        self.act_about.triggered.connect(self.about)
        self.drive_actions = (self.act_connect, self.act_disconnect, self.act_edit,
                              self.act_remove, self.act_open)

    def _build_ui(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_add)
        m.addSeparator()
        m.addAction(self.act_import)
        m.addAction(self.act_export)
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
        m.addAction(self.act_check_updates)
        m.addSeparator()
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
        self.version_label.linkActivated.connect(lambda _l: self.show_changelog())
        self.statusBar().addPermanentWidget(self.version_label)

    def _apply_settings(self):
        apply_theme(setting(self.settings, "theme"))
        self.timer.start(setting(self.settings, "refresh_seconds") * 1000)
        if self.tray is not None or self.isVisible():
            self._update_tray()

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
        err = bookmarks.sync(self.drives, self.settings)
        if err:
            self.statusBar().showMessage(err, 8000)

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
        self._notify_changes()
        if self.tray is None and self.isVisible() and setting(self.settings, "tray_enabled"):
            self._update_tray()  # e.g. the tray host appeared after we started
        self._update_tray_status()

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

    def _helper_done(self, ok, error, reply):
        label, self._pending = self._pending, ""
        after, self._after_helper = self._after_helper, None
        if ok and after:
            self.statusBar().showMessage(label + " — done.", 5000)
            after(reply)
            self.refresh()
            if self._import_queue:
                QTimer.singleShot(0, self._next_import)
            return
        if ok:
            self.statusBar().showMessage(label + " — done.", 5000)
        else:
            self.statusBar().showMessage(label + " — failed.", 5000)
            if not self.isVisible():
                self.show_window()
            box = QMessageBox(QMessageBox.Warning, APP_NAME, label + " failed.", parent=self)
            first, _, rest = error.partition("\n\n")
            box.setInformativeText(first)
            if rest:
                box.setDetailedText(rest)
            box.exec()
        self.refresh()
        if self._import_queue:
            QTimer.singleShot(0, self._next_import)

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

    # -- export / import ----------------------------------------------------

    EXPORT_FORMAT = "catamount-drives"

    def export_drives(self):
        if not self.drives:
            QMessageBox.information(self, APP_NAME, "There are no drives to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export drives",
                                              os.path.expanduser("~/catamount-drives.json"),
                                              "CataMount drives (*.json)")
        if not path:
            return
        drives = []
        for d in self.drives:
            spec = {k: v for k, v in d.spec.items() if k not in ("id", "spec_version")}
            if isinstance(spec.get("smb"), dict):
                spec["smb"] = {k: v for k, v in spec["smb"].items() if k not in ("uid", "gid")}
            drives.append(spec)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"format": self.EXPORT_FORMAT, "version": 1, "drives": drives},
                          fh, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, "Could not save: %s" % exc)
            return
        self.statusBar().showMessage("Exported %d drives (passwords are never exported)."
                                     % len(drives), 6000)

    def import_drives(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import drives", os.path.expanduser("~"),
                                              "CataMount drives (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("format") != self.EXPORT_FORMAT or not isinstance(data.get("drives"), list):
                raise ValueError("not a CataMount export")
        except (OSError, ValueError, AttributeError) as exc:
            QMessageBox.warning(self, APP_NAME, "Could not read %s: %s" % (path, exc))
            return
        existing = {(d.spec.get("server"), d.spec.get("share")) for d in self.drives}
        queue = [s for s in data["drives"] if isinstance(s, dict)
                 and (s.get("server"), s.get("share")) not in existing]
        skipped = len(data["drives"]) - len(queue)
        if not queue:
            QMessageBox.information(self, APP_NAME, "All drives in that file already exist here.")
            return
        if skipped:
            self.statusBar().showMessage("Skipping %d drive(s) that already exist." % skipped, 6000)
        self._import_total = len(queue)
        self._import_queue = queue
        self._next_import()

    def _next_import(self):
        if not self._import_queue or self.helper.busy:
            return
        spec = self._import_queue.pop(0)
        n = self._import_total - len(self._import_queue)
        dlg = DriveDialog(self.settings, taken=self._taken(), parent=self, template=spec,
                          title="Import drive %d of %d" % (n, self._import_total))
        if dlg.exec():
            new_spec, password = dlg.request()
            self._run("apply", {"spec": new_spec, "password": password, "connect": True},
                      "Connecting %s" % new_spec["name"])
        elif self._import_queue:
            QTimer.singleShot(0, self._next_import)

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
            self._run_on(d, command)

    def _run_on(self, d, command):
        if self.helper.busy:
            return
        verb = "Connecting" if command == "connect" else "Disconnecting"
        self._run(command, {"id": d.id}, "%s %s" % (verb, d.name))

    def open_folder(self):
        d = self.selected()
        if d:
            self._open(d)

    def _open(self, d):
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
        upd = self.updates.update
        can_install = bool(upd and self.updates.kind
                           and updater.pick_assets(upd, self.updates.kind))
        dlg = ChangelogDialog(self, update=upd, can_install=can_install,
                              error=self.updates.last_error)
        result = dlg.exec()
        if result == ChangelogDialog.INSTALL:
            self.install_update()
        elif result == ChangelogDialog.DOWNLOAD_PAGE:
            QDesktopServices.openUrl(QUrl(upd.get("url") or updater.RELEASES_URL))

    # -- updates ----------------------------------------------------------

    def _auto_check(self):
        if setting(self.settings, "auto_check_updates"):
            self.updates.check()

    def _refresh_version_label(self):
        upd = self.updates.update
        if upd:
            self.version_label.setText(
                '<a href="changelog" style="text-decoration:none">v%s '
                '<span style="color:#16A34A">●</span></a>' % __version__)
            self.version_label.setToolTip("Update available: %s — click for details"
                                          % upd["version"])
        else:
            self.version_label.setText('<a href="changelog">v%s</a>' % __version__)
            self.version_label.setToolTip("Show changelog")

    def _update_checked(self, manual):
        self._refresh_version_label()
        upd = self.updates.update
        if not manual:
            if upd and self.tray is not None and \
                    self.settings.value("notified_version", "") != upd["version"]:
                self.settings.setValue("notified_version", upd["version"])
                self.tray.showMessage(APP_NAME, "CataMount %s is available." % upd["version"],
                                      QSystemTrayIcon.Information, 8000)
            return
        if upd:
            self.show_changelog()
        elif self.updates.last_error:
            QMessageBox.warning(self, APP_NAME, self.updates.last_error)
        else:
            QMessageBox.information(self, APP_NAME, "CataMount %s is the latest version."
                                    % __version__)

    def install_update(self):
        if self.helper.busy or self.updates.busy:
            return
        self.statusBar().showMessage("Downloading update…")
        self.act_check_updates.setEnabled(False)
        self.updates.download()

    def _update_downloaded(self, ok, path_or_error, sha256):
        self.act_check_updates.setEnabled(True)
        if not ok:
            self.statusBar().showMessage("Update download failed.", 5000)
            QMessageBox.warning(self, APP_NAME, path_or_error)
            return
        version = self.updates.update["version"]
        self._after_helper = lambda _reply: self._update_installed(version)
        self._run("install-update", {"path": path_or_error, "sha256": sha256},
                  "Installing CataMount %s" % version)

    def _update_installed(self, version):
        answer = QMessageBox.question(
            self, APP_NAME, "CataMount %s is installed. Restart CataMount now to use it?" % version)
        if answer == QMessageBox.Yes:
            self.restart()

    def restart(self):
        from PySide6.QtCore import QProcess
        from catamount.app import release_instance_lock
        release_instance_lock()
        launcher = "/usr/bin/catamount"
        if os.path.exists(launcher):
            QProcess.startDetached(launcher, [])
        else:
            QProcess.startDetached(sys.executable, ["-m", "catamount"])
        self.quit()

    def about(self):
        QMessageBox.about(
            self, "About %s" % APP_NAME,
            "<b>%s %s</b><br>Map SMB and NFS network drives as persistent systemd mounts."
            "<br><br>Copyright 2026 Spazcat Enterprises<br>Licensed under the Apache License 2.0"
            "<br><a href='https://github.com/samschultzponsys/catamount'>"
            "github.com/samschultzponsys/catamount</a>" % (APP_NAME, __version__))
