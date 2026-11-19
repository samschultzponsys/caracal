# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Start-at-login via an XDG autostart entry (starts hidden in the tray)."""

import os

from caracal import APP_ID, APP_NAME

ENTRY = """[Desktop Entry]
Type=Application
Name={name}
Comment=Network drives in the system tray
Exec=caracal --tray
Icon={appid}
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
"""


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", APP_ID + ".desktop")


def is_enabled():
    return os.path.isfile(path())


def set_enabled(enabled):
    p = path()
    if enabled:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(ENTRY.format(name=APP_NAME, appid=APP_ID))
    else:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
