# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Folder contents model with Explorer behavior: columns Name / Date modified / Type / Size,
optional grouping (date buckets like "Yesterday", "Earlier this week", type, name, size),
Windows-style sizes ("1,234 KB") and dates, theme icons, async thumbnails, rename, drag and
drop routed through FileOperation. Loads in a background thread so slow network folders
don't freeze the window, and reloads when the folder changes."""

import math
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import (QAbstractItemModel, QDateTime, QFileInfo, QFileSystemWatcher,
                            QMimeData, QMimeDatabase, QModelIndex, QObject, QSize, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import QFont, QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QApplication

from caracal.explorer import theme

THUMB_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg", "ico", "xpm",
              "heic", "avif"}
COLUMNS = ("Name", "Date modified", "Type", "Size")
NAME, DATE, TYPE, SIZE = range(4)
GROUP_ROLE = Qt.UserRole + 10
GROUPS = (("none", "(None)"), ("date", "Date modified"), ("type", "Type"), ("name", "Name"),
          ("size", "Size"))


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    hidden: bool
    type_name: str = ""
    group: int = 0          # index into the model's group list


# --------------------------------------------------------------------------
# grouping rules (Windows Explorer's buckets)
# --------------------------------------------------------------------------

def date_bucket(mtime, now=None):
    now = time.time() if now is None else now
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    week_start = midnight - t.tm_wday * 86400            # Monday
    month_start = time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    prev_month = time.mktime((t.tm_year - (t.tm_mon == 1), (t.tm_mon - 2) % 12 + 1, 1,
                              0, 0, 0, 0, 0, -1))
    year_start = time.mktime((t.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))
    if mtime >= midnight + 86400:
        return 0, "In the future"
    if mtime >= midnight:
        return 1, "Today"
    if mtime >= midnight - 86400:
        return 2, "Yesterday"
    if mtime >= week_start:
        return 3, "Earlier this week"
    if mtime >= week_start - 7 * 86400:
        return 4, "Last week"
    if mtime >= month_start:
        return 5, "Earlier this month"
    if mtime >= prev_month:
        return 6, "Last month"
    if mtime >= year_start:
        return 7, "Earlier this year"
    return 8, "A long time ago"


def size_bucket(e):
    if e.is_dir:
        return 0, "Unspecified"
    s = e.size
    for i, (limit, label) in enumerate(((0, "Empty (0 KB)"), (16 * 1024, "Tiny (0 - 16 KB)"),
                                        (1024 ** 2, "Small (16 KB - 1 MB)"),
                                        (128 * 1024 ** 2, "Medium (1 - 128 MB)"),
                                        (1024 ** 3, "Large (128 MB - 1 GB)"),
                                        (4 * 1024 ** 3, "Huge (1 - 4 GB)")), 1):
        if s <= limit:
            return i, label
    return 7, "Gigantic (> 4 GB)"


def name_bucket(e):
    c = e.name[:1].upper()
    if "A" <= c <= "H":
        return 1, "A - H"
    if "I" <= c <= "P":
        return 2, "I - P"
    if "Q" <= c <= "Z":
        return 3, "Q - Z"
    if c.isdigit():
        return 0, "0 - 9"
    return 4, "Other"


def win_size(n):
    """Windows Explorer style: always KB, rounded up, with separators."""
    return "%s KB" % format(int(math.ceil(n / 1024.0)), ",")


class _Relay(QObject):
    loaded = Signal(int, str, list, str)
    thumb = Signal(str, QImage)


