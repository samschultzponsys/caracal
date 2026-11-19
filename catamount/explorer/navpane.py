# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Left navigation pane: Home/Quick access, This PC, Network, Trash."""

import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QStyledItemDelegate, QTreeView

from catamount.explorer import places, theme

LOC = Qt.UserRole
LAZY = Qt.UserRole + 1          # folder path whose subfolders load on expand
PINNED = Qt.UserRole + 2        # draw a pin at the right edge (Quick access)

LABEL_DEFAULTS = {"quick": "Quick access", "home": "Home", "thispc": "This PC",
                  "network": "Network", "trash": "Trash", "homefolder": "Home folder"}


class PinDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(PINNED):
            r = option.rect
            x, y = r.right() - 16, r.top() + (r.height() - 12) // 2
            if theme.get().has_icon("pin"):
                theme.get().icon("pin").paint(painter, x, y, 12, 12)
            else:
                # simple push-pin glyph
                from PySide6.QtGui import QPen
                painter.save()
                painter.setRenderHint(painter.RenderHint.Antialiasing)
                painter.setPen(QPen(theme.color("subtle"), 1.4))
                painter.drawLine(x + 3, y + 1, x + 9, y + 1)
                painter.drawRect(x + 4, y + 1, 4, 5)
                painter.drawLine(x + 2, y + 6, x + 10, y + 6)
                painter.drawLine(x + 6, y + 6, x + 6, y + 11)
                painter.restore()

    def sizeHint(self, option, index):
        sz = super().sizeHint(option, index)
        return QSize(sz.width(), max(sz.height(), int(theme.get().data.get("row_height", 24))))
TRASH = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                     "Trash", "files")


