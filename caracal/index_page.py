# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Main-window tab: choose which drives are indexed, and see indexing progress."""

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from caracal import indexdb
from caracal.model import format_size


def _ago(ts):
    if not ts:
        return "never"
    mins = int((time.time() - ts) / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return "%d min ago" % mins
    if mins < 48 * 60:
        return "%d h ago" % (mins // 60)
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class IndexPage(QWidget):
    def __init__(self, manager, open_search, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.open_search = open_search
        self._updating = False
        lay = QVBoxLayout(self)
        intro = QLabel("Indexed drives can be searched instantly from Caracal's search "
                       "window, the desktop search (KRunner / GNOME), and the file manager's "
                       "right-click menu. The index stays on this computer and searching "
                       "never touches the network.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Drive", "Names", "Contents", "Files", "Status", "Updated"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Interactive)
        hdr.resizeSection(4, 220)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.setToolTip("Names: index file and folder names. Contents: also index text "
                             "inside documents, PDFs and text files (reads every supported "
                             "file over the network once).")
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.info = QLabel()
        self.info.setWordWrap(True)
        row.addWidget(self.info, 1)
        self.rescan_btn = QPushButton("Rescan now")
        self.rescan_btn.clicked.connect(lambda: self._act("rescan"))
        self.full_btn = QPushButton("Full refresh")
        self.full_btn.setToolTip("Re-read every folder (also picks up edits to existing files)")
        self.full_btn.clicked.connect(lambda: self._act("full"))
        self.rebuild_btn = QPushButton("Rebuild…")
        self.rebuild_btn.clicked.connect(lambda: self._act("rebuild"))
        search = QPushButton("Search…")
        search.clicked.connect(lambda: self.open_search())
        for b in (self.rescan_btn, self.full_btn, self.rebuild_btn, search):
            row.addWidget(b)
        lay.addLayout(row)
        manager.changed.connect(self.refresh)
        self.refresh()

    def _selected_id(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def _update_buttons(self):
        drive_id = self._selected_id()
        on = bool(drive_id) and self.manager.is_enabled(drive_id)
        for b in (self.rescan_btn, self.full_btn, self.rebuild_btn):
            b.setEnabled(on)

    def refresh(self):
        sel = self._selected_id()
        status = self.manager.status()
        self._updating = True
        self.tree.clear()
        total_files = 0
        for drive_id, d in sorted(self.manager.drives.items(), key=lambda kv: kv[1].name.lower()):
            st = status.get(drive_id, {})
            item = QTreeWidgetItem([d.name, "", "", format(st.get("files") or 0, ",")
                                    if st.get("names") else "", st.get("state", ""),
                                    _ago(st.get("last_scan")) if st.get("names") else ""])
            item.setData(0, Qt.UserRole, drive_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(1, Qt.Checked if st.get("names") else Qt.Unchecked)
            item.setCheckState(2, Qt.Checked if st.get("content") else Qt.Unchecked)
            item.setToolTip(4, st.get("state", ""))
            if st.get("content_files"):
                item.setToolTip(2, "%s files with searchable contents"
                                % format(st["content_files"], ","))
            self.tree.addTopLevelItem(item)
            if drive_id == sel:
                item.setSelected(True)
            if st.get("names"):
                total_files += st.get("files") or 0
        self._updating = False
        path = indexdb.default_path()
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(path + suffix)
            except OSError:
                pass
        from caracal import indexstore
        extra = indexstore.note()
        self.info.setText("%s files indexed · index size %s\n%s%s" % (
            format(total_files, ","), format_size(size), indexstore.describe(),
            ("\n⚠ " + extra) if extra else ""))
        self._update_buttons()

    def _item_changed(self, item, column):
        if self._updating or column not in (1, 2):
            return
        drive_id = item.data(0, Qt.UserRole)
        names = item.checkState(1) == Qt.Checked
        content = item.checkState(2) == Qt.Checked
        if column == 2 and content and not names:
            names = True
        if column == 1 and not names and self.manager.is_enabled(drive_id):
            if QMessageBox.question(self, "Search index",
                                    "Stop indexing this drive and delete its index?") \
                    != QMessageBox.Yes:
                self.refresh()
                return
        self.manager.set_drive(drive_id, names, content)

    def _act(self, what):
        drive_id = self._selected_id()
        if not drive_id:
            return
        if what == "rebuild":
            if QMessageBox.question(self, "Search index", "Delete this drive's index and build "
                                    "it again from scratch?") != QMessageBox.Yes:
                return
            self.manager.rebuild(drive_id)
        else:
            self.manager.rescan(drive_id, full=(what == "full"))
