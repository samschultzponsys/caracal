# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""One-time move of per-user data from CataMount (the old name) to Caracal.

Runs at startup before anything reads settings. Safe to run repeatedly; it only acts
when the old data exists and the new location doesn't.

- ~/.config/catamount/catamount.conf -> ~/.config/caracal/caracal.conf (copied)
- ~/.local/share/catamount/ (search index, Explorer theme) -> ~/.local/share/caracal/
- start-at-login entry, and the default-file-manager association, re-pointed at Caracal
SFTP/FTP passwords and rclone configs stay in ~/.config/catamount/ because existing
user units reference them there; they move the next time a location is saved.
"""

import os
import shutil
import subprocess


def _cfg(*p):
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), *p)


def _data(*p):
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), *p)


def run():
    done = []
    old_conf, new_conf = _cfg("catamount", "catamount.conf"), _cfg("caracal", "caracal.conf")
    if os.path.exists(old_conf) and not os.path.exists(new_conf):
        os.makedirs(os.path.dirname(new_conf), exist_ok=True)
        shutil.copy2(old_conf, new_conf)
        done.append("settings")
    old_data, new_data = _data("catamount"), _data("caracal")
    if os.path.isdir(old_data) and not os.path.exists(new_data):
        try:
            os.rename(old_data, new_data)
            done.append("data")
        except OSError:
            pass
    old_auto = _cfg("autostart", "com.spazcat.CataMount.desktop")
    if os.path.exists(old_auto):
        from caracal import autostart
        try:
            autostart.set_enabled(True)
            os.unlink(old_auto)
            done.append("autostart")
        except OSError:
            pass
    # Default file manager association and the "show in folder" D-Bus service.
    try:
        cur = subprocess.run(["xdg-mime", "query", "default", "inode/directory"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        cur = ""
    if cur == "com.spazcat.CataMount.Explorer.desktop":
        try:
            subprocess.run(["xdg-mime", "default", "com.spazcat.Caracal.Explorer.desktop",
                            "inode/directory"], capture_output=True, timeout=10)
            done.append("default file manager")
        except (OSError, subprocess.SubprocessError):
            pass
    svc = _data("dbus-1", "services", "org.freedesktop.FileManager1.service")
    try:
        with open(svc, encoding="utf-8") as fh:
            text = fh.read()
        if "catamount" in text:
            with open(svc, "w", encoding="utf-8") as fh:
                fh.write(text.replace("/usr/bin/catamount", "/usr/bin/caracal"))
    except OSError:
        pass
    return done
