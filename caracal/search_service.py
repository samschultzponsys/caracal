# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Desktop search service: answers KRunner (KDE Plasma) and GNOME Shell searches from the
local index. Started on demand by D-Bus (`caracal --search-service`), uses GLib/Gio only
(no Qt, so it starts fast), reads the index read-only, and exits after a few idle minutes.

Bus name: com.spazcat.Caracal.SearchProvider
  /krunner                               org.kde.krunner1
  /com/spazcat/Caracal/SearchProvider  org.gnome.Shell.SearchProvider2
"""

import os
import subprocess
import sys

BUS_NAME = "com.spazcat.Caracal.SearchProvider"
KRUNNER_PATH = "/krunner"
GNOME_PATH = "/com/spazcat/Caracal/SearchProvider"
IDLE_EXIT_SECONDS = 300
MAX_RESULTS = 20

INTROSPECTION = """
<node>
  <interface name="org.kde.krunner1">
    <method name="Actions"><arg type="a(sss)" direction="out"/></method>
    <method name="Match">
      <arg name="query" type="s" direction="in"/>
      <arg type="a(sssida{sv})" direction="out"/>
    </method>
    <method name="Run">
      <arg name="matchId" type="s" direction="in"/>
      <arg name="actionId" type="s" direction="in"/>
    </method>
  </interface>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
"""

# KRunner match types
EXACT, POSSIBLE = 100, 30


class Searcher:
    """Index lookups shared by both desktop protocols (no D-Bus here, so it's testable)."""

    def __init__(self, db_path=None):
        from caracal import indexdb
        self.indexdb = indexdb
        self.db_path = db_path
        self.conn = None
        self.seen = {}   # path -> Result, for GetResultMetas

    def _conn(self):
        if self.conn is None:
            self.conn = self.indexdb.connect(self.db_path, readonly=True)
        return self.conn

    def search(self, text, limit=MAX_RESULTS):
        text = text.strip()
        if len(text) < 2:
            return []
        try:
            results, _total, _ms = self.indexdb.search(self._conn(), text, limit=limit,
                                                       order="name")
        except Exception:
            self.conn = None  # reopen next time (e.g. the index was just created)
            return []
        # Exact-name and prefix matches first, then shorter names.
        low = text.lower()
        results.sort(key=lambda r: (r.name.lower() != low, not r.name.lower().startswith(low),
                                    len(r.name)))
        if len(self.seen) > 5000:
            self.seen.clear()
        self.seen.update((r.path, r) for r in results)
        return results

    def krunner_matches(self, text):
        out = []
        low = text.strip().lower()
        for i, r in enumerate(self.search(text)):
            relevance = max(0.1, 0.9 - i * 0.03)
            kind = EXACT if r.name.lower() == low else POSSIBLE
            props = {"subtext": r.folder_path}
            out.append((r.path, r.name, icon_name(r.name, r.is_dir), kind, relevance, props))
        return out

    def paths(self, terms):
        return [r.path for r in self.search(" ".join(terms))]


def icon_name(name, is_dir):
    if is_dir:
        return "folder"
    try:
        from gi.repository import Gio
        ctype, _uncertain = Gio.content_type_guess(name, None)
        icon = Gio.content_type_get_icon(ctype)
        names = icon.get_names() if hasattr(icon, "get_names") else []
        return names[0] if names else "text-x-generic"
    except Exception:
        return "text-x-generic"


def open_path(path, folder=False):
    target = os.path.dirname(path) if folder else path
    try:
        from gi.repository import Gio
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(target).get_uri(), None)
    except Exception:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def show_in_folder(path):
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                      None, Gio.DBusCallFlags.NONE, 3000, None)
    except Exception:
        open_path(path, folder=True)


def copy_text(text):
    """Put text on the clipboard with whatever tool the session has."""
    tools = (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"])
    import shutil
    for tool in tools:
        if shutil.which(tool[0]):
            try:
                subprocess.run(tool, input=text.encode(), timeout=5, check=False)
                return
            except (OSError, subprocess.SubprocessError):
                continue


def main():
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        print("The desktop search service needs PyGObject (python3-gi / python3-gobject).",
              file=sys.stderr)
        return 1

    searcher = Searcher()
    loop = GLib.MainLoop()
    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
    state = {"timer": 0}

    def touch():
        if state["timer"]:
            GLib.source_remove(state["timer"])
        state["timer"] = GLib.timeout_add_seconds(IDLE_EXIT_SECONDS, lambda: loop.quit() or False)

    def call(conn, sender, path, iface, method, params, invocation):
        touch()
        args = params.unpack()
        try:
            if iface == "org.kde.krunner1":
                if method == "Actions":
                    invocation.return_value(GLib.Variant("(a(sss))", ([
                        ("folder", "Open containing folder", "folder-open"),
                        ("copy", "Copy path", "edit-copy")],)))
                elif method == "Match":
                    rows = [(m[0], m[1], m[2], m[3], m[4],
                             {k: GLib.Variant("s", v) for k, v in m[5].items()})
                            for m in searcher.krunner_matches(args[0])]
                    invocation.return_value(GLib.Variant("(a(sssida{sv}))", (rows,)))
                elif method == "Run":
                    match_id, action = args
                    if action == "folder":
                        show_in_folder(match_id)
                    elif action == "copy":
                        copy_text(match_id)
                    else:
                        open_path(match_id)
                    invocation.return_value(None)
            elif iface == "org.gnome.Shell.SearchProvider2":
                if method == "GetInitialResultSet":
                    invocation.return_value(GLib.Variant("(as)", (searcher.paths(args[0]),)))
                elif method == "GetSubsearchResultSet":
                    invocation.return_value(GLib.Variant("(as)", (searcher.paths(args[1]),)))
                elif method == "GetResultMetas":
                    metas = []
                    for p in args[0]:
                        r = searcher.seen.get(p)
                        is_dir = bool(r and r.is_dir)
                        name = os.path.basename(p.rstrip("/"))
                        gicon = Gio.ThemedIcon.new(icon_name(name, is_dir)).to_string()
                        metas.append({"id": GLib.Variant("s", p),
                                      "name": GLib.Variant("s", name),
                                      "description": GLib.Variant("s", os.path.dirname(p)),
                                      "gicon": GLib.Variant("s", gicon)})
                    invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
                elif method == "ActivateResult":
                    open_path(args[0])
                    invocation.return_value(None)
                elif method == "LaunchSearch":
                    subprocess.Popen(["caracal", "--search-window", " ".join(args[0])],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    invocation.return_value(None)
        except Exception as exc:  # never leave the desktop hanging on a reply
            invocation.return_dbus_error("com.spazcat.Caracal.Error", str(exc))

    def on_bus(conn, _name):
        conn.register_object(KRUNNER_PATH, node.lookup_interface("org.kde.krunner1"),
                             call, None, None)
        conn.register_object(GNOME_PATH, node.lookup_interface("org.gnome.Shell.SearchProvider2"),
                             call, None, None)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     on_bus, None, lambda *_: loop.quit())
    touch()
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
