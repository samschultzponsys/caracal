# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Light / dark / follow-system theme handling."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = (("system", "Follow system"), ("light", "Light"), ("dark", "Dark"))

_original = {}


def _dark_palette():
    p = QPalette()
    base, alt, text = QColor(30, 30, 30), QColor(45, 45, 45), QColor(230, 230, 230)
    window = QColor(37, 37, 38)
    accent = QColor(64, 128, 220)
    for group in (QPalette.Active, QPalette.Inactive):
        p.setColor(group, QPalette.Window, window)
        p.setColor(group, QPalette.WindowText, text)
        p.setColor(group, QPalette.Base, base)
        p.setColor(group, QPalette.AlternateBase, alt)
        p.setColor(group, QPalette.ToolTipBase, alt)
        p.setColor(group, QPalette.ToolTipText, text)
        p.setColor(group, QPalette.Text, text)
        p.setColor(group, QPalette.Button, alt)
        p.setColor(group, QPalette.ButtonText, text)
        p.setColor(group, QPalette.BrightText, Qt.red)
        p.setColor(group, QPalette.Link, QColor(110, 165, 255))
        p.setColor(group, QPalette.Highlight, accent)
        p.setColor(group, QPalette.HighlightedText, Qt.white)
        p.setColor(group, QPalette.PlaceholderText, QColor(140, 140, 140))
    disabled = QColor(120, 120, 120)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Base, window)
    p.setColor(QPalette.Disabled, QPalette.Button, window)
    return p


def apply_theme(mode):
    app = QApplication.instance()
    if not _original:
        _original["style"] = app.style().name()
        _original["palette"] = QPalette(app.palette())
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
            mode, Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)
        return
    # Older Qt: switch to Fusion with an explicit palette.
    if mode == "system":
        app.setStyle(QStyleFactory.create(_original["style"]))
        app.setPalette(_original["palette"])
    else:
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(_dark_palette() if mode == "dark" else app.style().standardPalette())
