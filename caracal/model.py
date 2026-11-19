# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Drive model, unit-file parsing, address parsing and status queries.

No Qt imports here so it can be unit tested without a display.
"""

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field

UNIT_DIR = os.environ.get("CARACAL_UNIT_DIR", "/etc/systemd/system")
FSTAB = os.environ.get("CARACAL_FSTAB", "/etc/fstab")
MANAGED_HEADER = "# Managed by Caracal"
ID_PREFIX = "# Caracal-Id: "
SPEC_PREFIX = "# Caracal-Spec: "
# Units written before the rename (CataMount) are still ours.
MANAGED_HEADERS = (MANAGED_HEADER, "# Managed by CataMount")
SPEC_PREFIXES = (SPEC_PREFIX, "# CataMount-Spec: ")
ID_PREFIXES = (ID_PREFIX, "# CataMount-Id: ")
NETWORK_FSTYPES = {"cifs": "SMB", "smb3": "SMB", "nfs": "NFS", "nfs4": "NFS"}


def escape_path(path):
    """Same algorithm as `systemd-escape --path` (and the helper)."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "-"
    out = []
    joined = "/".join(parts)
    for i, ch in enumerate(joined):
        if ch == "/":
            out.append("-")
        elif (ch.isascii() and (ch.isalnum() or ch in ":_.")) and not (i == 0 and ch == "."):
            out.append(ch)
        else:
            out.extend("\\x%02x" % b for b in ch.encode("utf-8"))
    return "".join(out)


# --------------------------------------------------------------------------
# Drives
# --------------------------------------------------------------------------

@dataclass
class Drive:
    spec: dict
    mount_unit: str
    automount_unit: str = ""
    state: str = "unknown"      # connected | ready | connecting | failed | disconnected
    enabled: bool = False

    @property
    def id(self):
        return self.spec.get("id", "")

    @property
    def name(self):
        return self.spec.get("name", self.mount_unit)

    @property
    def kind(self):
        return self.spec.get("type", "")

    @property
    def mount_point(self):
        return self.spec.get("mount_point", "")

    @property
    def remote(self):
        return format_remote(self.kind, self.spec.get("server", ""), self.spec.get("share", ""))

    @property
    def units(self):
        return [u for u in (self.automount_unit, self.mount_unit) if u]


def format_remote(kind, server, share):
    if kind == "smb":
        return "\\\\%s\\%s" % (server, share.replace("/", "\\"))
    if kind == "nfs":
        return "%s:%s" % (server, share)
    return ""


def _read_head(path, max_lines=6):
    lines = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        pass
    return lines


def load_drives(unit_dir=None):
    """Read every Caracal-managed unit and return a list of Drives."""
    unit_dir = unit_dir or UNIT_DIR
    try:
        names = sorted(os.listdir(unit_dir))
    except OSError:
        return []
    drives = {}
    autos = {}
    for name in names:
        path = os.path.join(unit_dir, name)
        if not os.path.isfile(path):
            continue
        if name.endswith(".mount"):
            head = _read_head(path)
            if not head or head[0] not in MANAGED_HEADERS:
                continue
            spec = {}
            for line in head:
                sp = next((p for p in SPEC_PREFIXES if line.startswith(p)), None)
                ip = next((p for p in ID_PREFIXES if line.startswith(p)), None)
                if sp:
                    try:
                        spec = json.loads(line[len(sp):])
                    except ValueError:
                        spec = {}
                elif ip and "id" not in spec:
                    spec.setdefault("id", line[len(ip):].strip())
            if not isinstance(spec, dict):
                spec = {}
            spec.setdefault("name", name)
            drives[name[:-len(".mount")]] = Drive(spec=spec, mount_unit=name)
        elif name.endswith(".automount"):
            head = _read_head(path, 2)
            if head and head[0] in MANAGED_HEADERS:
                autos[name[:-len(".automount")]] = name
    for base, auto in autos.items():
        if base in drives:
            drives[base].automount_unit = auto
    return sorted(drives.values(), key=lambda d: d.name.lower())


def new_drive_id():
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def query_units(units):
    """Return {unit: {"ActiveState":..., "SubState":..., "UnitFileState":...}}."""
    if not units:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "show", "--no-pager",
             "-p", "Id,ActiveState,SubState,UnitFileState", "--", *units],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_systemctl_show(proc.stdout)


