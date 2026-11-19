# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""The Explorer window (Windows 11 Explorer-style layout)."""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from PySide6.QtCore import (QItemSelectionModel, QModelIndex, QMimeData, QMimeDatabase, QObject, QSize,
                            Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListView, QMainWindow, QMenu, QMessageBox, QSplitter, QStackedWidget, QTabBar,
    QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from caracal import indexdb, user_mounts
from caracal.background import run_async
from caracal.explorer import fileops, openwith, places, theme
from caracal.explorer.dialogs import (LocationDialog, OpenWithDialog, OperationDialog,
                                        PropertiesDialog)
from caracal.explorer.columns import CATEGORIES, COLUMNS, COMMON, DATE, NAME, index_of
from caracal.explorer.headers import ChooseColumnsDialog, FilterHeader, FilterPopup
from caracal.explorer.model import DirModel
from caracal.explorer.ribbon import GroupDelegate, Ribbon
from caracal.explorer.navpane import TRASH, NavPane
from caracal.explorer.widgets import (PSEUDO, AddressBar, PreviewPane, Tile, TilePage, ago,
                                        location_label)
from caracal.model import STATE_LABELS, format_size, format_usage

VIEW_MODES = {"xl": ("Extra large icons", 256), "large": ("Large icons", 96),
              "medium": ("Medium icons", 48), "small": ("Small icons", 24),
              "list": ("List", 16), "details": ("Details", 16)}
TERMINALS = (("konsole", "--workdir"), ("gnome-terminal", "--working-directory="),
             ("kgx", "--working-directory="), ("ptyxis", "--working-directory="),
             ("xfce4-terminal", "--working-directory="), ("mate-terminal", "--working-directory="),
             ("tilix", "--working-directory="), ("alacritty", "--working-directory"),
             ("kitty", "--directory"), ("foot", "--working-directory="), ("xterm", None))


def explorer_setting(settings, key, default):
    v = settings.value("explorer/" + key, default)
    if isinstance(default, bool):
        return v if isinstance(v, bool) else str(v).lower() in ("true", "1")
    if isinstance(default, int):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    return v


class _SearchRelay(QObject):
    batch = Signal(int, list)
    finished = Signal(int, bool)


