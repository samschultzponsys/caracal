# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Per-user network locations: SFTP/SCP (sshfs) and FTP/FTPS/WebDAV (rclone).

These are FUSE mounts owned by the user, so no admin password is needed. Each location
is a systemd *user* service (~/.config/systemd/user/caracal-<id>.service) whose first
lines mark it as Caracal's, like the system drive units. Passwords are kept in
~/.config/caracal/secrets/ (0600; rclone stores them obscured in its own 0600 config).
No Qt imports.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass

MANAGED_HEADER = "# Managed by Caracal"
SPEC_PREFIX = "# Caracal-Spec: "
LEGACY = ("# Managed by CataMount", "# CataMount-Spec: ", "catamount-")   # before the rename
UNIT_PREFIX = "caracal-"
KINDS = {"sftp": "SFTP / SCP (SSH)", "ftp": "FTP", "ftps": "FTPS (FTP over TLS)",
         "webdav": "WebDAV (HTTPS)"}
DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21, "webdav": 443}

HOST_RE = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9._-]{0,252}))$")
USER_RE = re.compile(r"^[A-Za-z0-9._@+\\-]{0,128}$")
ID_RE = re.compile(r"^[0-9a-f-]{36}$")


class LocationError(Exception):
    pass


def config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def unit_dir():
    return os.path.join(config_home(), "systemd", "user")


def secrets_dir():
    return os.path.join(config_home(), "caracal", "secrets")


def rclone_dir():
    return os.path.join(config_home(), "caracal", "rclone")


def unit_name(loc_id):
    """The location's unit file name: an existing one (possibly from before the rename)
    is kept so running mounts aren't disturbed; new ones use the Caracal prefix."""
    legacy = "%s%s.service" % (LEGACY[2], loc_id)
    if os.path.exists(os.path.join(unit_dir(), legacy)):
        return legacy
    return "%s%s.service" % (UNIT_PREFIX, loc_id)


def _legacy_dir(*parts):
    return os.path.join(config_home(), "catamount", *parts)


def tool_for(kind):
    return "sshfs" if kind == "sftp" else "rclone"


def tools_available():
    return {"sshfs": bool(shutil.which("sshfs")), "rclone": bool(shutil.which("rclone")),
            "fusermount": bool(shutil.which("fusermount3") or shutil.which("fusermount"))}


@dataclass
class Location:
    spec: dict
    state: str = "disconnected"   # connected | connecting | failed | disconnected

    @property
    def id(self):
        return self.spec["id"]

    @property
    def name(self):
        return self.spec.get("name", "")

    @property
    def kind(self):
        return self.spec.get("type", "")

    @property
    def mount_point(self):
        return self.spec.get("mount_point", "")

    @property
    def unit(self):
        return unit_name(self.id)

    @property
    def remote(self):
        s = self.spec
        if s.get("type") == "webdav":
            return s.get("url", "")
        port = s.get("port") or DEFAULT_PORTS.get(s.get("type"), 0)
        user = (s.get("user") + "@") if s.get("user") else ""
        port_s = "" if port == DEFAULT_PORTS.get(s.get("type")) else ":%d" % port
        return "%s://%s%s%s%s" % (s.get("type"), user, s.get("host", ""), port_s,
                                 s.get("path") or "/")


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(raw):
    if not isinstance(raw, dict):
        raise LocationError("Invalid location.")
    spec = {}
    spec["id"] = raw.get("id") or str(uuid.uuid4())
    if not ID_RE.match(spec["id"]):
        raise LocationError("Invalid id.")
    kind = raw.get("type")
    if kind not in KINDS:
        raise LocationError("Choose SFTP, FTP, FTPS or WebDAV.")
    spec["type"] = kind
    name = (raw.get("name") or "").strip()
    if not name or "\n" in name or len(name) > 200:
        raise LocationError("Enter a name.")
    spec["name"] = name
    if kind == "webdav":
        url = (raw.get("url") or "").strip()
        if not re.match(r"^https?://[^\s]+$", url):
            raise LocationError("Enter the WebDAV address, e.g. https://cloud.example.com/remote.php/dav/files/me/")
        spec["url"] = url
    else:
        host = (raw.get("host") or "").strip()
        if not HOST_RE.match(host):
            raise LocationError("Enter a valid server name or address.")
        spec["host"] = host
        port = raw.get("port") or DEFAULT_PORTS[kind]
        if not isinstance(port, int) or not 0 < port < 65536:
            raise LocationError("Invalid port.")
        spec["port"] = port
        path = (raw.get("path") or "").strip() or ("" if kind == "sftp" else "/")
        if any(c in path for c in "\n\r\x00"):
            raise LocationError("Invalid folder path on the server.")
        spec["path"] = path
    user = (raw.get("user") or "").strip()
    if not USER_RE.match(user):
        raise LocationError("Invalid user name.")
    spec["user"] = user
    auth = raw.get("auth") or ("key" if kind == "sftp" else "password")
    if auth not in ("key", "password", "anonymous"):
        raise LocationError("Invalid sign-in method.")
    if kind == "sftp" and auth == "anonymous":
        raise LocationError("SFTP needs a key or a password.")
    spec["auth"] = auth
    key_file = (raw.get("key_file") or "").strip()
    if key_file and (not key_file.startswith("/") or "\n" in key_file):
        raise LocationError("The key file must be a full path.")
    spec["key_file"] = key_file
    mp = (raw.get("mount_point") or "").strip()
    home = os.path.expanduser("~")
    if not mp.startswith("/") or "\n" in mp or any(p in ("", ".", "..") for p in mp.strip("/").split("/")):
        raise LocationError("The local folder must be a full path.")
    mp = "/" + mp.strip("/")
    if not (mp.startswith(home + "/") or mp.startswith("/run/user/") or mp.startswith("/mnt/")
            or mp.startswith("/media/")):
        raise LocationError("Put the local folder inside your home folder, e.g. %s/Network/%s"
                            % (home, re.sub(r"[^\w.-]+", "-", name)))
    spec["mount_point"] = mp
    spec["start_at_login"] = bool(raw.get("start_at_login", True))
    spec["read_only"] = bool(raw.get("read_only", False))
    return spec


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _sd_quote(arg):
    """Quote one argument for a systemd Exec= line."""
    arg = arg.replace("%", "%%").replace("$", "$$")
    if re.match(r"^[A-Za-z0-9_@%+=:,./-]+$", arg):
        return arg
    return '"%s"' % arg.replace("\\", "\\\\").replace('"', '\\"')


