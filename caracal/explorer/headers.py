# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Details-view header like Explorer's: hover a column to get a ▾ filter button, a ✓ on
filtered columns, a checkbox filter list, and the "Choose Details" dialog."""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from caracal.explorer import theme
from caracal.explorer.columns import CATEGORIES, COLUMNS, NAME

ARROW_W = 16


class FilterHeader(QHeaderView):
    filterRequested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setMouseTracking(True)
        self.setSectionsClickable(True)
        self.setSectionsMovable(True)
        self.setHighlightSections(False)
        self.filtered = set()
        self._hover = -1
        self._swallow = False

    def _arrow_rect(self, logical):
        x = self.sectionViewportPosition(logical)
        w = self.sectionSize(logical)
        return QRect(x + w - ARROW_W, 0, ARROW_W, self.height())

    def mouseMoveEvent(self, ev):
        idx = self.logicalIndexAt(ev.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.viewport().update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        self._hover = -1
        self.viewport().update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint()
        idx = self.logicalIndexAt(pos)
        if ev.button() == Qt.LeftButton and idx >= 0 and self._arrow_rect(idx).contains(pos) \
                and self.cursor().shape() != Qt.SplitHCursor:
            self._swallow = True
            r = self._arrow_rect(idx)
            self.filterRequested.emit(idx, self.viewport().mapToGlobal(
                QPoint(r.right() - 200, r.bottom())))
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._swallow:
            self._swallow = False
            return
        super().mouseReleaseEvent(ev)

    def paintSection(self, painter, rect, logical):
        painter.save()
        super().paintSection(painter, rect, logical)
        painter.restore()
        filtered = logical in self.filtered
        if logical != self._hover and not filtered:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRect(rect.right() - ARROW_W + 1, rect.top(), ARROW_W - 1, rect.height())
        if logical == self._hover:
            painter.fillRect(r, theme.color("hover"))
            painter.setPen(QPen(theme.color("border"), 1))
            painter.drawLine(r.topLeft(), r.bottomLeft())
        cx, cy = r.center().x(), r.center().y() + 1
        color = theme.color("text")
        if filtered:
            painter.setPen(QPen(color, 1.4))   # check mark
            painter.drawPolyline(QPolygon([QPoint(cx - 4, cy - 6), QPoint(cx - 2, cy - 4),
                                           QPoint(cx + 2, cy - 8)]))
            cy += 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygon([QPoint(cx - 3, cy - 1), QPoint(cx + 4, cy - 1),
                                      QPoint(cx, cy + 3)]))
        painter.restore()


class FilterPopup(QFrame):
    """Checkbox list of a column's values; ticking applies the filter straight away."""

    def __init__(self, title, values, selected, on_change, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("FilterPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self.on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        if len(values) > 12:
            self.find = QLineEdit()
            self.find.setPlaceholderText("Search %s" % title)
            self.find.setClearButtonEnabled(True)
            self.find.textChanged.connect(self._narrow)
            lay.addWidget(self.find)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setFrameShape(QFrame.NoFrame)
        for label, count in values:
            it = QListWidgetItem("%s (%d)" % (label, count))
            it.setData(Qt.UserRole, label)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if label in selected else Qt.Unchecked)
            self.list.addItem(it)
        if not values:
            lay.addWidget(QLabel("No values in this folder."))
        rows = min(max(len(values), 1), 14)
        self.list.setFixedHeight(rows * (self.list.sizeHintForRow(0) if values else 20) + 6)
        self.list.itemChanged.connect(lambda _i: self._apply())
        self.list.itemClicked.connect(self._toggle_row)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        clear = QPushButton("Clear filter")
        clear.clicked.connect(self._clear)
        row.addStretch(1)
        row.addWidget(clear)
        lay.addLayout(row)
        self.setMinimumWidth(220)

    def _toggle_row(self, it):
        # clicking the text (not just the box) toggles it, like Explorer
        pos = self.list.viewport().mapFromGlobal(QCursor.pos())
        if pos.x() - self.list.visualItemRect(it).left() > 22:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _narrow(self, text):
        t = text.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def checked(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _apply(self):
        self.on_change(self.checked())

    def _clear(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self._apply()
        self.close()


class ChooseColumnsDialog(QDialog):
    """Explorer's "Choose Details": tick the columns to show in this view."""

    def __init__(self, visible, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Details")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Select the details you want to display for the items in this folder."))
        self.find = QLineEdit()
        self.find.setPlaceholderText("Search details")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._narrow)
        lay.addWidget(self.find)
        self.list = QListWidget()
        for cat in CATEGORIES:
            head = QListWidgetItem(cat)
            head.setFlags(Qt.NoItemFlags)
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            self.list.addItem(head)
            for i, c in enumerate(COLUMNS):
                if c.category != cat:
                    continue
                it = QListWidgetItem(c.title if c.key != "track" else "# (track number)")
                it.setData(Qt.UserRole, i)
                flags = Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
                it.setFlags(flags if i != NAME else Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if i in visible or i == NAME else Qt.Unchecked)
                self.list.addItem(it)
        lay.addWidget(self.list, 1)
        note = QLabel("Music and video details need ffprobe (ffmpeg); PDF details need pdfinfo "
                      "(poppler-utils). Picture, music and document details are read in the "
                      "background only while one of their columns is in use.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(360, 520)

    def _narrow(self, text):
        t = text.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) is not None:
                it.setHidden(bool(t) and t not in it.text().lower())

    def chosen(self):
        out = set()
        for i in range(self.list.count()):
            it = self.list.item(i)
            col = it.data(Qt.UserRole)
            if col is not None and it.checkState() == Qt.Checked:
                out.add(col)
        out.add(NAME)
        return out
