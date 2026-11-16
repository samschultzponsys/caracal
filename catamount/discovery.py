# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Find servers and shares on the network, and test connections, without root.

- Servers: mDNS/DNS-SD via `avahi-browse` (_smb._tcp, _nfs._tcp).
- SMB shares and connection tests: `smbclient` (samba-client / smbclient package).
- NFS exports: `showmount` (nfs-utils / nfs-common).

All of these are optional at runtime; callers check `available()` first.
Credentials go to smbclient through a temporary 0600 auth file, never argv.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

SAFE_ENV = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")


def available():
    return {
        "avahi": bool(shutil.which("avahi-browse")),
        "smbclient": bool(shutil.which("smbclient")),
        "showmount": bool(shutil.which("showmount") or os.path.exists("/usr/sbin/showmount")
                          or os.path.exists("/sbin/showmount")),
    }


def _run(cmd, timeout=15):
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=timeout, env=SAFE_ENV)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 127, "", str(exc)


def _showmount():
    return shutil.which("showmount") or next(
        (p for p in ("/usr/sbin/showmount", "/sbin/showmount") if os.path.exists(p)), "showmount")


# --------------------------------------------------------------------------
# servers
# --------------------------------------------------------------------------

@dataclass
class Server:
    name: str
    host: str
    address: str = ""
    kinds: set = field(default_factory=set)   # {"smb", "nfs"}

    @property
    def label(self):
        kinds = "/".join(sorted(k.upper() for k in self.kinds))
        return "%s  (%s)" % (self.name, kinds) if kinds else self.name


def _avahi_unescape(text):
    return re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), text)


def parse_avahi(text):
    """Parse `avahi-browse -ptr` output into Servers keyed by host name."""
    servers = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 9 or parts[0] != "=":
            continue
        _, _iface, proto, name, stype, _domain, host, address, _port = parts[:9]
        kind = {"_smb._tcp": "smb", "_nfs._tcp": "nfs"}.get(stype)
        if not kind:
            continue
        host = host.rstrip(".")
        srv = servers.get(host)
        if srv is None:
            srv = servers[host] = Server(name=_avahi_unescape(name), host=host)
        if proto == "IPv4" or not srv.address:
            srv.address = address
        srv.kinds.add(kind)
    return sorted(servers.values(), key=lambda s: s.name.lower())


def discover_servers(timeout=8):
    if not shutil.which("avahi-browse"):
        return []
    out = ""
    for stype in ("_smb._tcp", "_nfs._tcp"):
        _code, stdout, _err = _run(["avahi-browse", "-ptr", stype], timeout=timeout)
        out += stdout
    return parse_avahi(out)


# --------------------------------------------------------------------------
# SMB
# --------------------------------------------------------------------------

SMB_ERRORS = (
    ("NT_STATUS_LOGON_FAILURE", "The user name or password is wrong."),
    ("NT_STATUS_ACCESS_DENIED", "Access denied. Check the user name and password."),
    ("NT_STATUS_BAD_NETWORK_NAME", "That share doesn't exist on the server."),
    ("NT_STATUS_ACCOUNT_DISABLED", "That account is disabled."),
    ("NT_STATUS_PASSWORD_EXPIRED", "The password has expired."),
    ("NT_STATUS_HOST_UNREACHABLE", "The server can't be reached."),
    ("NT_STATUS_IO_TIMEOUT", "The server didn't answer in time."),
    ("NT_STATUS_CONNECTION_REFUSED", "The server refused the connection (is SMB enabled?)."),
    ("NT_STATUS_UNSUCCESSFUL", "The server can't be reached."),
    ("NT_STATUS_RESOURCE_NAME_NOT_FOUND", "The server name couldn't be found."),
    ("NT_STATUS_INVALID_NETWORK_RESPONSE", "The server name couldn't be found."),
    ("timed out", "The server didn't answer in time."),
)


def explain_smb_error(text):
    for code, msg in SMB_ERRORS:
        if code in text:
            return msg
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "The connection failed."


def needs_login(text):
    return any(c in text for c in ("NT_STATUS_ACCESS_DENIED", "NT_STATUS_LOGON_FAILURE"))


class _AuthFile:
    """Temporary smbclient auth file (0600), removed afterwards."""

    def __init__(self, username, password, domain):
        self.args = []
        self.path = None
        self.creds = (username or "", password or "", domain or "")

    def __enter__(self):
        username, password, domain = self.creds
        if not username:
            self.args = ["-N"]
            return self
        folder = os.environ.get("XDG_RUNTIME_DIR") or None
        fd, self.path = tempfile.mkstemp(prefix="catamount-", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("username=%s\npassword=%s\n" % (username, password))
            if domain:
                fh.write("domain=%s\n" % domain)
        self.args = ["-A", self.path]
        return self

    def __exit__(self, *exc):
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass


def parse_smb_shares(text, include_hidden=False):
    shares = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[0] == "Disk":
            name = parts[1]
            if name.endswith("$") and not include_hidden:
                continue
            shares.append((name, parts[2] if len(parts) > 2 else ""))
    return sorted(shares, key=lambda s: s[0].lower())


def list_smb_shares(server, username="", password="", domain="", kerberos=False):
    """Return (shares, error, needs_login). shares = [(name, comment)]."""
    with _AuthFile("" if kerberos else username, password, domain) as auth:
        args = ["--use-kerberos=required"] if kerberos else auth.args
        code, out, err = _run(["smbclient", "-g", "-L", "//" + server, *args], timeout=20)
    shares = parse_smb_shares(out)
    if shares:
        return shares, "", False
    text = out + err
    if code == 0:
        return [], "", False
    return [], explain_smb_error(text), needs_login(text)


def test_smb(server, share, username="", password="", domain="", kerberos=False, guest=False):
    """Return (ok, message)."""
    with _AuthFile("" if (guest or kerberos) else username, password, domain) as auth:
        args = ["--use-kerberos=required"] if kerberos else auth.args
        code, out, err = _run(["smbclient", "//%s/%s" % (server, share), *args, "-c", "ls"],
                              timeout=20)
    if code == 0:
        return True, "Connected to \\\\%s\\%s successfully." % (server, share.replace("/", "\\"))
    return False, explain_smb_error(out + err)


# --------------------------------------------------------------------------
# NFS
# --------------------------------------------------------------------------

def parse_showmount(text):
    exports = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("/"):
            path, _, clients = line.partition(" ")
            exports.append((path, clients.strip()))
    return exports


def list_nfs_exports(server):
    """Return (exports, error). exports = [(path, allowed clients)]."""
    host = server.strip("[]")
    code, out, err = _run([_showmount(), "-e", "--no-headers", host], timeout=15)
    if code == 0:
        return parse_showmount(out), ""
    msg = (err or out).strip().splitlines()
    text = msg[-1] if msg else "The server didn't answer."
    if "RPC" in text or "timed out" in text:
        text = ("The server didn't answer the export-list request. NFSv4-only servers "
                "don't publish one, so type the export path instead.")
    return [], text


def test_nfs(server, export):
    exports, err = list_nfs_exports(server)
    if err:
        return False, err
    paths = [p for p, _ in exports]
    if export in paths or export == "/":
        return True, "The server is exporting %s." % export
    if paths:
        return False, "%s isn't in the server's export list: %s" % (export, ", ".join(paths))
    return False, "The server isn't exporting anything to this computer."