def secret_path(loc_id):
    new = os.path.join(secrets_dir(), loc_id)
    old = _legacy_dir("secrets", loc_id)
    return old if not os.path.exists(new) and os.path.exists(old) else new


def rclone_conf(loc_id):
    new = os.path.join(rclone_dir(), loc_id + ".conf")
    old = _legacy_dir("rclone", loc_id + ".conf")
    return old if not os.path.exists(new) and os.path.exists(old) else new


def fusermount():
    return shutil.which("fusermount3") or shutil.which("fusermount") or "/usr/bin/fusermount3"


def exec_lines(spec):
    """Return (Type=, ExecStart=) for the service."""
    mp = spec["mount_point"]
    if spec["type"] == "sftp":
        exe = shutil.which("sshfs") or "/usr/bin/sshfs"
        target = "%s%s:%s" % ((spec["user"] + "@") if spec["user"] else "", spec["host"],
                              spec["path"])
        opts = ["reconnect", "ServerAliveInterval=15", "ServerAliveCountMax=3", "idmap=user",
                "follow_symlinks", "StrictHostKeyChecking=accept-new"]
        if spec["read_only"]:
            opts.append("ro")
        if spec["auth"] == "key" and spec["key_file"]:
            opts.append("IdentityFile=" + spec["key_file"])
        if spec["auth"] == "password":
            opts += ["password_stdin", "PreferredAuthentications=password,keyboard-interactive"]
        else:
            opts.append("BatchMode=yes")
        args = [exe, "-f", "-o", ",".join(opts), "-p", str(spec["port"]), target, mp]
        if spec["auth"] == "password":
            # sshfs reads the password from stdin; feed it from the 0600 secret file.
            cmd = " ".join(shlex.quote(a) for a in args) + " < " + shlex.quote(secret_path(spec["id"]))
            return "simple", "/bin/sh -c " + _sd_quote("exec " + cmd)
        return "simple", " ".join(_sd_quote(a) for a in args)
    exe = shutil.which("rclone") or "/usr/bin/rclone"
    remote = "loc:" + (spec.get("path", "") if spec["type"] != "webdav" else "")
    args = [exe, "mount", remote, mp, "--config", rclone_conf(spec["id"]),
            "--vfs-cache-mode", "writes", "--dir-cache-time", "30s"]
    if spec["read_only"]:
        args.append("--read-only")
    return "notify", " ".join(_sd_quote(a) for a in args)