class NavPane(QTreeView):
    navigate = Signal(str)
    context = Signal(str, object)   # location, global pos

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavPane")
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setIconSize(QSize(18, 18))
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.expanded.connect(self._expand)
        self.clicked.connect(self._clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._ctx(self.indexAt(pos), self.viewport().mapToGlobal(pos)))
        self.pinned = []
        self.drives = []        # CataMount system drives (model.Drive)
        self.locations = []     # user_mounts.Location
        self.on_drop = None
        self._expanded_keys = set()
        self.style = "win10"
        self.labels = dict(LABEL_DEFAULTS)
        self.setItemDelegate(PinDelegate(self))

    # -- building ----------------------------------------------------------------

    def _item(self, text, icon_slot, loc, lazy=None, icon=None):
        it = QStandardItem(icon or theme.get().icon(icon_slot), text)
        it.setData(loc, LOC)
        it.setToolTip(loc if loc.startswith("/") else text)
        if lazy:
            it.setData(lazy, LAZY)
            it.appendRow(QStandardItem("…"))   # placeholder until expanded
        return it

    def label(self, key):
        return self.labels.get(key) or LABEL_DEFAULTS[key]

    def _folder_items(self):
        out = []
        for name, path, icon in places.user_dirs():
            slot = {"user-desktop": "desktop", "folder-download": "downloads",
                    "folder-documents": "documents", "folder-pictures": "pictures",
                    "folder-music": "music", "folder-videos": "videos"}.get(icon, "folder")
            out.append(self._item(name, slot, path, lazy=path))
        return out

    def _drive_items(self):
        out = []
        home_dir = os.path.expanduser("~")
        out.append(self._item(self.label("homefolder"), "home", home_dir, lazy=home_dir))
        for v in places.mounted_volumes():
            out.append(self._item(v.label, "drive-removable" if v.removable else "drive",
                                  v.path, lazy=v.path))
        for v in places.unmounted_devices():
            out.append(self._item(v.label, "drive-removable", "device:" + v.device))
        return out

    def _network_items(self, mapped_style):
        out = []
        for d in self.drives:
            up = d.state in ("connected", "ready")
            server = d.spec.get("server", "")
            label = ("%s (\\\\%s)" % (d.name, server) if d.kind == "smb" else
                     "%s (%s:)" % (d.name, server)) if mapped_style else d.name
            out.append(self._item(label, "drive-network" if up else "drive-network-offline",
                                  "net:sys:" + d.id, lazy=d.mount_point if up else None))
        for loc in self.locations:
            up = loc.state == "connected"
            label = "%s (%s)" % (loc.name, loc.spec.get("host") or loc.kind) if mapped_style else loc.name
            out.append(self._item(label, "drive-network" if up else "drive-network-offline",
                                  "net:user:" + loc.id, lazy=loc.mount_point if up else None))
        return out

    def rebuild(self):
        expanded = self._expanded_locations()
        self.model_.clear()
        root = self.model_.invisibleRootItem()
        win10 = self.style == "win10"
        top = self._item(self.label("quick" if win10 else "home"), "quick-access" if win10 else "home",
                         "catamount:home")
        for path in self.pinned:
            if os.path.isdir(path):
                it = self._item(os.path.basename(path.rstrip("/")) or path, "folder", path,
                                lazy=None, icon=self._pin_icon(path))
                it.setData(True, PINNED)
                top.appendRow(it)
        root.appendRow(top)
        if not win10:
            for it in self._folder_items():
                root.appendRow(it)
        pc = self._item(self.label("thispc"), "this-pc", "catamount:thispc")
        if win10:
            for it in self._folder_items():
                pc.appendRow(it)
        for it in self._drive_items():
            pc.appendRow(it)
        if win10:
            for it in self._network_items(True):
                pc.appendRow(it)
        root.appendRow(pc)
        net = self._item(self.label("network"), "network", "catamount:network")
        if not win10:
            for it in self._network_items(False):
                net.appendRow(it)
        net.appendRow(self._item("Add network location…", "add-location", "net:add"))
        root.appendRow(net)
        root.appendRow(self._item(self.label("trash"), "trash",
                                  TRASH if os.path.isdir(TRASH) else "trash:"))
        self.expand(self.model_.indexFromItem(top))
        self.expand(self.model_.indexFromItem(pc))
        if not win10:
            self.expand(self.model_.indexFromItem(net))
        self._restore_expanded(expanded)

    def _pin_icon(self, path):
        for _n, p, icon in places.user_dirs():
            if p == path:
                return theme.get().icon({"user-desktop": "desktop", "folder-download": "downloads",
                                         "folder-documents": "documents", "folder-pictures": "pictures",
                                         "folder-music": "music", "folder-videos": "videos"}.get(icon, "folder"))
        return theme.get().icon("folder")

    def _expanded_locations(self):
        out = set()

        def walk(parent):
            for r in range(self.model_.rowCount(parent)):
                idx = self.model_.index(r, 0, parent)
                if self.isExpanded(idx):
                    out.add(idx.data(LOC))
                    walk(idx)
        walk(self.model_.index(-1, -1))
        return out

    def _restore_expanded(self, locs):
        def walk(parent):
            for r in range(self.model_.rowCount(parent)):
                idx = self.model_.index(r, 0, parent)
                if idx.data(LOC) in locs and idx.data(LAZY):
                    self.expand(idx)
                    walk(idx)
        walk(self.model_.index(-1, -1))

    def _expand(self, index):
        item = self.model_.itemFromIndex(index)
        path = item.data(LAZY)
        if not path or (item.rowCount() == 1 and item.child(0).data(LOC)) or \
                (item.rowCount() and item.child(0).text() != "…"):
            return
        item.removeRows(0, item.rowCount())
        try:
            subs = sorted((e for e in os.scandir(path)
                           if e.is_dir() and not e.name.startswith(".")),
                          key=lambda e: e.name.lower())
        except OSError:
            subs = []
        for e in subs[:500]:
            child = self._item(e.name, "folder", e.path, icon=theme.get().icon("folder"))
            try:
                has_sub = any(x.is_dir() for x in os.scandir(e.path)) if len(subs) < 200 else True
            except OSError:
                has_sub = False
            if has_sub:
                child.setData(e.path, LAZY)
                child.appendRow(QStandardItem("…"))
            item.appendRow(child)

    def _clicked(self, index):
        loc = index.data(LOC)
        if loc:
            self.navigate.emit(loc)

    def _ctx(self, index, pos):
        if index.isValid() and index.data(LOC):
            self.context.emit(index.data(LOC), pos)

    def reveal(self, loc):
        """Expand the tree down to loc and select it (Explorer's "expand to open folder").
        Only walks folders under a root item that contains loc."""
        if not loc.startswith("/"):
            return False
        best = None

        def walk(parent):
            nonlocal best
            for r in range(self.model_.rowCount(parent)):
                idx = self.model_.index(r, 0, parent)
                lazy = idx.data(LAZY) or (idx.data(LOC) if (idx.data(LOC) or "").startswith("/") else None)
                if lazy and (loc == lazy or loc.startswith(lazy.rstrip("/") + "/")):
                    rank = (len(lazy), bool(idx.data(LAZY)))   # prefer expandable entries
                    if best is None or rank > best[2]:
                        best = (idx, lazy, rank)
                if idx.data(LOC) in ("catamount:home", "catamount:thispc", "catamount:network"):
                    walk(idx)
        walk(self.model_.index(-1, -1))
        if best is None:
            return False
        idx, base, _rank = best
        rel = loc[len(base):].strip("/")
        for part in [p for p in rel.split("/") if p]:
            self.expand(idx)
            item = self.model_.itemFromIndex(idx)
            nxt = None
            for r in range(item.rowCount()):
                if item.child(r).text() == part:
                    nxt = item.child(r).index()
                    break
            if nxt is None:
                break
            idx = nxt
        self.setCurrentIndex(idx)
        self.scrollTo(idx)
        return True

    def select_location(self, loc):
        """Highlight the item for loc (best effort, top two levels)."""
        def walk(parent, depth):
            for r in range(self.model_.rowCount(parent)):
                idx = self.model_.index(r, 0, parent)
                if idx.data(LOC) == loc:
                    return idx
                if depth < 2 and self.isExpanded(idx):
                    found = walk(idx, depth + 1)
                    if found is not None:
                        return found
            return None
        idx = walk(self.model_.index(-1, -1), 0)
        self.selectionModel().clearSelection()
        if idx is not None:
            self.setCurrentIndex(idx)

    # -- drops onto folders in the pane -------------------------------------------------

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        loc = idx.data(LOC) if idx.isValid() else None
        if loc and loc.startswith("/"):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        loc = idx.data(LOC) if idx.isValid() else None
        if not (loc and loc.startswith("/") and self.on_drop):
            return
        sources = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        from catamount.explorer.fileops import same_device
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            kind = "copy"
        elif mods & Qt.ShiftModifier:
            kind = "move"
        else:
            kind = "move" if all(same_device(s, loc) for s in sources) else "copy"
        self.on_drop(kind, sources, loc)
        e.acceptProposedAction()
