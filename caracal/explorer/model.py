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
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import (QAbstractItemModel, QDateTime, QFileInfo, QFileSystemWatcher,
                            QMimeData, QMimeDatabase, QModelIndex, QObject, QSize, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import QFont, QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QApplication
import shiboken6

from caracal import fsinfo
from caracal.explorer import columns as cols, metadata, theme
from caracal.explorer.columns import (COLUMNS, DATE, NAME, SIZE, TYPE, date_bucket,  # noqa: F401
                                      name_bucket, size_bucket)

THUMB_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg", "ico", "xpm",
              "heic", "avif"}
GROUP_ROLE = Qt.UserRole + 10


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
    atime: float = 0.0
    mode: int = 0o644
    uid: int = 0
    gid: int = 0
    is_link: bool = False
    link: str = None        # symlink target, read on demand
    meta: dict = None       # extra details (picture/media/document), loaded in the background


def win_size(n):
    """Windows Explorer style: always KB, rounded up, with separators."""
    return "%s KB" % format(int(math.ceil(n / 1024.0)), ",")


_RELAYS = []


class _Relay(QObject):
    """Carries worker-thread results to the GUI thread. Holds the model weakly and checks
    it's still alive, so results that arrive after a window closed are just dropped."""
    loaded = Signal(int, str, list, str)
    thumb = Signal(str, QImage)
    meta = Signal(int, list)
    partial = Signal(int, str, list, int)

    def __init__(self, owner):
        super().__init__()
        self._owner = weakref.ref(owner)
        self.loaded.connect(lambda *a: self._call("_loaded", a))
        self.thumb.connect(lambda *a: self._call("_thumb_ready", a))
        self.meta.connect(lambda *a: self._call("_meta_ready", a))
        self.partial.connect(lambda *a: self._call("_partial", a))

    def _call(self, name, args):
        owner = self._owner()
        if owner is not None and shiboken6.isValid(owner):
            getattr(owner, name)(*args)


