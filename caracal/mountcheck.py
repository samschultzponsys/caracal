# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Find stuck network mounts (any, not only Caracal's) and test whether Flatpak apps start.

A dead SMB/NFS/SFTP/FTP/WebDAV mount — "Transport endpoint is not connected", "Stale file
handle", or one that never answers — breaks anything that walks the folder tree. Flatpak's
sandbox setup does that for every app, so one dead mount can stop all Flatpak apps from
opening. No Qt imports; every check runs with a timeout."""

import concurrent.futures
import errno
import os
import re
import shutil
import subprocess

NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "davfs",
              "fuse.davfs2", "fuse.curlftpfs", "fuse.s3fs", "fuse.smbnetfs", "fuse.gcsfuse",
              "ceph", "glusterfs", "fuse.glusterfs"}
HARD = {errno.ENOTCONN: "Disconnected (transport endpoint is not connected)",
        errno.ESTALE: "Stale (the server forgot this mount)",
        errno.EIO: "I/O error", errno.EHOSTDOWN: "Server is down",
        errno.EHOSTUNREACH: "Server unreachable"}


def network_mounts(path="/proc/self/mountinfo"):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                f, r = left.split(), right.split()
                if len(f) >= 5 and len(r) >= 2 and r[0] in NETWORK_FS:
                    mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), f[4])
                    out.append({"path": mp, "fstype": r[0], "source": r[1]})
    except OSError:
        pass
    return out


def _probe(path, timeout):
    """'ok' | 'dead' | 'slow', message — statfs in a child process we can abandon."""
    try:
        proc = subprocess.run(["stat", "-f", "--", path], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return "slow", "Not responding (no answer in %d s)" % timeout
    except OSError:
        try:
            os.statvfs(path)
            return "ok", "OK"
        except OSError as exc:
            return ("dead", HARD[exc.errno]) if exc.errno in HARD else ("ok", "OK")
    if proc.returncode == 0:
        return "ok", "OK"
    err = (proc.stderr or "").strip().rsplit(": ", 1)[-1]
    low = err.lower()
    for text in ("transport endpoint is not connected", "stale file handle",
                 "input/output error", "host is down", "no route to host"):
        if text in low:
            return "dead", err
    return "ok", err or "OK"


def check(timeout=6):
    """Every network mount with its status: [{path, fstype, source, status, message}]."""
    mounts = network_mounts()
    if not mounts:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda m: _probe(m["path"], timeout), mounts))
    for m, (status, msg) in zip(mounts, results):
        m["status"], m["message"] = status, msg
    return mounts


def stuck(results):
    return [m for m in results if m["status"] != "ok"]


def flatpak_apps():
    exe = shutil.which("flatpak")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "list", "--app", "--columns=application,name"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    apps = []
    for line in out.splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip():
            apps.append((parts[0].strip(), (parts[1] if len(parts) > 1 else parts[0]).strip()))
    return apps


def test_flatpak(app_id, timeout=45):
    """Start the app's sandbox without the app itself (runs `true` inside it).
    Returns (ok, output)."""
    exe = shutil.which("flatpak")
    if not exe:
        return False, "Flatpak isn't installed."
    try:
        proc = subprocess.run([exe, "run", "--command=true", app_id], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, ("The sandbox didn't start within %d s — typically a network mount "
                       "that doesn't answer." % timeout)
    except OSError as exc:
        return False, str(exc)
    out = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return proc.returncode == 0, out or ("OK" if proc.returncode == 0 else
                                         "Exited with code %d" % proc.returncode)
