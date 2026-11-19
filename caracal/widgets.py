# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Small shared widgets."""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLineEdit


def add_reveal_action(line_edit):
    """Eye button inside a password field that toggles showing the text."""
    icon = QIcon.fromTheme("view-reveal-symbolic", QIcon.fromTheme("view-visible"))
    if icon.isNull():
        return None
    act = line_edit.addAction(icon, QLineEdit.TrailingPosition)
    act.setCheckable(True)
    act.setToolTip("Show password")
    act.toggled.connect(lambda on: line_edit.setEchoMode(
        QLineEdit.Normal if on else QLineEdit.Password))
    return act
