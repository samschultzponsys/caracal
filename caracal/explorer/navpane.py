# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Left navigation pane: Home/Quick access, This PC, Network, Trash."""

import os

from PySide6.QtCore import QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QStyledItemDelegate, QTreeView

from caracal import fsinfo
from caracal.background import run_async
from caracal.explorer import places, theme

LOC = Qt.UserRole
LAZY = Qt.UserRole + 1          # folder path whose subfolders load on expand
PINNED = Qt.UserRole + 2        # draw a pin at the right edge (Quick access)
SPACER = Qt.UserRole + 3        # blank gap between sections
LOADING = Qt.UserRole + 4       # placeholder row while subfolders load
STATUS = Qt.UserRole + 5        # network drive dot: "up", "busy", "down"

_WATCH = {"t": 0.0, "data": {}}


def watchdog_state():
    """What the root watchdog last found for each system drive (cached a few seconds)."""
    import json
    import time
    if time.monotonic() - _WATCH["t"] > 5:
        try:
            with open("/var/lib/caracal/watchdog.json", encoding="utf-8") as fh:
                data = json.load(fh)
            _WATCH["data"] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _WATCH["data"] = {}
        _WATCH["t"] = time.monotonic()
    return _WATCH["data"]


LABEL_DEFAULTS = {"quick": "Quick access", "home": "Home", "thispc": "This PC",
                  "network": "Network", "trash": "Trash", "homefolder": "Home folder"}


class PinDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(SPACER):
            return
        super().paint(painter, option, index)
        dot = index.data(STATUS)
        if dot:
            from PySide6.QtGui import QColor, QPen
            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            r = option.rect
            icon = option.decorationSize
            # bottom-right corner of the icon, like an overlay badge
            x = r.left() + 4 + icon.width() - 5
            y = r.top() + (r.height() + icon.height()) // 2 - 6
            color = {"up": "#2fb344", "busy": "#e8a317", "down": "#d93025"}[dot]
            painter.setPen(QPen(theme.color("content") if "content" in theme.get().colors()
                                else QColor("white"), 1.5))
            painter.setBrush(QColor(color))
            painter.drawEllipse(x, y, 7, 7)
            painter.restore()
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
        if index.data(SPACER):
            return QSize(10, 10)
        sz = super().sizeHint(option, index)
        # Explorer's navigation pane is a little roomier than the file list.
        return QSize(sz.width(), max(sz.height(), int(theme.get().data.get("row_height", 20)) + 3))
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
        self.setIconSize(QSize(16, 16))
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
        self.drives = []        # Caracal system drives (model.Drive)
        self.locations = []     # user_mounts.Location
        self.on_drop = None
        self._expanded_keys = set()
        self.hidden = set()     # locations the user hid from the pane
        self.setUniformRowHeights(False)
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
            it = self._item(label, "drive-network" if up else "drive-network-offline",
                            "net:sys:" + d.id, lazy=d.mount_point if up else None)
            self._set_status(it, d.state, d.mount_point, d.id)
            fast = getattr(self, "faster", {}).get(d.id)
            if fast:
                it.setToolTip(it.toolTip() + "\nFaster connection available: this folder is also "
                              "shared over NFS (%s). Right-click to map it." % fast)
            out.append(it)
        for loc in self.locations:
            up = loc.state == "connected"
            label = "%s (%s)" % (loc.name, loc.spec.get("host") or loc.kind) if mapped_style else loc.name
            it = self._item(label, "drive-network" if up else "drive-network-offline",
                            "net:user:" + loc.id, lazy=loc.mount_point if up else None)
            self._set_status(it, loc.state, loc.mount_point)
            out.append(it)
        return out

    def _set_status(self, it, state, mount_point, drive_id=None):
        """Green dot when mounted, amber while connecting, red when not mounted (an
        automount that hasn't been opened yet counts as not mounted)."""
        mounted = fsinfo.is_mounted(mount_point) if mount_point else False
        if state == "connecting":
            dot = "busy"
        elif mounted and not fsinfo.is_unresponsive(mount_point):
            dot = "up"
        else:
            dot = "down"
        it.setData(dot, STATUS)
        from caracal.model import STATE_LABELS
        text = {"up": "Connected", "busy": "Connecting…"}.get(dot) or \
            ("Not responding" if mounted else STATE_LABELS.get(state, "Not connected"))
        if state == "ready" and not mounted:
            text = "Not connected (connects when you open it)"
        watch = watchdog_state().get(drive_id or "", {})
        if dot != "up" and watch.get("state") in ("retrying", "unreachable"):
            import time
            nxt = watch.get("next_try") or 0
            mins = max(0, int(round((nxt - time.time()) / 60.0))) if nxt else None
            text = "Not connected — %s%s" % (
                watch.get("error") or "the last attempt failed",
                "" if mins is None else
                (", retrying in about %d min" % mins if mins else ", retrying now"))
        it.setToolTip("%s\n%s\n%s" % (it.text(), mount_point or "", text))

    def _spacer(self):
        it = QStandardItem("")
        it.setData(True, SPACER)
        it.setFlags(Qt.NoItemFlags)
        return it

    def entries(self):
        """Everything that can be shown or hidden: [(label, loc)] in pane order."""
        win10 = self.style == "win10"
        out = [(self.label("quick" if win10 else "home"), "caracal:home"),
               (self.label("thispc"), "caracal:thispc")]
        for name, path, _icon in places.user_dirs():
            out.append(("    " + name, path))
        out.append(("    " + self.label("homefolder"), os.path.expanduser("~")))
        for v in places.mounted_volumes():
            out.append(("    " + v.label, v.path))
        for d in self.drives:
            out.append(("    " + d.name, "net:sys:" + d.id))
        for l_ in self.locations:
            out.append(("    " + l_.name, "net:user:" + l_.id))
        out.append((self.label("network"), "caracal:network"))
        out.append((self.label("trash"), "trash"))
        return out

    def _add(self, parent, item):
        """Append unless the user hid it."""
        loc = item.data(LOC) or ""
        key = "trash" if loc in (TRASH, "trash:") else loc
        if key not in self.hidden:
            parent.appendRow(item)
            return True
        return False

    def rebuild(self):
        expanded = self._expanded_locations()
        self.model_.clear()
        root = self.model_.invisibleRootItem()
        win10 = self.style == "win10"
        sections = []
        top = self._item(self.label("quick" if win10 else "home"), "quick-access" if win10 else "home",
                         "caracal:home")
        for path in self.pinned:
            if fsinfo.isdir_or_assume(path):
                it = self._item(os.path.basename(path.rstrip("/")) or path, "folder", path,
                                lazy=None, icon=self._pin_icon(path))
                it.setData(True, PINNED)
                top.appendRow(it)
        if "caracal:home" not in self.hidden:
            sections.append([top])
        if not win10:
            sections.append([it for it in self._folder_items() if it.data(LOC) not in self.hidden])
        pc = self._item(self.label("thispc"), "this-pc", "caracal:thispc")
        if win10:
            for it in self._folder_items():
                self._add(pc, it)
        for it in self._drive_items():
            self._add(pc, it)
        if win10:
            for it in self._network_items(True):
                self._add(pc, it)
        if "caracal:thispc" not in self.hidden:
            sections.append([pc])
        net = self._item(self.label("network"), "network", "caracal:network")
        if not win10:
            for it in self._network_items(False):
                self._add(net, it)
        net.appendRow(self._item("Add network location…", "add-location", "net:add"))
        tail = []
        if "caracal:network" not in self.hidden:
            tail.append(net)
        trash = self._item(self.label("trash"), "trash", TRASH if os.path.isdir(TRASH) else "trash:")
        if "trash" not in self.hidden:
            tail.append(trash)
        if tail:
            sections.append(tail)
        first = True
        for group in sections:
            if not group:
                continue
            if not first:
                root.appendRow(self._spacer())
            first = False
            for it in group:
                root.appendRow(it)
        for item, want in ((top, True), (pc, True), (net, not win10)):
            idx = self.model_.indexFromItem(item)
            if idx.isValid() and want:
                self.expand(idx)
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
        self._restore_locs = set(locs)      # deeper levels re-open as their parents load
        def walk(parent):
            for r in range(self.model_.rowCount(parent)):
                idx = self.model_.index(r, 0, parent)
                if idx.data(LOC) in locs and idx.data(LAZY):
                    self.expand(idx)
                    walk(idx)
        walk(self.model_.index(-1, -1))

    def _expand(self, index):
        """Load a folder's subfolders in the background: on a slow or dropped network share
        this used to freeze the whole window."""
        item = self.model_.itemFromIndex(index)
        path = item.data(LAZY)
        if path and item.rowCount() == 1 and item.child(0).text() == "Not responding":
            item.child(0).setText("…")          # try again
        if not path or item.rowCount() != 1 or item.child(0).text() != "…" or \
                item.child(0).data(LOADING):
            return
        item.child(0).setData(True, LOADING)
        item.child(0).setText("Loading…")
        target = QPersistentModelIndex(index)
        remote = fsinfo.is_remote(path)

        def work():
            names = fsinfo.listdirs(path, timeout=8.0)
            if names is None:
                return None
            out = []
            for name in names:
                sub = os.path.join(path, name)
                # Looking inside every subfolder costs a round trip each on a network
                # share, so there we just offer the arrow and find out on expand.
                if remote or len(names) >= 200 or fsinfo.is_remote(sub):
                    has_sub = True
                else:
                    try:
                        with os.scandir(sub) as it:
                            has_sub = any(not x.name.startswith(".") and x.is_dir() for x in it)
                    except OSError:
                        has_sub = False
                out.append((name, sub, has_sub))
            return out

        run_async(self, work, lambda res, _err: self._expand_done(target, path, res))

    def _expand_done(self, target, path, subs):
        if not target.isValid():
            return                      # the pane was rebuilt meanwhile
        item = self.model_.itemFromIndex(self.model_.index(target.row(), target.column(),
                                                           target.parent()))
        if item is None or item.data(LAZY) != path:
            return
        item.removeRows(0, item.rowCount())
        if subs is None:
            note = QStandardItem("Not responding")
            note.setFlags(Qt.NoItemFlags)
            note.setToolTip("The folder didn't answer in time. Collapse and expand to retry.")
            item.appendRow(note)
            return
        for name, sub, has_sub in subs:
            child = self._item(name, "folder", sub, icon=theme.get().icon("folder"))
            if has_sub:
                child.setData(sub, LAZY)
                child.appendRow(QStandardItem("…"))
            item.appendRow(child)
            if has_sub and sub in getattr(self, "_restore_locs", ()):
                self.expand(self.model_.indexFromItem(child))
        if not subs and self.isExpanded(self.model_.indexFromItem(item)):
            self.collapse(self.model_.indexFromItem(item))

    def _clicked(self, index):
        loc = index.data(LOC)
        if loc:
            self.navigate.emit(loc)

    def _ctx(self, index, pos):
        # Right-click works everywhere, including empty space (loc "").
        loc = index.data(LOC) if index.isValid() else ""
        self.context.emit(loc or "", pos)

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
                if idx.data(LOC) in ("caracal:home", "caracal:thispc", "caracal:network"):
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
        from caracal.explorer.fileops import same_device
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            kind = "copy"
        elif mods & Qt.ShiftModifier:
            kind = "move"
        else:
            kind = "move" if all(same_device(s, loc) for s in sources) else "copy"
        self.on_drop(kind, sources, loc)
        e.acceptProposedAction()
