# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Cloud drives: detect the server (Nextcloud, ownCloud, OpenCloud/oCIS, FileRun) from
status.php, build its WebDAV address, and sign in with Nextcloud Login Flow v2 (the browser
asks the user to approve, the server hands back an app password). Standard library only.
Secrets are returned to the caller and never logged."""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Caracal (Linux file manager)"
PRESETS = {
    "nextcloud": {"label": "Nextcloud", "path": "/remote.php/dav/files/{user}/",
                  "vendor": "nextcloud", "flow": True},
    "filerun": {"label": "FileRun", "path": "/remote.php/dav/files/{user}/",
                "vendor": "nextcloud", "flow": True},
    "owncloud": {"label": "ownCloud (classic)", "path": "/remote.php/webdav/",
                 "vendor": "owncloud", "flow": False},
    "ocis": {"label": "OpenCloud / ownCloud Infinite Scale", "path": "/remote.php/webdav/",
             "vendor": "owncloud", "flow": False},
}
SIGN_IN_HELP = {
    "nextcloud": "Click Sign in with browser, or use an app password (Settings → Security → "
                 "Devices & sessions → Create new app password).",
    "filerun": "Click Sign in with browser. If that isn't offered, use FileRun's Connect app "
               "dialog: its user name (not your login name) and app password.",
    "owncloud": "Use your user name and an app password (Settings → Security → App passwords).",
    "ocis": "Use your user name and an app token (Preferences → App Tokens).",
}


class CloudError(Exception):
    pass


def normalize_server(text):
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    p = urllib.parse.urlsplit(text)
    path = p.path
    for marker in ("/remote.php", "/index.php", "/status.php", "/login"):
        if marker in path:
            path = path[:path.index(marker)]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path.rstrip("/"), "", ""))


def _request(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT,
                                                         "Accept": "application/json"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(64 * 1024) if exc.fp else b""
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CloudError("Couldn't reach the server: %s" % reason)


def product_from_status(status):
    """Map a status.php reply to a preset key, or None."""
    name = " ".join(str(status.get(k, "")) for k in ("productname", "product", "name")).lower()
    if "filerun" in name:
        return "filerun"
    if "nextcloud" in name:
        return "nextcloud"
    if "infinite scale" in name or "ocis" in name or "opencloud" in name:
        return "ocis"
    if "owncloud" in name:
        return "owncloud"
    return None


def detect(server):
    """(product key or None, status dict) from <server>/status.php."""
    server = normalize_server(server)
    code, body = _request(server + "/status.php")
    if code != 200:
        raise CloudError("The server didn't answer status.php (HTTP %d). Choose the kind of "
                         "server yourself." % code)
    try:
        status = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise CloudError("That doesn't look like a Nextcloud, ownCloud, OpenCloud or FileRun "
                         "server. Check the address or choose the kind yourself.")
    return product_from_status(status), status


def webdav_url(product, server, user):
    preset = PRESETS.get(product) or PRESETS["nextcloud"]
    return normalize_server(server) + preset["path"].format(
        user=urllib.parse.quote(user, safe="@._-+"))


# -- Login Flow v2 -------------------------------------------------------------------------

def login_flow_start(server):
    """{"login": url to open in the browser, "token", "endpoint"} or raises CloudError."""
    server = normalize_server(server)
    code, body = _request(server + "/index.php/login/v2", data=b"")
    if code != 200:
        raise CloudError("This server doesn't offer browser sign-in (HTTP %d). Use an app "
                         "password instead." % code)
    try:
        data = json.loads(body)
        return {"login": data["login"], "token": data["poll"]["token"],
                "endpoint": data["poll"]["endpoint"]}
    except (ValueError, KeyError, TypeError):
        raise CloudError("Unexpected answer from the server's browser sign-in.")


def login_flow_poll(flow, timeout=1200, interval=2, should_stop=lambda: False):
    """Wait until the user approved in the browser. Returns {"server", "user", "password"}
    (an app password made for Caracal), or raises CloudError (timeout, cancelled)."""
    end = time.monotonic() + timeout
    data = urllib.parse.urlencode({"token": flow["token"]}).encode()
    while time.monotonic() < end:
        if should_stop():
            raise CloudError("Sign-in cancelled.")
        code, body = _request(flow["endpoint"], data=data)
        if code == 200:
            try:
                r = json.loads(body)
                return {"server": r["server"], "user": r["loginName"],
                        "password": r["appPassword"]}
            except (ValueError, KeyError, TypeError):
                raise CloudError("Unexpected answer from the server's browser sign-in.")
        if code != 404:
            raise CloudError("Browser sign-in failed (HTTP %d)." % code)
        time.sleep(interval)
    raise CloudError("Sign-in timed out. Try again.")


def check_login(url, user, password, timeout=15):
    """PROPFIND the WebDAV root with the credentials: True, or raises CloudError."""
    import base64
    req = urllib.request.Request(url, method="PROPFIND", headers={
        "User-Agent": USER_AGENT, "Depth": "0",
        "Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CloudError("The server didn't accept that user name and password.")
        if exc.code == 404:
            raise CloudError("No WebDAV folder at %s (check the user name)." % url)
        if exc.code == 207:
            return True
        raise CloudError("The server answered HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError("Couldn't reach the server: %s" % getattr(exc, "reason", exc))