def parse_systemctl_show(text):
    result = {}
    for block in text.strip().split("\n\n"):
        props = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        if "Id" in props:
            result[props["Id"]] = props
    return result


def drive_state(drive, info):
    m = info.get(drive.mount_unit, {})
    a = info.get(drive.automount_unit, {}) if drive.automount_unit else {}
    m_state = m.get("ActiveState", "")
    a_state = a.get("ActiveState", "")
    primary = a if drive.automount_unit else m
    enabled = primary.get("UnitFileState", "") in ("enabled", "enabled-runtime", "linked")
    if m_state == "active":
        state = "connected"
    elif m_state in ("activating", "reloading"):
        state = "connecting"
    elif m_state == "failed" or a_state == "failed":
        state = "failed"
    elif a_state == "active":
        state = "ready"
    else:
        state = "disconnected"
    return state, enabled


def refresh_states(drives):
    units = [u for d in drives for u in d.units]
    info = query_units(units)
    for d in drives:
        d.state, d.enabled = drive_state(d, info)


STATE_LABELS = {
    "connected": "Connected",
    "ready": "Ready (connects on access)",
    "connecting": "Connecting…",
    "failed": "Failed",
    "disconnected": "Disconnected",
    "unknown": "Unknown",
}


# --------------------------------------------------------------------------
# fstab (read-only)
# --------------------------------------------------------------------------

@dataclass
class FstabEntry:
    source: str
    target: str
    fstype: str
    options: str
    mounted: bool = False

    @property
    def kind(self):
        return NETWORK_FSTYPES.get(self.fstype, self.fstype)


def _unoctal(text):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def mounted_targets():
    targets = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                fields = line.split()
                if len(fields) > 4:
                    targets.add(_unoctal(fields[4]))
    except OSError:
        pass
    return targets


def load_fstab(path=None):
    path = path or FSTAB
    entries = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return entries
    mounted = mounted_targets()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[2] not in NETWORK_FSTYPES:
            continue
        target = _unoctal(fields[1])
        entries.append(FstabEntry(source=_unoctal(fields[0]), target=target, fstype=fields[2],
                                  options=fields[3] if len(fields) > 3 else "defaults",
                                  mounted=target in mounted))
    return entries


# --------------------------------------------------------------------------
# Address parsing
# --------------------------------------------------------------------------

@dataclass
class Address:
    kind: str = ""          # smb | nfs | "" (unknown)
    server: str = ""
    share: str = ""
    username: str = ""
    domain: str = ""
    errors: list = field(default_factory=list)

    @property
    def valid(self):
        return bool(self.kind and self.server and self.share and not self.errors)


SERVER_RE = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9._-]{0,252}))$")


def _split_user(host):
    user = domain = ""
    if "@" in host:
        user, host = host.rsplit("@", 1)
        if ";" in user:
            domain, user = user.split(";", 1)
        elif "\\" in user:
            domain, user = user.split("\\", 1)
    return user, domain, host


def parse_address(text, default_kind="smb"):
    """Parse what a user might paste: \\\\srv\\share, smb://srv/share,
    //srv/share, nfs://srv/export, srv:/export, or srv/share."""
    text = text.strip()
    addr = Address()
    if not text:
        return addr
    lower = text.lower()
    if text.startswith("\\\\"):
        addr.kind = "smb"
        rest = text[2:].replace("\\", "/")
    elif lower.startswith("smb://") or lower.startswith("cifs://"):
        addr.kind = "smb"
        rest = text.split("://", 1)[1]
    elif lower.startswith("nfs://"):
        addr.kind = "nfs"
        rest = text[6:]
    elif text.startswith("//"):
        addr.kind = "smb"
        rest = text[2:]
    elif re.match(r"^(\[[^\]]+\]|[^/:\[\]]+):(/.*)$", text):
        m = re.match(r"^(\[[^\]]+\]|[^/:\[\]]+):(/.*)$", text)
        addr.kind = "nfs"
        addr.server = m.group(1)
        addr.share = "/" + "/".join(p for p in m.group(2).split("/") if p)
        _validate(addr)
        return addr
    else:
        addr.kind = default_kind
        rest = text.replace("\\", "/")

    host, _, path = rest.partition("/")
    if addr.kind == "smb":
        addr.username, addr.domain, host = _split_user(host)
    elif "@" in host:
        host = host.rsplit("@", 1)[1]
    if addr.kind == "nfs" and host.count(":") == 1 and not host.startswith("["):
        host = host.split(":", 1)[0]  # drop a port, NFS mounts don't take one here
    addr.server = host
    path = "/".join(p for p in path.split("/") if p)
    addr.share = path if addr.kind == "smb" else "/" + path
    _validate(addr)
    return addr


