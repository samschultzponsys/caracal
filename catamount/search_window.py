# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Instant search over the local index of network drives (Everything-style)."""

import os
import shutil
import subprocess
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QPushButton, QTableView, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QMimeDatabase

from catamount import APP_NAME, indexdb
from catamount.model import format_size

COLUMNS = ("Name", "In folder", "Size", "Modified", "Drive", "Match")
ORDER_KEYS = ("name", "folder", "size", "modified", "drive", "name")
# Column order on screen; content searches show the matching text right after the name.
LAYOUT_NAMES = (0, 1, 2, 3, 4)
LAYOUT_CONTENT = (0, 5, 1, 2, 3, 4)
WIDTHS = {0: 300, 1: 360, 2: 90, 3: 130, 4: 150, 5: 320}
TYPES = (("", "All types"), ("type:folder", "Folders"), ("type:file", "Files"),
         ("type:doc", "Documents"), ("type:sheet", "Spreadsheets"), ("type:slides", "Slides"),
         ("type:pic", "Pictures"), ("type:audio", "Audio"), ("type:video", "Video"),
         ("type:zip", "Archives"), ("type:code", "Code"))

HELP = ("Type part of a name. All words must match. Filters: ext:pdf  type:pic  "
        "size:>100mb  modified:week  folder:projects  drive:nas  content:\"some phrase\"  "
        "*.iso  report?.docx")


