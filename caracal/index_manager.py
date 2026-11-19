# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Runs indexing in the background of the tray app and keeps it up to date.

One worker thread handles jobs one at a time (a scan, then a content pass if enabled).
A timer schedules incremental scans and periodic full refreshes for drives that are
connected; an inotify watcher re-lists folders changed from this computer.
"""

import glob
import json
import os
import queue
import threading
import time

from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

from caracal import crawler, indexdb

INDEX_DEFAULTS = {
    "index/rescan_minutes": 15,
    "index/full_hours": 24,
    "index/pause_on_battery": True,
    "index/watch_local": True,
    "index/max_content_mb": 20,
    "index/mount_ready": True,
    "index/skip_system": True,
}


def on_battery():
    """True when running on battery with no AC power connected."""
    has_battery, ac_online = False, False
    for supply in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(os.path.join(supply, "type")) as fh:
                kind = fh.read().strip()
            if kind == "Battery":
                scope = os.path.join(supply, "scope")
                if os.path.exists(scope):
                    with open(scope) as fh:
                        if fh.read().strip() == "Device":
                            continue  # mouse/keyboard batteries
                has_battery = True
            elif kind in ("Mains", "USB", "USB_C", "USB_PD"):
                with open(os.path.join(supply, "online")) as fh:
                    ac_online = ac_online or fh.read().strip() == "1"
        except OSError:
            continue
    return has_battery and not ac_online


class FolderRoot:
    """Any folder the user chose to index (right-click → Index for fast search).
    Looks enough like a mapped Drive for the indexer and the Search index tab."""

    def __init__(self, path):
        import hashlib
        self.mount_point = path
        self.id = "folder-" + hashlib.sha1(path.encode()).hexdigest()[:16]
        self.name = os.path.basename(path.rstrip("/")) or path
        self.kind = "folder"
        self.spec = {"id": self.id, "name": self.name, "mount_point": path}

    @property
    def state(self):
        return "connected" if os.path.isdir(self.mount_point) else "disconnected"


class _Relay(QObject):
    progress = Signal(dict)
    finished = Signal(str, str, str)   # drive_id, kind, error


class IndexManager(QObject):
    changed = Signal()   # status/progress changed: refresh UI

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db_path = indexdb.default_path()
        self.drives = {}           # id -> Drive (from the main window)
        self.jobs = queue.Queue()
        self.pending = set()       # (drive_id, kind) queued or running
        self.current = None        # dict about the running job
        self.progress = {}         # drive_id -> last progress dict
        self.errors = {}           # drive_id -> last error
        self._stop = threading.Event()
        self._cancel = set()       # drive ids whose running job should stop
        self._dirty = {}           # drive_id -> set(rel) from the watcher
        self._dirty_lock = threading.Lock()
        self.relay = _Relay()
        self.relay.progress.connect(self._on_progress)
        self.relay.finished.connect(self._on_finished)
        self.worker = threading.Thread(target=self._work, name="caracal-index", daemon=True)
        self.worker.start()

        self.watcher = None
        self._notifier = None
        self._watch_lock = threading.Lock()
        self._start_watcher()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.schedule)
        self.timer.start(30 * 1000)
        self.dirty_timer = QTimer(self)
        self.dirty_timer.setSingleShot(True)
        self.dirty_timer.timeout.connect(self._flush_dirty)

    # -- settings ------------------------------------------------------------

    def opt(self, key):
        default = INDEX_DEFAULTS[key]
        value = self.settings.value(key, default)
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() in ("true", "1")
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _drive_prefs(self):
        try:
            data = json.loads(self.settings.value("index/drives", "{}") or "{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def is_enabled(self, drive_id):
        return bool(self._drive_prefs().get(drive_id, {}).get("names"))

    def content_enabled(self, drive_id):
        p = self._drive_prefs().get(drive_id, {})
        return bool(p.get("names") and p.get("content"))

    def set_drive(self, drive_id, names, content):
        prefs = self._drive_prefs()
        before = prefs.get(drive_id, {})
        prefs[drive_id] = {"names": bool(names), "content": bool(names and content)}
        self.settings.setValue("index/drives", json.dumps(prefs))
        if not names and before.get("names"):
            self._cancel.add(drive_id)
            self.enqueue(drive_id, "delete")
        elif before.get("content") and not content and names:
            self.enqueue(drive_id, "clear_content")
        self.schedule()
        self.changed.emit()

    # -- drives from the main window -----------------------------------------------

    # -- folders the user chose to index ---------------------------------------------

    def folder_paths(self):
        try:
            data = json.loads(self.settings.value("index/folders", "[]") or "[]")
            return [p for p in data if isinstance(p, str)]
        except ValueError:
            return []

    def add_folder(self, path, content=False):
        path = os.path.abspath(path)
        paths = self.folder_paths()
        if path not in paths:
            paths.append(path)
            self.settings.setValue("index/folders", json.dumps(paths))
        root = FolderRoot(path)
        self.drives[root.id] = root
        self.set_drive(root.id, True, content)
        self.rescan(root.id, full=True)
        return root.id

    def remove_folder(self, path):
        path = os.path.abspath(path)
        root = FolderRoot(path)
        self.settings.setValue("index/folders",
                               json.dumps([p for p in self.folder_paths() if p != path]))
        self.set_drive(root.id, False, False)

    def covering(self, path):
        """Ids of indexed roots (drives or folders) that contain path; [] if none."""
        path = os.path.abspath(path)
        out = []
        for drive_id, d in self.drives.items():
            mp = (d.mount_point or "").rstrip("/")
            if mp and self.is_enabled(drive_id) and (path == mp or path.startswith(mp + "/")):
                out.append(drive_id)
        return out

    def indexed_root(self, path):
        """The indexed root id exactly at path, if any."""
        path = os.path.abspath(path).rstrip("/")
        for drive_id, d in self.drives.items():
            if (d.mount_point or "").rstrip("/") == path and self.is_enabled(drive_id):
                return drive_id
        return None

    def update_drives(self, drives):
        new = {d.id: d for d in drives}
        for p in self.folder_paths():
            root = FolderRoot(p)
            new[root.id] = root
        for gone in set(self.drives) - set(new):
            if self.is_enabled(gone):
                self._cancel.add(gone)
                self.enqueue(gone, "delete")
                prefs = self._drive_prefs()
                prefs.pop(gone, None)
                self.settings.setValue("index/drives", json.dumps(prefs))
        first = not self.drives
        self.drives = new
        if first:
            QTimer.singleShot(3000, self.schedule)

    def available(self, drive):
        if drive.state == "connected":
            return True
        return drive.state == "ready" and self.opt("index/mount_ready")

    # -- jobs ----------------------------------------------------------------------

    def enqueue(self, drive_id, kind):
        key = (drive_id, kind)
        if key in self.pending:
            return
        self.pending.add(key)
        self.jobs.put(key)
        self.changed.emit()

    def rescan(self, drive_id, full=False):
        self.enqueue(drive_id, "full" if full else "scan")

    def rebuild(self, drive_id):
        self._cancel.add(drive_id)
        self.enqueue(drive_id, "delete")
        self.enqueue(drive_id, "full")

    def schedule(self):
        if self.opt("index/pause_on_battery") and on_battery():
            return
        conn = indexdb.connect(self.db_path, readonly=True)
        try:
            stats = indexdb.drive_stats(conn)
        finally:
            if conn is not None:
                conn.close()
        now = time.time()
        for drive_id, d in self.drives.items():
            if not self.is_enabled(drive_id) or not self.available(d):
                continue
            st = stats.get(drive_id, {})
            if now - (st.get("last_full") or 0) > self.opt("index/full_hours") * 3600:
                self.enqueue(drive_id, "full")
            elif now - (st.get("last_scan") or 0) > self.opt("index/rescan_minutes") * 60:
                self.enqueue(drive_id, "scan")

    def stop(self):
        self._stop.set()
        self.jobs.put(None)
        if self.watcher is not None:
            self.watcher.close()

    def _work(self):
        while not self._stop.is_set():
            key = self.jobs.get()
            if key is None:
                return
            drive_id, kind = key
            if kind != "delete":
                self._cancel.discard(drive_id)
            error = ""
            try:
                self._run_job(drive_id, kind)
            except crawler.Stopped:
                error = "" if self._stop.is_set() else "stopped"
            except OSError as exc:
                error = exc.strerror or str(exc)
            except Exception as exc:  # keep the worker alive
                error = "%s: %s" % (type(exc).__name__, exc)
            self.relay.finished.emit(drive_id, kind, error)

    def _run_job(self, drive_id, kind):
        if kind == "delete":
            self._cancel.discard(drive_id)
            if self.watcher is not None:
                with self._watch_lock:
                    self.watcher.remove_drive(drive_id)
            conn = indexdb.connect(self.db_path)
            try:
                indexdb.delete_drive(conn, drive_id)
            finally:
                conn.close()
            return
        if kind == "clear_content":
            conn = indexdb.connect(self.db_path)
            try:
                indexdb.clear_contents(conn, drive_id)
            finally:
                conn.close()
            return
        d = self.drives.get(drive_id)
        if d is None or not self.is_enabled(drive_id):
            return
        self.current = {"drive": drive_id, "kind": kind}

        def should_stop():
            return self._stop.is_set() or drive_id in self._cancel

        def progress(**info):
            info["drive"] = drive_id
            info["kind"] = kind
            self.relay.progress.emit(info)

        c = crawler.Crawler(self.db_path, drive_id, d.name, d.mount_point,
                            should_stop=should_stop, progress=progress,
                            skip_system=self.opt("index/skip_system"))
        if kind == "dirs":
            with self._dirty_lock:
                rels = self._dirty.pop(drive_id, set())
            if rels:
                c.sync_dirs(rels)
        else:
            c.scan(full=(kind == "full"))
            if self.opt("index/watch_local"):
                self._watch_drive(drive_id, d.mount_point)
        if self.content_enabled(drive_id) and kind in ("scan", "full", "dirs", "content"):
            c.index_contents(self.opt("index/max_content_mb") * 1024 * 1024)

    # -- results back on the UI thread ---------------------------------------------

    def _on_progress(self, info):
        self.progress[info["drive"]] = info
        self.changed.emit()

    def _on_finished(self, drive_id, kind, error):
        self.pending.discard((drive_id, kind))
        self.current = None
        self.progress.pop(drive_id, None)
        if error and error != "stopped":
            self.errors[drive_id] = error
        elif kind != "delete":
            self.errors.pop(drive_id, None)
        self.changed.emit()

    # -- watching local changes --------------------------------------------------------

    def _start_watcher(self):
        try:
            self.watcher = crawler.Watcher()
        except OSError:
            self.watcher = None
            return
        self._notifier = QSocketNotifier(self.watcher.fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_watch)

    def _watch_drive(self, drive_id, root):
        """Called in the worker thread after a scan: watch the drive's folders."""
        if self.watcher is None:
            return
        budget = max(0, crawler.max_watches() // 2 - self.watcher.count())
        conn = indexdb.connect(self.db_path, readonly=True)
        try:
            rels = [r[0] for r in conn.execute(
                "SELECT path FROM dirs WHERE drive=? ORDER BY length(path) LIMIT ?",
                (drive_id, budget))]
        finally:
            conn.close()
        for rel in rels:
            if self._stop.is_set():
                return
            with self._watch_lock:
                self.watcher.add(drive_id, root, rel)

    def _on_watch(self, *_):
        with self._watch_lock:
            changed = self.watcher.read()
        with self._dirty_lock:
            for drive_id, rels in changed.items():
                self._dirty.setdefault(drive_id, set()).update(rels)
        if changed:
            self.dirty_timer.start(2000)   # debounce bursts of changes

    def _flush_dirty(self):
        with self._dirty_lock:
            ids = list(self._dirty)
        for drive_id in ids:
            if self.is_enabled(drive_id):
                self.enqueue(drive_id, "dirs")
            else:
                with self._dirty_lock:
                    self._dirty.pop(drive_id, None)

    # -- status for the UI -----------------------------------------------------------------

    def status(self):
        """{drive_id: dict(stats..., state text)} for drives the user can see."""
        conn = indexdb.connect(self.db_path, readonly=True)
        try:
            stats = indexdb.drive_stats(conn)
        finally:
            if conn is not None:
                conn.close()
        out = {}
        for drive_id, d in self.drives.items():
            st = dict(stats.get(drive_id, {}))
            st["names"] = self.is_enabled(drive_id)
            st["content"] = self.content_enabled(drive_id)
            p = self.progress.get(drive_id)
            if p:
                if p.get("phase") == "contents":
                    st["state"] = "Reading contents… %d of %d" % (p.get("done", 0),
                                                                p.get("total", 0))
                else:
                    st["state"] = "Indexing… %s entries" % format(p.get("entries", 0), ",")
            elif any(k[0] == drive_id and k[1] != "delete" for k in self.pending):
                st["state"] = "Queued"
            elif drive_id in self.errors:
                st["state"] = "Error: " + self.errors[drive_id]
            elif not st["names"]:
                st["state"] = "Off"
            elif not st.get("last_scan"):
                st["state"] = "Waiting for the drive to connect" if not self.available(d) \
                    else "Waiting"
            else:
                st["state"] = "Up to date"
            out[drive_id] = st
        return out
