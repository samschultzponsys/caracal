# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Copy / move / delete / trash with progress, conflicts, cancel and undo.

The work runs in a Python thread; FileOperation emits Qt signals so a dialog can show
progress and ask what to do on name conflicts (the worker waits for the answer).
"""

import errno
import os
import shutil
import stat
import threading
import time

from PySide6.QtCore import QFile, QObject, Signal

CHUNK = 1024 * 1024

REPLACE, SKIP, KEEP_BOTH, CANCEL = "replace", "skip", "keep_both", "cancel"


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------
# freedesktop.org Trash (same layout Dolphin / Files / Nemo use)
# --------------------------------------------------------------------------

def _home_trash():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Trash")


def _mount_root(path):
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def trash_dir_for(path):
    """Trash folder for a path: the home trash on the home disk, else $top/.Trash-$uid."""
    home_trash = _home_trash()
    os.makedirs(os.path.dirname(home_trash), exist_ok=True)
    if same_device(os.path.dirname(os.path.abspath(path)), os.path.dirname(home_trash)):
        return home_trash
    top = _mount_root(path)
    shared = os.path.join(top, ".Trash")
    try:
        st = os.lstat(shared)
        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_mode & stat.S_ISVTX:
            return os.path.join(shared, str(os.getuid()))
    except OSError:
        pass
    return os.path.join(top, ".Trash-%d" % os.getuid())


def trash_info_path(trashed):
    """The .trashinfo file for a path inside a Trash/files folder."""
    files_dir = os.path.dirname(trashed)
    return os.path.join(os.path.dirname(files_dir), "info", os.path.basename(trashed) + ".trashinfo")


def trash_original(trashed):
    """(original path, deletion date string) from the .trashinfo, or (None, "")."""
    from urllib.parse import unquote
    try:
        with open(trash_info_path(trashed), encoding="utf-8") as fh:
            info = dict(l.strip().split("=", 1) for l in fh if "=" in l)
    except OSError:
        return None, ""
    path = unquote(info.get("Path", ""))
    if path and not path.startswith("/"):        # relative to the disk's top folder
        path = os.path.join(_mount_root(trashed), path)
    return path or None, info.get("DeletionDate", "").replace("T", " ")


def restore_from_trash(trashed):
    """Put a trashed item back where it was. Returns the restored path or raises OSError."""
    original, _date = trash_original(trashed)
    if not original:
        raise OSError(errno.ENOENT, "No record of where %s came from" % os.path.basename(trashed))
    os.makedirs(os.path.dirname(original), exist_ok=True)
    target = original
    if os.path.lexists(target):
        target = os.path.join(os.path.dirname(original),
                              unique_name(os.path.dirname(original), os.path.basename(original), "n"))
    shutil.move(trashed, target)
    try:
        os.unlink(trash_info_path(trashed))
    except OSError:
        pass
    return target


ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
                ".tar.zst", ".7z", ".rar")


def is_archive(path):
    return os.path.isfile(path) and path.lower().endswith(ARCHIVE_EXTS)


def extract_archive(path, dest_parent=None):
    """Extract into a new folder named after the archive. Returns that folder."""
    import subprocess
    low = path.lower()
    base = os.path.basename(path)
    for ext in sorted(ARCHIVE_EXTS, key=len, reverse=True):
        if low.endswith(ext):
            base = base[:-len(ext)]
            break
    parent = dest_parent or os.path.dirname(path)
    name = base if not os.path.exists(os.path.join(parent, base)) else unique_name(parent, base, "n")
    dest = os.path.join(parent, name)
    os.makedirs(dest)
    try:
        if low.endswith((".7z", ".rar", ".tar.zst")):
            tool = shutil.which("7z") or shutil.which("7za") or shutil.which("bsdtar")
            if not tool:
                raise OSError(errno.ENOENT, "Install p7zip or bsdtar to extract this kind of archive")
            args = [tool, "x", "-o" + dest, path] if "7z" in os.path.basename(tool) \
                else [tool, "-xf", path, "-C", dest]
            proc = subprocess.run(args, capture_output=True, text=True)
            if proc.returncode != 0:
                raise OSError(errno.EIO, (proc.stderr or proc.stdout).strip()[-300:])
        elif low.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(path) as zf:
                for member in zf.infolist():
                    target = os.path.realpath(os.path.join(dest, member.filename))
                    if not target.startswith(os.path.realpath(dest) + os.sep) and \
                            target != os.path.realpath(dest):
                        raise OSError(errno.EPERM, "Unsafe path in archive: %s" % member.filename)
                zf.extractall(dest)
        else:
            import tarfile
            with tarfile.open(path) as tf:
                tf.extractall(dest, filter="data")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def make_zip(paths, target):
    import zipfile
    root = os.path.dirname(paths[0])
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if os.path.isdir(p):
                for d, _dirs, files in os.walk(p):
                    for f in files:
                        full = os.path.join(d, f)
                        zf.write(full, os.path.relpath(full, root))
            else:
                zf.write(p, os.path.basename(p))
    return target


def trash_item(path):
    """Move one item to the trash. Returns the path inside Trash/files, or raises OSError."""
    from urllib.parse import quote
    trash = trash_dir_for(path)
    files, info = os.path.join(trash, "files"), os.path.join(trash, "info")
    os.makedirs(files, mode=0o700, exist_ok=True)
    os.makedirs(info, mode=0o700, exist_ok=True)
    name = os.path.basename(path.rstrip("/"))
    candidate, n = name, 1
    while True:
        try:
            fd = os.open(os.path.join(info, candidate + ".trashinfo"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
            base, ext = os.path.splitext(name)
            candidate = "%s.%d%s" % (base, n, ext)
    top = _mount_root(path)
    stored = os.path.abspath(path)
    if trash != _home_trash() and stored.startswith(top.rstrip("/") + "/"):
        stored = os.path.relpath(stored, top)        # relative paths on removable disks
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n"
                 % (quote(stored), time.strftime("%Y-%m-%dT%H:%M:%S")))
    target = os.path.join(files, candidate)
    try:
        os.rename(path, target)
    except OSError:
        os.unlink(os.path.join(info, candidate + ".trashinfo"))
        raise
    return target


def unique_name(folder, name, style="copy"):
    """'Report.docx' -> 'Report - Copy.docx' / 'Report (2).docx' (Explorer style)."""
    base, ext = os.path.splitext(name)
    if name.startswith(".") and base == name:
        base, ext = name, ""
    candidates = []
    if style == "copy":
        candidates.append("%s - Copy%s" % (base, ext))
        candidates += ["%s - Copy (%d)%s" % (base, i, ext) for i in range(2, 1000)]
    else:
        candidates += ["%s (%d)%s" % (base, i, ext) for i in range(2, 1000)]
    for c in candidates:
        if not os.path.lexists(os.path.join(folder, c)):
            return c
    return "%s %d%s" % (base, int(time.time()), ext)


def is_inside(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def same_device(a, b):
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def tree_size(paths, should_stop=lambda: False):
    files = size = 0
    for p in paths:
        if should_stop():
            raise Cancelled()
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            for root, dirs, fs in os.walk(p):
                if should_stop():
                    raise Cancelled()
                for f in fs:
                    try:
                        size += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
                    files += 1
        else:
            files += 1
            size += st.st_size
    return files, size


class FileOperation(QObject):
    """kind: copy | move | trash | delete. Signals are delivered on the UI thread."""

    progress = Signal(dict)
    conflict = Signal(dict)
    failed = Signal(str)
    done = Signal(dict)        # {"undo": record or None, "created": [...], "errors": [...]}

    def __init__(self, kind, sources, dest=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.sources = [os.path.abspath(s) for s in sources]
        self.dest = os.path.abspath(dest) if dest else None
        self._cancel = threading.Event()
        self._answer = threading.Event()
        self._choice = None
        self._apply_all = None
        self.errors = []
        self.created = []
        self.moves = []          # (src, dst) for undo
        self._skipped_tops = set()
        self.trashed = []        # (original, trash_path) for undo
        self.total_files = self.total_bytes = 0
        self.done_files = self.done_bytes = 0
        self.current = ""
        self.started = 0.0
        self._last = 0.0
        self.thread = None

    # -- control from the UI ---------------------------------------------------

    def start(self):
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self):
        self._cancel.set()
        self._choice = CANCEL
        self._answer.set()

    def answer(self, choice, apply_all=False):
        self._choice = choice
        if apply_all:
            self._apply_all = choice
        self._answer.set()

    # -- worker -------------------------------------------------------------------

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _report(self, force=False):
        now = time.monotonic()
        if force or now - self._last > 0.2:
            self._last = now
            elapsed = max(0.001, now - self.started)
            self.progress.emit({"kind": self.kind, "current": self.current,
                                "files": self.done_files, "total_files": self.total_files,
                                "bytes": self.done_bytes, "total_bytes": self.total_bytes,
                                "rate": self.done_bytes / elapsed})

    def _ask(self, src, dst):
        if self._apply_all:
            return self._apply_all
        self._answer.clear()
        try:
            s_st, d_st = os.stat(src), os.stat(dst)
            info = {"name": os.path.basename(dst), "src": src, "dst": dst,
                    "src_size": s_st.st_size, "dst_size": d_st.st_size,
                    "src_mtime": s_st.st_mtime, "dst_mtime": d_st.st_mtime}
        except OSError:
            info = {"name": os.path.basename(dst), "src": src, "dst": dst}
        self.conflict.emit(info)
        self._answer.wait()
        self._check()
        return self._choice

    def _run(self):
        try:
            if self.kind in ("copy", "move"):
                self._transfer()
            elif self.kind == "trash":
                self._trash()
            elif self.kind == "delete":
                self._delete()
        except Cancelled:
            pass
        except Exception as exc:  # report instead of dying silently
            self.errors.append(str(exc))
        undo = None
        if self.kind == "copy" and self.created:
            undo = {"kind": "copy", "created": list(self.created)}
        elif self.kind == "move" and self.moves:
            undo = {"kind": "move", "moves": list(self.moves)}
        elif self.kind == "trash" and self.trashed:
            undo = {"kind": "trash", "items": list(self.trashed)}
        self._report(force=True)
        self.done.emit({"undo": undo, "created": list(self.created), "errors": list(self.errors),
                        "cancelled": self._cancel.is_set()})

    # -- copy / move -----------------------------------------------------------------

    def _transfer(self):
        for src in self.sources:
            if os.path.isdir(src) and is_inside(self.dest, src):
                raise OSError(errno.EINVAL, "Can't put a folder inside itself: %s"
                              % os.path.basename(src))
        # A same-disk move is a rename: instant, no copying.
        if self.kind == "move" and all(same_device(s, self.dest) for s in self.sources):
            self.total_files = len(self.sources)
            for src in self.sources:
                self._check()
                self._move_rename(src, os.path.join(self.dest, os.path.basename(src)))
            return
        self.current = "Calculating…"
        self._report(force=True)
        self.total_files, self.total_bytes = tree_size(self.sources, self._cancel.is_set)
        for src in self.sources:
            self._check()
            name = os.path.basename(src)
            if self.kind == "copy" and os.path.dirname(src) == self.dest:
                dst = os.path.join(self.dest, unique_name(self.dest, name, "copy"))
            else:
                dst = os.path.join(self.dest, name)
            self._copy_any(src, dst, top=True)
            if self.kind == "move" and src not in self._skipped_tops:
                self._remove_source(src)

    def _move_rename(self, src, dst):
        if os.path.abspath(src) == os.path.abspath(dst):
            return
        if os.path.lexists(dst):
            if os.path.isdir(dst) and os.path.isdir(src):
                # Merge folders: move each child.
                for child in os.listdir(src):
                    self._move_rename(os.path.join(src, child), os.path.join(dst, child))
                try:
                    os.rmdir(src)
                except OSError:
                    pass
                return
            choice = self._ask(src, dst)
            if choice == SKIP:
                return
            if choice == KEEP_BOTH:
                dst = os.path.join(os.path.dirname(dst),
                                   unique_name(os.path.dirname(dst), os.path.basename(dst), "n"))
            elif choice == REPLACE:
                self._rm(dst)
        self.current = os.path.basename(src)
        os.rename(src, dst)
        self.moves.append((src, dst))
        self.done_files += 1
        self._report()

    def _copy_any(self, src, dst, top=False):
        self._check()
        try:
            st = os.lstat(src)
        except OSError as exc:
            self.errors.append("%s: %s" % (src, exc.strerror))
            return
        if stat.S_ISDIR(st.st_mode):
            if os.path.lexists(dst) and not os.path.isdir(dst):
                choice = self._ask(src, dst)
                if choice == SKIP:
                    if top:
                        self._skipped_tops.add(src)
                    return
                if choice == KEEP_BOTH:
                    dst = os.path.join(os.path.dirname(dst),
                                       unique_name(os.path.dirname(dst), os.path.basename(dst), "n"))
                else:
                    self._rm(dst)
            new = not os.path.exists(dst)
            os.makedirs(dst, exist_ok=True)
            if new and top:
                self.created.append(dst)
            try:
                entries = os.listdir(src)
            except OSError as exc:
                self.errors.append("%s: %s" % (src, exc.strerror))
                return
            for child in entries:
                self._copy_any(os.path.join(src, child), os.path.join(dst, child))
            try:
                shutil.copystat(src, dst)
            except OSError:
                pass
            return
        if os.path.lexists(dst):
            choice = self._ask(src, dst)
            if choice == SKIP:
                self.done_files += 1
                self.done_bytes += st.st_size
                if top:
                    self._skipped_tops.add(src)
                return
            if choice == KEEP_BOTH:
                dst = os.path.join(os.path.dirname(dst),
                                   unique_name(os.path.dirname(dst), os.path.basename(dst), "n"))
        self.current = os.path.basename(src)
        if stat.S_ISLNK(st.st_mode):
            if os.path.lexists(dst):
                os.unlink(dst)
            os.symlink(os.readlink(src), dst)
        else:
            self._copy_file(src, dst)
        if top:
            self.created.append(dst)
        self.done_files += 1
        self._report()

    def _copy_file(self, src, dst):
        tmp = dst + ".caracal-part"
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    self._check()
                    buf = fin.read(CHUNK)
                    if not buf:
                        break
                    fout.write(buf)
                    self.done_bytes += len(buf)
                    self._report()
            shutil.copystat(src, tmp)
            os.replace(tmp, dst)
        except Cancelled:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            self.errors.append("%s: %s" % (os.path.basename(src), exc.strerror or exc))

    def _remove_source(self, src):
        if self.errors:
            return  # never delete sources after a failed copy
        try:
            self._rm(src)
            self.moves.append((src, os.path.join(self.dest, os.path.basename(src))))
        except OSError as exc:
            self.errors.append("Copied but couldn't remove %s: %s" % (src, exc.strerror))

    @staticmethod
    def _rm(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

    # -- trash / delete ------------------------------------------------------------------

    def _trash(self):
        self.total_files = len(self.sources)
        for src in self.sources:
            self._check()
            self.current = os.path.basename(src)
            try:
                trashed = trash_item(src)
                self.trashed.append((src, trashed))
            except OSError:
                ok, trashed = QFile.moveToTrash(src)
                if ok:
                    self.trashed.append((src, trashed))
                else:
                    self.errors.append("NOTRASH:" + src)
            self.done_files += 1
            self._report()

    def _delete(self):
        self.total_files = len(self.sources)
        for src in self.sources:
            self._check()
            self.current = os.path.basename(src)
            try:
                self._rm(src)
            except OSError as exc:
                self.errors.append("%s: %s" % (os.path.basename(src), exc.strerror))
            self.done_files += 1
            self._report()


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

def undo(record):
    """Reverse a finished operation. Returns a list of error strings."""
    errors = []
    kind = record.get("kind")
    if kind == "rename":
        try:
            os.rename(record["new"], record["old"])
        except OSError as exc:
            errors.append(exc.strerror or str(exc))
    elif kind == "mkdir":
        try:
            os.rmdir(record["path"])
        except OSError as exc:
            errors.append(exc.strerror or str(exc))
    elif kind == "create":
        try:
            os.unlink(record["path"])
        except OSError as exc:
            errors.append(exc.strerror or str(exc))
    elif kind == "move":
        for src, dst in reversed(record["moves"]):
            try:
                if os.path.lexists(src):
                    errors.append("%s already exists" % src)
                    continue
                shutil.move(dst, src)
            except OSError as exc:
                errors.append(exc.strerror or str(exc))
    elif kind == "copy":
        for path in record["created"]:
            try:
                trash_item(path)
            except OSError:
                ok, _t = QFile.moveToTrash(path)
                if not ok:
                    errors.append("Couldn't remove %s" % path)
    elif kind == "trash":
        for original, trashed in record["items"]:
            try:
                if os.path.lexists(original):
                    errors.append("%s already exists" % original)
                    continue
                shutil.move(trashed, original)
                info = os.path.join(os.path.dirname(os.path.dirname(trashed)), "info",
                                    os.path.basename(trashed) + ".trashinfo")
                if os.path.exists(info):
                    os.unlink(info)
            except OSError as exc:
                errors.append(exc.strerror or str(exc))
    return errors