def show_in_folder(path):
    """Open the containing folder with the file selected when the file manager supports it."""
    uri = QUrl.fromLocalFile(path).toString()
    for tool in (["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                  "--object-path", "/org/freedesktop/FileManager1",
                  "--method", "org.freedesktop.FileManager1.ShowItems", "['%s']" % uri, ""],
                 ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                  "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                  "array:string:%s" % uri, "string:"]):
        if shutil.which(tool[0]):
            try:
                if subprocess.run(tool, capture_output=True, timeout=5).returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class ResultModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.show_match = False
        self.offline = set()
        self._mime = QMimeDatabase()
        self._icons = {}

    def set_rows(self, rows, show_match, offline):
        self.beginResetModel()
        self.rows = rows
        self.show_match = show_match
        self.offline = offline
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    @property
    def layout(self):
        return LAYOUT_CONTENT if self.show_match else LAYOUT_NAMES

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.layout)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section < len(self.layout):
            return COLUMNS[self.layout[section]]
        return None

    def _icon(self, r):
        key = "/" if r.is_dir else (r.name.rsplit(".", 1)[-1].lower() if "." in r.name else "")
        icon = self._icons.get(key)
        if icon is None:
            if r.is_dir:
                icon = QIcon.fromTheme("folder")
            else:
                mt = self._mime.mimeTypeForFile(r.name, QMimeDatabase.MatchExtension)
                icon = QIcon.fromTheme(mt.iconName(), QIcon.fromTheme(mt.genericIconName(),
                                                                      QIcon.fromTheme("text-x-generic")))
            self._icons[key] = icon
        return icon

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = self.layout[index.column()]
        if role == Qt.DisplayRole:
            if col == 0:
                return r.name
            if col == 1:
                return r.folder_path
            if col == 2:
                return "" if r.is_dir else format_size(r.size or 0)
            if col == 3:
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(r.mtime)) if r.mtime else ""
            if col == 4:
                return r.drive_name + (" (offline)" if r.drive_id in self.offline else "")
            if col == 5:
                return r.snippet
        if role == Qt.DecorationRole and col == 0:
            return self._icon(r)
        if role == Qt.ToolTipRole:
            return r.path if col < 5 else r.snippet
        if role == Qt.TextAlignmentRole and col == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class SearchWindow(QMainWindow):
    def __init__(self, drives_provider=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search network drives — %s" % APP_NAME)
        self.setWindowIcon(QIcon.fromTheme("com.spazcat.CataMount"))
        self.settings = QSettings("catamount", "catamount")
        self.drives_provider = drives_provider or (lambda: [])
        self.conn = None
        self.folder = ""
        self.order = "name"
        self.desc = False
        self.resize(980, 600)
        geo = self.settings.value("search/geometry")
        if geo is not None:
            self.restoreGeometry(geo)

        central = QWidget()
        lay = QVBoxLayout(central)
        top = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search files and folders on network drives…")
        self.query.setClearButtonEnabled(True)
        f = self.query.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        self.query.setFont(f)
        self.query.setToolTip(HELP)
        self.query.textChanged.connect(self._schedule)
        self.query.returnPressed.connect(self.open_current)
        self.query.installEventFilter(self)
        top.addWidget(self.query, 1)
        lay.addLayout(top)

        filters = QHBoxLayout()
        self.drive_box = QComboBox()
        self.drive_box.currentIndexChanged.connect(self._schedule)
        self.type_box = QComboBox()
        for value, label in TYPES:
            self.type_box.addItem(label, value)
        self.type_box.currentIndexChanged.connect(self._schedule)
        self.inside = QCheckBox("Search inside files")
        self.inside.setToolTip("Match the words against file contents (for drives with "
                               "content indexing turned on)")
        self.inside.toggled.connect(self._schedule)
        self.scope = QPushButton()
        self.scope.setFlat(True)
        self.scope.setToolTip("Click to search all drives")
        self.scope.clicked.connect(lambda: self.set_folder(""))
        self.scope.hide()
        filters.addWidget(self.drive_box)
        filters.addWidget(self.type_box)
        filters.addWidget(self.inside)
        filters.addWidget(self.scope)
        filters.addStretch(1)
        lay.addLayout(filters)

        self.model = ResultModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(
            self.table.fontMetrics().height() + 6)
        self.table.setSortingEnabled(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionsClickable(True)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(0, Qt.AscendingOrder)
        hdr.sectionClicked.connect(self._sort)
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.resizeSection(0, 300)
        hdr.resizeSection(1, 360)
        hdr.resizeSection(2, 90)
        hdr.resizeSection(3, 130)
        state = self.settings.value("search/columns")
        if state is not None:
            hdr.restoreState(state)
        self.table.doubleClicked.connect(lambda _i: self.open_current())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.table, 1)
        self.setCentralWidget(central)

        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)
        self.help = QLabel(HELP)
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.help)

        for seq, slot in ((QKeySequence("Ctrl+Return"), self.open_folder),
                          (QKeySequence("Ctrl+Shift+C"), self.copy_path),
                          (QKeySequence("Escape"), self.close),
                          (QKeySequence("Ctrl+F"), self.query.setFocus),
                          (QKeySequence("Ctrl+L"), self.query.setFocus)):
            act = QAction(self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            self.addAction(act)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.run)
        self.reload_drives()

    # -- public --------------------------------------------------------------

    def open_search(self, query="", folder=""):
        self.reload_drives()
        if folder:
            self.set_folder(folder)
        if query:
            self.query.setText(query)
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.query.setFocus()
        self.query.selectAll()
        self.run()

    def set_folder(self, folder):
        self.folder = os.path.abspath(folder) if folder else ""
        if self.folder:
            self.scope.setText("In: %s  ✕" % self.folder)
            self.scope.show()
        else:
            self.scope.hide()
        self._schedule()

    def reload_drives(self):
        current = self.drive_box.currentData()
        self.drive_box.blockSignals(True)
        self.drive_box.clear()
        self.drive_box.addItem("All drives", "")
        if self.conn is None:
            self.conn = indexdb.connect(readonly=True)
        for drive_id, st in sorted(indexdb.drive_stats(self.conn).items(),
                                   key=lambda kv: (kv[1]["name"] or "").lower()):
            self.drive_box.addItem(st["name"] or st["mount_point"], drive_id)
        idx = self.drive_box.findData(current)
        self.drive_box.setCurrentIndex(max(0, idx))
        self.drive_box.blockSignals(False)

    # -- searching -------------------------------------------------------------

    def _schedule(self, *_):
        self.timer.start(60)

    def _build_query(self):
        text = self.query.text().strip()
        if self.inside.isChecked() and text:
            parts = []
            for tok in text.split():
                if ":" in tok or "*" in tok or "?" in tok:
                    parts.append(tok)
                else:
                    parts.append('content:"%s"' % tok.strip('"'))
            text = " ".join(parts)
        t = self.type_box.currentData()
        if t:
            text = (text + " " + t).strip()
        q = indexdb.parse_query(text)
        q.folder = self.folder
        return q

    def run(self):
        if self.conn is None:
            self.conn = indexdb.connect(readonly=True)
        if self.conn is None:
            self.model.set_rows([], False, set())
            self.status.setText("No drives are indexed yet. In CataMount, open the "
                                "“Search index” tab and turn indexing on for a drive.")
            return
        q = self._build_query()
        drive = self.drive_box.currentData()
        try:
            rows, total, ms = indexdb.search(self.conn, q, limit=1000, order=self.order,
                                             descending=self.desc,
                                             drives=[drive] if drive else None)
        except Exception as exc:  # e.g. sqlite busy during a big write
            self.status.setText("Search failed: %s" % exc)
            return
        offline = {d.id for d in self.drives_provider()
                   if d.state not in ("connected", "ready")}
        old_layout = self.model.layout
        self.model.set_rows(rows, bool(q.content), offline)
        if self.model.layout != old_layout:
            hdr = self.table.horizontalHeader()
            for i, c in enumerate(self.model.layout[:-1]):
                hdr.resizeSection(i, WIDTHS[c])
        self.help.setVisible(q.empty)
        if q.empty:
            stats = indexdb.drive_stats(self.conn)
            files = sum(s["files"] for s in stats.values())
            newest = max([s["last_scan"] or 0 for s in stats.values()] or [0])
            when = self._ago(newest) if newest else "never"
            self.status.setText("%s files indexed on %d drive%s · updated %s"
                                % (format(files, ","), len(stats), "" if len(stats) == 1 else "s",
                                   when))
            return
        shown = format(total, ",") if total is not None else "%s+" % format(indexdb.COUNT_CAP, ",")
        note = " (showing first %d)" % len(rows) if total is None or total > len(rows) else ""
        self.status.setText("%s result%s in %.0f ms%s" % (shown, "" if total == 1 else "s", ms,
                                                           note))
        if rows:
            self.table.selectRow(0)

    @staticmethod
    def _ago(ts):
        mins = int((time.time() - ts) / 60)
        if mins < 1:
            return "just now"
        if mins < 60:
            return "%d min ago" % mins
        if mins < 48 * 60:
            return "%d h ago" % (mins // 60)
        return "%d days ago" % (mins // 1440)

    def _sort(self, section):
        key = ORDER_KEYS[self.model.layout[section]]
        if key == self.order:
            self.desc = not self.desc
        else:
            self.order, self.desc = key, key in ("size", "modified")
        self.table.horizontalHeader().setSortIndicator(
            section, Qt.DescendingOrder if self.desc else Qt.AscendingOrder)
        self.run()

    # -- actions -----------------------------------------------------------------

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.rows[r] for r in rows if r < len(self.model.rows)]

    def open_current(self):
        for r in self.selected()[:20]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(r.path))

    def open_folder(self):
        sel = self.selected()
        if sel:
            show_in_folder(sel[0].path)

    def copy_path(self):
        QApplication.clipboard().setText("\n".join(r.path for r in self.selected()))

    def _menu(self, pos):
        if not self.selected():
            return
        m = QMenu(self)
        m.addAction(QIcon.fromTheme("document-open"), "Open", self.open_current)
        m.addAction(QIcon.fromTheme("folder-open"), "Open containing folder", self.open_folder)
        m.addSeparator()
        m.addAction(QIcon.fromTheme("edit-copy"), "Copy full path", self.copy_path)
        m.addAction("Copy name", lambda: QApplication.clipboard().setText(
            "\n".join(r.name for r in self.selected())))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        # Down/Up arrows in the search field move through the results.
        if obj is self.query and event.type() == event.Type.KeyPress and \
                event.key() in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
            self.table.setFocus()
            QApplication.sendEvent(self.table, event)
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self.settings.setValue("search/geometry", self.saveGeometry())
        self.settings.setValue("search/columns", self.table.horizontalHeader().saveState())
        super().closeEvent(event)
