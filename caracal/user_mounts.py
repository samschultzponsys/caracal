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

from caracal import fsinfo

MANAGED_HEADER = "# Managed by Caracal"
SPEC_PREFIX = "# Caracal-Spec: "
LEGACY = ("# Managed by CataMount", "# CataMount-Spec: ", "catamount-")   # before the rename
UNIT_PREFIX = "caracal-"
KINDS = {"cloud": "Cloud drive (Nextcloud, ownCloud, OpenCloud, FileRun)",
         "sftp": "SFTP / SCP (SSH)", "ftp": "FTP", "ftps": "FTPS (FTP over TLS)",
         "webdav": "WebDAV (HTTPS)", "rclone": "Existing rclone remote (imported)"}
DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21, "webdav": 443, "cloud": 443, "rclone": 0}
RCLONE_KINDS = {"ftp", "ftps", "webdav", "cloud", "rclone"}
# Files on demand (like Windows "Files On-Demand"): the whole tree is listed, a file is
# downloaded the first time anything reads it, kept in a local cache (least-recently-used
# files evicted at the size limit, still listed and re-downloaded on access), and saves are
# written locally and uploaded in the background. Real files, never placeholders.
VFS_DEFAULTS = {"vfs_full": True, "cache_gb": 20, "cache_days": 30}
CLOUD_PRODUCTS = ("nextcloud", "filerun", "owncloud", "ocis")
WEBDAV_VENDORS = ("other", "nextcloud", "owncloud", "sharepoint", "sharepoint-ntlm", "rclone",
                  "fastmail")
# rclone config keys an imported remote may carry (anything else is left out)
RCLONE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

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
        if s.get("type") in ("webdav", "cloud"):
            return s.get("url", "")
        if s.get("type") == "rclone":
            return "rclone remote “%s”" % s.get("remote_name", "")
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
    if kind == "rclone":
        rname = (raw.get("remote_name") or "").strip()
        if not re.match(r"^[A-Za-z0-9_. -]{1,64}$", rname):
            raise LocationError("Invalid rclone remote name.")
        spec["remote_name"] = rname
        rtype = (raw.get("rclone_type") or "").strip()
        if not RCLONE_KEY_RE.match(rtype):
            raise LocationError("Invalid rclone remote type.")
        spec["rclone_type"] = rtype
        rpath = (raw.get("path") or "").strip()
        if any(c in rpath for c in "\n\r\x00"):
            raise LocationError("Invalid folder path on the server.")
        spec["path"] = rpath
        imported = (raw.get("imported_from") or "").strip()
        if imported and not re.match(r"^[A-Za-z0-9@_.:-]+\.service$", imported):
            raise LocationError("Invalid unit name.")
        spec["imported_from"] = imported
    elif kind == "cloud":
        server = (raw.get("server") or "").strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", server):
            raise LocationError("Enter the server address, e.g. https://cloud.example.com")
        spec["server"] = server
        product = raw.get("product") or "nextcloud"
        if product not in CLOUD_PRODUCTS:
            raise LocationError("Choose the kind of server.")
        spec["product"] = product
        url = (raw.get("url") or "").strip()
        if url and not re.match(r"^https?://[^\s]+$", url):
            raise LocationError("Invalid WebDAV address.")
        spec["url"] = url
    elif kind == "webdav":
        url = (raw.get("url") or "").strip()
        if not re.match(r"^https?://[^\s]+$", url):
            raise LocationError("Enter the WebDAV address, e.g. https://cloud.example.com/remote.php/dav/files/me/")
        spec["url"] = url
        vendor = raw.get("vendor") or "other"
        spec["vendor"] = vendor if vendor in WEBDAV_VENDORS else "other"
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
    if kind == "cloud":
        if not user:
            raise LocationError("Enter the user name (or sign in with the browser).")
        if not spec["url"]:
            from caracal import cloud
            spec["url"] = cloud.webdav_url(spec["product"], spec["server"], user)
    auth = raw.get("auth") or ("key" if kind == "sftp" else
                               "imported" if kind == "rclone" else "password")
    if kind == "rclone":
        auth = "imported"
    elif auth not in ("key", "password", "anonymous"):
        raise LocationError("Invalid sign-in method.")
    if kind == "cloud" and auth != "password":
        raise LocationError("Cloud drives sign in with an app password.")
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
    # listed in other file managers' sidebars (GTK / KDE bookmarks) under its name
    spec["bookmark"] = bool(raw.get("bookmark", kind in ("cloud", "rclone")))
    if kind in RCLONE_KINDS:
        spec["vfs_full"] = bool(raw.get("vfs_full", VFS_DEFAULTS["vfs_full"]))
        for key, lo, hi in (("cache_gb", 1, 100000), ("cache_days", 1, 3650)):
            try:
                value = int(raw.get(key, VFS_DEFAULTS[key]))
            except (TypeError, ValueError):
                raise LocationError("Invalid cache setting.")
            if not lo <= value <= hi:
                raise LocationError("The cache %s must be between %d and %d."
                                    % ("size (GB)" if key == "cache_gb" else "age (days)", lo, hi))
            spec[key] = value
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
    remote = "loc:" + (spec.get("path", "") if spec["type"] not in ("webdav", "cloud") else "")
    args = [exe, "mount", remote, mp, "--config", rclone_conf(spec["id"])] + vfs_args(spec)
    args += rc_args(spec["id"])
    if spec["read_only"]:
        args.append("--read-only")
    return "notify", " ".join(_sd_quote(a) for a in args)


