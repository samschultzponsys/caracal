# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Spot a faster way to reach a share: many NAS boxes (TrueNAS, Synology, Unraid, QNAP)
export the same folder over both SMB and NFS, and NFS is usually quicker and lighter from
Linux. Probing is quiet and cached: a TCP check of port 2049, then `showmount -e`.
No Qt imports."""

import os
import shutil
import socket
import subprocess
import threading
import time

CACHE_FOR = 30 * 60
_cache = {}          # server -> (time, exports list or None)
_lock = threading.Lock()


def port_open(server, port=2049, timeout=1.5):
    try:
        with socket.create_connection((server, port), timeout=timeout):
            return True
    except OSError:
        return False


def nfs_exports(server, timeout=6):
    """Export paths the server offers over NFS, [] if it answers with none, or None if it
    doesn't offer NFS (or can't be asked). Cached for 30 minutes."""
    with _lock:
        hit = _cache.get(server)
        if hit and time.monotonic() - hit[0] < CACHE_FOR:
            return hit[1]
    result = None
    if port_open(server):
        result = []
        exe = shutil.which("showmount") or next(
            (p for p in ("/usr/sbin/showmount", "/sbin/showmount") if os.path.exists(p)), None)
        if exe:
            try:
                out = subprocess.run([exe, "-e", "--no-headers", server], capture_output=True,
                                     text=True, timeout=timeout).stdout
                result = [line.split()[0] for line in out.splitlines() if line.strip()]
            except (OSError, subprocess.SubprocessError):
                pass
    with _lock:
        _cache[server] = (time.monotonic(), result)
    return result


def match_export(share, exports):
    """The export that most likely is the same folder as SMB share `share`."""
    leaf = [p for p in share.replace("\\", "/").split("/") if p]
    if not leaf or not exports:
        return None
    want = leaf[-1].lower()
    exact = [e for e in exports if e.rstrip("/").rsplit("/", 1)[-1].lower() == want]
    if exact:
        return min(exact, key=len)
    loose = [e for e in exports if want in e.lower()]
    return min(loose, key=len) if len(loose) == 1 else None


def nfs_alternative(server, share):
    """{"export": path or None, "exports": [...]} if the server offers NFS, else None."""
    exports = nfs_exports(server)
    if exports is None:
        return None
    return {"export": match_export(share, exports), "exports": exports}


def cached(server):
    with _lock:
        hit = _cache.get(server)
    return hit[1] if hit else None
