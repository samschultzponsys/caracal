# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Windows 10-style ribbon (File / Home / Share / View, collapsed until clicked) and the
group header delegate ("Yesterday", "Earlier this week" ...)."""

import os
import threading
import zipfile

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QStackedWidget,
    QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from caracal import __version__
from caracal.explorer import theme
from caracal.explorer.model import GROUP_ROLE


class GroupDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(GROUP_ROLE) and index.column() != 0:
            return   # the header row is drawn once, spanning the first column
        if index.data(GROUP_ROLE):
            painter.save()
            r = option.rect
            if option.state & QStyle.State_MouseOver:
                painter.fillRect(r, theme.color("hover"))
            accent = theme.color("group") if "group" in theme.get().colors() else theme.color("text")
            expanded = option.state & QStyle.State_Open
            painter.setPen(QPen(accent, 1.5))
            cx, cy = r.left() + 10, r.center().y()
            painter.setRenderHint(QPainter.Antialiasing)
            if expanded:
                painter.drawPolyline([QPoint(cx - 4, cy - 2),
                                      QPoint(cx, cy + 2),
                                      QPoint(cx + 4, cy - 2)])
            else:
                painter.drawPolyline([QPoint(cx - 2, cy - 4),
                                      QPoint(cx + 2, cy),
                                      QPoint(cx - 2, cy + 4)])
            f = index.data(Qt.FontRole) or option.font
            painter.setFont(f)
            text_rect = QRect(r.left() + 20, r.top(), r.width() - 20, r.height())
            painter.setPen(accent)
            text = index.data(Qt.DisplayRole) or ""
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            w = painter.fontMetrics().horizontalAdvance(text)
            painter.setPen(QPen(theme.color("border"), 1))
            y = cy + 1
            painter.drawLine(r.left() + 28 + w, y, r.right() - 8, y)
            painter.restore()
            return
        if index.column() == 0 and index.parent().isValid():
            # items under a group header are indented a little, like Explorer
            from PySide6.QtWidgets import QStyleOptionViewItem
            opt = QStyleOptionViewItem(option)
            opt.rect = option.rect.adjusted(10, 0, 0, 0)
            painter.fillRect(option.rect.adjusted(0, 0, -option.rect.width() + 10, 0),
                             option.palette.base())
            super().paint(painter, opt, index)
            return
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        sz = super().sizeHint(option, index)
        rh = int(theme.get().data.get("row_height", 24))
        if index.data(GROUP_ROLE):
            return QSize(sz.width(), rh + 8)
        return QSize(sz.width(), max(rh, sz.height()))


class Ribbon(QWidget):
    """Collapsed by default like Explorer: click a tab to drop the panel down; it hides
    again after a command unless pinned (Ctrl+F1 or the ⌃ button)."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setObjectName("CommandBar")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        strip = QWidget()
        strip.setObjectName("RibbonTabs")
        h = QHBoxLayout(strip)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(0)
        # Quick Access Toolbar (Explorer has it in the title bar, top left).
        self.qat = QWidget()
        self.qat.setObjectName("QAT")
        self.qat_lay = QHBoxLayout(self.qat)
        self.qat_lay.setContentsMargins(0, 0, 6, 0)
        self.qat_lay.setSpacing(0)
        h.addWidget(self.qat)
        self._build_qat()
        self.file_btn = QToolButton()
        self.file_btn.setText("File")
        self.file_btn.setObjectName("RibbonFile")
        self.file_btn.setPopupMode(QToolButton.InstantPopup)
        self.file_menu = QMenu(self)
        self.file_menu.aboutToShow.connect(self._fill_file_menu)
        self.file_btn.setMenu(self.file_menu)
        self.file_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        h.addWidget(self.file_btn)
        self.tab_btns = []
        for i, name in enumerate(("Home", "Share", "View")):
            b = QToolButton()
            b.setObjectName("RibbonTab")
            b.setText(name)
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Show the %s commands" % name)
            b.clicked.connect(lambda _c=False, i=i: self.toggle(i))
            self.tab_btns.append(b)
            h.addWidget(b)
        h.addStretch(1)
        self.pin_btn = QToolButton()
        self.pin_btn.setAutoRaise(True)
        self.pin_btn.setToolTip("Expand / minimize the ribbon (Ctrl+F1)")
        self.pin_btn.clicked.connect(self.toggle_pinned)
        h.addWidget(self.pin_btn)
        v.addWidget(strip)
        self.panel = QStackedWidget()
        self.panel.setObjectName("RibbonPanel")
        for page in (self._home(), self._share(), self._view()):
            self.panel.addWidget(page)
        v.addWidget(self.panel)
        self.pinned = str(win.settings.value("explorer/ribbon_pinned", "false")).lower() == "true"
        self.panel.setVisible(self.pinned)
        if self.pinned:
            self.tab_btns[0].setChecked(True)
        self._update_pin_icon()
        from PySide6.QtGui import QAction, QKeySequence
        a = QAction(win)
        a.setShortcut(QKeySequence("Ctrl+F1"))
        a.triggered.connect(self.toggle_pinned)
        win.addAction(a)

    # -- quick access toolbar --------------------------------------------------------

    QAT_COMMANDS = (("properties", "properties", "Properties (Alt+Enter)"),
                    ("new-folder", "new-folder", "New folder (Ctrl+Shift+N)"),
                    ("undo", "back", "Undo (Ctrl+Z)"),
                    ("delete", "delete", "Delete"),
                    ("rename", "rename", "Rename (F2)"),
                    ("refresh", "refresh", "Refresh (F5)"),
                    ("terminal", "terminal", "Open in terminal"))
    QAT_DEFAULT = ["properties", "new-folder"]

    def _qat_selection(self):
        import json
        try:
            v = json.loads(self.win.settings.value("explorer/qat", "") or "null")
        except ValueError:
            v = None
        return v if isinstance(v, list) else list(self.QAT_DEFAULT)

    def _qat_run(self, key):
        w = self.win
        {"properties": w.properties, "new-folder": w.new_folder, "undo": w.undo,
         "delete": lambda: w.delete(), "rename": w.rename, "refresh": w.refresh,
         "terminal": lambda: w.open_terminal(w.current_location())}[key]()

    def _build_qat(self):
        while self.qat_lay.count():
            wdg = self.qat_lay.takeAt(0).widget()
            if wdg:
                wdg.hide()
                wdg.deleteLater()
        chosen = self._qat_selection()
        for key, slot, tip in self.QAT_COMMANDS:
            if key in chosen:
                b = QToolButton()
                b.setIcon(theme.get().icon(slot))
                b.setIconSize(QSize(16, 16))
                b.setToolTip(tip)
                b.setAutoRaise(True)
                b._slot = slot
                b._qat_key = key
                b.clicked.connect(lambda _c=False, k=key: self._qat_run(k))
                self.qat_lay.addWidget(b)
        more = QToolButton()
        more.setText("▾")
        more.setAutoRaise(True)
        more.setToolTip("Customize Quick Access Toolbar")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        menu = QMenu(more)
        for key, _slot, tip in self.QAT_COMMANDS:
            a = menu.addAction(tip.split(" (")[0], lambda k=key: self._qat_toggle(k))
            a.setCheckable(True)
            a.setChecked(key in chosen)
        more.setMenu(menu)
        self.qat_lay.addWidget(more)
        if hasattr(self.win, "status"):
            self.update_qat_state()

    def update_qat_state(self):
        """Grey out commands that can't work here (like Explorer does on This PC)."""
        from caracal.explorer.widgets import PSEUDO
        w = self.win
        loc = w.current_location() or ""
        real = bool(loc) and loc not in PSEUDO and not w.searching
        has_sel = bool(w.selected_paths())
        can = {"properties": real or has_sel, "new-folder": real, "delete": has_sel,
               "rename": has_sel, "refresh": bool(loc), "terminal": real,
               "undo": bool(w.undo_stack)}
        for i in range(self.qat_lay.count()):
            b = self.qat_lay.itemAt(i).widget()
            key = getattr(b, "_qat_key", None)
            if key:
                b.setEnabled(can.get(key, True))
                if key == "new-folder" and not real:
                    b.setToolTip("New folder (open a folder first)")
                elif key == "new-folder":
                    b.setToolTip("New folder (Ctrl+Shift+N)")

    def _qat_toggle(self, key):
        import json
        chosen = self._qat_selection()
        chosen = [k for k in chosen if k != key] if key in chosen else chosen + [key]
        self.win.settings.setValue("explorer/qat", json.dumps(chosen))
        self._build_qat()

    # -- behavior -----------------------------------------------------------------

    def toggle(self, i):
        open_now = self.panel.isVisible() and self.panel.currentIndex() == i
        for j, b in enumerate(self.tab_btns):
            b.setChecked(j == i and not (open_now and not self.pinned))
        if open_now and not self.pinned:
            self.panel.hide()
            return
        self.panel.setCurrentIndex(i)
        self.panel.show()
        self._refresh_checks()

    def toggle_pinned(self):
        self.pinned = not self.pinned
        self.win.settings.setValue("explorer/ribbon_pinned", self.pinned)
        self.panel.setVisible(self.pinned)
        for j, b in enumerate(self.tab_btns):
            b.setChecked(self.pinned and j == self.panel.currentIndex())
        self._update_pin_icon()

    def _update_pin_icon(self):
        self.pin_btn.setText("⌃" if self.pinned else "⌄")

    def _after(self):
        if not self.pinned:
            self.panel.hide()
            for b in self.tab_btns:
                b.setChecked(False)

    def _cmd(self, fn):
        def run():
            self._after()
            QTimer.singleShot(0, fn)
        return run

    # -- building blocks -------------------------------------------------------------

    def _big(self, slot, text, fn=None, menu=None):
        b = QToolButton()
        b.setIcon(theme.get().icon(slot))
        b.setIconSize(QSize(32, 32))
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setAutoRaise(True)
        b.setMinimumWidth(58)
        b._slot = slot
        if fn:
            b.clicked.connect(self._cmd(fn))
        if menu is not None:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        return b

    def _small(self, slot, text, fn=None, menu=None):
        b = QToolButton()
        if slot:
            b.setIcon(theme.get().icon(slot))
        b.setIconSize(QSize(16, 16))
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setAutoRaise(True)
        b._slot = slot
        if fn:
            b.clicked.connect(self._cmd(fn))
        if menu is not None:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        return b

    def _group(self, title, big=(), small=()):
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(4, 2, 4, 0)
        g.setHorizontalSpacing(2)
        g.setVerticalSpacing(0)
        col = 0
        for b in big:
            g.addWidget(b, 0, col, 3, 1)
            col += 1
        for i, b in enumerate(small):
            g.addWidget(b, i % 3, col + i // 3)
        lab = QLabel(title)
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("color: %s; font-size: 8pt;" % theme.get().colors()["subtle"])
        g.addWidget(lab, 3, 0, 1, col + (len(small) + 2) // 3 or 1)
        return box

    def _page(self, groups):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(0)
        for i, gr in enumerate(groups):
            if i:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: %s;" % theme.get().colors()["border"])
                h.addWidget(sep)
            h.addWidget(gr)
        h.addStretch(1)
        return w

    def _home(self):
        w = self.win
        move_menu, copy_menu = QMenu(self), QMenu(self)
        move_menu.aboutToShow.connect(lambda: self._fill_dest_menu(move_menu, "move"))
        copy_menu.aboutToShow.connect(lambda: self._fill_dest_menu(copy_menu, "copy"))
        new_menu = QMenu(self)
        new_menu.aboutToShow.connect(lambda: self._fill_new(new_menu))
        open_menu = QMenu(self)
        open_menu.aboutToShow.connect(lambda: self._fill_open(open_menu))
        return self._page([
            self._group("Clipboard",
                        [self._big("pin", "Pin to Quick\naccess", self._pin_selected),
                         self._big("copy", "Copy", lambda: w.copy_selection()),
                         self._big("paste", "Paste", w.paste)],
                        [self._small("cut", "Cut", lambda: w.copy_selection(cut=True)),
                         self._small("copy", "Copy path", w.copy_path)]),
            self._group("Organize",
                        [self._big("folder", "Move\nto ▾", menu=move_menu),
                         self._big("copy", "Copy\nto ▾", menu=copy_menu),
                         self._big("delete", "Delete", lambda: w.delete()),
                         self._big("rename", "Rename", w.rename)]),
            self._group("New", [self._big("new-folder", "New\nfolder", w.new_folder)],
                        [self._small("new", "New item ▾", menu=new_menu)]),
            self._group("Open", [self._big("properties", "Properties", w.properties)],
                        [self._small("open", "Open ▾", menu=open_menu),
                         self._small("terminal", "Terminal",
                                     lambda: w.open_terminal(w.current_location()))]),
            self._group("Select", [], [self._small("", "Select all", w.select_all),
                                       self._small("", "Select none",
                                                   lambda: w.current_view().clearSelection()),
                                       self._small("", "Invert selection", w.invert_selection)]),
        ])

    def _share(self):
        w = self.win
        return self._page([
            self._group("Send", [self._big("new", "Zip", lambda: w.compress(w.selected_paths())),
                                 self._big("copy", "Copy\npath", w.copy_path)]),
            self._group("Network", [self._big("add-location", "Add network\nlocation",
                                              lambda: w.add_network_location()),
                                    self._big("network", "Manage\ndrives", w.ctl.show_window),
                                    self._big("search", "Instant\nsearch",
                                              lambda: w.ctl.open_search("", w.current_location() or ""))]),
        ])

    def _view(self):
        w = self.win
        self.ext_cb = QCheckBox("File name extensions")
        self.ext_cb.toggled.connect(lambda on: (not w.model.hide_ext) != on and w.toggle_ext())
        self.hidden_cb = QCheckBox("Hidden items")
        self.hidden_cb.toggled.connect(lambda on: self._set_hidden(on))
        sort_menu, group_menu, theme_menu = QMenu(self), QMenu(self), QMenu(self)
        theme_menu.aboutToShow.connect(lambda: (theme_menu.clear(), w.fill_theme_menu(theme_menu)))
        sort_menu.aboutToShow.connect(lambda: (sort_menu.clear(), w.fill_sort_menu(sort_menu, False)))
        group_menu.aboutToShow.connect(lambda: (group_menu.clear(), w.fill_group_menu(group_menu)))
        cols_menu = QMenu(self)
        cols_menu.aboutToShow.connect(lambda: (cols_menu.clear(), w.fill_columns_menu(cols_menu)))
        return self._page([
            self._group("Panes", [self._big("view", "Navigation\npane", w.toggle_nav),
                                  self._big("preview-pane", "Preview\npane", w.toggle_preview)]),
            self._group("Layout", [], [
                self._small("icons-view", "Extra large icons", lambda: w.set_view("xl")),
                self._small("icons-view", "Large icons", lambda: w.set_view("large")),
                self._small("icons-view", "Medium icons", lambda: w.set_view("medium")),
                self._small("icons-view", "Small icons", lambda: w.set_view("small")),
                self._small("details-view", "List", lambda: w.set_view("list")),
                self._small("details-view", "Details", lambda: w.set_view("details"))]),
            self._group("Current view", [self._big("sort", "Sort\nby ▾", menu=sort_menu)],
                        [self._small("view", "Group by ▾", menu=group_menu),
                         self._small("details-view", "Add columns ▾", menu=cols_menu),
                         self._small("details-view", "Size all columns to fit",
                                     w.size_columns_to_fit),
                         self._small("view", "Clear filters", w.clear_filters)]),
            self._group("Show/hide", [], [self.ext_cb, self.hidden_cb,
                                          self._small("view", "Theme ▾", menu=theme_menu)]),
            self._group("", [self._big("settings", "Options", w.ctl.preferences)]),
        ])

    def _refresh_checks(self):
        w = self.win
        for cb, val in ((self.ext_cb, not w.model.hide_ext), (self.hidden_cb, w.model.show_hidden)):
            cb.blockSignals(True)
            cb.setChecked(val)
            cb.blockSignals(False)

    def _set_hidden(self, on):
        if on != self.win.model.show_hidden:
            self.win.toggle_hidden()

    # -- menus ----------------------------------------------------------------------

    def _fill_file_menu(self):
        w = self.win
        m = self.file_menu
        m.clear()
        m.addAction("Open new window", lambda: w.ctl.open_explorer(w.current_location()))
        m.addAction("Open new tab", lambda: w.new_tab(w.current_location()))
        m.addAction("Open in terminal", lambda: w.open_terminal(w.current_location()))
        m.addSeparator()
        m.addAction("Change folder and search options", w.ctl.preferences)
        m.addAction("Manage network drives", w.ctl.show_window)
        m.addSeparator()
        m.addAction("Check for updates…", w.ctl.check_updates)
        m.addAction("What's new (changelog)", w.ctl.show_changelog)
        m.addAction("About Caracal %s" % __version__, w.ctl.show_about)
        m.addSeparator()
        m.addAction("Close", w.close)
        m.addAction("Exit Caracal (stop background tasks)", w.ctl.quit)

    def _fill_dest_menu(self, m, kind):
        m.clear()
        w = self.win
        for p in w.nav.pinned:
            m.addAction(theme.get().icon("folder"), os.path.basename(p.rstrip("/")) or p,
                        lambda p=p: w.run_op(kind, w.selected_paths(), p))
        m.addSeparator()
        m.addAction("Choose location…", lambda: self._choose_dest(kind))

    def _choose_dest(self, kind):
        w = self.win
        d = QFileDialog.getExistingDirectory(w, "Move items to" if kind == "move" else "Copy items to",
                                             w.current_location() or os.path.expanduser("~"))
        if d:
            w.run_op(kind, w.selected_paths(), d)

    def _fill_new(self, m):
        m.clear()
        self.win.new_menu.aboutToShow.emit()
        for a in self.win.new_menu.actions():
            m.addAction(a)

    def _fill_open(self, m):
        m.clear()
        w = self.win
        paths = w.selected_paths()
        m.addAction("Open", lambda: [w.open_file(p) if os.path.isfile(p) else w.go(p) for p in paths[:20]])
        if len(paths) == 1 and os.path.isfile(paths[0]):
            m.addAction("Open with…", lambda: w.open_with(paths[0]))

    def _pin_selected(self):
        w = self.win
        for p in (w.selected_paths() or [w.current_location()]):
            if p and os.path.isdir(p):
                w.pin(p)

    def _zip(self):
        w = self.win
        paths = w.selected_paths()
        if not paths:
            return
        dest_dir = os.path.dirname(paths[0])
        base = os.path.splitext(os.path.basename(paths[0]))[0] if len(paths) == 1 else "Archive"
        target = os.path.join(dest_dir, base + ".zip")
        n = 2
        while os.path.exists(target):
            target = os.path.join(dest_dir, "%s (%d).zip" % (base, n))
            n += 1

        def work():
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    if os.path.isdir(p):
                        for root, _dirs, files in os.walk(p):
                            for f in files:
                                full = os.path.join(root, f)
                                zf.write(full, os.path.relpath(full, dest_dir))
                    else:
                        zf.write(p, os.path.basename(p))
        w.status.setText("Compressing…")
        threading.Thread(target=work, daemon=True).start()
