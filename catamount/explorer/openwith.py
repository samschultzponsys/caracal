# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Find applications that can open a file type (freedesktop .desktop files). No Qt imports."""

import configparser
import os
import shlex
import subprocess
from dataclasses import dataclass


def _data_dirs():
    dirs = [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share")]
    return [os.path.join(d, "applications") for d in dirs if d]


@dataclass
class App:
    desktop_id: str
    name: str
    exec: str
    icon: str
    mimetypes: tuple

    def command(self, paths):
        """Build argv for opening paths with this app (field codes per the spec)."""
        try:
            parts = shlex.split(self.exec)
        except ValueError:
            parts = self.exec.split()
        argv = []
        used = False
        for p in parts:
            if p in ("%f", "%u"):
                argv.append(paths[0])
                used = True
            elif p in ("%F", "%U"):
                argv.extend(paths)
                used = True
            elif p in ("%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"):
                continue
            else:
                argv.append(p.replace("%%", "%"))
        if not used:
            argv.extend(paths)
        return argv

    def launch(self, paths):
        subprocess.Popen(self.command(paths), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


_cache = None


def all_apps():
    global _cache
    if _cache is not None:
        return _cache
    apps = {}
    for d in _data_dirs():
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                if not f.endswith(".desktop"):
                    continue
                desktop_id = os.path.relpath(os.path.join(root, f), d).replace("/", "-")
                if desktop_id in apps:
                    continue  # earlier dirs win (user overrides system)
                cp = configparser.RawConfigParser(strict=False, interpolation=None)
                try:
                    cp.read(os.path.join(root, f), encoding="utf-8")
                    e = cp["Desktop Entry"]
                except (configparser.Error, KeyError, UnicodeDecodeError):
                    continue
                if e.get("Type", "Application") != "Application" or e.get("NoDisplay") == "true" \
                        or e.get("Hidden") == "true" or not e.get("Exec"):
                    apps[desktop_id] = None
                    continue
                apps[desktop_id] = App(desktop_id, e.get("Name", desktop_id), e.get("Exec"),
                                       e.get("Icon", ""),
                                       tuple(m for m in e.get("MimeType", "").split(";") if m))
    _cache = sorted((a for a in apps.values() if a), key=lambda a: a.name.lower())
    return _cache


def apps_for(mimetypes):
    """Apps for a mime type (and its parents, most specific first), default app first."""
    defaults = default_ids(mimetypes[0]) if mimetypes else []
    found = []
    for mt in mimetypes:
        for a in all_apps():
            if mt in a.mimetypes and a not in found:
                found.append(a)
    found.sort(key=lambda a: (a.desktop_id not in defaults,
                              defaults.index(a.desktop_id) if a.desktop_id in defaults else 0))
    return found


def default_ids(mimetype):
    cfgs = [os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "mimeapps.list"), "/etc/xdg/mimeapps.list",
            "/usr/share/applications/mimeapps.list"]
    out = []
    for path in cfgs:
        cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            continue
        for section in ("Default Applications", "Added Associations"):
            if cp.has_section(section) and cp.has_option(section, mimetype):
                out += [x for x in cp.get(section, mimetype).split(";") if x and x not in out]
    return out


def set_default(mimetype, desktop_id):
    subprocess.run(["xdg-mime", "default", desktop_id, mimetype], capture_output=True, timeout=10)
