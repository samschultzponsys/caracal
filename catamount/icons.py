# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""App and tray icons."""

import os

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

from catamount import APP_ID

_HERE = os.path.dirname(os.path.abspath(__file__))
# Installed location first, then the source checkout.
_ICON_ROOTS = ("/usr/share/catamount/icons", os.path.join(_HERE, "..", "data", "icons"))

TRAY_STYLES = (("color", "Color"), ("light", "White (for dark panels)"),
               ("dark", "Dark (for light panels)"))
BADGE_COLORS = {"failed": "#DC2626", "connected": "#16A34A"}


def _find(*parts):
    for root in _ICON_ROOTS:
        p = os.path.join(root, *parts)
        if os.path.isfile(p):
            return p
    return None


def app_icon():
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull():
        p = _find("hicolor", "scalable", "apps", APP_ID + ".svg")
        icon = QIcon(p) if p else QIcon()
    if icon.isNull():
        style = QApplication.style()
        icon = QIcon.fromTheme("folder-remote", style.standardIcon(QStyle.SP_DriveNetIcon))
    return icon


def tray_icon(style="color", state=None):
    """Tray icon in the chosen style, with an optional status badge."""
    base = QIcon()
    if style in ("light", "dark"):
        p = _find("tray", "tray-%s.svg" % style)
        if p:
            base = QIcon(p)
    if base.isNull():
        base = app_icon()
    if state not in BADGE_COLORS:
        return base
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pm = QPixmap(base.pixmap(size, size))
        if pm.isNull():
            continue
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r = max(3.0, size * 0.2)
        p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 24)))
        p.setBrush(QColor(BADGE_COLORS[state]))
        p.drawEllipse(QRectF(size - 2 * r - 0.5, size - 2 * r - 0.5, 2 * r, 2 * r))
        p.end()
        icon.addPixmap(pm)
    return icon if not icon.isNull() else base
