# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Keep the UI responsive around network mounts.

A stat, listdir or statvfs on an SMB/NFS/SFTP mount whose server is slow or gone can block
for many seconds (forever on a hard NFS mount). The GUI thread must never wait on that.
This module knows which paths are network mounts from the kernel's mount table (reading it
never touches the share) and runs file calls on those paths in a worker thread with a
timeout. A mount that doesn't answer in time is marked unresponsive for a while so later
calls fail fast instead of piling up stuck threads."""

import os
import threading
import time

REMOTE_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "sshfs", "fuse.rclone",
             "rclone", "davfs", "fuse.davfs2", "fuse.curlftpfs", "9p", "ceph", "glusterfs",
             "fuse.glusterfs", "afs", "fuse.gvfsd-fuse", "fuse.s3fs", "fuse.smbnetfs",
             # systemd .automount points: touching one starts the network mount
             "autofs"}
DEAD_FOR = 30.0          # seconds a non-answering mount is skipped
DEFAULT_TIMEOUT = 1.5

_lock = threading.Lock()
_mounts = []             # [(mount point, fstype)] longest first
_mounts_at = 0.0
_dead = {}               # mount point -> time it may be tried again


def _unoctal(s):
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n") \
        .replace("\\134", "\\")


def mounts(max_age=2.0):
    """[(mount point, fstype)], longest mount point first. Cached for a couple of seconds."""
    global _mounts, _mounts_at
    with _lock:
        if time.monotonic() - _mounts_at < max_age:
            return _mounts
    out = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                f, r = left.split(), right.split()
                if len(f) >= 5 and r:
                    out.append((_unoctal(f[4]), r[0]))
    except OSError:
        pass
    out.sort(key=lambda m: len(m[0]), reverse=True)
    with _lock:
        _mounts, _mounts_at = out, time.monotonic()
    return out


def invalidate():
    global _mounts_at
    with _lock:
        _mounts_at = 0.0


def mount_for(path):
    """(mount point, fstype) of the filesystem `path` is on, from the table alone."""
    if not path or not path.startswith("/"):
        return ("", "")
    p = os.path.normpath(path)
    for mp, fstype in mounts():
        if p == mp or p.startswith(mp.rstrip("/") + "/") or mp == "/":
            return (mp, fstype)
    return ("/", "")


def is_remote(path):
    return mount_for(path)[1] in REMOTE_FS


def is_mounted(mount_point):
    """True if a real filesystem is mounted exactly at `mount_point` (an automount trap
    alone doesn't count). Reads the mount table only, so it never blocks."""
    mp = os.path.normpath(mount_point or "")
    return any(m == mp and t != "autofs" for m, t in mounts())


def is_unresponsive(path):
    mp = mount_for(path)[0]          # outside the lock (mounts() takes it)
    with _lock:
        until = _dead.get(mp)
        if until and until > time.monotonic():
            return True
        _dead.pop(mp, None)
    return False


def mark_responsive(path):
    mp = mount_for(path)[0]
    with _lock:
        _dead.pop(mp, None)


def call(fn, path, *args, timeout=DEFAULT_TIMEOUT, default=None, mark_dead=True):
    """fn(path, *args), but on a network mount only wait `timeout` seconds. Returns
    `default` on OSError, timeout, or when the mount recently didn't answer."""
    if not is_remote(path):
        try:
            return fn(path, *args)
        except (OSError, ValueError):
            return default
    if is_unresponsive(path):
        return default
    box = {}
    done = threading.Event()

    def run():
        try:
            box["v"] = fn(path, *args)
        except (OSError, ValueError):
            box["v"] = default
        done.set()

    threading.Thread(target=run, daemon=True, name="fsinfo").start()
    if not done.wait(timeout):
        if not mark_dead:
            return default
        mp = mount_for(path)[0]          # (takes the lock itself: look it up first)
        with _lock:
            _dead[mp] = time.monotonic() + DEAD_FOR
        return default
    return box.get("v", default)


# convenience wrappers ------------------------------------------------------

def exists(path, timeout=DEFAULT_TIMEOUT):
    return bool(call(os.path.exists, path, timeout=timeout, default=False))


def isdir(path, timeout=DEFAULT_TIMEOUT):
    return bool(call(os.path.isdir, path, timeout=timeout, default=False))


def isfile(path, timeout=DEFAULT_TIMEOUT):
    return bool(call(os.path.isfile, path, timeout=timeout, default=False))


def isdir_or_assume(path):
    """For lists of remembered places: network folders are assumed to still exist
    (checking would wake or wait on the server); local ones are checked."""
    return True if is_remote(path) else os.path.isdir(path)


def realpath(path):
    """os.path.realpath, except network paths are only normalized (resolving symlinks
    would stat every component on the share)."""
    if is_remote(path):
        return os.path.normpath(path)
    return os.path.realpath(path)


def statvfs(path, timeout=DEFAULT_TIMEOUT):
    return call(os.statvfs, path, timeout=timeout, default=None)


def listdirs(path, limit=500, timeout=4.0):
    """Sorted visible subfolder names, or None if the folder couldn't be read in time."""
    def scan(p):
        with os.scandir(p) as it:
            out = []
            for e in it:
                if not e.name.startswith("."):
                    try:
                        if e.is_dir():
                            out.append(e.name)
                    except OSError:
                        pass
            return sorted(out, key=str.lower)[:limit]
    return call(scan, path, timeout=timeout, default=None)
