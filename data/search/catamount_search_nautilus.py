# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Files (Nautilus) right-click entry: "Search here (CataMount, instant)".

Loaded by nautilus-python (Fedora: nautilus-python, Debian/Ubuntu: python3-nautilus).
Also works in Caja when copied to caja-python's extension folder.
"""
import subprocess

from gi.repository import GObject

try:
    from gi.repository import Nautilus as FM
except ImportError:  # Caja
    from gi.repository import Caja as FM


def _launch(folder_uri):
    from gi.repository import Gio
    path = Gio.File.new_for_uri(folder_uri).get_path()
    if path:
        subprocess.Popen(["catamount", "--search-window", "--folder", path])


def _item(uri):
    item = FM.MenuItem(name="CataMount::SearchHere", label="Search here (CataMount, instant)",
                       tip="Instant search on indexed network drives", icon="system-search")
    item.connect("activate", lambda *_: _launch(uri))
    return item


class CataMountSearch(GObject.GObject, FM.MenuProvider):
    # Signatures differ between Nautilus 4.x (no window argument) and older versions/Caja.
    def get_file_items(self, *args):
        files = args[-1]
        if len(files) == 1 and files[0].is_directory():
            return [_item(files[0].get_uri())]
        return []

    def get_background_items(self, *args):
        return [_item(args[-1].get_uri())]
