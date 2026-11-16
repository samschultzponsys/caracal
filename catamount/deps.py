# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Runtime checks for the host tools CataMount relies on."""

import os
import shutil

SBIN = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")

# tool -> (what it is for, Fedora package, Debian/Ubuntu package)
TOOLS = {
    "mount.cifs": ("SMB drives", "cifs-utils", "cifs-utils"),
    "mount.nfs": ("NFS drives", "nfs-utils", "nfs-common"),
    "pkexec": ("making changes", "polkit", "pkexec"),
    "systemctl": ("everything", "systemd", "systemd"),
}


def _find(tool):
    for d in SBIN:
        p = os.path.join(d, tool)
        if os.access(p, os.X_OK):
            return p
    return shutil.which(tool)


def missing():
    """Return [(tool, purpose, fedora_pkg, debian_pkg)] for tools that are not installed."""
    return [(t, *info) for t, info in TOOLS.items() if not _find(t)]


def is_fedora_like():
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            text = fh.read().lower()
    except OSError:
        return False
    return any(k in text for k in ("fedora", "rhel", "centos", "rocky", "alma"))


def install_hint(items):
    pkgs = sorted({(f if is_fedora_like() else d) for _t, _p, f, d in items})
    cmd = "sudo dnf install " if is_fedora_like() else "sudo apt install "
    return cmd + " ".join(pkgs)


APPINDICATOR_UUID = "appindicatorsupport@rgcjonas.gmail.com"


def is_gnome():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return "gnome" in desktop and "ubuntu" not in desktop


def appindicator_status():
    """On GNOME: 'enabled', 'disabled' or 'missing' for the tray extension. None elsewhere."""
    if not is_gnome() or not shutil.which("gnome-extensions"):
        return None
    import subprocess
    try:
        proc = subprocess.run(["gnome-extensions", "info", APPINDICATOR_UUID],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return "missing"
    for line in proc.stdout.splitlines():
        key, _, value = line.strip().partition(":")
        if key.strip().lower() in ("enabled", "state"):
            v = value.strip().lower()
            return "enabled" if v in ("yes", "true", "enabled", "active") else "disabled"
    return "disabled"


def enable_appindicator():
    import subprocess
    try:
        return subprocess.run(["gnome-extensions", "enable", APPINDICATOR_UUID],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def appindicator_install_hint():
    pkg = "gnome-shell-extension-appindicator"
    return ("sudo dnf install " if is_fedora_like() else "sudo apt install ") + pkg