class DirModel(QAbstractItemModel):
    directoryLoaded = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.root = ""
        self.entries = []           # all entries (filtered for hidden)
        self.groups = []            # [(label, [Entry...])] when grouped
        self.group_by = "none"
        self.sort_col = NAME
        self.sort_order = Qt.AscendingOrder
        self.show_hidden = False
        self.hide_ext = False
        self.thumb_size = 0
        self.on_drop = None
        self.on_rename = None
        self.loading = False
        self.error = ""             # why the folder couldn't be read ("" = fine)
        self._gen = 0
        self._mime = QMimeDatabase()
        self._types = {}
        self._thumbs = {}
        self._pending = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._relay = _Relay()
        self._relay.loaded.connect(self._loaded)
        self._relay.thumb.connect(self._thumb_ready)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._reload_timer.start(300))
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self.reload)

    # -- loading -------------------------------------------------------------------

    def setRootPath(self, path):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.root = path
        self.error = ""
        self.beginResetModel()
        self.entries, self.groups = [], []
        self._thumbs.clear()
        self.endResetModel()
        if path:
            self.watcher.addPath(path)
            self.reload()
        return QModelIndex()

    def rootPath(self):
        return self.root

    def reload(self):
        if not self.root:
            return
        self._gen += 1
        gen, root = self._gen, self.root
        self.loading = True
        threading.Thread(target=self._scan, args=(gen, root), daemon=True).start()

    def _scan(self, gen, root):
        out = []
        err = ""
        try:
            with os.scandir(root) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=True)
                    except OSError:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                    is_dir = stat.S_ISDIR(st.st_mode)
                    out.append(Entry(e.name, e.path, is_dir, 0 if is_dir else st.st_size,
                                     st.st_mtime, e.name.startswith(".")))
        except PermissionError:
            err = "You don't currently have permission to access this folder."
        except FileNotFoundError:
            err = "This folder doesn't exist anymore. It may have been moved or deleted."
        except OSError as exc:
            err = "This folder can't be opened: %s" % (exc.strerror or exc)
        self._relay.loaded.emit(gen, root, out, err)

    def _loaded(self, gen, root, entries, err=""):
        if gen != self._gen or root != self.root:
            return
        self.loading = False
        self.error = err
        for e in entries:
            e.type_name = self.type_name(e)
        self.all_entries = entries
        self._rebuild()
        self.directoryLoaded.emit(root)

    def _rebuild(self):
        self.beginResetModel()
        self.entries = [e for e in getattr(self, "all_entries", [])
                        if self.show_hidden or not e.hidden]
        self._sort_entries(self.entries)
        self.groups = []
        if self.group_by != "none":
            buckets = {}
            for e in self.entries:
                if self.group_by == "date":
                    key = date_bucket(e.mtime)
                elif self.group_by == "size":
                    key = size_bucket(e)
                elif self.group_by == "name":
                    key = name_bucket(e)
                else:
                    key = (0 if e.is_dir else 1, e.type_name)
                buckets.setdefault(key, []).append(e)
            keys = sorted(buckets)
            if self.group_by in ("date",) and self.sort_col == DATE and \
                    self.sort_order == Qt.AscendingOrder:
                keys.reverse()
            self.groups = [(k[1], buckets[k]) for k in keys]
            for gi, (_label, items) in enumerate(self.groups):
                for e in items:
                    e.group = gi
        self.endResetModel()

    def _sort_entries(self, items):
        rev = self.sort_order == Qt.DescendingOrder
        col = self.sort_col
        if col == DATE:
            key = lambda e: e.mtime  # noqa: E731
        elif col == SIZE:
            key = lambda e: e.size  # noqa: E731
        elif col == TYPE:
            key = lambda e: (e.type_name.lower(), e.name.lower())  # noqa: E731
        else:
            key = lambda e: _natural(e.name)  # noqa: E731
        items.sort(key=key, reverse=rev)
        if col in (NAME, TYPE, SIZE):
            items.sort(key=lambda e: not e.is_dir)   # folders first, like Explorer

    def set_group_by(self, mode):
        self.group_by = mode
        self._rebuild()

    def set_show_hidden(self, on):
        self.show_hidden = on
        self._rebuild()

    def sort(self, column, order=Qt.AscendingOrder):
        self.sort_col, self.sort_order = column, order
        self._rebuild()

    def type_name(self, e):
        if e.is_dir:
            return "File folder"
        ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.name.lstrip(".") else ""
        if ext not in self._types:
            mt = self._mime.mimeTypeForFile(e.name, QMimeDatabase.MatchExtension)
            comment = mt.comment() if mt.isValid() and not mt.isDefault() else ""
            self._types[ext] = comment[:1].upper() + comment[1:] if comment else \
                ("%s File" % ext.upper() if ext else "File")
        return self._types[ext]

    # -- structure -------------------------------------------------------------------
    # Grouped: top level = groups (internalId 0), children = entries (internalId = group+1).
    # Flat: top level = entries (internalId 0, but no groups).

    def grouped(self):
        return bool(self.groups)

    def index(self, row, column=0, parent=QModelIndex()):
        if isinstance(row, str):        # index(path) convenience
            return self.index_for_path(row)
        if column < 0 or column >= 4 or row < 0:
            return QModelIndex()
        if not parent.isValid():
            n = len(self.groups) if self.grouped() else len(self.entries)
            return self.createIndex(row, column, 0) if row < n else QModelIndex()
        if self.grouped() and parent.internalId() == 0:
            g = parent.row()
            if g < len(self.groups) and row < len(self.groups[g][1]):
                return self.createIndex(row, column, g + 1)
        return QModelIndex()

    def parent(self, index=QModelIndex()):
        if not index.isValid() or index.internalId() == 0:
            return QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, 0)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self.groups) if self.grouped() else len(self.entries)
        if self.grouped() and parent.internalId() == 0 and parent.column() == 0:
            return len(self.groups[parent.row()][1])
        return 0

    def columnCount(self, parent=QModelIndex()):
        return 4

    def hasChildren(self, parent=QModelIndex()):
        return self.rowCount(parent) > 0

    def entry(self, index):
        if not index.isValid():
            return None
        if self.grouped():
            if index.internalId() == 0:
                return None
            items = self.groups[index.internalId() - 1][1]
            return items[index.row()] if index.row() < len(items) else None
        return self.entries[index.row()] if index.row() < len(self.entries) else None

    def is_group(self, index):
        return index.isValid() and self.grouped() and index.internalId() == 0

    def index_for_path(self, path):
        for i, e in enumerate(self.entries if not self.grouped() else []):
            if e.path == path:
                return self.createIndex(i, 0, 0)
        for gi, (_l, items) in enumerate(self.groups):
            for i, e in enumerate(items):
                if e.path == path:
                    return self.createIndex(i, 0, gi + 1)
        return QModelIndex()

    def filePath(self, index):
        e = self.entry(index)
        return e.path if e else ""

    def isDir(self, index):
        e = self.entry(index)
        return bool(e and e.is_dir)

    def fileInfo(self, index):
        return QFileInfo(self.filePath(index))

    def item_count(self):
        return len(self.entries)

    # -- data ----------------------------------------------------------------------

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section < 4:
            return COLUMNS[section]
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole and section == SIZE:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if self.is_group(index):
            label, items = self.groups[index.row()]
            if role == Qt.DisplayRole and index.column() == 0:
                return "%s (%d)" % (label, len(items)) if self.settings_bool("group_counts") else label
            if role == GROUP_ROLE:
                return True
            if role == Qt.FontRole:
                f = QFont(QApplication.font())
                f.setPointSizeF(f.pointSizeF() * 1.15)
                return f
            return None
        e = self.entry(index)
        if e is None:
            return None
        col = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == NAME:
                if self.hide_ext and not e.is_dir and "." in e.name.lstrip("."):
                    return e.name.rsplit(".", 1)[0]
                return e.name
            if role == Qt.EditRole:
                return None
            if col == DATE:
                return self.format_date(e.mtime)
            if col == TYPE:
                return e.type_name
            if col == SIZE:
                return "" if e.is_dir else self.format_size(e.size)
        if role == Qt.DecorationRole and col == NAME:
            info = QFileInfo(e.path)
            if self.thumb_size and not e.is_dir and \
                    e.name.rsplit(".", 1)[-1].lower() in THUMB_EXTS:
                icon = self._thumbs.get(e.path)
                if icon is not None:
                    return icon
                self._request_thumb(e)
            return theme.get().icon("folder") if e.is_dir and theme.get()._file("folder") \
                else theme.get().file_icon(info)
        if role == Qt.TextAlignmentRole and col == SIZE:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and col != NAME:
            return theme.color("subtle")
        if role == Qt.ToolTipRole and col == NAME and "/Trash/files/" in e.path or \
                role == Qt.ToolTipRole and col == NAME and "/.Trash-" in e.path:
            from caracal.explorer.fileops import trash_original
            orig, when = trash_original(e.path)
            if orig:
                return "%s\nOriginal location: %s\nDate deleted: %s" % (e.name, os.path.dirname(orig), when)
        if role == Qt.ToolTipRole and col == NAME:
            return "%s\nType: %s\nDate modified: %s%s" % (
                e.name, e.type_name, self.format_date(e.mtime),
                "" if e.is_dir else "\nSize: " + self.format_size(e.size))
        return None

    def settings_bool(self, key, default=False):
        v = self.settings.value("explorer/" + key, default)
        return v if isinstance(v, bool) else str(v).lower() in ("true", "1")

    def format_date(self, ts):
        fmt = self.settings.value("explorer/date_format", "M/d/yyyy h:mm AP") or "M/d/yyyy h:mm AP"
        return QDateTime.fromSecsSinceEpoch(int(ts)).toString(fmt)

    def format_size(self, n):
        if self.settings.value("explorer/size_style", "windows") == "auto":
            from caracal.model import format_size
            return format_size(n)
        return win_size(n)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        if self.is_group(index):
            return Qt.ItemIsEnabled
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if index.column() == NAME:
            f |= Qt.ItemIsEditable
        if self.isDir(index):
            f |= Qt.ItemIsDropEnabled
        return f

    def setData(self, index, value, role=Qt.EditRole):
        e = self.entry(index)
        if role != Qt.EditRole or e is None or index.column() != NAME:
            return False
        new_name = str(value).strip()
        if not new_name or "/" in new_name:
            return False
        if self.hide_ext and not e.is_dir and "." in e.name.lstrip("."):
            new_name += "." + e.name.rsplit(".", 1)[1]
        if new_name == e.name:
            return False
        new = os.path.join(os.path.dirname(e.path), new_name)
        if os.path.lexists(new):
            return False
        try:
            os.rename(e.path, new)
        except OSError:
            return False
        if self.on_rename:
            self.on_rename(e.path, new)
        e.name, e.path = new_name, new
        self.dataChanged.emit(index, index)
        self.reload()
        return True

    # -- thumbnails ----------------------------------------------------------------------

    def _request_thumb(self, e):
        if e.size > 64 * 1024 * 1024:
            return
        with self._lock:
            if e.path in self._pending:
                return
            self._pending.add(e.path)
        self._pool.submit(self._load_thumb, e.path, self.thumb_size)

    def _load_thumb(self, path, edge):
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and (sz.width() > edge or sz.height() > edge):
            reader.setScaledSize(sz.scaled(QSize(edge, edge), Qt.KeepAspectRatio))
        self._relay.thumb.emit(path, reader.read())

    def _thumb_ready(self, path, img):
        with self._lock:
            self._pending.discard(path)
        self._thumbs[path] = QIcon(QPixmap.fromImage(img)) if not img.isNull() \
            else theme.get().file_icon(QFileInfo(path))
        idx = self.index_for_path(path)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def clear_thumbs(self):
        self._thumbs.clear()

    # -- drag and drop --------------------------------------------------------------------

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        paths = sorted({self.filePath(i) for i in indexes if self.entry(i)})
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        md.setText("\n".join(paths))
        return md

    def supportedDragActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction

    def canDropMimeData(self, data, action, row, column, parent):
        return data.hasUrls()

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasUrls() or self.on_drop is None:
            return False
        e = self.entry(parent)
        dest = e.path if (e and e.is_dir) else self.root
        sources = [u.toLocalFile() for u in data.urls() if u.isLocalFile()]
        sources = [s for s in sources if s != dest and (os.path.dirname(s) != dest)]
        if not sources:
            return False
        mods = QApplication.keyboardModifiers()
        if mods & Qt.ControlModifier:
            kind = "copy"
        elif mods & Qt.ShiftModifier:
            kind = "move"
        else:
            from caracal.explorer.fileops import same_device
            kind = "move" if all(same_device(s, dest) for s in sources) else "copy"
        self.on_drop(kind, sources, dest)
        return False   # FileOperation does the work (with progress)


def _natural(name):
    """Natural sort: 'file 2' before 'file 10', case-insensitive (like Explorer)."""
    import re
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in
            re.split(r"(\d+)", name.lower()) if t != ""]
