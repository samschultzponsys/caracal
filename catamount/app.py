# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from catamount import APP_ID, APP_NAME, __version__

_server = None


def release_instance_lock():
    """Stop answering as the running instance (used right before a restart)."""
    if _server is not None:
        _server.close()


def _server_name():
    return "catamount-%d" % os.getuid()


def _forward_to_running(message):
    """If CataMount is already running, ask it to show itself and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(500):
        return False
    sock.write(message.encode())
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print("%s %s" % (APP_NAME, __version__))
        return 0
    from catamount import cli
    if "--help" in argv or "-h" in argv:
        print(cli.USAGE)
        return 0
    if "--search-service" in argv:
        from catamount import search_service
        return search_service.main()
    if cli.wants_cli(argv):
        return cli.main(argv)
    search = cli.search_window_args(argv)   # (query, folder) or None
    start_in_tray = "--tray" in argv

    QApplication.setApplicationName("catamount")
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive; MainWindow decides

    # Single instance: a second launch just brings the first window forward.
    if search is not None:
        message = "search\t%s\t%s" % (search[1], search[0])
    else:
        message = "tray" if start_in_tray else "show"
    if _forward_to_running(message):
        return 0
    global _server
    QLocalServer.removeServer(_server_name())  # clear a stale socket after a crash
    server = _server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(_server_name())

    from catamount.icons import app_icon
    from catamount.mainwindow import MainWindow
    app.setWindowIcon(app_icon())
    win = MainWindow(start_in_tray=start_in_tray, search=search)

    def on_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.waitForReadyRead(500)
            msg = bytes(conn.readAll()).decode(errors="replace")
            if msg.startswith("search\t"):
                _, folder, query = (msg.split("\t", 2) + ["", ""])[:3]
                win.open_search(query, folder)
            elif msg.strip() != "tray":
                win.show_window()
            conn.disconnectFromServer()
            conn.deleteLater()

    server.newConnection.connect(on_connection)
    return app.exec()