class ExplorerWindow(QMainWindow):
    def __init__(self, ctl, location=None):
        super().__init__()
        self.ctl = ctl
        self.settings = ctl.settings
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName("ExplorerRoot")
        self.setWindowIcon(theme.get().icon("folder"))
        self.tabs_state = []
        self.undo_stack = []
        self.search_gen = 0
        self.searching = False
        self._net_sig = None
        self.resize(1180, 720)
        geo = self.settings.value("explorer/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._build()
        self.apply_appearance()
        start = location or ("caracal:thispc" if explorer_setting(self.settings, "start", "home")
                             == "thispc" else "caracal:home")
        self.new_tab(start)
        ctl.drive_listeners.append(self._drives_changed)
        self._drives_changed()

    # ======================================================================
    # construction
    # ======================================================================

    def _tbtn(self, slot, tip, slot_fn=None, text=None, menu=None):
        b = QToolButton()
        b.setIcon(theme.get().icon(slot))
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        if text:
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        if slot_fn:
            b.clicked.connect(slot_fn)
        if menu is not None:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        b._slot = slot
        return b

    def _build(self):
        root = QWidget()
        root.setObjectName("ExplorerRoot")
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Tabs
        tabrow = QWidget()
        tabrow.setObjectName("TabRow")
        th = QHBoxLayout(tabrow)
        th.setContentsMargins(6, 4, 6, 0)
        self.tabbar = QTabBar()
        self.tabbar.setObjectName("Tabs")
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDocumentMode(True)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.tabMoved.connect(self._tab_moved)
        th.addWidget(self.tabbar)
        th.addWidget(self._tbtn("new-tab", "New tab (Ctrl+T)", lambda: self.new_tab(None)))
        th.addStretch(1)
        v.addWidget(tabrow)
        self.tabrow = tabrow
        self.layout_style = explorer_setting(self.settings, "layout", "win10")
        self.ribbon = Ribbon(self)
        v.addWidget(self.ribbon)

        # Address row
        arow = QWidget()
        arow.setObjectName("AddressRow")
        ah = QHBoxLayout(arow)
        ah.setContentsMargins(6, 4, 6, 4)
        self.back_btn = self._tbtn("back", "Back (Alt+Left)", self.back)
        self.fwd_btn = self._tbtn("forward", "Forward (Alt+Right)", self.forward)
        self.up_btn = self._tbtn("up", "Up to parent folder (Alt+Up)", self.up)
        self.refresh_btn = self._tbtn("refresh", "Refresh (F5)", self.refresh)
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.recent_btn = QToolButton()
        self.recent_btn.setText("⌄")
        self.recent_btn.setAutoRaise(True)
        self.recent_btn.setToolTip("Recent locations")
        self.recent_btn.setPopupMode(QToolButton.InstantPopup)
        self.recent_btn.setMenu(self.recent_menu)
        self.recent_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        for b in (self.back_btn, self.fwd_btn, self.recent_btn, self.up_btn):
            ah.addWidget(b)
        self.address = AddressBar()
        self.address.navigate.connect(self.go)
        ah.addWidget(self.address, 1)
        # Inside the address box, at the right: history dropdown and refresh (Explorer).
        self.addr_drop = QToolButton()
        self.addr_drop.setText("⌄")
        self.addr_drop.setAutoRaise(True)
        self.addr_drop.setToolTip("Previous locations")
        self.addr_drop.setPopupMode(QToolButton.InstantPopup)
        self.addr_drop.setMenu(self.recent_menu)
        self.addr_drop.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.address.add_trailing(self.addr_drop)
        self.refresh_btn.setIconSize(QSize(14, 14))
        self.address.add_trailing(self.refresh_btn)
        ah.setSpacing(2)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self.search.setMaximumWidth(320)
        self.search.addAction(theme.get().icon("search"), QLineEdit.TrailingPosition)
        self.search.textChanged.connect(lambda _t: self.search_timer.start(250))
        self.search.returnPressed.connect(self.run_search)
        ah.addWidget(self.search)
        v.addWidget(arow)

        # Command bar
        cbar = QWidget()
        cbar.setObjectName("CommandBar")
        ch = QHBoxLayout(cbar)
        ch.setContentsMargins(6, 2, 6, 4)
        self.new_menu = QMenu(self)
        self.new_menu.aboutToShow.connect(self._fill_new_menu)
        self.cmd_new = self._tbtn("new", "Create a new item", text="New", menu=self.new_menu)
        self.cmd_cut = self._tbtn("cut", "Cut (Ctrl+X)", lambda: self.copy_selection(cut=True))
        self.cmd_copy = self._tbtn("copy", "Copy (Ctrl+C)", lambda: self.copy_selection())
        self.cmd_paste = self._tbtn("paste", "Paste (Ctrl+V)", self.paste)
        self.cmd_rename = self._tbtn("rename", "Rename (F2)", self.rename)
        self.cmd_delete = self._tbtn("delete", "Delete (Del)", lambda: self.delete())
        self.sort_menu = QMenu(self)
        self.sort_menu.aboutToShow.connect(self._fill_sort_menu)
        self.view_menu = QMenu(self)
        self.view_menu.aboutToShow.connect(self._fill_view_menu)
        self.more_menu = QMenu(self)
        self.more_menu.aboutToShow.connect(self._fill_more_menu)
        self.cmd_sort = self._tbtn("sort", "Sort", text="Sort", menu=self.sort_menu)
        self.cmd_view = self._tbtn("view", "Layout and view options", text="View", menu=self.view_menu)
        self.cmd_more = self._tbtn("more", "See more", menu=self.more_menu)
        for w in (self.cmd_new, _vsep(), self.cmd_cut, self.cmd_copy, self.cmd_paste,
                  self.cmd_rename, self.cmd_delete, _vsep(), self.cmd_sort, self.cmd_view,
                  _vsep(), self.cmd_more):
            ch.addWidget(w)
        ch.addStretch(1)
        self.cmd_details_pane = self._tbtn("preview-pane", "Preview pane (Alt+P)",
                                           self.toggle_preview, text="Preview")
        self.cmd_details_pane.setCheckable(True)
        ch.addWidget(self.cmd_details_pane)
        v.addWidget(cbar)
        self.cbar = cbar

        # Body
        self.split = QSplitter(Qt.Horizontal)
        self.nav = NavPane()
        self.nav.navigate.connect(self.go)
        self.nav.context.connect(self._nav_menu)
        self.nav.on_drop = self.run_op
        self.split.addWidget(self.nav)

        self.model = DirModel(self.settings, self)
        self.model.on_drop = self.run_op
        self.model.on_rename = lambda old, new: self.undo_stack.append(
            {"kind": "rename", "old": old, "new": new})
        self.model.directoryLoaded.connect(self._dir_loaded)
        self.model.modelAboutToBeReset.connect(self._save_view_state)
        self.model.modelReset.connect(self._model_reset)
        self._saved_view = None
        self._collapsed = {}          # folder -> set(group labels the user collapsed)

        self.details = _TypeAheadTree(self)
        self.details.setObjectName("FileView")
        self.details.setHeader(FilterHeader(self.details))
        self.details.setModel(self.model)
        self.details.setRootIsDecorated(False)
        self.details.setIndentation(0)
        self.details.setItemsExpandable(True)
        self.details.setExpandsOnDoubleClick(False)
        self.details.setSortingEnabled(True)
        self.details.header().setSortIndicator(NAME, Qt.AscendingOrder)
        self.details.setUniformRowHeights(False)
        self.details.setAllColumnsShowFocus(True)
        self.details.setItemDelegate(GroupDelegate(self.details))
        hdr = self.details.header()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        em = self.fontMetrics().averageCharWidth()
        for i, c in enumerate(COLUMNS):
            hdr.resizeSection(i, max(40 if c.width < 8 else 60, em * c.width))
        hdr.resizeSection(NAME, max(280, em * 50))
        hdr.setStretchLastSection(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.sortIndicatorChanged.connect(self._sort_changed)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._header_menu)
        state = self.settings.value("explorer/columns2")
        if state is None or not hdr.restoreState(state):
            for i in range(4, len(COLUMNS)):     # defaults: Name, Date modified, Type, Size
                hdr.setSectionHidden(i, True)
            old = self.settings.value("explorer/columns")     # before 1.2: 4 columns only
            if old is not None:
                self._restore_old_widths(old)
        hdr.filterRequested.connect(self._filter_popup)
        hdr.sectionResized.connect(lambda *_a: self._columns_changed())
        self.model.modelReset.connect(self._sync_filter_marks)
        self._columns_changed()

        self.icons = _TypeAheadList(self)
        self.icons.setObjectName("FileView")
        self.icons.setModel(self.model)
        self.icons.setSelectionModel(self.details.selectionModel())
        self.icons.setResizeMode(QListView.Adjust)
        self.icons.setMovement(QListView.Static)
        self.icons.setWordWrap(True)
        self.icons.setUniformItemSizes(True)
        self.icons.setSpacing(6)
        self.icons.setViewportMargins(6, 6, 6, 6)

        for view in (self.details, self.icons):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
            view.setSelectionBehavior(QAbstractItemView.SelectRows)
            view.setEditTriggers(QAbstractItemView.EditKeyPressed)
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.setDropIndicatorShown(True)
            view.setDragDropMode(QAbstractItemView.DragDrop)
            view.setDefaultDropAction(Qt.MoveAction)
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, w=view: self._view_menu_at(w, pos))
            view.doubleClicked.connect(self.activate)
            view.clicked.connect(lambda idx: self.activate(idx) if explorer_setting(
                self.settings, "single_click", False) else None)
            view.installEventFilter(self)
        self.details.selectionModel().selectionChanged.connect(self._selection_changed)
        self.details.expanded.connect(lambda i: self._group_toggled(i, True))
        self.details.collapsed.connect(lambda i: self._group_toggled(i, False))
        self.empty_labels = []
        for view in (self.details, self.icons):
            lab = QLabel(view.viewport())
            lab.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            lab.setWordWrap(True)
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            lab.setStyleSheet("color: palette(placeholder-text); padding: 20px;")
            lab.hide()
            self.empty_labels.append(lab)
            view.viewport().installEventFilter(self)

        self.results_model = QStandardItemModel(self)
        self.results_model.setHorizontalHeaderLabels(["Name", "Folder", "Date modified", "Size"])
        self.results = QTreeView()
        self.results.setObjectName("FileView")
        self.results.setModel(self.results_model)
        self.results.setRootIsDecorated(False)
        self.results.setSortingEnabled(True)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.header().resizeSection(0, 320)
        self.results.header().resizeSection(1, 380)
        self.results.doubleClicked.connect(self._open_result)
        self.results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._results_menu)
        self.results.setDragEnabled(True)
        self.results.selectionModel().selectionChanged.connect(self._selection_changed)

        self.results.viewport().installEventFilter(self)
        self.tiles = TilePage()
        self.tiles.activated.connect(self._tile_activated)
        self.tiles.context.connect(self._tile_menu)

        self.tiles.viewport().installEventFilter(self)
        self.content = QStackedWidget()
        for w in (self.details, self.icons, self.results, self.tiles):
            self.content.addWidget(w)
        self.split.addWidget(self.content)
        self.preview = PreviewPane()
        self.split.addWidget(self.preview)
        self.split.setStretchFactor(1, 1)
        nav_w = int(theme.get().data.get("nav_width", 250))
        self.split.setSizes([nav_w, 900, 240])
        sizes = self.settings.value("explorer/split")
        if sizes:
            try:
                self.split.setSizes([int(x) for x in sizes])
            except (TypeError, ValueError):
                pass
        v.addWidget(self.split, 1)
        self.setCentralWidget(root)

        # Status bar
        sb = self.statusBar()
        sb.setObjectName("StatusBar")
        self.version_label = QLabel()
        self.version_label.setTextFormat(Qt.RichText)
        self.version_label.linkActivated.connect(lambda _l: self.ctl.show_changelog())
        sb.addWidget(self.version_label)
        self.status = QLabel()
        sb.addWidget(self.status, 1)
        self.refresh_version()
        self.sb_details = self._tbtn("details-view", "Details (Ctrl+Shift+6)",
                                     lambda: self.set_view("details"))
        self.sb_icons = self._tbtn("icons-view", "Large icons (Ctrl+Shift+2)",
                                   lambda: self.set_view("large"))
        self.sb_preview = self._tbtn("preview-pane", "Preview pane (Alt+P)", self.toggle_preview)
        for b in (self.sb_preview, self.sb_details, self.sb_icons):
            b.setCheckable(True)
            b.setIconSize(QSize(16, 16))
            sb.addPermanentWidget(b)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.run_search)
        self.search_relay = _SearchRelay()
        self.search_relay.batch.connect(self._search_batch)
        self.search_relay.finished.connect(self._search_done)

        self._shortcuts()
        self.nav.setVisible(explorer_setting(self.settings, "nav", True))
        show_prev = explorer_setting(self.settings, "preview", True)
        self.preview.setVisible(show_prev)
        self.cmd_details_pane.setChecked(show_prev)
        self.sb_preview.setChecked(show_prev)
        self.model.hide_ext = not explorer_setting(self.settings, "show_ext", True)
        self._apply_hidden()
        self.set_view(explorer_setting(self.settings, "view", "details"), save=False)
        self.apply_layout()

    def _shortcuts(self):
        keys = [
            ("Alt+Left", self.back), ("Backspace", self.back), ("Alt+Right", self.forward),
            ("Alt+Up", self.up), ("F5", self.refresh), ("Ctrl+R", self.refresh),
            ("Ctrl+L", self.address.start_edit), ("Alt+D", self.address.start_edit),
            ("F4", self.address.start_edit), ("Ctrl+F", self._focus_search),
            ("Ctrl+E", self._focus_search), ("F3", self._focus_search),
            ("Ctrl+T", lambda: self.new_tab(None)), ("Ctrl+W", self.close_current_tab),
            ("Ctrl+Tab", lambda: self._cycle_tab(1)), ("Ctrl+Shift+Tab", lambda: self._cycle_tab(-1)),
            ("Ctrl+N", lambda: self.ctl.open_explorer(self.current_location())),
            ("Ctrl+Shift+N", self.new_folder), ("F2", self.rename),
            ("Delete", lambda: self.delete()), ("Shift+Delete", lambda: self.delete(permanent=True)),
            ("Ctrl+D", lambda: self.delete()),
            ("Ctrl+A", self.select_all), ("Ctrl+C", lambda: self.copy_selection()),
            ("Ctrl+X", lambda: self.copy_selection(cut=True)), ("Ctrl+V", self.paste),
            ("Ctrl+Z", self.undo), ("Alt+Return", self.properties), ("Alt+P", self.toggle_preview),
            ("Ctrl+H", self.toggle_hidden), ("Ctrl+Shift+C", self.copy_path),
            ("Ctrl+Shift+1", lambda: self.set_view("xl")), ("Ctrl+Shift+2", lambda: self.set_view("large")),
            ("Ctrl+Shift+3", lambda: self.set_view("medium")), ("Ctrl+Shift+4", lambda: self.set_view("small")),
            ("Ctrl+Shift+5", lambda: self.set_view("list")), ("Ctrl+Shift+6", lambda: self.set_view("details")),
        ]
        for seq, fn in keys:
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.WindowShortcut)
            a.triggered.connect(fn)
            self.addAction(a)

    # -- layout style ------------------------------------------------------------------

    def apply_layout(self):
        self.layout_style = explorer_setting(self.settings, "layout", "win10")
        win10 = self.layout_style == "win10"
        self.ribbon.setVisible(win10)
        self.cbar.setVisible(not win10)
        self._update_tabrow()
        self.nav.style = self.layout_style
        from caracal.explorer.navpane import LABEL_DEFAULTS
        from caracal.explorer import widgets as _w
        self.nav.labels = {k: str(self.settings.value("explorer/label_" + k, "") or "")
                           for k in LABEL_DEFAULTS}
        _w.LABEL_OVERRIDES.update({
            "caracal:home": self.nav.label("quick") if win10 else self.nav.label("home"),
            "caracal:thispc": self.nav.label("thispc"),
            "caracal:network": self.nav.label("network")})
        if self.current_location():
            self._show(self.current_location())
        self._rebuild_nav()

    def _update_tabrow(self):
        default = self.layout_style != "win10"
        self.tabrow.setVisible(explorer_setting(self.settings, "tabs", default)
                               or self.tabbar.count() > 1)

    def _fill_recent_menu(self):
        m = self.recent_menu
        m.clear()
        t = self._tab()
        if not t:
            return
        seen = []
        for i in range(len(t["history"]) - 1, -1, -1):
            loc = t["history"][i]
            if loc in seen:
                continue
            seen.append(loc)
            a = m.addAction(location_label(loc), lambda i=i: self._jump(i))
            a.setCheckable(True)
            a.setChecked(i == t["index"])
            if len(seen) >= 12:
                break

    def _jump(self, i):
        t = self._tab()
        t["index"] = i
        self._show(t["history"][i])

    # -- per-folder view settings (like Explorer remembering each folder) ----------------

    def _prefs(self):
        try:
            return json.loads(self.settings.value("explorer/folder_prefs", "{}") or "{}")
        except ValueError:
            return {}

    def _folder_pref(self, key, default):
        loc = self.current_location() or ""
        p = self._prefs().get(loc, {})
        if key in p:
            return p[key]
        if key in ("group", "sort") and loc and os.path.realpath(loc) in self._download_dirs():
            return "date" if key == "group" else [DATE, int(Qt.DescendingOrder.value)]
        return default

    def _download_dirs(self):
        return {os.path.realpath(p) for _n, p, icon in places.user_dirs() if icon == "folder-download"}

    def _save_folder_pref(self, key, value):
        loc = self.current_location()
        if not loc or loc in PSEUDO:
            return
        prefs = self._prefs()
        prefs.setdefault(loc, {})[key] = value
        if len(prefs) > 2000:
            prefs = dict(list(prefs.items())[-1500:])
        self.settings.setValue("explorer/folder_prefs", json.dumps(prefs))

    def _apply_folder_prefs(self, loc):
        sort = self._folder_pref("sort", [NAME, int(Qt.AscendingOrder.value)])
        hdr = self.details.header()
        hdr.blockSignals(True)
        hdr.setSortIndicator(int(sort[0]), Qt.SortOrder(int(sort[1])))
        hdr.blockSignals(False)
        self.model.sort_col, self.model.sort_order = int(sort[0]), Qt.SortOrder(int(sort[1]))
        group = self._folder_pref("group", "none")
        self.model.group_by = group if self.view_mode == "details" else "none"

    def _sort_changed(self, col, order):
        self._save_folder_pref("sort", [col, int(order.value)])

    def set_group(self, mode):
        self._save_folder_pref("group", mode)
        if self.view_mode != "details" and mode != "none":
            self.set_view("details")
        self.model.set_group_by(mode)

    def _model_reset(self):
        QTimer.singleShot(0, self._after_reset)   # let the views reset first

    def _save_view_state(self):
        """Remember selection, current item and scroll position so a reload (the folder
        changed on disk, a rename, a new file) doesn't throw them away."""
        if not self.model.root:
            return
        cur = self.details.selectionModel().currentIndex()
        self._saved_view = {
            "root": self.model.root,
            "selected": self.selected_paths() if self.content.currentWidget() in
            (self.details, self.icons) else [],
            "current": self.model.filePath(cur) if cur.isValid() else "",
            "scroll": (self.details.verticalScrollBar().value(),
                       self.icons.verticalScrollBar().value()),
        }

    def _after_reset(self):
        root = self.model.root
        if self.model.grouped():
            collapsed = self._collapsed.get(root, set())
            self._restoring_groups = True
            for r, (label, _items) in enumerate(self.model.groups):
                self.details.setFirstColumnSpanned(r, QModelIndex(), True)
                self.details.setExpanded(self.model.index(r, 0), label not in collapsed)
            self._restoring_groups = False
        saved, self._saved_view = self._saved_view, None
        if saved and saved["root"] == root and self.model.entries:
            sm = self.details.selectionModel()
            sm.blockSignals(True)
            for p in saved["selected"]:
                idx = self.model.index_for_path(p)
                if idx.isValid():
                    sm.select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            cur = self.model.index_for_path(saved["current"]) if saved["current"] else QModelIndex()
            if cur.isValid():
                sm.setCurrentIndex(cur, QItemSelectionModel.NoUpdate)
            sm.blockSignals(False)
            self.details.verticalScrollBar().setValue(saved["scroll"][0])
            self.icons.verticalScrollBar().setValue(saved["scroll"][1])
        self._update_empty_message()
        self._update_status()

    def _group_toggled(self, index, expanded):
        if getattr(self, "_restoring_groups", False) or not self.model.is_group(index):
            return
        label = self.model.groups[index.row()][0]
        s = self._collapsed.setdefault(self.model.root, set())
        (s.discard if expanded else s.add)(label)

    def _update_empty_message(self):
        if not hasattr(self, "empty_labels"):
            return
        msg = ""
        if not self.model.loading and not self.searching:
            if self.model.error:
                msg = self.model.error
            elif self.model.root and not self.model.entries:
                msg = "This folder is empty."
        for lab in self.empty_labels:
            lab.setText(msg)
            lab.setVisible(bool(msg))
            lab.setGeometry(lab.parentWidget().rect().adjusted(0, 30, 0, 0))

    def _dir_loaded(self, _path):
        self._update_empty_message()
        pending = getattr(self, "_pending_select", None)
        if pending:
            self._pending_select = None
            self.select_paths(pending)
        self._update_status()
        self._selection_changed()

    def refresh_version(self):
        html, tip = self.ctl.version_html()
        self.version_label.setText(html + "&nbsp;&nbsp;|")
        self.version_label.setToolTip(tip)

    def _rebuild_nav(self):
        """Rebuild the navigation tree and re-highlight the folder we're in."""
        self.nav.hidden = self._load_nav_hidden()
        self.nav.rebuild()
        loc = self.current_location() if hasattr(self, "tabs_state") else None
        if loc and not (explorer_setting(self.settings, "nav_follow", True) and self.nav.reveal(loc)):
            self.nav.select_location(loc)

    def apply_appearance(self):
        t = theme.get()
        self.setPalette(t.palette())
        self.setStyleSheet(t.stylesheet())
        self.setFont(t.font(QApplication.font()))
        for b in self.findChildren(QToolButton):
            slot = getattr(b, "_slot", None)
            if slot:
                b.setIcon(t.icon(slot))
        self.model.clear_thumbs()
        self._rebuild_nav()
        self.address.set_location(self.current_location() or "caracal:home")

    # ======================================================================
    # tabs and history
    # ======================================================================

    def new_tab(self, loc):
        loc = loc or ("caracal:thispc" if explorer_setting(self.settings, "start", "home")
                      == "thispc" else "caracal:home")
        self.tabs_state.append({"history": [], "index": -1, "loc": None})
        i = self.tabbar.addTab(location_label(loc))
        self.tabbar.setCurrentIndex(i)
        self.go(loc)
        if hasattr(self, "tabrow"):
            self._update_tabrow()

    def close_tab(self, i):
        if self.tabbar.count() <= 1:
            self.close()
            return
        self.tabs_state.pop(i)
        self.tabbar.removeTab(i)
        self._update_tabrow()

    def close_current_tab(self):
        self.close_tab(self.tabbar.currentIndex())

    def _cycle_tab(self, step):
        n = self.tabbar.count()
        self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + step) % n)

    def _tab_moved(self, frm, to):
        self.tabs_state.insert(to, self.tabs_state.pop(frm))

    def _tab_changed(self, i):
        if 0 <= i < len(self.tabs_state) and self.tabs_state[i]["loc"]:
            self._show(self.tabs_state[i]["loc"])

    def _tab(self):
        i = self.tabbar.currentIndex()
        return self.tabs_state[i] if 0 <= i < len(self.tabs_state) else None

    def current_location(self):
        t = self._tab()
        return t["loc"] if t else None

    def back(self):
        t = self._tab()
        if t and t["index"] > 0:
            t["index"] -= 1
            self._show(t["history"][t["index"]])

    def forward(self):
        t = self._tab()
        if t and t["index"] < len(t["history"]) - 1:
            t["index"] += 1
            self._show(t["history"][t["index"]])

    def up(self):
        loc = self.current_location()
        if not loc or loc in PSEUDO:
            return
        if loc == "/":
            self.go("caracal:thispc")
            return
        self.go(os.path.dirname(loc.rstrip("/")) or "/")

    def refresh(self):
        loc = self.current_location()
        self.ctl.refresh()
        if loc:
            self._show(loc)

    # ======================================================================
    # navigation
    # ======================================================================

    def go(self, loc):
        """Navigate the current tab (handles special locations)."""
        if not loc:
            return
        if loc == "net:add":
            self.add_network_location()
            return
        if loc.startswith("connect:"):
            self.add_network_location(loc[len("connect:"):])
            return
        if loc.startswith("net:sys:"):
            d = next((d for d in self.ctl.drives if d.id == loc[8:]), None)
            if d is None:
                return
            if d.state in ("connected", "ready"):
                self.go(d.mount_point)
            else:
                self._connect_then(d, d.mount_point)
            return
        if loc.startswith("net:user:"):
            lid = loc[9:]
            l_ = next((l for l in self.nav.locations if l.id == lid), None)
            if l_ is None:
                return
            if l_.state == "connected":
                self.go(l_.mount_point)
            else:
                self._connect_user(l_, then_open=True)
            return
        if loc.startswith("device:"):
            dev = loc[7:]
            self.status.setText("Mounting %s…" % dev)
            run_async(self, lambda: places.mount_device(dev), self._device_mounted)
            return
        if loc == "trash:":
            os.makedirs(TRASH, exist_ok=True)
            loc = TRASH
        if loc not in PSEUDO:
            loc = os.path.abspath(os.path.expanduser(loc))
            if not os.path.exists(loc):
                QMessageBox.warning(self, "Explorer", "Can't find “%s”. Check the spelling and "
                                    "try again." % loc)
                return
            if os.path.isfile(loc):
                self.go(os.path.dirname(loc))
                self.select_paths([loc])
                return
        t = self._tab()
        if t is None:
            return
        if t["loc"] == loc and not self.searching:
            return
        del t["history"][t["index"] + 1:]
        t["history"].append(loc)
        t["index"] = len(t["history"]) - 1
        self._show(loc)

    def _show(self, loc):
        t = self._tab()
        t["loc"] = loc
        self._end_search(clear_box=True)
        i = self.tabbar.currentIndex()
        self.tabbar.setTabText(i, location_label(loc))
        self.tabbar.setTabToolTip(i, loc)
        self.tabbar.setTabIcon(i, theme.get().icon(PSEUDO[loc][1]) if loc in PSEUDO
                               else theme.get().icon("folder"))
        self.setWindowTitle(location_label(loc))
        self.address.set_location(loc)
        self.back_btn.setEnabled(t["index"] > 0)
        self.fwd_btn.setEnabled(t["index"] < len(t["history"]) - 1)
        self.up_btn.setEnabled(loc not in PSEUDO)
        self.search.setPlaceholderText("Search %s" % location_label(loc))
        if not (explorer_setting(self.settings, "nav_follow", True) and self.nav.reveal(loc)):
            self.nav.select_location(loc)
        if loc in PSEUDO:
            self._show_page(loc)
        else:
            self._record_visit(loc)
            self._apply_folder_prefs(loc)
            self.model.setRootPath(loc)
            self.content.setCurrentWidget(self.icons if self.view_mode != "details" else self.details)
        for w in (self.cmd_cut, self.cmd_copy, self.cmd_paste, self.cmd_rename, self.cmd_delete,
                  self.cmd_sort, self.cmd_new):
            w.setEnabled(loc not in PSEUDO)
        self._update_status()
        self._selection_changed()

    def select_paths(self, paths):
        if self.model.loading:
            self._pending_select = list(paths)
            return

        def do():
            sm = self.details.selectionModel()
            sm.clearSelection()
            for p in paths:
                idx = self.model.index(p)
                if idx.isValid():
                    sm.select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                    self.current_view().scrollTo(idx)
                    sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
        QTimer.singleShot(0, do)

    def _device_mounted(self, mp, error):
        if error or not mp:
            QMessageBox.warning(self, "Explorer", "Couldn't open the drive: %s" % (error or "unknown"))
            return
        self._rebuild_nav()
        self.go(mp)

    # ======================================================================
    # views
    # ======================================================================

    @property
    def view_mode(self):
        return getattr(self, "_view_mode", "details")

    def current_view(self):
        w = self.content.currentWidget()
        return w if isinstance(w, (QTreeView, QListView)) else self.details

    def set_view(self, mode, save=True):
        if mode not in VIEW_MODES:
            mode = "details"
        self._view_mode = mode
        if save:
            self.settings.setValue("explorer/view", mode)
        size = VIEW_MODES[mode][1]
        if mode == "details":
            self.model.thumb_size = 0
        elif mode == "list":
            self.icons.setViewMode(QListView.ListMode)
            self.icons.setFlow(QListView.TopToBottom)
            self.icons.setWrapping(True)
            self.icons.setIconSize(QSize(16, 16))
            self.icons.setGridSize(QSize())
            self.model.thumb_size = 0
        else:
            self.icons.setViewMode(QListView.IconMode if size > 24 else QListView.ListMode)
            self.icons.setFlow(QListView.LeftToRight)
            self.icons.setWrapping(True)
            self.icons.setIconSize(QSize(size, size))
            text_h = self.fontMetrics().height() * 2 + 8
            if size > 24:
                self.icons.setGridSize(QSize(max(size + 40, 100), size + text_h + 10))
            else:
                self.icons.setGridSize(QSize(220, 28))
            self.model.thumb_size = size if size >= 48 else 0
        self.model.clear_thumbs()
        want_group = self._folder_pref("group", "none") if mode == "details" else "none"
        if want_group != self.model.group_by and self.model.root:
            self.model.set_group_by(want_group)
        else:
            self.model.group_by = want_group
        if self.current_location() and self.current_location() not in PSEUDO and not self.searching:
            self.content.setCurrentWidget(self.details if mode == "details" else self.icons)
        self.sb_details.setChecked(mode == "details")
        self.sb_icons.setChecked(mode in ("large", "xl", "medium"))

    def fill_theme_menu(self, m):
        current = theme.get().data.get("mode", "system")
        for key, label in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            a = m.addAction(label, lambda k=key: self.set_theme_mode(k))
            a.setCheckable(True)
            a.setChecked(current == key)

    def set_theme_mode(self, mode):
        """Light/dark for Explorer windows only (the desktop and other apps are untouched)."""
        d = theme.ensure_theme_dir()
        path = os.path.join(d, "theme.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        self.ctl.apply_explorer_settings()

    # -- columns, sorting, grouping, filters ----------------------------------------

    def _restore_old_widths(self, state):
        """Carry the Name/Date/Type/Size widths over from the pre-1.2 header."""
        from PySide6.QtWidgets import QHeaderView as _HV
        tmp_model = QStandardItemModel(0, 4)
        tmp = _HV(Qt.Horizontal)
        tmp.setModel(tmp_model)
        if tmp.restoreState(state):
            hdr = self.details.header()
            for i in range(4):
                if tmp.sectionSize(i) > 10:
                    hdr.resizeSection(i, tmp.sectionSize(i))
                hdr.setSectionHidden(i, tmp.isSectionHidden(i) and i != NAME)

    def visible_columns(self):
        hdr = self.details.header()
        return [hdr.logicalIndex(v) for v in range(hdr.count())
                if not hdr.isSectionHidden(hdr.logicalIndex(v))]

    def _columns_changed(self):
        self.model.set_visible_columns(self.visible_columns())

    def toggle_column(self, col, show=None):
        hdr = self.details.header()
        if col == NAME:
            return
        show = hdr.isSectionHidden(col) if show is None else show
        if show and hdr.isSectionHidden(col):
            hdr.moveSection(hdr.visualIndex(col), hdr.count() - 1)   # new columns go on the right
        hdr.setSectionHidden(col, not show)
        if show and hdr.sectionSize(col) < 30:
            hdr.resizeSection(col, self.fontMetrics().averageCharWidth() * COLUMNS[col].width)
        self._columns_changed()

    def choose_columns(self):
        d = ChooseColumnsDialog(set(self.visible_columns()), self)
        if d.exec():
            chosen = d.chosen()
            for i in range(len(COLUMNS)):
                self.toggle_column(i, i in chosen)

    def size_columns_to_fit(self):
        for c in self.visible_columns():
            self.details.resizeColumnToContents(c)

    def fill_columns_menu(self, m, around=None):
        hdr = self.details.header()
        shown = set(self.visible_columns())
        for key in COMMON:
            col = index_of(key)
            a = m.addAction(COLUMNS[col].title, lambda c=col: self.toggle_column(c))
            a.setCheckable(True)
            a.setChecked(col in shown)
            a.setEnabled(col != NAME)
        extra = [c for c in shown if COLUMNS[c].key not in COMMON]
        for col in extra:      # other columns already showing, so they can be unticked here
            a = m.addAction(COLUMNS[col].title, lambda c=col: self.toggle_column(c))
            a.setCheckable(True)
            a.setChecked(True)
        m.addSeparator()
        if around is not None and around >= 0:
            m.addAction("Size column to fit", lambda: self.details.resizeColumnToContents(around))
        m.addAction("Size all columns to fit", self.size_columns_to_fit)
        if self.model.filters:
            m.addAction("Clear all filters", self.clear_filters)
        m.addSeparator()
        m.addAction("More…", self.choose_columns)
        del hdr

    def _header_menu(self, pos):
        hdr = self.details.header()
        m = QMenu(self)
        self.fill_columns_menu(m, hdr.logicalIndexAt(pos))
        m.exec(hdr.mapToGlobal(pos))

    def _filter_popup(self, col, pos):
        values = self.model.filter_values(col)
        selected = self.model.filters.get(col, set())
        pop = FilterPopup(COLUMNS[col].title, values, selected,
                          lambda labels, c=col: self._set_filter(c, labels), self)
        pop.move(pos)
        pop.show()
        self._filter_pop = pop

    def _set_filter(self, col, labels):
        self.model.set_filter(col, labels)
        self._update_status()

    def clear_filters(self):
        self.model.clear_filters()
        self._update_status()

    def _sync_filter_marks(self):
        hdr = self.details.header()
        hdr.filtered = set(self.model.filters)
        hdr.viewport().update()

    # -- trash and archives --------------------------------------------------------

    def in_trash(self):
        loc = self.current_location() or ""
        return loc.startswith("/") and (os.path.realpath(loc) == os.path.realpath(TRASH) or
                                        os.path.basename(os.path.dirname(loc)).startswith(".Trash"))

    def restore(self, paths):
        errors = []
        for p in paths:
            try:
                fileops.restore_from_trash(p)
            except OSError as exc:
                errors.append("%s: %s" % (os.path.basename(p), exc.strerror or exc))
        if errors:
            QMessageBox.warning(self, "Restore", "\n".join(errors[:15]))
        self.status.setText("Restored %d item%s" % (len(paths) - len(errors),
                                                    "" if len(paths) - len(errors) == 1 else "s"))

    def extract(self, path):
        self.status.setText("Extracting %s…" % os.path.basename(path))
        run_async(self, lambda: fileops.extract_archive(path), lambda dest, err: (
            QMessageBox.warning(self, "Extract", "Couldn't extract: %s" % err) if err
            else (self.status.setText("Extracted to %s" % os.path.basename(dest)),
                  self.select_paths([dest]))))

    def compress(self, paths):
        if not paths:
            return
        folder = os.path.dirname(paths[0])
        base = os.path.splitext(os.path.basename(paths[0]))[0] if len(paths) == 1 else \
            os.path.basename(folder) or "Archive"
        name = base + ".zip"
        if os.path.exists(os.path.join(folder, name)):
            name = fileops.unique_name(folder, name, "n")
        target = os.path.join(folder, name)
        self.status.setText("Compressing…")
        run_async(self, lambda: fileops.make_zip(paths, target), lambda r, err: (
            QMessageBox.warning(self, "Compress", "Couldn't create the ZIP file: %s" % err) if err
            else (self.undo_stack.append({"kind": "create", "path": target}),
                  self.select_paths([target]))))

    def toggle_preview(self):
        on = not self.preview.isVisible()
        self.preview.setVisible(on)
        self.cmd_details_pane.setChecked(on)
        self.sb_preview.setChecked(on)
        self.settings.setValue("explorer/preview", on)
        self._selection_changed()

    def toggle_nav(self):
        on = not self.nav.isVisible()
        self.nav.setVisible(on)
        self.settings.setValue("explorer/nav", on)

    def toggle_hidden(self):
        self.settings.setValue("explorer/show_hidden",
                               not explorer_setting(self.settings, "show_hidden", False))
        self._apply_hidden()

    def toggle_ext(self):
        show = not explorer_setting(self.settings, "show_ext", True)
        self.settings.setValue("explorer/show_ext", show)
        self.model.hide_ext = not show
        self.model.layoutChanged.emit()

    def _apply_hidden(self):
        self.model.set_show_hidden(explorer_setting(self.settings, "show_hidden", False))

    def _fill_view_menu(self):
        m = self.view_menu
        m.clear()
        for key, (label, _s) in VIEW_MODES.items():
            a = m.addAction(label, lambda k=key: self.set_view(k))
            a.setCheckable(True)
            a.setChecked(self.view_mode == key)
        m.addSeparator()
        g = m.addMenu("Group by")
        self.fill_group_menu(g)
        th = m.addMenu("Theme")
        self.fill_theme_menu(th)
        show = m.addMenu("Show")
        for label, on, fn in (("Navigation pane", self.nav.isVisible(), self.toggle_nav),
                              ("Preview pane", self.preview.isVisible(), self.toggle_preview),
                              ("File name extensions", not self.model.hide_ext, self.toggle_ext),
                              ("Hidden items", explorer_setting(self.settings, "show_hidden", False),
                               self.toggle_hidden)):
            a = show.addAction(label, fn)
            a.setCheckable(True)
            a.setChecked(on)

    def _fill_sort_menu(self):
        m = self.sort_menu
        m.clear()
        self.fill_sort_menu(m)

    def _column_choices(self, m, current, pick, with_none=False):
        """Visible columns first, then every other column under More (by category)."""
        if with_none:
            a = m.addAction("(None)", lambda: pick(-1))
            a.setCheckable(True)
            a.setChecked(current == -1)
        shown = self.visible_columns()
        for c in shown:
            a = m.addAction(COLUMNS[c].title, lambda c=c: pick(c))
            a.setCheckable(True)
            a.setChecked(current == c)
        more = m.addMenu("More")
        for cat in CATEGORIES:
            rest = [i for i, col in enumerate(COLUMNS) if col.category == cat and i not in shown]
            if not rest:
                continue
            sub = more.addMenu(cat) if cat != "General" else more
            for c in rest:
                a = sub.addAction(COLUMNS[c].title, lambda c=c: pick(c))
                a.setCheckable(True)
                a.setChecked(current == c)
        more.addSeparator()
        more.addAction("Choose columns…", self.choose_columns)

    def fill_sort_menu(self, m, with_group=True):
        col = self.details.header().sortIndicatorSection()
        order = self.details.header().sortIndicatorOrder()
        self._column_choices(m, col, lambda c: self.details.sortByColumn(c, order))
        m.addSeparator()
        for o, label in ((Qt.AscendingOrder, "Ascending"), (Qt.DescendingOrder, "Descending")):
            a = m.addAction(label, lambda o=o: self.details.sortByColumn(col, o))
            a.setCheckable(True)
            a.setChecked(order == o)
        if with_group:
            m.addSeparator()
            g = m.addMenu("Group by")
            self.fill_group_menu(g)

    def fill_group_menu(self, m):
        current = index_of(self._folder_pref("group", "none"))
        self._column_choices(m, current, lambda c: self.set_group(
            "none" if c < 0 else COLUMNS[c].key), with_none=True)

    def _fill_more_menu(self):
        m = self.more_menu
        m.clear()
        a = m.addAction("Undo", self.undo)
        a.setEnabled(bool(self.undo_stack))
        m.addSeparator()
        m.addAction("Select all", self.select_all)
        m.addAction("Select none", lambda: self.current_view().clearSelection())
        m.addAction("Invert selection", self.invert_selection)
        m.addSeparator()
        loc = self.current_location()
        if loc and loc not in PSEUDO:
            m.addAction(theme.get().icon("pin"), "Pin to Quick access", lambda: self.pin(loc))
            m.addAction(theme.get().icon("terminal"), "Open in terminal", lambda: self.open_terminal(loc))
            m.addAction("Copy path", lambda: QApplication.clipboard().setText(loc))
            m.addAction(theme.get().icon("properties"), "Properties", self.properties)
            m.addSeparator()
        m.addAction(theme.get().icon("add-location"), "Add network location…",
                    lambda: self.add_network_location())
        m.addAction(theme.get().icon("network"), "Manage network drives…", self.ctl.show_window)
        m.addAction(theme.get().icon("search"), "Instant search (all indexed drives)…",
                    lambda: self.ctl.open_search())
        if loc and os.path.realpath(loc) == os.path.realpath(TRASH):
            m.addAction(theme.get().icon("trash"), "Empty Trash", self.empty_trash)
        m.addSeparator()
        m.addAction(theme.get().icon("settings"), "Options", self.ctl.preferences)
        m.addAction("Check for updates…", self.ctl.check_updates)
        m.addAction("What's new (changelog)", self.ctl.show_changelog)
        m.addSeparator()
        m.addAction("Exit Caracal (stop background tasks)", self.ctl.quit)

    # ======================================================================
    # tile pages
    # ======================================================================

    def _record_visit(self, loc):
        try:
            visits = json.loads(self.settings.value("explorer/visits", "{}") or "{}")
        except ValueError:
            visits = {}
        visits[loc] = visits.get(loc, 0) + 1
        if len(visits) > 300:   # keep the most used
            visits = dict(sorted(visits.items(), key=lambda kv: -kv[1])[:200])
        self.settings.setValue("explorer/visits", json.dumps(visits))

    def frequent_folders(self, limit=8):
        try:
            visits = json.loads(self.settings.value("explorer/visits", "{}") or "{}")
        except ValueError:
            return []
        skip = set(self.nav.pinned) | {p for _n, p, _i in places.user_dirs()} | \
            {os.path.expanduser("~"), "/"}
        out = [p for p, _n in sorted(visits.items(), key=lambda kv: -kv[1])
               if p not in skip and os.path.isdir(p)]
        return out[:limit]

    def _show_page(self, loc):
        sections = []
        if loc == "caracal:home":
            pinned = [Tile(p, theme.get().icon("folder"), os.path.basename(p.rstrip("/")) or p,
                           p.replace(os.path.expanduser("~"), "~"))
                      for p in self.nav.pinned if os.path.isdir(p)]
            folders = [Tile(path, theme.get().icon({"user-desktop": "desktop",
                                                     "folder-download": "downloads",
                                                     "folder-documents": "documents",
                                                     "folder-pictures": "pictures",
                                                     "folder-music": "music",
                                                     "folder-videos": "videos"}.get(icon, "folder")),
                            name, "Stored locally")
                       for name, path, icon in places.user_dirs()]
            recent = [Tile(p, theme.file_info_icon(p), os.path.basename(p),
                           "%s · %s" % (os.path.dirname(p).replace(os.path.expanduser("~"), "~"),
                                        ago(ts)))
                      for p, ts in recent_files(16)]
            frequent = [Tile(p, theme.get().icon("folder"), os.path.basename(p.rstrip("/")) or p,
                             p.replace(os.path.expanduser("~"), "~"))
                        for p in self.frequent_folders()]
            sections = [("Quick access", pinned + [t_ for t_ in folders
                                                   if t_.key not in self.nav.pinned]),
                        ("Frequent folders", frequent), ("Recent files", recent)]
        elif loc == "caracal:thispc":
            folders = [Tile(path, theme.get().icon("folder"), name, "") for name, path, _i in
                       places.user_dirs()]
            drives = []
            for v in places.mounted_volumes():
                drives.append(Tile(v.path, theme.get().icon("drive-removable" if v.removable else "drive"),
                                   v.label, format_usage(v.used, v.size), v.used, v.size))
            for v in places.unmounted_devices():
                drives.append(Tile("device:" + v.device, theme.get().icon("drive-removable"),
                                   v.label, "%s · double-click to open" % format_size(v.size)))
            sections = [("Folders", folders), ("Devices and drives", drives),
                        ("Network locations", self._net_tiles())]
        elif loc == "caracal:network":
            add = [Tile("net:add", theme.get().icon("add-location"), "Add network location",
                        "SMB, NFS, SFTP/SCP, FTP, WebDAV"),
                   Tile("net:browse", theme.get().icon("search"), "Browse the network",
                        "Find SMB/NFS servers and shares")]
            sections = [("Network locations", self._net_tiles()), ("", add)]
        self.tiles.set_sections(sections, single_click=explorer_setting(self.settings, "single_click", False))
        self.content.setCurrentWidget(self.tiles)

    def _net_tiles(self):
        tiles = []
        for d in self.ctl.drives:
            up = d.state in ("connected", "ready")
            usage = self.ctl._usage.get(d.id) if d.state == "connected" else None
            sub = format_usage(*usage) if usage else "%s · %s" % (d.remote, STATE_LABELS.get(d.state, d.state))
            tiles.append(Tile("net:sys:" + d.id, theme.get().icon("drive-network" if up else "drive-network-offline"),
                              d.name, sub, *(usage or (None, None))))
        for l_ in self.nav.locations:
            up = l_.state == "connected"
            tiles.append(Tile("net:user:" + l_.id, theme.get().icon("drive-network" if up else "drive-network-offline"),
                              l_.name, "%s · %s" % (l_.remote, STATE_LABELS.get(l_.state, l_.state))))
        return tiles

    def _tile_activated(self, key):
        if key == "net:browse":
            self.ctl.browse_and_add()
        else:
            self.go(key)

    def _tile_menu(self, key, pos):
        m = QMenu(self)
        m.addAction(theme.get().icon("open"), "Open", lambda: self._tile_activated(key))
        if key.startswith("/"):
            m.addAction("Open in new tab", lambda: self.new_tab(key))
            m.addAction(theme.get().icon("pin"), "Pin to Quick access", lambda: self.pin(key))
            if key in self.nav.pinned:
                m.addAction("Unpin from Quick access", lambda: self.unpin(key))
            for v in places.mounted_volumes():
                if v.path == key and v.removable:
                    m.addAction(theme.get().icon("eject"), "Eject", lambda p=key: self.eject(p))
            m.addAction(theme.get().icon("properties"), "Properties",
                        lambda: PropertiesDialog([key], self).exec())
        elif key.startswith("net:"):
            self._net_actions(m, key)
        m.exec(pos)

    # ======================================================================
    # network
    # ======================================================================

    def _drives_changed(self):
        locs = user_mounts.load_locations()
        user_mounts.refresh_states(locs)
        sig = ([(d.id, d.name, d.state) for d in self.ctl.drives],
               [(l.id, l.name, l.state) for l in locs])
        self.nav.locations = locs
        self.nav.drives = list(self.ctl.drives)
        pinned = self._load_pins()
        if sig != self._net_sig or pinned != self.nav.pinned:
            self._net_sig = sig
            self.nav.pinned = pinned
            self._rebuild_nav()
            loc = self.current_location()
            if loc in ("caracal:thispc", "caracal:network") and not self.searching:
                self._show_page(loc)

    def _nav_menu(self, loc, pos):
        t = theme.get()
        m = QMenu(self)
        if loc.startswith("/"):
            m.addAction(t.icon("open"), "Open", lambda: self.go(loc))
            m.addAction("Open in new tab", lambda: self.new_tab(loc))
            m.addAction("Open in new window", lambda: self.ctl.open_explorer(loc))
            m.addSeparator()
            if loc in self.nav.pinned:
                m.addAction(t.icon("pin"), "Unpin from Quick access", lambda: self.unpin(loc))
            else:
                m.addAction(t.icon("pin"), "Pin to Quick access", lambda: self.pin(loc))
            self._index_menu(m, loc)
            m.addAction(t.icon("terminal"), "Open in terminal", lambda: self.open_terminal(loc))
            for v in places.mounted_volumes():
                if v.path == loc and v.removable:
                    m.addAction(t.icon("eject"), "Eject", lambda p=loc: self.eject(p))
            if os.path.realpath(loc) == os.path.realpath(TRASH):
                m.addAction(t.icon("trash"), "Empty Trash", self.empty_trash)
            m.addAction(t.icon("properties"), "Properties", lambda: PropertiesDialog([loc], self).exec())
        elif loc.startswith("net:sys:") or loc.startswith("net:user:"):
            self._net_actions(m, loc)
            mp = self._net_mount_point(loc)
            if mp and os.path.ismount(mp):
                self._index_menu(m, mp)
        elif loc in ("caracal:home", "caracal:thispc", "caracal:network"):
            m.addAction(t.icon("open"), "Open", lambda: self.go(loc))
            m.addAction("Open in new tab", lambda: self.new_tab(loc))
        # Hide this item
        hide_key = "trash" if loc in (TRASH, "trash:") else loc
        hideable = {e[1] for e in self.nav.entries()}
        if hide_key in hideable:
            m.addAction("Hide from navigation pane", lambda: self._set_nav_hidden(hide_key, True))
        # Network: map / add locations (always available)
        if not m.isEmpty():
            m.addSeparator()
        m.addAction(t.icon("drive-network"), "Map network drive (SMB / NFS)…",
                    lambda: self.ctl.add_drive())
        m.addAction(t.icon("add-location"), "Add network location (SFTP, FTP, WebDAV)…",
                    lambda: self.add_network_location())
        m.addAction(t.icon("search"), "Browse the network…", self.ctl.browse_and_add)
        m.addAction(t.icon("network"), "Manage network drives…", self.ctl.show_window)
        m.addSeparator()
        show = m.addMenu("Show in navigation pane")
        for label, key in self.nav.entries():
            a = show.addAction(label, lambda k=key: self._set_nav_hidden(k, k not in self.nav.hidden))
            a.setCheckable(True)
            a.setChecked(key not in self.nav.hidden)
        show.addSeparator()
        show.addAction("Show everything", self._show_all_nav)
        a = m.addAction("Expand to open folder", lambda: self.settings.setValue(
            "explorer/nav_follow", not explorer_setting(self.settings, "nav_follow", True)))
        a.setCheckable(True)
        a.setChecked(explorer_setting(self.settings, "nav_follow", True))
        m.exec(pos)

    def _net_mount_point(self, key):
        if key.startswith("net:sys:"):
            d = next((d for d in self.ctl.drives if d.id == key[8:]), None)
            return d.mount_point if d else None
        l_ = next((l for l in self.nav.locations if l.id == key[9:]), None)
        return l_.mount_point if l_ else None

    def _load_nav_hidden(self):
        try:
            return set(json.loads(self.settings.value("explorer/nav_hidden", "[]") or "[]"))
        except ValueError:
            return set()

    def _set_nav_hidden(self, key, hidden):
        h = self._load_nav_hidden()
        (h.add if hidden else h.discard)(key)
        self.settings.setValue("explorer/nav_hidden", json.dumps(sorted(h)))
        self.nav.hidden = h
        self._rebuild_nav()

    def _show_all_nav(self):
        self.settings.setValue("explorer/nav_hidden", "[]")
        self.nav.hidden = set()
        self._rebuild_nav()

    def _net_actions(self, m, key):
        if key.startswith("net:sys:"):
            d = next((d for d in self.ctl.drives if d.id == key[8:]), None)
            if not d:
                return
            m.addAction("Open in new tab", lambda: self._open_net_in_tab(key))
            if d.state not in ("connected",):
                m.addAction(theme.get().icon("connect"), "Connect", lambda: self.ctl._run_on(d, "connect"))
            if d.state != "disconnected":
                m.addAction(theme.get().icon("disconnect"), "Disconnect",
                            lambda: self.ctl._run_on(d, "disconnect"))
            m.addSeparator()
            m.addAction(theme.get().icon("properties"), "Edit…", lambda: self.ctl.edit_drive_obj(d))
            m.addAction(theme.get().icon("delete"), "Remove…", lambda: self.ctl.remove_drive_obj(d))
        else:
            l_ = next((l for l in self.nav.locations if l.id == key[9:]), None)
            if not l_:
                return
            m.addAction("Open in new tab", lambda: self._open_net_in_tab(key))
            if l_.state != "connected":
                m.addAction(theme.get().icon("connect"), "Connect", lambda: self._connect_user(l_))
            else:
                m.addAction(theme.get().icon("disconnect"), "Disconnect", lambda: self._user_op(
                    user_mounts.disconnect, l_, "Disconnecting %s" % l_.name))
            m.addSeparator()
            m.addAction(theme.get().icon("properties"), "Edit…", lambda: self.add_network_location(edit=l_))
            m.addAction(theme.get().icon("delete"), "Remove…", lambda: self._remove_user(l_))

    def _open_net_in_tab(self, key):
        self.new_tab("caracal:network")
        self.go(key)

    def _connect_then(self, d, path):
        self.status.setText("Connecting %s…" % d.name)
        self.ctl._run_on(d, "connect")

        def check(tries=40):
            dd = next((x for x in self.ctl.drives if x.id == d.id), None)
            if dd and dd.state in ("connected", "ready"):
                self.go(path)
            elif tries > 0 and self.ctl.helper.busy or (dd and dd.state == "connecting"):
                QTimer.singleShot(500, lambda: check(tries - 1))
        QTimer.singleShot(800, check)

    def _connect_user(self, l_, then_open=False):
        def done(_r, err):
            self._drives_changed()
            if err:
                detail = user_mounts.last_error(l_)
                QMessageBox.warning(self, "Explorer", "Couldn't connect %s.\n\n%s"
                                    % (l_.name, detail or err))
            elif then_open:
                self._wait_mounted(l_.mount_point, 30)
        self.status.setText("Connecting %s…" % l_.name)
        run_async(self, lambda: user_mounts.connect(l_), done)

    def _wait_mounted(self, mp, tries):
        if os.path.ismount(mp):
            self._drives_changed()
            self.go(mp)
        elif tries > 0:
            QTimer.singleShot(500, lambda: self._wait_mounted(mp, tries - 1))
        else:
            self.status.setText("The location didn't connect. Check its log with Edit… or try again.")

    def _user_op(self, fn, l_, label):
        self.status.setText(label + "…")
        run_async(self, lambda: fn(l_), lambda _r, err: (
            QMessageBox.warning(self, "Explorer", str(err)) if err else None,
            self._drives_changed()))

    def _remove_user(self, l_):
        if QMessageBox.question(self, "Explorer", "Remove “%s”? Files on the server aren't "
                                "touched." % l_.name) == QMessageBox.Yes:
            self._user_op(user_mounts.remove, l_, "Removing %s" % l_.name)

    def add_network_location(self, address="", edit=None):
        """SMB/NFS go to Caracal's drive dialog; SFTP/FTP/WebDAV are user mounts."""
        if edit is None:
            low = address.lower()
            if low.startswith(("sftp://", "ssh://", "scp://", "ftp://", "ftps://", "http://", "https://", "dav")):
                kind = "sftp" if low.startswith(("sftp", "ssh", "scp")) else (
                    "ftps" if low.startswith("ftps") else "ftp" if low.startswith("ftp") else "webdav")
            elif address:
                self.ctl.add_drive(address)
                return
            else:
                m = QMenu(self)
                choice = {}
                for k, label in (("smb", "Windows share / Samba (SMB)"), ("nfs", "NFS export"),
                                 ("sftp", "SFTP / SCP (SSH server)"), ("ftp", "FTP"),
                                 ("ftps", "FTPS (FTP over TLS)"), ("webdav", "WebDAV (Nextcloud, …)")):
                    m.addAction(label, lambda k=k: choice.setdefault("k", k))
                m.exec(self.cursor().pos())
                kind = choice.get("k")
                if not kind:
                    return
                if kind in ("smb", "nfs"):
                    self.ctl.add_drive("", kind)
                    return
            dlg = LocationDialog(kind=kind, parent=self)
            if address:
                _prefill_location(dlg, address)
        else:
            dlg = LocationDialog(edit, parent=self)
        if not dlg.exec():
            return
        spec, pw = dlg.spec(), dlg.password_value()
        self.status.setText("Connecting %s…" % spec["name"])

        def saved(loc, err):
            self._drives_changed()
            if err:
                QMessageBox.warning(self, "Explorer", str(err))
                return
            self._wait_mounted(loc.mount_point, 40)
        run_async(self, lambda: user_mounts.save(spec, pw), saved)

    def eject(self, path):
        run_async(self, lambda: places.eject(path), lambda _r, _e: (self._rebuild_nav(),
                                                                   self.go("caracal:thispc")))

    # ======================================================================
    # selection, status, preview
    # ======================================================================

    def selected_paths(self):
        if self.content.currentWidget() is self.results:
            rows = {i.row() for i in self.results.selectionModel().selectedRows()}
            return [self.results_model.item(r, 0).data(Qt.UserRole) for r in sorted(rows)]
        if self.content.currentWidget() is self.tiles:
            return []
        rows = self.details.selectionModel().selectedRows(0)
        return [p for p in (self.model.filePath(i) for i in rows) if p]

    def _selection_changed(self, *_):
        if not hasattr(self, "status"):
            return
        self._update_status()
        if self.preview.isVisible():
            self.preview.show_paths(self.selected_paths()[:50])

    def _update_status(self):
        if not hasattr(self, "status"):
            return
        loc = self.current_location()
        if self.searching:
            n = self.results_model.rowCount()
            sel = len(self.selected_paths())
            self.status.setText("%d result%s%s" % (n, "" if n == 1 else "s",
                                                  ("   |   %d selected" % sel) if sel else ""))
            return
        if not loc or loc in PSEUDO:
            self.status.setText("")
            return
        if self.model.loading and not self.model.item_count():
            self.status.setText("Working on it…")
            return
        n = self.model.item_count()
        sel = self.selected_paths()
        text = "%s item%s" % (format(n, ","), "" if n == 1 else "s")
        if self.model.filters:
            text = "%s of %s items (filtered)" % (format(n, ","), format(self.model.total_count(), ","))
        if sel:
            size = sum(os.path.getsize(p) for p in sel if os.path.isfile(p))
            text += "   |   %d item%s selected%s" % (len(sel), "" if len(sel) == 1 else "s",
                                                      ("  " + format_size(size)) if size else "")
        try:
            st = os.statvfs(loc)
            text += "   |   %s free" % format_size(st.f_bavail * st.f_frsize)
        except OSError:
            pass
        self.status.setText(text)

    def _type_ahead(self, view, buf):
        model = self.model
        if model.grouped():
            order = [e for _label, items in model.groups for e in items]
        else:
            order = list(model.entries)
        if not order:
            return
        cur_path = model.filePath(view.currentIndex())
        start = next((i for i, e in enumerate(order) if e.path == cur_path), -1)
        # Same letter repeated ("ccc"): cycle among items starting with that letter.
        cycling = len(buf) > 1 and buf == buf[0] * len(buf)
        needle = buf[0] if cycling else buf
        n = len(order)
        first = start + 1 if (cycling or len(buf) == 1) else max(start, 0)
        for k in range(n):
            e = order[(first + k) % n]
            if e.name.lower().startswith(needle):
                idx = model.index_for_path(e.path)
                if model.grouped() and idx.parent().isValid():
                    self.details.expand(idx.parent())
                view.selectionModel().setCurrentIndex(
                    idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                view.scrollTo(idx)
                return

    def select_all(self):
        sm = self.details.selectionModel()
        sm.clearSelection()
        for e in self.model.entries:
            sm.select(self.model.index_for_path(e.path),
                      QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def invert_selection(self):
        sm = self.details.selectionModel()
        for e in self.model.entries:
            sm.select(self.model.index_for_path(e.path),
                      QItemSelectionModel.Toggle | QItemSelectionModel.Rows)

    # ======================================================================
    # opening
    # ======================================================================

    def activate(self, index):
        if self.model.is_group(index):
            g = index.siblingAtColumn(0)
            self.details.setExpanded(g, not self.details.isExpanded(g))
            return
        path = self.model.filePath(index)
        if not path:
            return
        if os.path.isdir(path):
            self.go(path)
        else:
            self.open_file(path)

    def open_file(self, path, app=None):
        if app is not None:
            app.launch([path])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_with(self, path):
        dlg = OpenWithDialog(path, self)
        if dlg.exec() and dlg.chosen():
            app = dlg.chosen()
            if dlg.always.isChecked():
                openwith.set_default(dlg.mimetype, app.desktop_id)
            app.launch([path])

    def open_terminal(self, path):
        custom = self.settings.value("explorer/terminal", "")
        if custom:
            subprocess.Popen([custom], cwd=path, start_new_session=True)
            return
        for exe, flag in TERMINALS:
            if shutil.which(exe):
                if flag is None:
                    argv = [exe]
                elif flag.endswith("="):
                    argv = [exe, flag + path]
                else:
                    argv = [exe, flag, path]
                subprocess.Popen(argv, cwd=path, start_new_session=True)
                return
        QMessageBox.warning(self, "Explorer", "No terminal app was found.")

    ZOOM_STEPS = ("details", "small", "medium", "large", "xl")

    def eventFilter(self, obj, event):
        et = event.type()
        viewports = (self.details.viewport(), self.icons.viewport())
        if obj in viewports and et == event.Type.Resize:
            for lab in self.empty_labels:
                if lab.parentWidget() is obj:
                    lab.setGeometry(obj.rect().adjusted(0, 30, 0, 0))
        if et == event.Type.MouseButtonPress and obj in viewports + (self.results.viewport(),
                                                                    self.tiles.viewport()):
            btn = event.button()
            if btn == Qt.BackButton:
                self.back()
                return True
            if btn == Qt.ForwardButton:
                self.forward()
                return True
            if btn == Qt.MiddleButton and obj in viewports:
                view = self.details if obj is viewports[0] else self.icons
                path = self.model.filePath(view.indexAt(event.position().toPoint()))
                if path and os.path.isdir(path):
                    here = self.tabbar.currentIndex()
                    self.new_tab(path)               # opens in the background, like Explorer
                    self.tabbar.setCurrentIndex(here)
                    return True
        if et == event.Type.Wheel and obj in viewports and event.modifiers() & Qt.ControlModifier:
            steps = self.ZOOM_STEPS
            cur = self.view_mode if self.view_mode in steps else "details"
            i = steps.index(cur) + (1 if event.angleDelta().y() > 0 else -1)
            self.set_view(steps[max(0, min(len(steps) - 1, i))])
            return True
        if obj in (self.details, self.icons) and et == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.current_view().state() != \
                    QAbstractItemView.EditingState:
                paths = self.selected_paths()
                if len(paths) == 1 and os.path.isdir(paths[0]):
                    self.go(paths[0])
                else:
                    for p in paths[:20]:
                        if os.path.isfile(p):
                            self.open_file(p)
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.BackButton:
            self.back()
        elif event.button() == Qt.ForwardButton:
            self.forward()
        else:
            super().mousePressEvent(event)

    # ======================================================================
    # file operations
    # ======================================================================

    def run_op(self, kind, sources, dest=None):
        if not sources:
            return
        op = FileOpRunner(self, kind, sources, dest)
        self._ops = [o for o in getattr(self, "_ops", []) if not o.finished] + [op]
        op.start()

    def active_ops(self):
        return [o for o in getattr(self, "_ops", []) if not o.finished]

    def restore_state(self):
        """What a restart needs to reopen this window the same way."""
        return {"tabs": [t["loc"] for t in self.tabs_state if t.get("loc")],
                "current": self.current_location() or "",
                "geometry": bytes(self.saveGeometry().toBase64()).decode()}

    def copy_selection(self, cut=False):
        paths = self.selected_paths()
        if not paths:
            return
        md = QMimeData()
        urls = [QUrl.fromLocalFile(p) for p in paths]
        md.setUrls(urls)
        verb = "cut" if cut else "copy"
        md.setData("x-special/gnome-copied-files",
                   ("%s\n%s" % (verb, "\n".join(u.toString() for u in urls))).encode())
        md.setData("application/x-kde-cutselection", b"1" if cut else b"0")
        md.setText("\n".join(paths))
        QApplication.clipboard().setMimeData(md)
        self.status.setText("%d item%s %s" % (len(paths), "" if len(paths) == 1 else "s",
                                             "cut" if cut else "copied"))

    def paste(self):
        loc = self.current_location()
        if not loc or loc in PSEUDO:
            return
        md = QApplication.clipboard().mimeData()
        if md is None:
            return
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        cut = False
        if md.hasFormat("x-special/gnome-copied-files"):
            raw = bytes(md.data("x-special/gnome-copied-files")).decode(errors="replace")
            cut = raw.startswith("cut")
            if not paths:
                paths = [QUrl(u).toLocalFile() for u in raw.splitlines()[1:] if u]
        elif md.hasFormat("application/x-kde-cutselection"):
            cut = bytes(md.data("application/x-kde-cutselection")).strip() == b"1"
        if not paths and md.hasImage():
            img = md.imageData()
            name = fileops.unique_name(loc, "Pasted image.png", "n") \
                if os.path.exists(os.path.join(loc, "Pasted image.png")) else "Pasted image.png"
            img.save(os.path.join(loc, name))
            return
        if not paths:
            return
        self.run_op("move" if cut else "copy", paths, loc)
        if cut:
            QApplication.clipboard().clear()

    def delete(self, permanent=False):
        paths = self.selected_paths()
        if not paths:
            return
        n = len(paths)
        what = "“%s”" % os.path.basename(paths[0]) if n == 1 else "these %d items" % n
        in_trash = all(os.path.realpath(p).startswith(os.path.realpath(TRASH)) for p in paths)
        if permanent or in_trash:
            if QMessageBox.question(self, "Delete", "Permanently delete %s?\n\nThis can't be "
                                    "undone." % what) != QMessageBox.Yes:
                return
            self.run_op("delete", paths)
            return
        if explorer_setting(self.settings, "confirm_delete", False) and \
                QMessageBox.question(self, "Delete", "Move %s to the Trash?" % what) != QMessageBox.Yes:
            return
        self.run_op("trash", paths)

    def rename(self):
        view = self.current_view()
        idx = view.currentIndex()
        if self.selected_paths() and idx.isValid():
            view.edit(idx.siblingAtColumn(0))

    def new_folder(self):
        loc = self.current_location()
        if not loc or loc in PSEUDO:
            return
        name = "New folder"
        if os.path.exists(os.path.join(loc, name)):
            name = fileops.unique_name(loc, name, "n")
        path = os.path.join(loc, name)
        try:
            os.mkdir(path)
        except OSError as exc:
            QMessageBox.warning(self, "Explorer", "Couldn't create the folder: %s" % exc.strerror)
            return
        self.undo_stack.append({"kind": "mkdir", "path": path})
        self._edit_when_ready(path)

    def new_file(self, template=None, name="New text document.txt"):
        loc = self.current_location()
        if not loc or loc in PSEUDO:
            return
        if template:
            name = os.path.basename(template)
        if os.path.exists(os.path.join(loc, name)):
            name = fileops.unique_name(loc, name, "n")
        path = os.path.join(loc, name)
        try:
            if template:
                shutil.copy2(template, path)
            else:
                open(path, "x").close()
        except OSError as exc:
            QMessageBox.warning(self, "Explorer", "Couldn't create the file: %s" % exc.strerror)
            return
        self.undo_stack.append({"kind": "create", "path": path})
        self._edit_when_ready(path)

    def _edit_when_ready(self, path, tries=20):
        idx = self.model.index(path)
        if idx.isValid():
            view = self.current_view()
            view.setCurrentIndex(idx)
            view.scrollTo(idx)
            view.edit(idx)
        elif tries:
            QTimer.singleShot(100, lambda: self._edit_when_ready(path, tries - 1))

    def _fill_new_menu(self):
        m = self.new_menu
        m.clear()
        m.addAction(theme.get().icon("new-folder"), "Folder", self.new_folder)
        m.addAction(theme.file_info_icon("x.txt"), "Text document", lambda: self.new_file())
        tdir = places.templates_dir()
        if os.path.isdir(tdir):
            tpls = sorted(f for f in os.listdir(tdir) if not f.startswith("."))
            if tpls:
                m.addSeparator()
                for f in tpls[:30]:
                    p = os.path.join(tdir, f)
                    m.addAction(theme.file_info_icon(p), os.path.splitext(f)[0],
                                lambda p=p: self.new_file(template=p))

    def undo(self):
        if not self.undo_stack:
            return
        rec = self.undo_stack.pop()
        errors = fileops.undo(rec)
        if errors:
            QMessageBox.warning(self, "Undo", "Some changes couldn't be undone:\n\n" + "\n".join(errors[:10]))
        else:
            self.status.setText("Undone.")

    def empty_trash(self):
        if QMessageBox.question(self, "Empty Trash", "Permanently delete everything in the "
                                "Trash?") != QMessageBox.Yes:
            return
        base = os.path.dirname(TRASH)
        items = [os.path.join(TRASH, f) for f in os.listdir(TRASH)] if os.path.isdir(TRASH) else []
        info = os.path.join(base, "info")
        self.run_op("delete", items)
        if os.path.isdir(info):
            for f in os.listdir(info):
                try:
                    os.unlink(os.path.join(info, f))
                except OSError:
                    pass

    def properties(self):
        paths = self.selected_paths()
        loc = self.current_location()
        if not paths and loc and loc not in PSEUDO:
            paths = [loc]
        if paths:
            PropertiesDialog(paths, self).exec()

    def copy_path(self):
        paths = self.selected_paths() or [self.current_location() or ""]
        QApplication.clipboard().setText("\n".join(paths))

    # -- pins ----------------------------------------------------------------------------

    def _load_pins(self):
        raw = self.settings.value("explorer/pinned", "")
        try:
            pins = json.loads(raw) if raw else None
        except ValueError:
            pins = None
        if pins is None:
            pins = [p for _n, p, _i in places.user_dirs()[:4]]
        return [p for p in pins if isinstance(p, str)]

    def pin(self, path):
        pins = self._load_pins()
        if path not in pins:
            pins.append(path)
            self.settings.setValue("explorer/pinned", json.dumps(pins))
        self._drives_changed()

    def unpin(self, path):
        pins = [p for p in self._load_pins() if p != path]
        self.settings.setValue("explorer/pinned", json.dumps(pins))
        self._drives_changed()

    # ======================================================================
    # context menus
    # ======================================================================

    def _view_menu_at(self, view, pos):
        idx = view.indexAt(pos)
        if idx.isValid() and not view.selectionModel().isSelected(idx):
            view.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        paths = self.selected_paths() if idx.isValid() else []
        m = QMenu(self)
        if paths:
            self._item_menu(m, paths)
        else:
            self._background_menu(m)
        m.exec(view.viewport().mapToGlobal(pos))

    def _item_menu(self, m, paths):
        t = theme.get()
        one = paths[0] if len(paths) == 1 else None
        open_a = m.addAction(t.icon("open"), "Open", lambda: [self.activate(self.model.index(p)) for p in paths[:20]])
        f = open_a.font()
        f.setBold(True)
        open_a.setFont(f)
        if one and os.path.isdir(one):
            m.addAction("Open in new tab", lambda: self.new_tab(one))
            m.addAction("Open in new window", lambda: self.ctl.open_explorer(one))
        if one and os.path.isfile(one):
            ow = m.addMenu("Open with")
            mt = QMimeDatabase().mimeTypeForFile(one)
            for app in openwith.apps_for([mt.name()] + mt.allAncestors())[:12]:
                ow.addAction(_app_icon(app.icon), app.name, lambda a=app: a.launch([one]))
            ow.addSeparator()
            ow.addAction("Choose another app…", lambda: self.open_with(one))
        if self.in_trash():
            m.addSeparator()
            m.addAction(t.icon("undo") if not t.icon("undo").isNull() else t.icon("back"),
                        "Restore", lambda: self.restore(paths))
        if one and fileops.is_archive(one):
            m.addAction(t.icon("open"), "Extract all…", lambda: self.extract(one))
        m.addAction(t.icon("new"), "Compress to ZIP file", lambda: self.compress(paths))
        m.addSeparator()
        m.addAction(t.icon("cut"), "Cut", lambda: self.copy_selection(cut=True))
        m.addAction(t.icon("copy"), "Copy", lambda: self.copy_selection())
        if one and os.path.isdir(one):
            m.addAction(t.icon("paste"), "Paste into folder", lambda: self._paste_into(one))
        m.addSeparator()
        m.addAction("Copy as path", self.copy_path)
        if one and os.path.isdir(one):
            if one in self.nav.pinned:
                m.addAction(t.icon("pin"), "Unpin from Quick access", lambda: self.unpin(one))
            else:
                m.addAction(t.icon("pin"), "Pin to Quick access", lambda: self.pin(one))
            m.addAction(t.icon("terminal"), "Open in terminal", lambda: self.open_terminal(one))
            m.addAction(t.icon("search"), "Search in this folder (instant)",
                        lambda: self.ctl.open_search("", one))
            self._index_menu(m, one)
        if one:
            m.addAction(t.icon("rename"), "Rename", self.rename)
        m.addAction(t.icon("delete"), "Delete", lambda: self.delete())
        m.addSeparator()
        m.addAction(t.icon("properties"), "Properties", self.properties)

    # -- search index from the right-click menu ---------------------------------------

    def _index_menu(self, m, path):
        """Add 'Index for fast search' (or its management) for a folder or mount."""
        if not path or not os.path.isdir(path):
            return
        idx = getattr(self.ctl, "index", None)
        if idx is None:
            return
        t = theme.get()
        root = idx.indexed_root(path)
        covering = idx.covering(path)
        if root:
            sub = m.addMenu(t.icon("search"), "Fast search index")
            sub.addAction("Re-index now", lambda: idx.rescan(root, full=True))
            content = idx.content_enabled(root)
            a = sub.addAction("Include file contents",
                              lambda: idx.set_drive(root, True, not content))
            a.setCheckable(True)
            a.setChecked(content)
            sub.addSeparator()
            if root.startswith("folder-"):
                sub.addAction("Stop indexing this folder", lambda: idx.remove_folder(path))
            else:
                sub.addAction("Stop indexing this drive", lambda: idx.set_drive(root, False, False))
        elif covering:
            a = m.addAction(t.icon("search"), "Indexed for fast search (part of %s)"
                            % idx.drives[covering[0]].name)
            a.setEnabled(False)
        else:
            drive = next((d for d in self.ctl.drives if d.mount_point.rstrip("/") ==
                          path.rstrip("/")), None)
            if drive is not None:
                m.addAction(t.icon("search"), "Index this drive for fast search",
                            lambda: idx.set_drive(drive.id, True, False))
            else:
                m.addAction(t.icon("search"), "Index this folder for fast search",
                            lambda: self._start_index(path))

    def _start_index(self, path):
        self.ctl.index.add_folder(path)
        self.status.setText("Indexing %s in the background. Searches here get faster once "
                            "it's done." % (os.path.basename(path.rstrip("/")) or path))

    def _paste_into(self, folder):
        md = QApplication.clipboard().mimeData()
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md else []
        cut = md is not None and (
            bytes(md.data("x-special/gnome-copied-files")).startswith(b"cut")
            or bytes(md.data("application/x-kde-cutselection")).strip() == b"1")
        if paths:
            self.run_op("move" if cut else "copy", paths, folder)

    def _background_menu(self, m):
        t = theme.get()
        v = m.addMenu(t.icon("view"), "View")
        for key, (label, _s) in VIEW_MODES.items():
            a = v.addAction(label, lambda k=key: self.set_view(k))
            a.setCheckable(True)
            a.setChecked(self.view_mode == key)
        s = m.addMenu(t.icon("sort"), "Sort by")
        self.fill_sort_menu(s, with_group=False)
        g = m.addMenu("Group by")
        self.fill_group_menu(g)
        m.addAction(t.icon("refresh"), "Refresh", self.refresh)
        m.addSeparator()
        md = QApplication.clipboard().mimeData()
        a = m.addAction(t.icon("paste"), "Paste", self.paste)
        a.setEnabled(bool(md and (md.hasUrls() or md.hasImage())))
        a = m.addAction("Undo", self.undo)
        a.setEnabled(bool(self.undo_stack))
        m.addSeparator()
        new = m.addMenu(t.icon("new"), "New")
        self.new_menu.aboutToShow.emit()
        for act in self.new_menu.actions():
            new.addAction(act)
        loc = self.current_location()
        if self.in_trash():
            m.addSeparator()
            m.addAction("Restore all items", lambda: self.restore(
                [e.path for e in self.model.entries]))
            m.addAction(t.icon("trash"), "Empty Trash", self.empty_trash)
        m.addSeparator()
        m.addAction(t.icon("terminal"), "Open in terminal", lambda: self.open_terminal(loc))
        m.addAction(t.icon("pin"), "Pin to Quick access", lambda: self.pin(loc))
        self._index_menu(m, loc)
        m.addAction(t.icon("properties"), "Properties", self.properties)

    # ======================================================================
    # search
    # ======================================================================

    def _focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def run_search(self):
        text = self.search.text().strip()
        if not text:
            self._end_search()
            return
        loc = self.current_location()
        scope = loc if loc and loc not in PSEUDO else ""
        self.search_gen += 1
        gen = self.search_gen
        self.searching = True
        self.results_model.removeRows(0, self.results_model.rowCount())
        self.content.setCurrentWidget(self.results)
        indexed = self._indexed_scope(scope)
        if indexed is not None:
            conn = indexdb.connect(readonly=True)
            q = indexdb.parse_query(text)
            q.folder = scope
            rows, total, ms = indexdb.search(conn, q, limit=2000, drives=indexed or None)
            conn.close()
            self._search_batch(gen, [(r.path, r.is_dir, r.mtime, r.size) for r in rows])
            self.status.setText("%s results in %.0f ms (instant index)%s" % (
                format(total, ",") if total is not None else "20,000+", ms,
                "" if total is None or total <= len(rows) else " · showing %d" % len(rows)))
            return
        self.status.setText("Searching %s…" % location_label(scope))
        words = [w.lower() for w in text.split()]
        root = scope or os.path.expanduser("~")
        threading.Thread(target=self._walk_search, args=(gen, root, words), daemon=True).start()

    def _indexed_scope(self, scope):
        """Index roots to search if scope is inside an indexed drive or folder (or global)."""
        try:
            idx = self.ctl.index
        except AttributeError:
            return None
        if not scope:
            enabled = [i for i in idx.drives if idx.is_enabled(i)]
            return enabled or None
        return idx.covering(scope) or None

    def _walk_search(self, gen, root, words):
        batch, count, last = [], 0, time.monotonic()
        for dirpath, dirs, files in os.walk(root):
            if gen != self.search_gen:
                return
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in dirs + files:
                low = name.lower()
                if all(w in low for w in words):
                    p = os.path.join(dirpath, name)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    batch.append((p, os.path.isdir(p), st.st_mtime, st.st_size))
                    count += 1
            if batch and (time.monotonic() - last > 0.3 or len(batch) > 200):
                self.search_relay.batch.emit(gen, batch)
                batch, last = [], time.monotonic()
            if count >= 5000:
                break
        if batch:
            self.search_relay.batch.emit(gen, batch)
        self.search_relay.finished.emit(gen, count >= 5000)

    def _search_batch(self, gen, rows):
        if gen != self.search_gen:
            return
        t = theme.get()
        for path, is_dir, mtime, size in rows:
            name = QStandardItem(t.icon("folder") if is_dir else theme.file_info_icon(path),
                                 os.path.basename(path))
            name.setData(path, Qt.UserRole)
            folder = QStandardItem(os.path.dirname(path))
            date = QStandardItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)))
            sz = QStandardItem("" if is_dir else format_size(size))
            sz.setData(size, Qt.UserRole + 1)
            self.results_model.appendRow([name, folder, date, sz])
        self._update_status()

    def _search_done(self, gen, capped):
        if gen == self.search_gen:
            n = self.results_model.rowCount()
            self.status.setText("%d result%s%s" % (n, "" if n == 1 else "s",
                                                  " (stopped at 5,000)" if capped else ""))

    def _end_search(self, clear_box=False):
        if not self.searching and not clear_box:
            return
        self.search_gen += 1
        self.searching = False
        if clear_box and self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        loc = self.current_location()
        if loc and loc not in PSEUDO:
            self.content.setCurrentWidget(self.details if self.view_mode == "details" else self.icons)
        elif loc:
            self.content.setCurrentWidget(self.tiles)
        self._update_empty_message()
        self._update_status()

    def _open_result(self, idx):
        path = self.results_model.item(idx.row(), 0).data(Qt.UserRole)
        if os.path.isdir(path):
            self.go(path)
        else:
            self.open_file(path)

    def _results_menu(self, pos):
        paths = self.selected_paths()
        if not paths:
            return
        m = QMenu(self)
        m.addAction(theme.get().icon("open"), "Open", lambda: [
            self.go(p) if os.path.isdir(p) else self.open_file(p) for p in paths[:20]])
        m.addAction(theme.get().icon("folder"), "Open file location", lambda: (
            self.go(os.path.dirname(paths[0])), self.select_paths([paths[0]])))
        m.addSeparator()
        m.addAction(theme.get().icon("copy"), "Copy", lambda: self.copy_selection())
        m.addAction("Copy as path", self.copy_path)
        m.addAction(theme.get().icon("delete"), "Delete", lambda: self.delete())
        m.addAction(theme.get().icon("properties"), "Properties", self.properties)
        m.exec(self.results.viewport().mapToGlobal(pos))

    # ======================================================================

    def closeEvent(self, event):
        self.settings.setValue("explorer/geometry", self.saveGeometry())
        self.settings.setValue("explorer/columns2", self.details.header().saveState())
        self.settings.setValue("explorer/split", self.split.sizes())
        if self._drives_changed in self.ctl.drive_listeners:
            self.ctl.drive_listeners.remove(self._drives_changed)
        super().closeEvent(event)