def _validate(addr):
    if not addr.server:
        addr.errors.append("Missing server name.")
    elif not SERVER_RE.match(addr.server):
        addr.errors.append("'%s' is not a valid server name or address." % addr.server)
    if addr.kind == "smb" and not addr.share:
        addr.errors.append("Missing share name (e.g. \\\\server\\share).")
    if addr.kind == "nfs" and addr.share in ("", "/") and addr.server:
        pass  # exporting / is legal for NFSv4 pseudo-root
    if any(p in (".", "..") for p in addr.share.split("/")):
        addr.errors.append("The path cannot contain '.' or '..'.")


def suggest_mount_point(root, addr, taken=()):
    """Suggest an unused folder like /mnt/<share>."""
    leaf = ""
    if addr.share:
        leaf = [p for p in addr.share.split("/") if p][-1:] or [""]
        leaf = leaf[0]
    if not leaf:
        leaf = addr.server.strip("[]").split(".")[0] or "share"
    leaf = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip("-.") or "share"
    root = "/" + root.strip("/") if root.strip("/") else "/mnt"
    candidate = "%s/%s" % (root, leaf)
    n = 2
    while candidate in taken:
        candidate = "%s/%s-%d" % (root, leaf, n)
        n += 1
    return candidate


# --------------------------------------------------------------------------
# Disk space
# --------------------------------------------------------------------------

def disk_usage(path):
    """(used_bytes, total_bytes) for a mounted path. May block on a hung server,
    so call it from a worker thread only, and only for connected drives
    (calling it on an automount point would trigger the mount)."""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return total - free, total


def format_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000 or unit == "PB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit)).replace(".0 ", " ")
        n /= 1000.0
    return ""


def format_usage(used, total):
    if not total:
        return ""
    return "%s free of %s" % (format_size(total - used), format_size(total))


# --------------------------------------------------------------------------
# fstab -> Caracal drive template (migration aid; fstab itself is never edited)
# --------------------------------------------------------------------------

def fstab_to_template(entry, mount_root="/mnt", taken=()):
    kind = "smb" if entry.kind == "SMB" else "nfs"
    addr = parse_address(entry.source, kind)
    opts = {}
    for opt in entry.options.split(","):
        key, _, value = opt.partition("=")
        opts[key.strip().lower()] = value.strip()
    known = {"ro", "rw", "vers", "nfsvers", "username", "user", "domain", "dom", "workgroup",
             "guest", "sec", "seal", "credentials", "cred", "password", "pass", "uid", "gid",
             "defaults", "_netdev", "nofail", "auto", "noauto", "user_xattr", "iocharset",
             "soft", "hard", "timeo", "retrans"}
    extra = [o for o in entry.options.split(",")
             if o and o.split("=", 1)[0].lower() not in known and not o.startswith("x-systemd")]
    spec = {
        "name": "%s on %s" % (([p for p in addr.share.split("/") if p] or [addr.share])[-1],
                              addr.server),
        "type": kind, "server": addr.server, "share": addr.share,
        "mount_point": suggest_mount_point(mount_root, addr, set(taken) | {entry.target}),
        "read_only": "ro" in opts,
        "automount": True, "start_at_boot": True,
        "extra_options": ",".join(extra),
    }
    if kind == "smb":
        version = opts.get("vers", "")
        spec["smb"] = {
            "username": opts.get("username") or opts.get("user") or addr.username,
            "domain": opts.get("domain") or opts.get("dom") or opts.get("workgroup") or addr.domain,
            "guest": "guest" in opts,
            "kerberos": opts.get("sec", "").startswith("krb5"),
            "seal": "seal" in opts,
            "version": version if version in ("3.1.1", "3.02", "3.0", "2.1", "2.0", "1.0") else "",
        }
    else:
        version = opts.get("vers") or opts.get("nfsvers") or ("4" if entry.fstype == "nfs4" else "")
        spec["nfs"] = {"version": version if version in ("4.2", "4.1", "4", "3") else "",
                       "soft": "soft" in opts}
    return spec
