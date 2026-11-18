# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Walk a mounted share into the search index. No Qt imports; runs in a worker thread.

Full scan: list every folder.
Incremental scan: stat every known folder and re-list only those whose modified time
changed (adding/removing/renaming entries changes a folder's mtime on SMB and NFS).
Edits that don't change a folder's mtime (rewriting a file in place) are caught by the
periodic full scan, or right away when made from this computer (inotify, see Watcher).
"""

import ctypes
import ctypes.util
import errno
import os
import select
import stat as statmod
import struct
import time

from catamount import extract, indexdb

SKIP_NAMES = {"$RECYCLE.BIN", "System Volume Information", "#recycle", "#snapshot",
              "@eaDir", ".snapshot", ".snapshots", "@Recycle", ".AppleDouble", ".Trashes",
              ".DS_Store", "Thumbs.db", "desktop.ini", "lost+found", "@Recently-Snapshot"}


class Stopped(Exception):
    pass


class Crawler:
    def __init__(self, db_path, drive_id, name, mount_point, should_stop=None, progress=None,
                 throttle=0.0, skip_system=True):
        self.db_path = db_path
        self.drive_id = drive_id
        self.name = name
        self.root = mount_point.rstrip("/") or "/"
        self.should_stop = should_stop or (lambda: False)
        self.progress = progress or (lambda **kw: None)
        self.throttle = throttle
        self.skip_system = skip_system
        self.conn = None
        self.dirs_done = 0
        self.entries = 0
        self.changed = 0
        self._last_report = 0.0
        self._ops = 0

    # -- helpers -----------------------------------------------------------

    def _abs(self, rel):
        return os.path.join(self.root, rel) if rel else self.root

    def _check(self):
        if self.should_stop():
            raise Stopped()

    def _tick(self, rel, force=False):
        now = time.monotonic()
        if force or now - self._last_report > 0.5:
            self._last_report = now
            self.progress(phase="names", dirs=self.dirs_done, entries=self.entries, path=rel)

    def _commit_maybe(self):
        self._ops += 1
        if self._ops >= 2000:
            self.conn.commit()
            self._ops = 0

    def _skip(self, name):
        return self.skip_system and (name in SKIP_NAMES or name.startswith(".Trash-"))

    # -- names ---------------------------------------------------------------

    def sync_dir(self, rel, recurse_all):
        """Bring one folder's entries in line with the share. Returns new subfolders
        (or all subfolders when recurse_all)."""
        self._check()
        path = self._abs(rel)
        try:
            st = os.stat(path)
            listing = list(os.scandir(path))
        except FileNotFoundError:
            indexdb.delete_tree(self.conn, self.drive_id, rel)
            self.changed += 1
            return []
        except PermissionError:
            return []
        dir_id = indexdb.add_dir(self.conn, self.drive_id, rel, st.st_mtime)
        existing = indexdb.dir_entries(self.conn, dir_id)
        seen = set()
        subdirs = []
        for entry in listing:
            name = entry.name
            if self._skip(name):
                continue
            try:
                est = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = statmod.S_ISDIR(est.st_mode)
            size = 0 if is_dir else est.st_size
            mtime = est.st_mtime
            seen.add(name)
            self.entries += 1
            old = existing.get(name)
            child_rel = (rel + "/" + name) if rel else name
            if old is None:
                indexdb.add_file(self.conn, self.drive_id, dir_id, name, is_dir, size, mtime)
                self.changed += 1
                if is_dir:
                    subdirs.append(child_rel)
            else:
                fid, old_dir, old_size, old_mtime = old
                if bool(old_dir) != is_dir:
                    if old_dir:
                        indexdb.delete_tree(self.conn, self.drive_id, child_rel)
                    indexdb.delete_file(self.conn, fid)
                    indexdb.add_file(self.conn, self.drive_id, dir_id, name, is_dir, size, mtime)
                    self.changed += 1
                    if is_dir:
                        subdirs.append(child_rel)
                else:
                    if old_size != size or old_mtime != mtime:
                        indexdb.update_file(self.conn, fid, size, mtime)
                        self.changed += 1
                    if is_dir and (recurse_all or indexdb.get_dir(
                            self.conn, self.drive_id, child_rel) is None):
                        subdirs.append(child_rel)
            self._commit_maybe()
        for name, (fid, was_dir, _s, _m) in existing.items():
            if name not in seen:
                if was_dir:
                    indexdb.delete_tree(self.conn, self.drive_id,
                                        (rel + "/" + name) if rel else name)
                indexdb.delete_file(self.conn, fid)
                self.changed += 1
        self.dirs_done += 1
        self._tick(rel)
        if self.throttle:
            time.sleep(self.throttle)
        return subdirs

    def _walk(self, start, recurse_all):
        stack = [start]
        while stack:
            rel = stack.pop()
            stack.extend(reversed(self.sync_dir(rel, recurse_all)))

    def scan(self, full=False):
        """Full or incremental name scan. Returns number of changes."""
        self.conn = indexdb.connect(self.db_path)
        try:
            indexdb.upsert_drive(self.conn, self.drive_id, self.name, self.root)
            if not os.path.isdir(self.root):
                raise OSError(errno.ENOENT, "Drive folder not available", self.root)
            first = indexdb.get_dir(self.conn, self.drive_id, "") is None
            if full or first:
                self._walk("", recurse_all=True)
            else:
                known = [r[0] for r in self.conn.execute(
                    "SELECT path, mtime FROM dirs WHERE drive=? ORDER BY path",
                    (self.drive_id,)).fetchall()]
                mtimes = dict(self.conn.execute(
                    "SELECT path, mtime FROM dirs WHERE drive=?", (self.drive_id,)).fetchall())
                for rel in known:
                    self._check()
                    if rel not in mtimes:
                        continue  # removed meanwhile with its parent
                    try:
                        st = os.stat(self._abs(rel))
                    except FileNotFoundError:
                        indexdb.delete_tree(self.conn, self.drive_id, rel)
                        mtimes = dict(self.conn.execute(
                            "SELECT path, mtime FROM dirs WHERE drive=?",
                            (self.drive_id,)).fetchall())
                        self.changed += 1
                        continue
                    except OSError:
                        continue
                    if st.st_mtime != mtimes[rel]:
                        for sub in self.sync_dir(rel, recurse_all=False):
                            self._walk(sub, recurse_all=True)
                    else:
                        self.dirs_done += 1
                        self._tick(rel)
            now = time.time()
            fields = {"last_scan": now, "last_error": ""}
            if full or first:
                fields["last_full"] = now
            indexdb.update_counts(self.conn, self.drive_id, **fields)
            self._tick("", force=True)
            return self.changed
        except Stopped:
            self.conn.commit()
            raise
        except OSError as exc:
            self.conn.commit()
            indexdb.update_counts(self.conn, self.drive_id,
                                  last_error=exc.strerror or str(exc))
            raise
        finally:
            self.conn.close()
            self.conn = None

    def sync_dirs(self, rels):
        """Re-list specific folders (e.g. after local changes seen by the watcher)."""
        self.conn = indexdb.connect(self.db_path)
        try:
            for rel in sorted(set(rels)):
                for sub in self.sync_dir(rel, recurse_all=False):
                    self._walk(sub, recurse_all=True)
            indexdb.update_counts(self.conn, self.drive_id)
        finally:
            self.conn.close()
            self.conn = None

    # -- contents ------------------------------------------------------------

    def index_contents(self, max_bytes=20 * 1024 * 1024):
        """Extract text for files that are new or changed since their last extraction."""
        self.conn = indexdb.connect(self.db_path)
        pdf = extract.supported("x.pdf")
        try:
            rows = self.conn.execute(
                "SELECT f.id, d.path, f.name, f.size, f.mtime FROM files f "
                "JOIN dirs d ON d.id=f.dir WHERE f.drive=? AND f.is_dir=0 "
                "AND f.content_mtime != f.mtime AND f.size <= ?",
                (self.drive_id, max_bytes)).fetchall()
            todo = [r for r in rows if extract.supported(r[2], pdf)]
            done = 0
            for fid, rel, name, _size, mtime in todo:
                self._check()
                path = os.path.join(self._abs(rel), name)
                text = extract.extract(path)
                indexdb.set_content(self.conn, fid, text, mtime)
                done += 1
                self._commit_maybe()
                now = time.monotonic()
                if now - self._last_report > 0.5:
                    self._last_report = now
                    self.progress(phase="contents", done=done, total=len(todo), path=rel)
                if self.throttle:
                    time.sleep(self.throttle)
            indexdb.update_counts(self.conn, self.drive_id)
            return done
        except Stopped:
            self.conn.commit()
            raise
        finally:
            self.conn.close()
            self.conn = None


# --------------------------------------------------------------------------
# inotify: pick up changes made from this computer right away
# --------------------------------------------------------------------------

IN_MODIFY = 0x002
IN_ATTRIB = 0x004
IN_CLOSE_WRITE = 0x008
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_DELETE_SELF = 0x400
IN_MOVE_SELF = 0x800
IN_ONLYDIR = 0x01000000
IN_IGNORED = 0x8000
IN_Q_OVERFLOW = 0x4000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
WATCH_MASK = (IN_CLOSE_WRITE | IN_ATTRIB | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE |
              IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF | IN_ONLYDIR)


def max_watches():
    try:
        with open("/proc/sys/fs/inotify/max_user_watches") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 8192


class Watcher:
    """Minimal inotify wrapper. Folders are tracked as (drive_id, rel_path)."""

    def __init__(self):
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        self.libc = ctypes.CDLL(libc_name, use_errno=True)
        self.fd = self.libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.wds = {}        # wd -> (drive_id, rel)
        self.by_key = {}     # (drive_id, rel) -> wd
        self.overflowed = False

    def add(self, drive_id, root, rel):
        key = (drive_id, rel)
        if key in self.by_key:
            return True
        path = os.path.join(root, rel) if rel else root
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), WATCH_MASK)
        if wd < 0:
            return False
        self.wds[wd] = key
        self.by_key[key] = wd
        return True

    def remove_drive(self, drive_id):
        for key, wd in list(self.by_key.items()):
            if key[0] == drive_id:
                self.libc.inotify_rm_watch(self.fd, wd)
                self.by_key.pop(key, None)
                self.wds.pop(wd, None)

    def count(self):
        return len(self.wds)

    def read(self):
        """Return {drive_id: set(rel folders that changed)}; non-blocking."""
        changed = {}
        while True:
            r, _w, _x = select.select([self.fd], [], [], 0)
            if not r:
                break
            try:
                data = os.read(self.fd, 64 * 1024)
            except BlockingIOError:
                break
            pos = 0
            while pos + 16 <= len(data):
                wd, mask, _cookie, length = struct.unpack_from("iIII", data, pos)
                pos += 16 + length
                if mask & IN_Q_OVERFLOW:
                    self.overflowed = True
                    continue
                key = self.wds.get(wd)
                if key is None:
                    continue
                if mask & IN_IGNORED:
                    self.wds.pop(wd, None)
                    self.by_key.pop(key, None)
                    continue
                drive_id, rel = key
                if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                    changed.setdefault(drive_id, set()).add(parent)
                else:
                    changed.setdefault(drive_id, set()).add(rel)
        return changed

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