class _TypeAhead:
    """Explorer-style type-to-select: matches the start of item names (never group
    headers); typing the same letter again cycles through items starting with it."""

    def __init__(self, win):
        self._win = win
        self._buf = ""
        self._last = 0.0

    def keyboardSearch(self, text):
        if not text or not text.isprintable():
            return
        now = time.monotonic()
        interval = QApplication.keyboardInputInterval() / 1000.0
        if now - self._last > interval:
            self._buf = ""
        self._last = now
        self._buf += text.lower()
        self._win._type_ahead(self, self._buf)


class _TypeAheadTree(_TypeAhead, QTreeView):
    def __init__(self, win):
        QTreeView.__init__(self)
        _TypeAhead.__init__(self, win)


class _TypeAheadList(_TypeAhead, QListView):
    def __init__(self, win):
        QListView.__init__(self)
        _TypeAhead.__init__(self, win)


class FileOpRunner:
    """Runs a FileOperation with a delayed progress dialog, error reporting and undo."""

    def __init__(self, win, kind, sources, dest):
        self.win = win
        self.op = fileops.FileOperation(kind, sources, dest, win)
        self.dialog = OperationDialog(self.op, location_label(dest) if dest else "", win)
        self.op.done.connect(self._done)
        self.finished = False

    def start(self):
        self.op.start()
        QTimer.singleShot(500, lambda: (not self.finished) and self.dialog.show())

    def _done(self, result):
        self.finished = True
        self.dialog.close()
        win = self.win
        if result.get("undo"):
            win.undo_stack.append(result["undo"])
        errors = [e for e in result["errors"] if not e.startswith("NOTRASH:")]
        notrash = [e[8:] for e in result["errors"] if e.startswith("NOTRASH:")]
        if notrash:
            n = len(notrash)
            if QMessageBox.question(win, "Delete", "%s can't be moved to the Trash (network drives "
                                    "and some disks don't have one).\n\nDelete %s permanently?"
                                    % ("This item" if n == 1 else "%d items" % n,
                                       "it" if n == 1 else "them")) == QMessageBox.Yes:
                win.run_op("delete", notrash)
        if errors and not result.get("cancelled"):
            QMessageBox.warning(win, "Explorer", "Some items couldn't be processed:\n\n"
                                + "\n".join(errors[:15]))
        if self.op.kind in ("copy", "move") and result.get("created"):
            if self.op.dest == win.current_location():
                win.select_paths(result["created"])
        win.ctl.refresh_soon()


