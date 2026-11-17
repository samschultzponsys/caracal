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


def tray_icon(style="color", state=None, lit=True):
    """Tray icon in the chosen style.

    lit=False (no drive connected) shows the icon dimmed: the color icon's network
    nodes go dark, the white/dark icons fade. state="failed" adds a red badge.
    """
    base = QIcon()
    if style in ("light", "dark"):
        p = _find("tray", "tray-%s.svg" % style)
        if p:
            base = QIcon(p)
    elif not lit:
        p = _find("tray", "tray-color-idle.svg")
        if p:
            base = QIcon(p)
    if base.isNull():
        base = app_icon()
    fade = not lit and style in ("light", "dark")
    badge = BADGE_COLORS.get(state)
    if not fade and not badge:
        return base
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        src = base.pixmap(size, size)
        if src.isNull():
            continue
        pm = QPixmap(src.size())
        pm.setDevicePixelRatio(src.devicePixelRatio())
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(0.4 if fade else 1.0)
        p.drawPixmap(0, 0, src)
        p.setOpacity(1.0)
        if badge:
            r = max(3.0, size * 0.2)
            p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 24)))
            p.setBrush(QColor(badge))
            p.drawEllipse(QRectF(size - 2 * r - 0.5, size - 2 * r - 0.5, 2 * r, 2 * r))
        p.end()
        icon.addPixmap(pm)
    return icon if not icon.isNull() else base
