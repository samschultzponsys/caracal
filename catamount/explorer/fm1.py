# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Make CataMount Explorer the default file manager.

- Folders: `xdg-mime default com.spazcat.CataMount.Explorer.desktop inode/directory`
  (the previous default is remembered and restored when turned off).
- "Show in folder" from other apps: the org.freedesktop.FileManager1 D-Bus interface,
  served from the running app (and D-Bus-activatable through a user service file).
"""

import os
import shutil
import subprocess

DESKTOP_ID = "com.spazcat.CataMount.Explorer.desktop"
BUS_NAME = "org.freedesktop.FileManager1"
PATH = "/org/freedesktop/FileManager1"
XML = """
<node>
  <interface name="org.freedesktop.FileManager1">
    <method name="ShowFolders"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
    <method name="ShowItems"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
    <method name="ShowItemProperties"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
  </interface>
</node>
"""


def _service_file():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "dbus-1", "services", "org.freedesktop.FileManager1.service")


def current_default():
    try:
        return subprocess.run(["xdg-mime", "query", "default", "inode/directory"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def is_default():
    return current_default() == DESKTOP_ID


def set_default(settings, on):
    """Turn 'default file manager' on or off. Returns an error string or ""."""
    if not shutil.which("xdg-mime"):
        return "xdg-mime (xdg-utils) isn't installed."
    if on:
        prev = current_default()
        if prev and prev != DESKTOP_ID:
            settings.setValue("explorer/previous_fm", prev)
        subprocess.run(["xdg-mime", "default", DESKTOP_ID, "inode/directory"],
                       capture_output=True, timeout=10)
        path = _service_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[D-BUS Service]\nName=%s\nExec=/usr/bin/catamount --explorer-service\n"
                     % BUS_NAME)
    else:
        prev = settings.value("explorer/previous_fm", "") or ""
        if prev:
            subprocess.run(["xdg-mime", "default", prev, "inode/directory"],
                           capture_output=True, timeout=10)
        try:
            os.unlink(_service_file())
        except FileNotFoundError:
            pass
    return ""


def maybe_serve(ctl):
    """Own org.freedesktop.FileManager1 while we're the default file manager.

    Uses Gio on the GLib main context, which Qt's event loop drives on Linux desktops.
    """
    if not is_default():
        return
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return
    from PySide6.QtCore import QTimer, QUrl
    node = Gio.DBusNodeInfo.new_for_xml(XML)

    def call(conn, sender, path, iface, method, params, invocation):
        uris, _startup = params.unpack()
        paths = [QUrl(u).toLocalFile() or u for u in uris]
        invocation.return_value(None)

        def act():
            if method == "ShowFolders":
                for p in paths[:10]:
                    ctl.open_explorer(p)
            elif method == "ShowItems":
                folders = {}
                for p in paths:
                    folders.setdefault(os.path.dirname(p), []).append(p)
                for folder, items in list(folders.items())[:10]:
                    w = ctl.open_explorer(folder)
                    w.select_paths(items)
            elif method == "ShowItemProperties" and paths:
                from catamount.explorer.dialogs import PropertiesDialog
                w = ctl.open_explorer(os.path.dirname(paths[0]))
                PropertiesDialog(paths, w).exec()
        QTimer.singleShot(0, act)

    def on_bus(conn, _name):
        conn.register_object(PATH, node.interfaces[0], call, None, None)

    ctl._fm1_owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                                       Gio.BusNameOwnerFlags.REPLACE |
                                       Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
                                       on_bus, None, None)
