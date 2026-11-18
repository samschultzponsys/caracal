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

TRAY_STYLES = (("color", "Color tile"), ("light", "Simple (for dark panels)"),
               ("dark", "Simple (for light panels)"))
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


# Glyph colors for the monochrome tray styles: (not connected, connected)
MONO_TINTS = {"light": ("#9CA3AF", "#34D399"),   # for dark panels
              "dark": ("#6B7280", "#059669")}    # for light panels


def tray_icon(style="color", state=None, lit=True):
    """Tray icon in the chosen style.

    lit=False (nothing connected): grey in every style. lit=True: green (the color icon,
    or a green glyph for the white/dark styles). state="failed" adds a red badge.
    """
    base = QIcon()
    tint = None
    if style in MONO_TINTS:
        p = _find("tray", "tray-%s.svg" % style)
        if p:
            base = QIcon(p)
            tint = MONO_TINTS[style][1 if lit else 0]
    elif not lit:
        p = _find("tray", "tray-color-idle.svg")
        if p:
            base = QIcon(p)
    if base.isNull():
        base = app_icon()
    badge = BADGE_COLORS.get(state)
    if not tint and not badge:
        return base
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        src = base.pixmap(size, size)
        if src.isNull():
            continue
        pm = QPixmap(src)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if tint:
            # Recolor the glyph, keeping its shape (alpha).
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), QColor(tint))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if badge:
            r = max(3.0, size * 0.2)
            p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 24)))
            p.setBrush(QColor(badge))
            p.drawEllipse(QRectF(size - 2 * r - 0.5, size - 2 * r - 0.5, 2 * r, 2 * r))
        p.end()
        icon.addPixmap(pm)
    return icon if not icon.isNull() else base