def render_unit(spec):
    svc_type, exec_start = exec_lines(spec)
    mp = spec["mount_point"]
    lines = [
        MANAGED_HEADER,
        SPEC_PREFIX + json.dumps(spec, sort_keys=True, separators=(",", ":")),
        "",
        "[Unit]",
        "Description=Caracal: %s" % spec["name"].replace("%", "%%"),
        "After=network-online.target",
        "",
        "[Service]",
        "Type=%s" % svc_type,
        "ExecStartPre=/usr/bin/mkdir -p %s" % _sd_quote(mp),
        "ExecStart=%s" % exec_start,
        "ExecStop=%s -u %s" % (_sd_quote(fusermount()), _sd_quote(mp)),
        "Restart=on-failure",
        "RestartSec=15",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    return "\n".join(lines) + "\n"


def render_rclone(spec, obscured_password):
    lines = ["[loc]"]
    if spec["type"] in ("ftp", "ftps"):
        lines += ["type = ftp", "host = " + spec["host"], "port = %d" % spec["port"]]
        if spec["auth"] == "anonymous":
            lines.append("user = anonymous")
        else:
            lines.append("user = " + spec["user"])
        if spec["type"] == "ftps":
            lines.append("explicit_tls = true")
    else:
        lines += ["type = webdav", "url = " + spec["url"], "vendor = other"]
        if spec["user"]:
            lines.append("user = " + spec["user"])
    if obscured_password:
        lines.append("pass = " + obscured_password)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# files and systemd
# --------------------------------------------------------------------------

def _write_private(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def systemctl(*args, check=True):
    proc = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                          timeout=60)
    if check and proc.returncode != 0:
        raise LocationError((proc.stderr or proc.stdout).strip() or "systemctl failed")
    return proc


def obscure(password):
    proc = subprocess.run([shutil.which("rclone") or "rclone", "obscure", "-"], input=password,
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise LocationError("rclone could not store the password: %s" % proc.stderr.strip())
    return proc.stdout.strip()


def load_locations():
    out = []
    try:
        names = sorted(os.listdir(unit_dir()))
    except OSError:
        return out
    for name in names:
        if not (name.startswith((UNIT_PREFIX, LEGACY[2])) and name.endswith(".service")):
            continue
        try:
            with open(os.path.join(unit_dir(), name), encoding="utf-8") as fh:
                first, second = fh.readline().rstrip("\n"), fh.readline().rstrip("\n")
        except OSError:
            continue
        if first not in (MANAGED_HEADER, LEGACY[0]):
            continue
        prefix = next((p for p in (SPEC_PREFIX, LEGACY[1]) if second.startswith(p)), None)
        if prefix is None:
            continue
        try:
            spec = json.loads(second[len(prefix):])
        except ValueError:
            continue
        if isinstance(spec, dict) and spec.get("id"):
            out.append(Location(spec))
    return sorted(out, key=lambda l: l.name.lower())


def refresh_states(locations):
    if not locations:
        return
    try:
        proc = systemctl("show", "-p", "Id,ActiveState", "--", *[l.unit for l in locations],
                         check=False)
    except (OSError, subprocess.SubprocessError):
        return
    states = {}
    for block in proc.stdout.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        states[props.get("Id")] = props.get("ActiveState", "")
    for loc in locations:
        active = states.get(loc.unit, "")
        if active == "active":
            loc.state = "connected" if os.path.ismount(loc.mount_point) else "connecting"
        elif active in ("activating", "reloading"):
            loc.state = "connecting"
        elif active == "failed":
            loc.state = "failed"
        else:
            loc.state = "disconnected"


def save(raw, password=None, connect=True):
    """Create or update a location. password=None keeps the stored one."""
    spec = validate(raw)
    tool = tool_for(spec["type"])
    if not shutil.which(tool):
        pkg = {"sshfs": "fuse-sshfs (Fedora) / sshfs (Debian)", "rclone": "rclone"}[tool]
        raise LocationError("%s isn't installed. Install the %s package." % (tool, pkg))
    unit_path = os.path.join(unit_dir(), unit_name(spec["id"]))
    if os.path.exists(unit_path):
        systemctl("stop", unit_name(spec["id"]), check=False)
    if spec["type"] == "sftp":
        if spec["auth"] == "password":
            if password is not None:
                _write_private(secret_path(spec["id"]), password)
            elif not os.path.exists(secret_path(spec["id"])):
                raise LocationError("Enter the password.")
        else:
            _remove(secret_path(spec["id"]))
    else:
        conf = rclone_conf(spec["id"])
        obscured = ""
        if spec["auth"] == "password":
            if password is not None:
                obscured = obscure(password)
            else:
                old = {}
                try:
                    with open(conf, encoding="utf-8") as fh:
                        old = dict(l.split(" = ", 1) for l in fh.read().splitlines() if " = " in l)
                except OSError:
                    pass
                obscured = old.get("pass", "")
                if not obscured:
                    raise LocationError("Enter the password.")
        _write_private(conf, render_rclone(spec, obscured))
    _write_private(unit_path, render_unit(spec), 0o644)
    systemctl("daemon-reload")
    systemctl("enable" if spec["start_at_login"] else "disable", unit_name(spec["id"]), check=False)
    if connect:
        systemctl("start", unit_name(spec["id"]), check=False)
    return Location(spec)


def connect(loc):
    systemctl("reset-failed", loc.unit, check=False)
    systemctl("start", loc.unit)


def disconnect(loc):
    systemctl("stop", loc.unit)


def remove(loc):
    systemctl("stop", loc.unit, check=False)
    systemctl("disable", loc.unit, check=False)
    _remove(os.path.join(unit_dir(), loc.unit))
    _remove(secret_path(loc.id))
    _remove(rclone_conf(loc.id))
    systemctl("daemon-reload", check=False)
    try:
        os.rmdir(loc.mount_point)
    except OSError:
        pass


def last_error(loc, lines=12):
    try:
        proc = subprocess.run(["journalctl", "--user", "-u", loc.unit, "-n", str(lines),
                               "--no-pager", "-o", "cat"], capture_output=True, text=True,
                              timeout=10)
        return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
