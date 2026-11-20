# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Explorer widgets: breadcrumb address bar, tile pages (Home / This PC / Network),
and the preview / details pane."""

import os
import time

from PySide6.QtCore import QFileInfo, QMimeDatabase, QSize, Qt, Signal
from PySide6.QtGui import QFont, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar,
    QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from caracal.explorer import theme
from caracal.model import format_size

PSEUDO = {"caracal:home": ("Home", "home"), "caracal:thispc": ("This PC", "this-pc"),
          "caracal:network": ("Network", "network"), "caracal:search": ("Search", "search")}


LABEL_OVERRIDES = {}   # pseudo location -> user's name (e.g. "This PC" for home)


def location_label(loc):
    if loc in LABEL_OVERRIDES and LABEL_OVERRIDES[loc]:
        return LABEL_OVERRIDES[loc]
    if loc in PSEUDO:
        return PSEUDO[loc][0]
    if loc == "/":
        return "Local Disk (/)"
    if loc == os.path.expanduser("~"):
        return "Home"
    return os.path.basename(loc.rstrip("/")) or loc


# --------------------------------------------------------------------------
# breadcrumb address bar
# --------------------------------------------------------------------------

class AddressBar(QFrame):
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AddressBar")
        self.location = ""
        self.stack = QStackedWidget(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack, 1)
        self._lay = lay
        self.crumbs = QWidget()
        self.crumb_lay = QHBoxLayout(self.crumbs)
        self.crumb_lay.setContentsMargins(0, 0, 0, 0)
        self.crumb_lay.setSpacing(0)
        self.edit = QLineEdit()
        self.edit.setFrame(False)
        # Folder autocomplete while typing a path.
        from PySide6.QtCore import QDir
        from PySide6.QtWidgets import QCompleter, QFileSystemModel
        self._fs = QFileSystemModel(self)
        self._fs.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self._fs.setRootPath("/")
        comp = QCompleter(self._fs, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.edit.setCompleter(comp)
        self.edit.returnPressed.connect(self._typed)
        self.edit.installEventFilter(self)
        self.stack.addWidget(self.crumbs)
        self.stack.addWidget(self.edit)
        self.setCursor(Qt.IBeamCursor)

    def add_trailing(self, widget):
        """Buttons inside the box at the right end (history dropdown, refresh)."""
        self._lay.addWidget(widget)

    def set_location(self, loc):
        self.location = loc
        while self.crumb_lay.count():
            w = self.crumb_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        icon_btn = QToolButton()
        icon_btn.setAutoRaise(True)
        icon_btn.setIcon(self._icon_for(loc))
        icon_btn.setIconSize(QSize(16, 16))
        icon_btn.clicked.connect(self.start_edit)
        self.crumb_lay.addWidget(icon_btn)
        for label, target in self._segments(loc):
            sep = QToolButton()
            sep.setAutoRaise(True)
            sep.setText("›")
            sep.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(sep)
            menu.aboutToShow.connect(lambda m=menu, t=target: self._fill_children(m, t))
            sep.setMenu(menu)
            sep.setStyleSheet("QToolButton::menu-indicator { image: none; }")
            self.crumb_lay.addWidget(sep)
            b = QToolButton()
            b.setAutoRaise(True)
            b.setText(label)
            b.clicked.connect(lambda _c=False, t=target: self.navigate.emit(t))
            self.crumb_lay.addWidget(b)
        self.crumb_lay.addStretch(1)
        self.stack.setCurrentIndex(0)

    def _icon_for(self, loc):
        if loc in PSEUDO:
            return theme.get().icon(PSEUDO[loc][1])
        home = os.path.expanduser("~")
        if loc == home:
            return theme.get().icon("home")
        if loc.startswith(home + "/"):
            first = os.path.join(home, loc[len(home) + 1:].split("/", 1)[0])
            from caracal.explorer import places
            slots = {"user-desktop": "desktop", "folder-download": "downloads",
                     "folder-documents": "documents", "folder-pictures": "pictures",
                     "folder-music": "music", "folder-videos": "videos"}
            for _n, p, icon in places.user_dirs():
                if p == first:
                    return theme.get().icon(slots.get(icon, "folder"))
        return theme.get().icon("folder")

    def _segments(self, loc):
        if loc in PSEUDO:
            return [(location_label(loc), loc)]
        home = os.path.expanduser("~")
        if loc == home:
            return [(location_label(home), home)]
        if loc.startswith(home + "/"):
            # Like Explorer: a folder in your home starts the trail ("Downloads › …").
            first = loc[len(home) + 1:].split("/", 1)[0]
            base = os.path.join(home, first)
            segs = [(first, base)]
            rel = loc[len(base):].strip("/")
        else:
            segs = [("This PC", "caracal:thispc"), ("Local Disk (/)", "/")]
            rel = loc.strip("/")
            base = ""
        for part in [p for p in rel.split("/") if p]:
            base = base + "/" + part
            segs.append((part, base))
        if len(segs) > 7:   # keep it readable: collapse the middle
            segs = segs[:1] + [("…", segs[-6][1])] + segs[-5:]
        return segs

    def _fill_children(self, menu, target):
        menu.clear()
        if target in PSEUDO or not os.path.isdir(target):
            return
        try:
            subs = sorted((e for e in os.scandir(target) if e.is_dir() and not e.name.startswith(".")),
                          key=lambda e: e.name.lower())
        except OSError:
            return
        for e in subs[:60]:
            menu.addAction(theme.get().icon("folder"), e.name,
                           lambda p=e.path: self.navigate.emit(p))

    def mousePressEvent(self, event):
        self.start_edit()

    def start_edit(self):
        self.edit.setText(self.location if self.location not in PSEUDO else location_label(self.location))
        self.stack.setCurrentIndex(1)
        self.edit.setFocus()
        self.edit.selectAll()

    def _typed(self):
        text = self.edit.text().strip()
        lowered = text.lower()
        aliases = {"this pc": "caracal:thispc", "home": "caracal:home",
                   "network": "caracal:network", "computer": "caracal:thispc"}
        if lowered in aliases:
            self.navigate.emit(aliases[lowered])
        elif text.startswith("~"):
            self.navigate.emit(os.path.expanduser(text))
        elif text.startswith("\\\\") or text.startswith("smb://") or text.startswith("sftp://") \
                or text.startswith("ftp://"):
            self.navigate.emit("connect:" + text)
        elif text:
            self.navigate.emit(text)
        self.stack.setCurrentIndex(0)

    def eventFilter(self, obj, event):
        if obj is self.edit:
            if event.type() == event.Type.FocusOut or (
                    event.type() == event.Type.KeyPress and event.key() == Qt.Key_Escape):
                self.stack.setCurrentIndex(0)
        return super().eventFilter(obj, event)


# --------------------------------------------------------------------------
# tile pages: Home, This PC, Network
# --------------------------------------------------------------------------

class Tile(QFrame):
    """Clickable tile: icon, title, optional usage bar and subtitle."""

    activated = Signal(object)
    context = Signal(object, object)

    def __init__(self, key, icon, title, subtitle="", used=None, total=None, wide=True):
        super().__init__()
        self.key = key
        self.setObjectName("Tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(250 if wide else 130)
        self.setMaximumWidth(330 if wide else 180)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        ic = QLabel()
        ic.setPixmap(icon.pixmap(QSize(40, 40)))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setWordWrap(False)
        col.addWidget(t)
        if total:
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(1000 * (used or 0) / total))
            bar.setTextVisible(False)
            bar.setFixedHeight(12)
            if used / total > 0.9:
                bar.setStyleSheet("QProgressBar::chunk { background: #C42B1C; }")
            else:
                bar.setStyleSheet("QProgressBar::chunk { background: %s; }"
                                  % theme.get().data.get("accent", "#0067C0"))
            col.addWidget(bar)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet("color: %s;" % theme.get().colors()["subtle"])
            col.addWidget(s)
        lay.addLayout(col, 1)

    def enterEvent(self, e):
        self.setStyleSheet("#Tile { background: %s; border-radius: 4px; }"
                           % theme.get().colors()["hover"])

    def leaveEvent(self, e):
        self.setStyleSheet("")

    def mouseDoubleClickEvent(self, e):
        self.activated.emit(self.key)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self.context.emit(self.key, e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton and getattr(self, "single_click", False):
            self.activated.emit(self.key)


class TilePage(QScrollArea):
    activated = Signal(object)
    context = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ThisPC")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.body = None

    def set_sections(self, sections, single_click=False):
        """sections: [(heading, [Tile...])]"""
        body = QWidget()
        body.setObjectName("ThisPC")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 10, 16, 10)
        for heading, tiles in sections:
            if not tiles:
                continue
            h = QLabel(heading)
            f = QFont(h.font())
            f.setBold(True)
            h.setFont(f)
            lay.addWidget(h)
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            cols = max(1, (self.viewport().width() - 40) // 270)
            for i, tile in enumerate(tiles):
                tile.single_click = single_click
                tile.activated.connect(self.activated)
                tile.context.connect(self.context)
                grid.addWidget(tile, i // cols, i % cols)
            grid.setColumnStretch(cols, 1)
            lay.addLayout(grid)
            lay.addSpacing(10)
        lay.addStretch(1)
        self.setWidget(body)
        self.body = body


# --------------------------------------------------------------------------
# preview / details pane
# --------------------------------------------------------------------------

TEXT_EXTS = {"txt", "md", "log", "csv", "json", "xml", "yaml", "yml", "ini", "conf", "cfg",
             "py", "sh", "js", "ts", "c", "h", "cpp", "java", "go", "rs", "html", "css", "sql",
             "toml", "desktop", "service"}


class PreviewPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PreviewPane")
        self.setMinimumWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image.setMinimumHeight(160)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.hide()
        self.title = QLabel()
        self.title.setWordWrap(True)
        f = QFont(self.title.font())
        f.setPointSizeF(f.pointSizeF() * 1.2)
        f.setBold(True)
        self.title.setFont(f)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.image, 3)
        lay.addWidget(self.text, 3)
        lay.addWidget(self.title)
        lay.addWidget(self.details, 1)
        self.mime = QMimeDatabase()
        self.show_paths([])

    def show_paths(self, paths):
        self.text.hide()
        self.image.show()
        if not paths:
            # Explorer shows only a centered hint when nothing is selected.
            self.image.clear()
            self.title.setText("")
            self.details.setText("")
            self.image.setText("Select a file to preview.")
            self.title.hide()
            self.details.hide()
            return
        self.image.setText("")
        self.title.show()
        self.details.show()
        if len(paths) > 1:
            self.image.setPixmap(theme.get().icon("file").pixmap(96, 96))
            self.title.setText("%d items selected" % len(paths))
            total = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
            self.details.setText("Total size of files: %s" % format_size(total))
            return
        path = paths[0]
        info = QFileInfo(path)
        mt = self.mime.mimeTypeForFile(path, QMimeDatabase.MatchExtension)
        self.title.setText(info.fileName())
        rows = [("Type", "File folder" if info.isDir() else mt.comment() or mt.name())]
        if info.isFile():
            rows.append(("Size", format_size(info.size())))
        rows.append(("Date modified", info.lastModified().toString("yyyy-MM-dd HH:mm")))
        rows.append(("Location", os.path.dirname(path)))
        self.details.setText("<br>".join("<span style='color:%s'>%s</span><br>%s"
                                         % (theme.get().colors()["subtle"], k, v.replace("&", "&amp;").replace("<", "&lt;"))
                                         for k, v in rows))
        ext = info.suffix().lower()
        if info.isFile() and info.size() < 64 * 1024 * 1024 and \
                QImageReader.imageFormat(path):
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            edge = max(160, min(self.width() - 30, 600))
            sz = reader.size()
            if sz.isValid():
                reader.setScaledSize(sz.scaled(QSize(edge, edge), Qt.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                self.image.setPixmap(QPixmap.fromImage(img))
                return
        if info.isFile() and (ext in TEXT_EXTS or mt.inherits("text/plain")) and info.size() < 4 * 1024 * 1024:
            try:
                with open(path, "rb") as fh:
                    raw = fh.read(64 * 1024)
                if b"\x00" not in raw[:2048]:
                    self.image.hide()
                    self.text.setPlainText(raw.decode("utf-8", "replace"))
                    self.text.show()
                    return
            except OSError:
                pass
        icon = theme.get().icon("folder") if info.isDir() else theme.get().file_icon(info)
        self.image.setPixmap(icon.pixmap(96, 96))


def ago(ts):
    mins = int((time.time() - ts) / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return "%d min ago" % mins
    if mins < 1440:
        return "%d h ago" % (mins // 60)
    return time.strftime("%Y-%m-%d", time.localtime(ts))
