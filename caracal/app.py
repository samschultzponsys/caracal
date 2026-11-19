# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import json
import os
import signal
import sys
import time

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from caracal import APP_ID, APP_NAME, __version__

_server = None


def release_instance_lock():
    """Stop answering as the running instance (used right before a restart)."""
    if _server is not None:
        _server.close()


def _restore_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "caracal", "restart.json")


def save_restore_state(windows, request=""):
    """Written right before a restart so the new process reopens the same windows."""
    path = _restore_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"time": time.time(), "windows": windows, "request": request}, fh)
    except OSError:
        pass


def take_restore_state():
    """The saved state if it's fresh (from a restart a moment ago), deleting it."""
    path = _restore_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        os.remove(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("time", 0)) > 120:
        return None
    return data


def split_message(raw):
    """Messages from a second launch are "<version>\n<request>" (1.3+) or just the request."""
    if "\n" in raw:
        version, _, msg = raw.partition("\n")
        return version.strip(), msg
    return "", raw


def _server_name():
    return "caracal-%d" % os.getuid()


def _forward_to_running(message):
    """If Caracal is already running, ask it to show itself and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(500):
        return False
    sock.write(("%s\n%s" % (__version__, message)).encode())
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    try:
        from caracal import migrate
        migrate.run()          # CataMount -> Caracal, once
    except Exception:          # never block startup on migration
        pass
    if "--version" in argv:
        print("%s %s" % (APP_NAME, __version__))
        return 0
    from caracal import cli
    if "--help" in argv or "-h" in argv:
        print(cli.USAGE)
        return 0
    if "--search-service" in argv:
        from caracal import search_service
        return search_service.main()
    if cli.wants_cli(argv):
        return cli.main(argv)
    search = cli.search_window_args(argv)   # (query, folder) or None
    explore = cli.explorer_args(argv)       # path ("" = default start) or None
    start_in_tray = "--tray" in argv or "--explorer-service" in argv
    restore = take_restore_state() if "--restore" in argv else None

    QApplication.setApplicationName("caracal")
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive; MainWindow decides

    # Single instance: a second launch just brings the first window forward.
    if explore is not None:
        message = "explore\t%s" % explore
    elif search is not None:
        message = "search\t%s\t%s" % (search[1], search[0])
    else:
        message = "tray" if start_in_tray else "show"
    if restore is None and _forward_to_running(message):
        return 0
    global _server
    QLocalServer.removeServer(_server_name())  # clear a stale socket after a crash
    server = _server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(_server_name())

    from caracal.icons import app_icon
    from caracal.mainwindow import MainWindow
    app.setWindowIcon(app_icon())
    from caracal.dialogs import setting
    from PySide6.QtCore import QSettings, QTimer
    explorer_start = restore is None and (explore is not None or (
        not start_in_tray and search is None and
        setting(QSettings("caracal", "caracal"), "explorer_mode")))
    win = MainWindow(start_in_tray=start_in_tray or explorer_start or restore is not None,
                     search=search)
    if explorer_start:
        QTimer.singleShot(0, lambda: win.open_explorer(explore or None))
    if restore is not None:
        QTimer.singleShot(0, lambda: _reopen(win, restore, handle))
    from caracal.explorer import fm1
    fm1.maybe_serve(win)

    def handle(msg):
        if msg.startswith("explore\t"):
            win.open_explorer(msg.split("\t", 1)[1] or None)
        elif msg.startswith("search\t"):
            _, folder, query = (msg.split("\t", 2) + ["", ""])[:3]
            win.open_search(query, folder)
        elif msg.strip() != "tray":
            win.show_default()

    def on_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.waitForReadyRead(500)
            version, msg = split_message(bytes(conn.readAll()).decode(errors="replace"))
            conn.disconnectFromServer()
            conn.deleteLater()
            if version and version != __version__:
                # a different (newly installed) version was launched: hand over to it
                win.restart_for_update(msg or "show")
            else:
                handle(msg)

    server.newConnection.connect(on_connection)

    # The package's install scripts send SIGUSR1 after an upgrade: restart into the new
    # files. Python only runs signal handlers between bytecodes, so a timer keeps it ticking.
    upgraded = []
    signal.signal(signal.SIGUSR1, lambda *_a: upgraded.append(True))
    tick = QTimer()
    tick.timeout.connect(lambda: upgraded and (upgraded.clear(), win.restart_for_update()))
    tick.start(1000)
    return app.exec()


def _reopen(win, state, handle):
    """After a restart: the same Explorer windows and tabs, then whatever was asked for."""
    from PySide6.QtCore import QByteArray
    for w in state.get("windows") or []:
        tabs = [t for t in w.get("tabs") or [] if t]
        try:
            x = win.open_explorer(tabs[0] if tabs else None)
            for loc in tabs[1:]:
                x.new_tab(loc)
            if w.get("current") in tabs:
                x.tabbar.setCurrentIndex(tabs.index(w["current"]))
            if w.get("geometry"):
                x.restoreGeometry(QByteArray.fromBase64(w["geometry"].encode()))
        except Exception:      # a window that can't come back shouldn't stop the rest
            continue
    if state.get("request"):
        handle(state["request"])