class DirModel(QAbstractItemModel):
    directoryLoaded = Signal(str)
    progress = Signal()          # loading count / details progress changed (status bar)

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
        self.all_entries = []
        self.filters = {}           # column -> set of bucket labels to show (header filters)
        self.visible_cols = {NAME, DATE, TYPE, SIZE}
        self.cache_lookup = None    # path -> (rows, root id) from the search index, or None
        self.stale_error = ""       # live listing failed; the index's copy is shown
        self.from_index = False     # showing the index's listing while the live one loads
        self._gen = 0
        self._by_path = {}
        self._meta_cache = {}       # (path, mtime) -> details dict
        self._meta_pending = set()
        self._meta_pool = ThreadPoolExecutor(max_workers=3)
        self._mime = QMimeDatabase()
        self._types = {}
        self._thumbs = {}
        self._pending = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._relay = _Relay(self)
        _RELAYS.append(self._relay)   # outlives the model: worker threads may still emit
        self._meta_timer = QTimer(self)        # re-sort/regroup once details trickle in
        self._meta_timer.setSingleShot(True)
        self._meta_timer.timeout.connect(self._rebuild)
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
        self.stale_error = ""
        self.filters = {}
        self.beginResetModel()
        self.entries, self.groups, self.all_entries = [], [], []
        self._by_path = {}
        self._thumbs.clear()
        self.endResetModel()
        if path:
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
        self.load_started = time.monotonic()
        self.from_index = False
        if not self.all_entries and self.cache_lookup is not None:
            self._show_cached(root)
        self._refreshing = bool(self.all_entries)
        self.loaded_count = 0
        self.progress.emit()
        threading.Thread(target=self._scan, args=(gen, root), daemon=True).start()

    def _show_cached(self, root):
        """Indexed folder: show the index's listing right away (a local database read),
        while the live listing from the share loads and then replaces it."""
        try:
            hit = self.cache_lookup(root)
        except Exception:
            hit = None
        if not hit or not hit[0]:
            return
        uid, gid = os.getuid(), os.getgid()
        entries = []
        for name, is_dir, size, mtime in hit[0]:
            e = Entry(name, os.path.join(root, name), is_dir, 0 if is_dir else size, mtime,
                      name.startswith("."), mode=0o40755 if is_dir else 0o100644,
                      uid=uid, gid=gid)
            e.type_name = self.type_name(e)
            e.meta = self._meta_cache.get((e.path, e.mtime))
            entries.append(e)
        self.all_entries = entries
        self._by_path = {e.path: e for e in entries}
        self.from_index = True
        self._rebuild()

    def _scan(self, gen, root):
        out = []
        err = ""
        last, sent = time.monotonic(), 0
        try:
            with os.scandir(root) as it:
                for e in it:
                    if gen != self._gen:
                        return              # navigated away: stop reading the share
                    now = time.monotonic()
                    if now - last > 0.25:
                        # show what we have so far (big or slow folders fill in as they load)
                        self._relay.partial.emit(gen, root, out[sent:], len(out))
                        last, sent = now, len(out)
                    try:
                        st = e.stat(follow_symlinks=True)
                    except OSError:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                    is_dir = stat.S_ISDIR(st.st_mode)
                    try:
                        link = e.is_symlink()
                    except OSError:
                        link = False
                    out.append(Entry(e.name, e.path, is_dir, 0 if is_dir else st.st_size,
                                     st.st_mtime, e.name.startswith("."), atime=st.st_atime,
                                     mode=st.st_mode, uid=st.st_uid, gid=st.st_gid,
                                     is_link=link))
        except PermissionError:
            err = "You don't currently have permission to access this folder."
        except FileNotFoundError:
            err = "This folder doesn't exist anymore. It may have been moved or deleted."
        except OSError as exc:
            err = "This folder can't be opened: %s" % (exc.strerror or exc)
        self._relay.loaded.emit(gen, root, out, err)

    def _partial(self, gen, root, chunk, count):
        if gen != self._gen or root != self.root:
            return
        self.loaded_count = count
        if chunk and not getattr(self, "_refreshing", False):
            for e in chunk:
                e.type_name = self.type_name(e)
                e.meta = self._meta_cache.get((e.path, e.mtime))
                self._by_path[e.path] = e
            self.all_entries = self.all_entries + chunk
            self._rebuild()
        self.progress.emit()

    def _loaded(self, gen, root, entries, err=""):
        if gen != self._gen or root != self.root:
            return
        if err and self.from_index and self.all_entries:
            # the share didn't answer: keep showing the index's copy, and say so
            self.loading = False
            self.stale_error = err
            self.progress.emit()
            self.directoryLoaded.emit(root)
            return
        self.stale_error = ""
        self.loading = False
        self.from_index = False
        self.loaded_count = len(entries)
        if not err and root not in self.watcher.directories() and \
                not fsinfo.is_unresponsive(root):
            self.watcher.addPath(root)
        self.error = err
        for e in entries:
            e.type_name = self.type_name(e)
            e.meta = self._meta_cache.get((e.path, e.mtime))
        self.all_entries = entries
        self._by_path = {e.path: e for e in entries}
        self._rebuild()
        self._request_meta()
        self.progress.emit()
        self.directoryLoaded.emit(root)

    def group_col(self):
        return cols.index_of(self.group_by) if self.group_by != "none" else -1

    def passes_filters(self, e, skip=None):
        for col, allowed in self.filters.items():
            if col != skip and cols.bucket(col, e)[1] not in allowed:
                return False
        return True

    def _rebuild(self):
        self.beginResetModel()
        self.entries = [e for e in self.all_entries
                        if (self.show_hidden or not e.hidden) and self.passes_filters(e)]
        self._sort_entries(self.entries)
        self.groups = []
        gcol = self.group_col()
        if gcol >= 0:
            buckets = {}
            for e in self.entries:
                buckets.setdefault(cols.bucket(gcol, e), []).append(e)
            keys = sorted(buckets)
            if self.sort_col == gcol and self.sort_order == Qt.DescendingOrder and \
                    COLUMNS[gcol].kind != "date":
                keys.reverse()
            elif COLUMNS[gcol].kind == "date" and self.sort_col == gcol and \
                    self.sort_order == Qt.AscendingOrder:
                keys.reverse()
            self.groups = [(k[1], buckets[k]) for k in keys]
            for gi, (_label, items) in enumerate(self.groups):
                for e in items:
                    e.group = gi
        self.endResetModel()

    def _sort_entries(self, items):
        rev = self.sort_order == Qt.DescendingOrder
        col = self.sort_col if 0 <= self.sort_col < len(COLUMNS) else NAME
        present = [e for e in items if cols.value(col, e) not in (None, "")]
        missing = [e for e in items if cols.value(col, e) in (None, "")]
        present.sort(key=lambda e: cols.sort_key(col, e), reverse=rev)
        missing.sort(key=lambda e: _natural(e.name))
        items[:] = present + missing
        if cols.folders_first(col):
            items.sort(key=lambda e: not e.is_dir)   # folders first, like Explorer

    def set_filter(self, col, labels):
        if labels:
            self.filters[col] = set(labels)
        else:
            self.filters.pop(col, None)
        self._request_meta()
        self._rebuild()

    def clear_filters(self):
        self.filters = {}
        self._rebuild()

    def filter_values(self, col):
        """[(label, count)] for the header filter of `col`, in Explorer's order. Counts
        respect the other columns' filters, so the lists narrow each other down."""
        counts = {}
        for e in self.all_entries:
            if (self.show_hidden or not e.hidden) and self.passes_filters(e, skip=col):
                k = cols.bucket(col, e)
                counts[k] = counts.get(k, 0) + 1
        return [(k[1], counts[k]) for k in sorted(counts)]

    # -- extra details (pictures, media, documents) -------------------------------------

    def meta_needed(self):
        want = set(self.visible_cols) | {self.sort_col, self.group_col()} | set(self.filters)
        return any(0 <= c < len(COLUMNS) and COLUMNS[c].meta for c in want)

    def set_visible_columns(self, visible):
        self.visible_cols = set(visible)
        self._request_meta()

    def _request_meta(self):
        if not self.meta_needed():
            return
        todo = [e for e in self.all_entries if e.meta is None and e.path not in self._meta_pending]
        if not todo:
            return
        gen = self._gen
        for i in range(0, len(todo), 40):
            chunk = [(e.path, e.is_dir, e.mtime) for e in todo[i:i + 40]]
            self._meta_pending.update(p for p, _d, _m in chunk)
            self._meta_pool.submit(self._load_meta, gen, chunk)

    def _load_meta(self, gen, chunk):
        out = []
        for path, is_dir, mtime in chunk:
            if gen != self._gen:
                out.append((path, mtime, None))
                continue
            out.append((path, mtime, metadata.read(path, is_dir)))
        self._relay.meta.emit(gen, out)

    def _meta_ready(self, gen, results):
        for path, mtime, info in results:
            self._meta_pending.discard(path)
            if info is None:
                continue
            if len(self._meta_cache) > 50000:
                self._meta_cache.clear()
            self._meta_cache[(path, mtime)] = info
            e = self._by_path.get(path)
            if e is not None and e.mtime == mtime:
                e.meta = info
        self.progress.emit()
        if gen != self._gen:
            return
        meta_order = {c for c in (self.sort_col, self.group_col(), *self.filters)
                      if 0 <= c < len(COLUMNS) and COLUMNS[c].meta}
        if meta_order:
            self._meta_timer.start(400)
        else:
            self._emit_all_changed()

    def details_left(self):
        return len(self._meta_pending)

    def _emit_all_changed(self):
        last = len(COLUMNS) - 1
        if self.grouped():
            for gi, (_l, items) in enumerate(self.groups):
                if items:
                    parent = self.createIndex(gi, 0, 0)
                    self.dataChanged.emit(self.index(0, 0, parent),
                                          self.index(len(items) - 1, last, parent))
        elif self.entries:
            self.dataChanged.emit(self.createIndex(0, 0, 0),
                                  self.createIndex(len(self.entries) - 1, last, 0))

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
        if column < 0 or column >= len(COLUMNS) or row < 0:
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
        return len(COLUMNS)

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

    def total_count(self):
        return sum(1 for e in self.all_entries if self.show_hidden or not e.hidden)

    # -- data ----------------------------------------------------------------------

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(COLUMNS):
            if role == Qt.DisplayRole:
                return COLUMNS[section].title
            if role == Qt.TextAlignmentRole and cols.right_aligned(section):
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
            return cols.display(col, e, self)
        if role == Qt.DecorationRole and col == NAME:
            if self.thumb_size and not e.is_dir and \
                    e.name.rsplit(".", 1)[-1].lower() in THUMB_EXTS:
                icon = self._thumbs.get(e.path)
                if icon is not None:
                    return icon
                self._request_thumb(e)
            return theme.get().name_icon(e.name, e.is_dir)
        if role == Qt.TextAlignmentRole and cols.right_aligned(col):
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