def cache_dir(loc_id):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "caracal", "rclone", loc_id)


def vfs_args(spec):
    if not spec.get("vfs_full", True):
        return ["--vfs-cache-mode", "writes", "--dir-cache-time", "30s",
                "--cache-dir", cache_dir(spec["id"])]
    return ["--vfs-cache-mode", "full",
            "--vfs-cache-max-size", "%dG" % spec.get("cache_gb", VFS_DEFAULTS["cache_gb"]),
            "--vfs-cache-max-age", "%dh" % (24 * spec.get("cache_days", VFS_DEFAULTS["cache_days"])),
            "--vfs-write-back", "5s", "--dir-cache-time", "30s",
            "--cache-dir", cache_dir(spec["id"])]


# --------------------------------------------------------------------------
# remote control (cache usage, pending uploads): a private unix socket where rclone
# supports one, else localhost TCP with a random password kept in a 0600 htpasswd file
# (never on the command line, where other users could read it in the process list).
# --------------------------------------------------------------------------

def runtime_dir():
    rt = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(rt, "caracal")


def rc_socket(loc_id):
    return os.path.join(runtime_dir(), "rc-%s.sock" % loc_id[:8])


def rc_info_path(loc_id):
    return os.path.join(rclone_dir(), loc_id + ".rc.json")


_unix_rc = {}


def rclone_supports_unix_rc():
    """Newer rclone describes --rc-addr as '... or [unix://]/path/to/socket'."""
    exe = shutil.which("rclone")
    if not exe:
        return False
    if exe not in _unix_rc:
        try:
            out = subprocess.run([exe, "help", "flags"], capture_output=True, text=True,
                                 timeout=20).stdout
            line = next((l for l in out.splitlines() if "--rc-addr" in l), "")
            _unix_rc[exe] = "unix" in line.lower()
        except (OSError, subprocess.SubprocessError):
            _unix_rc[exe] = False
    return _unix_rc[exe]


def _rc_info(loc_id, create=False):
    try:
        with open(rc_info_path(loc_id), encoding="utf-8") as fh:
            info = json.load(fh)
        if isinstance(info, dict) and info.get("mode") in ("unix", "tcp"):
            return info
    except (OSError, ValueError):
        pass
    if not create:
        return None
    if rclone_supports_unix_rc():
        info = {"mode": "unix", "socket": rc_socket(loc_id)}
    else:
        import base64
        import hashlib
        import secrets
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        password = secrets.token_urlsafe(24)
        info = {"mode": "tcp", "port": port, "user": "caracal", "password": password}
        sha = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
        _write_private(os.path.join(rclone_dir(), loc_id + ".htpasswd"),
                       "caracal:{SHA}%s\n" % sha)
    _write_private(rc_info_path(loc_id), json.dumps(info))
    return info