def recent_files(limit=20):
    path = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "recently-used.xbel")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    items = []
    for b in root.iter("bookmark"):
        href = b.get("href", "")
        if not href.startswith("file://"):
            continue
        p = QUrl(href).toLocalFile()
        stamp = b.get("modified") or b.get("visited") or ""
        try:
            ts = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            ts = 0
        if os.path.isfile(p):
            items.append((p, ts))
    items.sort(key=lambda x: -x[1])
    seen, out = set(), []
    for p, ts in items:
        if p not in seen:
            seen.add(p)
            out.append((p, ts))
        if len(out) >= limit:
            break
    return out


def _prefill_location(dlg, address):
    m = re.match(r"^(\w+)://(?:([^@/]+)@)?([^/:]+)(?::(\d+))?(/.*)?$", address)
    if not m:
        return
    if m.group(1).lower().startswith("http"):
        dlg.url.setText(address)
        return
    if m.group(2):
        dlg.user.setText(m.group(2))
    dlg.host.setText(m.group(3))
    if m.group(4):
        dlg.port.setValue(int(m.group(4)))
    if m.group(5):
        dlg.path.setText(m.group(5))


def _app_icon(name):
    from PySide6.QtGui import QIcon
    if name and os.path.isabs(name):
        return QIcon(name)
    return QIcon.fromTheme(name or "application-x-executable")


def _vsep():
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setFixedHeight(22)
    f.setStyleSheet("color: palette(mid);")
    return f
