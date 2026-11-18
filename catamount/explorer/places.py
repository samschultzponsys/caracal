# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Quick access folders, drives and removable devices. No Qt imports."""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

USER_DIRS = (("DESKTOP", "Desktop", "user-desktop"), ("DOWNLOAD", "Downloads", "folder-download"),
             ("DOCUMENTS", "Documents", "folder-documents"), ("PICTURES", "Pictures", "folder-pictures"),
             ("MUSIC", "Music", "folder-music"), ("VIDEOS", "Videos", "folder-videos"))
HIDDEN_MOUNTS = ("/boot", "/efi", "/proc", "/sys", "/dev", "/run", "/tmp", "/var", "/snap",
                 "/usr", "/opt", "/srv", "/root")
REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3",
           "fuseblk", "zfs", "jfs", "reiserfs", "hfsplus", "iso9660", "udf", "bcachefs"}
NET_FS = {"cifs", "smb3", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.gvfsd-fuse"}


def home():
    return os.path.expanduser("~")


def user_dirs():
    """[(name, path, icon)] from ~/.config/user-dirs.dirs (XDG), falling back to defaults."""
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config"),
                       "user-dirs.dirs")
    found = {}
    try:
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^XDG_(\w+)_DIR="(.*)"', line.strip())
                if m:
                    found[m.group(1)] = m.group(2).replace("$HOME", home())
    except OSError:
        pass
    out = []
    for key, label, icon in USER_DIRS:
        path = found.get(key, os.path.join(home(), label))
        if os.path.isdir(path) and os.path.abspath(path) != home():
            out.append((os.path.basename(path) or label, path, icon))
    return out


def templates_dir():
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config"),
                       "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^XDG_TEMPLATES_DIR="(.*)"', line.strip())
                if m:
                    return m.group(1).replace("$HOME", home())
    except OSError:
        pass
    return os.path.join(home(), "Templates")


@dataclass
class Volume:
    label: str
    path: str            # mount point ("" when not mounted)
    device: str = ""
    fstype: str = ""
    removable: bool = False
    size: int = 0
    used: int = 0

    @property
    def mounted(self):
        return bool(self.path)


def _unoctal(text):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def mounted_volumes():
    """Local disks the user would see in This PC (no network, no system mounts)."""
    vols = {}
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    for line in lines:
        left, _, right = line.partition(" - ")
        f = left.split()
        r = right.split()
        if len(f) < 5 or len(r) < 2:
            continue
        mp, fstype, source = _unoctal(f[4]), r[0], r[1]
        if fstype not in REAL_FS:
            continue
        if mp != "/" and (mp.startswith(HIDDEN_MOUNTS) and not mp.startswith("/run/media/")):
            continue
        if source in vols.values():
            continue
        vols[mp] = source
    out = []
    for mp, source in vols.items():
        try:
            st = os.statvfs(mp)
            size, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        except OSError:
            size = free = 0
        if mp == "/":
            label = "Local Disk (/)"
        elif mp == home():
            label = "Home (%s)" % mp
        else:
            label = os.path.basename(mp) or mp
        out.append(Volume(label, mp, source, "", mp.startswith(("/run/media/", "/media/")),
                          size, size - free))
    out.sort(key=lambda v: (v.path != "/", v.removable, v.label.lower()))
    return out


def unmounted_devices():
    """Partitions with a filesystem that aren't mounted (USB sticks etc.), via lsblk."""
    if not shutil.which("lsblk"):
        return []
    try:
        proc = subprocess.run(["lsblk", "-J", "-b", "-o",
                               "PATH,LABEL,SIZE,FSTYPE,MOUNTPOINTS,RM,HOTPLUG,TYPE"],
                              capture_output=True, text=True, timeout=5)
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []

    def walk(devs):
        for d in devs or []:
            mps = [m for m in (d.get("mountpoints") or []) if m]
            if d.get("fstype") and not mps and d.get("type") in ("part", "disk", "crypt") \
                    and (d.get("rm") or d.get("hotplug")) and d.get("fstype") not in ("swap", "crypto_LUKS"):
                label = d.get("label") or "%s drive" % human(int(d.get("size") or 0))
                out.append(Volume(label, "", d.get("path", ""), d.get("fstype", ""), True,
                                  int(d.get("size") or 0), 0))
            walk(d.get("children"))

    walk(data.get("blockdevices"))
    return out


def mount_device(device):
    """Mount a removable device with udisksctl (no root needed). Returns the mount point."""
    proc = subprocess.run(["udisksctl", "mount", "-b", device, "--no-user-interaction"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or "Could not mount %s" % device)
    m = re.search(r" at (.+?)\.?$", proc.stdout.strip())
    return m.group(1) if m else ""


def eject(path_or_device):
    dev = path_or_device
    if path_or_device.startswith("/") and not path_or_device.startswith("/dev/"):
        for v in mounted_volumes():
            if v.path == path_or_device:
                dev = v.device
    subprocess.run(["udisksctl", "unmount", "-b", dev], capture_output=True, timeout=60)
    subprocess.run(["udisksctl", "power-off", "-b", dev], capture_output=True, timeout=60)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit)).replace(".0 ", " ")
        n /= 1000.0
    return ""
