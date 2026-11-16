# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""File-manager sidebar entries for drives (per user).

GTK file managers (Files/Nautilus, Nemo, Thunar, Caja) read ~/.config/gtk-3.0/bookmarks.
Dolphin reads ~/.local/share/user-places.xbel (only touched if it already exists).
CataMount only adds/removes entries it created itself, tracked in preferences, so a
bookmark the user deletes by hand stays deleted.
"""

import json
import os
import re
from urllib.parse import quote
from xml.sax.saxutils import escape


def _config(*parts):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, *parts)


def _data(*parts):
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, *parts)


def gtk_path():
    return _config("gtk-3.0", "bookmarks")


def kde_path():
    return _data("user-places.xbel")


def uri(path):
    return "file://" + quote(path)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".catamount-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# -- GTK ---------------------------------------------------------------------

def gtk_add(path, name):
    text = _read(gtk_path()) or ""
    u = uri(path)
    lines = [ln for ln in text.splitlines() if ln.split(" ", 1)[0] != u]
    lines.append("%s %s" % (u, name.replace("\n", " ")))
    _write(gtk_path(), "\n".join(lines) + "\n")


def gtk_remove(path):
    text = _read(gtk_path())
    if text is None:
        return
    u = uri(path)
    lines = [ln for ln in text.splitlines() if ln.split(" ", 1)[0] != u]
    _write(gtk_path(), "\n".join(lines) + ("\n" if lines else ""))


# -- KDE ---------------------------------------------------------------------

def _kde_block_re(u):
    return re.compile(r'\s*<bookmark href="%s">.*?</bookmark>' % re.escape(escape(u)), re.S)


def kde_add(path, name):
    text = _read(kde_path())
    if text is None or "</xbel>" not in text:
        return  # not a KDE user (or an unexpected file): leave it alone
    u = uri(path)
    text = _kde_block_re(u).sub("", text)
    block = ('\n <bookmark href="%s">\n  <title>%s</title>\n  <info>\n'
             '   <metadata owner="http://freedesktop.org">\n'
             '    <bookmark:icon name="folder-remote"/>\n   </metadata>\n  </info>\n'
             ' </bookmark>\n' % (escape(u), escape(name)))
    idx = text.rindex("</xbel>")
    _write(kde_path(), text[:idx].rstrip() + block + text[idx:])


def kde_remove(path):
    text = _read(kde_path())
    if text is None:
        return
    new = _kde_block_re(uri(path)).sub("", text)
    if new != text:
        _write(kde_path(), new)


# -- sync ----------------------------------------------------------------------

def sync(drives, settings):
    """Bring sidebar entries in line with each drive's "bookmark" setting.

    Only changes compared to what CataMount recorded last time are applied.
    Returns an error string, or "".
    """
    try:
        recorded = json.loads(settings.value("bookmarks", "{}") or "{}")
        if not isinstance(recorded, dict):
            recorded = {}
    except ValueError:
        recorded = {}
    desired = {d.id: {"mp": d.mount_point, "name": d.name}
               for d in drives if d.spec.get("bookmark") and d.mount_point}
    if desired == recorded:
        return ""
    try:
        for drive_id, old in recorded.items():
            if desired.get(drive_id) != old:
                gtk_remove(old["mp"])
                kde_remove(old["mp"])
        for drive_id, new in desired.items():
            if recorded.get(drive_id) != new:
                gtk_add(new["mp"], new["name"])
                kde_add(new["mp"], new["name"])
    except (OSError, KeyError, TypeError) as exc:
        return "Could not update file manager bookmarks: %s" % exc
    settings.setValue("bookmarks", json.dumps(desired, sort_keys=True))
    return ""
