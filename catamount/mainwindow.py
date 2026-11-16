# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Main window: list of managed drives plus a read-only fstab view."""

import json
import os
import shutil
import subprocess
import sys

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence,
                           QTextCursor)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QStackedWidget, QStyle, QSystemTrayIcon, QTabWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from catamount import APP_NAME, __version__
from catamount import bookmarks, deps
from catamount.dialogs import ChangelogDialog, DriveDialog, PreferencesDialog, setting
from catamount.helper_client import HelperClient
from catamount.icons import tray_icon
from catamount import updater
from catamount.update_client import UpdateClient
from catamount.background import run_async
from catamount.widgets import add_reveal_action
from catamount.model import (STATE_LABELS, disk_usage, format_usage, fstab_to_template,
                             load_drives, load_fstab, refresh_states)
from catamount.theme import apply_theme


def is_auth_error(text):
    t = text.lower()
    return any(k in t for k in ("error(13)", "permission denied", "logon_failure",
                                "status_logon_failure", "error(126)"))


class LogDialog(QDialog):
    def __init__(self, parent, drive, text, hint):
        super().__init__(parent)
        self.parent_window = parent
        self.drive = drive
        self.setWindowTitle("Log — %s" % drive.name)
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        f = self.view.font()
        f.setFamily("monospace")
        self.view.setFont(f)
        lay.addWidget(self.view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        self.admin = bb.addButton("Show full log (admin)", QDialogButtonBox.ActionRole)
        self.admin.clicked.connect(self._admin)
        copy = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.view.toPlainText()))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._set(text, hint)

    def _set(self, text, hint=""):
        if not text or text.startswith("-- No entries --"):
            text = "No log entries for this drive yet."
            if hint and ("permission" in hint.lower() or "not seeing" in hint.lower()):
                text += ("\n\nYour account can't read the system log. Use “Show full log "
                         "(admin)”, or add yourself to the systemd-journal group.")
        self.view.setPlainText(text)
        self.view.moveCursor(QTextCursor.End)
        self.view.horizontalScrollBar().setValue(0)

    def _admin(self):
        helper = self.parent_window.helper
        if helper.busy:
            return
        self.admin.setEnabled(False)

        def done(ok, error, reply):
            helper.finished.disconnect(done)
            helper.finished.connect(self.parent_window._helper_done)
            self.admin.setEnabled(True)
            self._set(reply.get("log", "") if ok else error)

        helper.finished.disconnect(self.parent_window._helper_done)
        helper.finished.connect(done)
        helper.run("log", {"id": self.drive.id})


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
        self._pending_drive = None
        self._usage = {}          # drive id -> (used, total)
        self._usage_inflight = set()
        self.tray = None
        self.updates = UpdateClient(self)
        self.updates.checked.connect(self._update_checked)
        self.updates.downloaded.connect(self._update_downloaded)

        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(480)
        self.resize(820, 480)

        self._build_actions()
        self._build_ui()
        self._restore_window_state()
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
            self._update_tray()

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
        if len(self.drives) > 1:
            m.addSeparator()
            a = m.addAction("Connect all", lambda: self.run_all("connect"))
            a.setEnabled(not self.helper.busy)
            a = m.addAction("Disconnect all", lambda: self.run_all("disconnect"))
            a.setEnabled(not self.helper.busy)
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

    def notify(self, message, warning=False, timeout=6000):
        """Desktop notification through the tray, or notify-send when there is no tray."""
        if self.tray is not None:
            self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.Warning if warning
                                  else QSystemTrayIcon.Information, timeout)
        elif shutil.which("notify-send"):
            try:
                subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", "com.spazcat.CataMount",
                                  "-u", "normal", APP_NAME, message],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

    def _notify_changes(self):
        states = {d.id: (d.name, d.state) for d in self.drives}
        prev, self._prev_states = self._prev_states, states
        if prev is None or not setting(self.settings, "notify_failures"):
            return
        for drive_id, (name, state) in states.items():
            old = prev.get(drive_id, (name, None))[1]
            if state == "failed" and old not in (None, "failed"):
                self.notify("%s failed to connect." % name, warning=True, timeout=8000)
            elif old == "connected" and state == "disconnected" and not self._pending:
                self.notify("%s was disconnected." % name)
            elif old == "failed" and state == "connected" and not self._pending:
                self.notify("%s is connected again." % name)

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

    def _restore_window_state(self):
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        hdr = self.settings.value("window/columns")
        if hdr is not None:
            self.tree.header().restoreState(hdr)

    def _save_window_state(self):
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/columns", self.tree.header().saveState())

    def hideEvent(self, event):
        self._save_window_state()
        super().hideEvent(event)

    def quit(self):
        self._save_window_state()
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
        self.act_duplicate = QAction(_icon(self, "edit-copy", st.SP_FileIcon), "D&uplicate…", self)
        self.act_duplicate.triggered.connect(self.duplicate_drive)
        self.act_password = QAction(_icon(self, "dialog-password", st.SP_DialogYesButton),
                                    "Change &password…", self)
        self.act_password.triggered.connect(lambda: self.change_password())
        self.act_log = QAction(_icon(self, "text-x-generic", st.SP_FileDialogInfoView),
                               "Show &log", self)
        self.act_log.triggered.connect(self.show_log)
        self.act_connect_all = QAction("Connect &all", self)
        self.act_connect_all.triggered.connect(lambda: self.run_all("connect"))
        self.act_disconnect_all = QAction("Disconnect a&ll", self)
        self.act_disconnect_all.triggered.connect(lambda: self.run_all("disconnect"))
        self.act_fstab_copy = QAction("Copy as CataMount drive…", self)
        self.act_fstab_copy.triggered.connect(self.copy_fstab_entry)
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
        m.addAction(self.act_duplicate)
        m.addAction(self.act_password)
        m.addAction(self.act_log)
        m.addSeparator()
        m.addAction(self.act_connect_all)
        m.addAction(self.act_disconnect_all)
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
        self.tree.setHeaderLabels(["Name", "Remote", "Local folder", "Status", "Space"])
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
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
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
        self.fstab_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.fstab_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fstab_tree.customContextMenuRequested.connect(self._fstab_menu)
        self.fstab_tree.itemSelectionChanged.connect(
            lambda: self.fstab_copy_btn.setEnabled(bool(self.fstab_tree.selectedItems())))
        fl.addWidget(self.fstab_tree)
        row = QHBoxLayout()
        row.addStretch(1)
        self.fstab_copy_btn = QPushButton("Copy as CataMount drive…")
        self.fstab_copy_btn.setToolTip("Create a CataMount drive with the same settings. "
                                       "The fstab entry itself is left alone.")
        self.fstab_copy_btn.setEnabled(False)
        self.fstab_copy_btn.clicked.connect(self.copy_fstab_entry)
        row.addWidget(self.fstab_copy_btn)
        fl.addLayout(row)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.stack, "Network drives")
        self.tabs.addTab(fstab_page, "System mounts (fstab)")
        self.setCentralWidget(self.tabs)

        self.summary_label = QLabel()
        self.statusBar().addWidget(self.summary_label)
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
            usage = self._usage.get(d.id) if d.state == "connected" else None
            item = QTreeWidgetItem([d.name, d.remote, d.mount_point,
                                    STATE_LABELS.get(d.state, d.state),
                                    format_usage(*usage) if usage else ""])
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

        sel = self.fstab_tree.selectedItems()
        sel_target = sel[0].text(1) if sel else None
        self.fstab_tree.clear()
        self.fstab_entries = load_fstab()
        for i, e in enumerate(self.fstab_entries):
            item = QTreeWidgetItem([e.source, e.target, e.kind, "Yes" if e.mounted else "No",
                                    e.options])
            item.setData(0, Qt.UserRole, i)
            self.fstab_tree.addTopLevelItem(item)
            if e.target == sel_target:
                item.setSelected(True)
        connected = sum(1 for d in self.drives if d.state == "connected")
        failed = sum(1 for d in self.drives if d.state == "failed")
        summary = "%d of %d connected" % (connected, len(self.drives)) if self.drives else ""
        if failed:
            summary += " · %d failed" % failed
        self.summary_label.setText(summary)
        self._refresh_usage()
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
        self.act_duplicate.setEnabled(bool(d) and not busy)
        self.act_password.setEnabled(bool(d) and not busy and d.kind == "smb"
                                     and not d.spec.get("smb", {}).get("guest")
                                     and not d.spec.get("smb", {}).get("kerberos"))
        self.act_log.setEnabled(bool(d))
        self.act_connect_all.setEnabled(bool(self.drives) and not busy)
        self.act_disconnect_all.setEnabled(bool(self.drives) and not busy)

    # -- actions ----------------------------------------------------------

    def _run(self, command, payload, label, drive_id=None):
        self._pending = label
        self._pending_drive = drive_id
        self.statusBar().showMessage(label + "…")
        self.helper.run(command, payload)
        self._update_actions()

    def _helper_done(self, ok, error, reply):
        label, self._pending = self._pending, ""
        drive_id, self._pending_drive = self._pending_drive, None
        after, self._after_helper = self._after_helper, None
        if ok and after:
            self.statusBar().showMessage(label + " — done.", 5000)
            after(reply)
            self.refresh()
            if self._import_queue:
                QTimer.singleShot(0, self._next_import)
            return
        if ok and reply.get("failed"):
            names = {d.id: d.name for d in self.drives}
            lines = ["%s: %s" % (names.get(i, i), msg.partition("\n\n")[0])
                     for i, msg in reply["failed"].items()]
            QMessageBox.warning(self, APP_NAME, "%s — some drives failed:\n\n%s"
                                % (label, "\n".join(lines)))
        elif ok:
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
            drive = next((d for d in self.drives if d.id == drive_id), None)
            fix = None
            if drive is not None and drive.kind == "smb" and is_auth_error(error) \
                    and not drive.spec.get("smb", {}).get("guest"):
                fix = box.addButton("Update password…", QMessageBox.ActionRole)
                box.addButton(QMessageBox.Close)
            box.exec()
            if fix is not None and box.clickedButton() is fix:
                QTimer.singleShot(0, lambda: self.change_password(drive))
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
                      "Connecting %s" % spec["name"], spec["id"])

    def edit_drive(self):
        d = self.selected()
        if not d:
            return
        dlg = DriveDialog(self.settings, drive=d, taken=self._taken(), parent=self)
        if dlg.exec():
            spec, password = dlg.request()
            self._run("apply", {"spec": spec, "password": password,
                                "connect": d.state in ("connected", "ready", "connecting")},
                      "Saving %s" % spec["name"], spec["id"])

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
        self._run(command, {"id": d.id}, "%s %s" % (verb, d.name), d.id)

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
        menu.addAction(self.act_duplicate)
        menu.addAction(self.act_password)
        menu.addAction(self.act_log)
        menu.addSeparator()
        menu.addAction(self.act_remove)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _fstab_menu(self, pos):
        if self.fstab_tree.itemAt(pos):
            menu = QMenu(self)
            menu.addAction(self.act_fstab_copy)
            menu.exec(self.fstab_tree.viewport().mapToGlobal(pos))

    # -- more drive actions ------------------------------------------------

    def _refresh_usage(self):
        """Update free-space figures for connected drives in the background."""
        for d in self.drives:
            if d.state != "connected" or d.id in self._usage_inflight:
                continue
            self._usage_inflight.add(d.id)
            run_async(self, lambda p=d.mount_point: disk_usage(p),
                      lambda r, e, i=d.id: self._usage_done(i, r, e))

    def _usage_done(self, drive_id, result, error):
        self._usage_inflight.discard(drive_id)
        if result and result != self._usage.get(drive_id):
            self._usage[drive_id] = result
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.data(0, Qt.UserRole) == drive_id:
                    item.setText(4, format_usage(*result))
                    used, total = result
                    item.setToolTip(4, "%.0f%% used" % (100.0 * used / total) if total else "")

    def run_all(self, command):
        if self.helper.busy or not self.drives:
            return
        verb = "Connecting" if command == "connect" else "Disconnecting"
        self._run(command, {"ids": [d.id for d in self.drives]}, "%s all drives" % verb)

    def duplicate_drive(self):
        d = self.selected()
        if not d:
            return
        template = {k: v for k, v in d.spec.items() if k != "id"}
        template["name"] = d.name + " (copy)"
        taken = set(self._taken())
        mp, n = d.mount_point + "-2", 2
        while mp in taken:
            n += 1
            mp = "%s-%d" % (d.mount_point, n)
        template["mount_point"] = mp
        dlg = DriveDialog(self.settings, taken=taken, parent=self, template=template,
                          title="Duplicate network drive")
        if dlg.exec():
            spec, password = dlg.request()
            self._run("apply", {"spec": spec, "password": password, "connect": True},
                      "Connecting %s" % spec["name"], spec["id"])

    def change_password(self, drive=None):
        d = drive or self.selected()
        if not d or self.helper.busy:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Change password")
        lay = QVBoxLayout(dlg)
        user = d.spec.get("smb", {}).get("username", "")
        lab = QLabel("New password for %s on %s:" % (user or "this share", d.remote))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        add_reveal_action(pw)
        lay.addWidget(pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec() or not pw.text():
            return
        if "\n" in pw.text():
            QMessageBox.warning(self, APP_NAME, "The password cannot contain line breaks.")
            return
        self._run("apply", {"spec": dict(d.spec), "password": pw.text(),
                            "connect": d.state != "disconnected"},
                  "Updating password for %s" % d.name, d.id)

    def show_log(self):
        d = self.selected()
        if not d:
            return
        args = ["journalctl", "--no-pager", "-o", "short-iso", "-n", "200"]
        for u in d.units:
            args += ["-u", u]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
            text = proc.stdout.strip()
            hint = proc.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            text, hint = "", str(exc)
        LogDialog(self, d, text, hint).exec()

    def copy_fstab_entry(self):
        sel = self.fstab_tree.selectedItems()
        if not sel:
            return
        entry = self.fstab_entries[sel[0].data(0, Qt.UserRole)]
        template = fstab_to_template(entry, setting(self.settings, "mount_root"), self._taken())
        QMessageBox.information(
            self, APP_NAME,
            "This creates a CataMount drive with the same share and options, in a new local "
            "folder (%s).\n\nCataMount never edits /etc/fstab. When you're happy with the new "
            "drive, remove or comment out the old line in /etc/fstab yourself; you can then "
            "edit the drive to use %s." % (template["mount_point"], entry.target))
        dlg = DriveDialog(self.settings, taken=self._taken(), parent=self, template=template,
                          title="Copy fstab entry")
        if dlg.exec():
            spec, password = dlg.request()
            self._run("apply", {"spec": spec, "password": password, "connect": True},
                      "Connecting %s" % spec["name"], spec["id"])

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
            if upd and \
                    self.settings.value("notified_version", "") != upd["version"]:
                self.settings.setValue("notified_version", upd["version"])
                self.notify("CataMount %s is available." % upd["version"], timeout=8000)
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