def rc_args(loc_id):
    info = _rc_info(loc_id, create=True)
    if info["mode"] == "unix":
        return ["--rc", "--rc-addr", "unix://" + info["socket"], "--rc-no-auth"]
    return ["--rc", "--rc-addr", "127.0.0.1:%d" % info["port"],
            "--rc-htpasswd", os.path.join(rclone_dir(), loc_id + ".htpasswd")]


def rc_call(loc_id, method, params=None, timeout=5):
    """Call rclone's remote control for a location. Returns the JSON reply or raises
    LocationError."""
    import http.client
    import socket
    info = _rc_info(loc_id)
    if info is None:
        raise LocationError("This location has no remote control (save it again).")
    body = json.dumps(params or {}).encode()
    headers = {"Content-Type": "application/json"}
    try:
        if info["mode"] == "unix":
            class _UnixConn(http.client.HTTPConnection):
                def connect(self):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(timeout)
                    self.sock.connect(info["socket"])
            conn = _UnixConn("localhost", timeout=timeout)
        else:
            import base64
            conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=timeout)
            headers["Authorization"] = "Basic " + base64.b64encode(
                ("%s:%s" % (info["user"], info["password"])).encode()).decode()
        conn.request("POST", "/" + method, body, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
    except (OSError, http.client.HTTPException) as exc:
        raise LocationError("rclone isn't answering (%s)." % exc)
    try:
        reply = json.loads(data or b"{}")
    except ValueError:
        reply = {}
    if resp.status != 200:
        raise LocationError(reply.get("error") or "rclone said %d" % resp.status)
    return reply


def cache_stats(loc_id):
    """{"used", "files", "uploading", "queued", "errored", "full"} for a running rclone
    location, or None if it isn't running."""
    try:
        r = rc_call(loc_id, "vfs/stats")
    except LocationError:
        return None
    dc = r.get("diskCache") or {}
    return {"used": int(dc.get("bytesUsed") or 0), "files": int(dc.get("files") or 0),
            "uploading": int(dc.get("uploadsInProgress") or 0),
            "queued": int(dc.get("uploadsQueued") or 0),
            "errored": int(dc.get("erroredFiles") or 0), "full": bool(dc.get("outOfSpace")),
            "mode": (r.get("opt") or {}).get("CacheMode")}


def pending_uploads(loc_id):
    st = cache_stats(loc_id)
    return (st["uploading"] + st["queued"]) if st else 0


def cache_size_on_disk(loc_id):
    total = 0
    for dirpath, _dirs, files in os.walk(cache_dir(loc_id)):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
            except OSError:
                pass
    return total


def free_up_space(loc):
    """Drop the location's local copies (they stay listed and re-download on access).
    Refuses while uploads are pending."""
    pending = pending_uploads(loc.id)
    if pending:
        raise LocationError("%d file%s still uploading. Try again once they're done."
                            % (pending, "" if pending == 1 else "s"))
    was_running = systemctl("is-active", loc.unit, check=False).stdout.strip() == "active"
    if was_running:
        systemctl("stop", loc.unit)
    try:
        # the cache stores unsaved changes too: only drop it when nothing is waiting
        if _dirty_files_in_cache(loc.id):
            raise LocationError("Some saved changes haven't been uploaded yet. Connect the "
                                "location and let them finish first.")
        shutil.rmtree(cache_dir(loc.id), ignore_errors=True)
    finally:
        if was_running:
            systemctl("start", loc.unit, check=False)


def _dirty_files_in_cache(loc_id):
    """Files rclone's metadata marks as modified-but-not-uploaded (survives restarts)."""
    meta = os.path.join(cache_dir(loc_id), "vfsMeta")
    for dirpath, _dirs, files in os.walk(meta):
        for f in files:
            try:
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    if json.load(fh).get("Dirty"):
                        return True
            except (OSError, ValueError):
                continue
    return False


def keep_on_device(folder, should_stop=lambda: False, progress=lambda n, b: None):
    """Download every file under folder into the cache by reading it (rclone keeps what's
    read). Files can still be evicted later if the cache limit is reached."""
    count = total = 0
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            if should_stop():
                return count, total
            try:
                with open(os.path.join(dirpath, f), "rb") as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
            except OSError:
                continue
            count += 1
            progress(count, total)
    return count, total


def wait_for_uploads(loc_id, timeout=120):
    """Block until the location has no pending uploads (or timeout). For ExecStop and Exit."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = cache_stats(loc_id)
        if not st or st["uploading"] + st["queued"] == 0:
            return True
        time.sleep(2)
    return False


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
        # A connection process that died leaves a dead FUSE mount behind ("Transport endpoint
        # is not connected"), which breaks anything that walks the folder tree — notably
        # every Flatpak app's sandbox setup. Clear it before starting and after stopping.
        "ExecStartPre=-%s -uz %s" % (_sd_quote(fusermount()), _sd_quote(mp)),
        "ExecStartPre=/usr/bin/mkdir -p %s" % _sd_quote(mp),
    ]
    info = _rc_info(spec["id"]) if spec["type"] in RCLONE_KINDS else None
    if info and info.get("mode") == "unix":
        lines += ["ExecStartPre=/usr/bin/mkdir -p %s" % _sd_quote(os.path.dirname(info["socket"])),
                  "ExecStartPre=-/usr/bin/rm -f %s" % _sd_quote(info["socket"])]
    lines += [
        "ExecStart=%s" % exec_start,
    ]
    if spec["type"] in RCLONE_KINDS and spec.get("vfs_full", True):
        # let background uploads finish (up to 2 min) before unmounting at logout/shutdown
        lines.append("ExecStop=-%s --wait-uploads %s" % (_sd_quote(caracal_exe()), spec["id"]))
        lines.append("TimeoutStopSec=150")
    lines += [
        "ExecStop=%s -u %s" % (_sd_quote(fusermount()), _sd_quote(mp)),
        "ExecStopPost=-%s -uz %s" % (_sd_quote(fusermount()), _sd_quote(mp)),
        # network changes, sleep/resume: keep retrying instead of giving up after 5 tries
        "Restart=on-failure",
        "RestartSec=15",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    lines[lines.index("[Unit]") + 2:lines.index("[Unit]") + 2] = ["StartLimitIntervalSec=0"]
    return "\n".join(lines) + "\n"


def caracal_exe():
    return "/usr/bin/caracal" if os.path.exists("/usr/bin/caracal") else \
        (shutil.which("caracal") or "/usr/bin/caracal")


def render_imported(section):
    """An imported rclone remote's settings, renamed to [loc]. Values are copied as they
    are (rclone keeps passwords obscured in its config already)."""
    lines = ["[loc]"]
    for key, value in section.items():
        if RCLONE_KEY_RE.match(key) and "\n" not in str(value):
            lines.append("%s = %s" % (key, value))
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
        vendor = spec.get("vendor", "other")
        if spec["type"] == "cloud":
            from caracal import cloud
            vendor = cloud.PRESETS[spec["product"]]["vendor"]
        lines += ["type = webdav", "url = " + spec["url"], "vendor = " + vendor]
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
            loc.state = "connected" if fsinfo.is_mounted(loc.mount_point) else "connecting"
        elif active in ("activating", "reloading"):
            loc.state = "connecting"
        elif active == "failed":
            loc.state = "failed"
        else:
            loc.state = "disconnected"


def upgrade_units():
    """Rewrite location units made by older versions (no restart; takes effect the next
    time each one starts). Returns how many changed."""
    changed = 0
    for loc in load_locations():
        path = os.path.join(unit_dir(), loc.unit)
        try:
            spec = validate(dict(loc.spec))
            text = render_unit(spec)
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    continue
            _write_private(path, text, 0o644)
            changed += 1
        except (OSError, LocationError, KeyError, ValueError):
            continue
    if changed:
        systemctl("daemon-reload", check=False)
    return changed


def save(raw, password=None, connect=True):
    """Create or update a location. password=None keeps the stored one. Imported rclone
    remotes pass their config section as raw["rclone_section"] (kept out of the spec)."""
    section = raw.get("rclone_section") if isinstance(raw, dict) else None
    spec = validate(raw)
    tool = tool_for(spec["type"])
    if not shutil.which(tool):
        pkg = {"sshfs": "fuse-sshfs (Fedora) / sshfs (Debian)", "rclone": "rclone"}[tool]
        raise LocationError("%s isn't installed. Install the %s package." % (tool, pkg))
    unit_path = os.path.join(unit_dir(), unit_name(spec["id"]))
    if os.path.exists(unit_path):
        systemctl("stop", unit_name(spec["id"]), check=False)
    mp = spec["mount_point"]
    # rclone refuses a non-empty folder; never clear one for the user
    if os.path.isdir(mp) and not fsinfo.is_mounted(mp):
        try:
            busy = bool(os.listdir(mp))
        except OSError:
            busy = False
        if busy:
            raise LocationError("%s isn't empty. Choose an empty or new folder (nothing in it "
                                "was changed)." % mp)
    if spec["type"] == "rclone":
        if section is not None:
            if not isinstance(section, dict) or section.get("type") != spec["rclone_type"]:
                raise LocationError("Invalid rclone remote settings.")
            _write_private(rclone_conf(spec["id"]), render_imported(section))
        elif not os.path.exists(rclone_conf(spec["id"])):
            raise LocationError("The rclone remote's settings are missing.")
    elif spec["type"] == "sftp":
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
    _remove(rc_info_path(loc.id))
    _remove(os.path.join(rclone_dir(), loc.id + ".htpasswd"))
    if not _dirty_files_in_cache(loc.id):
        shutil.rmtree(cache_dir(loc.id), ignore_errors=True)


def hand_back(loc):
    """Undo a take-over: remove Caracal's copy and start the original unit again."""
    old = loc.spec.get("imported_from")
    if not old:
        raise LocationError("This location wasn't imported.")
    if pending_uploads(loc.id):
        raise LocationError("Files are still uploading. Try again once they're done.")
    remove(loc)
    give_back(old)
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


# --------------------------------------------------------------------------
# importing rclone mounts made outside Caracal
# --------------------------------------------------------------------------

def rclone_config_path():
    return os.path.join(config_home(), "rclone", "rclone.conf")


def read_rclone_config(path=None):
    """{remote name: {key: value}} from an rclone.conf (only unencrypted configs)."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        with open(path or rclone_config_path(), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    if "RCLONE_ENCRYPT_V0" in text:
        return {}
    try:
        cp.read_string(text)
    except configparser.Error:
        return {}
    return {s: dict(cp.items(s)) for s in cp.sections()}


def _flag(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _size_gb(text):
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGT]?)i?B?$", (text or "").strip(), re.I)
    if not m:
        return None
    n = float(m.group(1)) * {"": 1 / 1024 ** 3, "K": 1 / 1024 ** 2, "M": 1 / 1024,
                              "G": 1, "T": 1024}[m.group(2).upper()]
    return max(1, int(round(n)))


def _age_days(text):
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)?$", (text or "").strip())
    if not m:
        return None
    hours = float(m.group(1)) * {"ms": 1 / 3600000, "s": 1 / 3600, "m": 1 / 60, "h": 1,
                                  "d": 24, "w": 168, "y": 8760, None: 1 / 3600}[m.group(2)]
    return max(1, int(round(hours / 24)))


def find_rclone_mounts():
    """User units (not Caracal's) that run `rclone mount`: [{unit, remote, path,
    mount_point, config, section, cache_gb, cache_days, vfs_full, active}]."""
    out = []
    try:
        names = sorted(os.listdir(unit_dir()))
    except OSError:
        return out
    home = os.path.expanduser("~")
    for name in names:
        if not name.endswith(".service") or name.startswith((UNIT_PREFIX, LEGACY[2])):
            continue
        path = os.path.join(unit_dir(), name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        exec_line = next((l.split("=", 1)[1].strip() for l in text.splitlines()
                          if l.strip().startswith("ExecStart=")), "")
        if "rclone" not in exec_line or " mount" not in exec_line:
            continue
        try:
            args = shlex.split(exec_line.lstrip("-@:+!"))
        except ValueError:
            continue
        args = [a.replace("%h", home).replace("${HOME}", home).replace("$HOME", home)
                for a in args]
        try:
            i = args.index("mount")
        except ValueError:
            continue
        pos = [a for a in args[i + 1:] if not a.startswith("-")]
        # positional args: remote:path then mount point (flag values can look positional,
        # so take the first "name:..." and the first absolute path after it)
        remote_arg = next((a for a in pos if re.match(r"^[A-Za-z0-9_. -]+:", a)), None)
        if not remote_arg:
            continue
        rest = pos[pos.index(remote_arg) + 1:]
        mp = next((a for a in rest if a.startswith("/") or a.startswith("~")), None)
        if not mp:
            continue
        mp = os.path.expanduser(mp).rstrip("/") or "/"
        rname, _, rpath = remote_arg.partition(":")
        conf = _flag(args, "--config") or rclone_config_path()
        section = read_rclone_config(conf).get(rname)
        if not section or "type" not in section:
            continue
        mode = _flag(args, "--vfs-cache-mode") or "off"
        active = systemctl("is-active", name, check=False).stdout.strip() == "active"
        out.append({"unit": name, "remote": rname, "path": rpath, "mount_point": mp,
                    "config": conf, "section": section,
                    "cache_gb": _size_gb(_flag(args, "--vfs-cache-max-size")) or 20,
                    "cache_days": _age_days(_flag(args, "--vfs-cache-max-age")) or 30,
                    "vfs_full": mode == "full" or mode not in ("off", "minimal", "writes"),
                    "active": active})
    return out


def nice_name(text):
    """"SpazcatDrive" / "spazcat-drive" -> "Spazcat Drive"."""
    base = os.path.basename(text.rstrip("/")) or text
    base = re.sub(r"[_-]+", " ", base)
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return " ".join(w[:1].upper() + w[1:] for w in base.split()) or text


def take_over(found, name=None):
    """Recreate an outside rclone mount as a Caracal location: same remote settings, mount
    point and cache flags. The old unit is stopped and disabled, not deleted (give_back()
    undoes it)."""
    old = found["unit"]
    systemctl("disable", "--now", old, check=False)
    import time
    for _ in range(20):        # wait for the old mount to go away
        if not fsinfo.is_mounted(found["mount_point"]):
            break
        fsinfo.invalidate()
        time.sleep(0.5)
    raw = {"type": "rclone", "name": name or nice_name(found["mount_point"]),
           "remote_name": found["remote"], "rclone_type": found["section"]["type"],
           "path": found["path"], "mount_point": found["mount_point"],
           "imported_from": old, "vfs_full": found["vfs_full"],
           "cache_gb": found["cache_gb"], "cache_days": found["cache_days"],
           "rclone_section": found["section"], "start_at_login": True}
    try:
        return save(raw, connect=True)
    except LocationError:
        give_back(old)            # put things back the way they were
        raise


def give_back(old_unit):
    """Re-enable a unit Caracal took over (after Caracal's copy was removed)."""
    if old_unit and os.path.exists(os.path.join(unit_dir(), old_unit)):
        systemctl("enable", "--now", old_unit, check=False)
