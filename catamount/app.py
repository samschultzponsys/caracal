# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from catamount import APP_ID, APP_NAME, __version__


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print("%s %s" % (APP_NAME, __version__))
        return 0
    QApplication.setApplicationName("catamount")
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(argv)
    app.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon.fromTheme("folder-remote")))

    from catamount.mainwindow import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()
