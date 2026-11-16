# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Update logic that doesn't need Qt: versions, release parsing, cache, checksums.

Updates come from GitHub Releases. CataMount checks at most once a day (cached,
with ETag so unchanged answers don't count against the API rate limit), downloads
the RPM or DEB plus SHA256SUMS, checks the hash and installs through the helper.
"""

import hashlib
import json
import os
import re
import subprocess
import time

from catamount import REPO, __version__

API_URL = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_URL = "https://github.com/%s/releases" % REPO
CHECK_INTERVAL = 24 * 3600
USER_AGENT = "CataMount/%s (update check)" % __version__


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "catamount")


def cache_path():
    return os.path.join(cache_dir(), "update.json")


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)$")


def parse_version(text):
    m = VERSION_RE.match((text or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_newer(candidate, current=__version__):
    c, cur = parse_version(candidate), parse_version(current)
    return bool(c and cur and c > cur)


# --------------------------------------------------------------------------
# install type and assets
# --------------------------------------------------------------------------

def _query(cmd):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_kind():
    """'rpm' or 'deb' if CataMount was installed from a package, else None."""
    if _query(["rpm", "-q", "--quiet", "catamount"]):
        return "rpm"
    if _query(["dpkg-query", "-W", "-f=${Status}", "catamount"]):
        return "deb"
    return None


def package_name(kind, version):
    if kind == "rpm":
        return "catamount-%s-1.noarch.rpm" % version
    if kind == "deb":
        return "catamount_%s_all.deb" % version
    return None


def parse_release(data):
    """Reduce a GitHub release JSON object to what we need, or None if unusable."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    version = parse_version(tag)
    if not version:
        return None
    assets = {}
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            assets[str(a["name"])] = str(a["browser_download_url"])
    return {
        "tag": tag,
        "version": "%d.%d" % version,
        "notes": str(data.get("body") or ""),
        "url": str(data.get("html_url") or RELEASES_URL),
        "assets": assets,
    }


def pick_assets(release, kind):
    """Return (package_name, package_url, sums_url) or None when not available."""
    name = package_name(kind, release["version"])
    assets = release.get("assets", {})
    if name in assets and "SHA256SUMS" in assets:
        return name, assets[name], assets["SHA256SUMS"]
    return None


def parse_sha256sums(text):
    sums = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$", line.strip())
        if m:
            sums[os.path.basename(m.group(2))] = m.group(1).lower()
    return sums


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

def load_cache():
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data):
    os.makedirs(cache_dir(), exist_ok=True)
    tmp = cache_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, cache_path())


def check_due(cache, now=None):
    now = time.time() if now is None else now
    if now < float(cache.get("backoff_until") or 0):
        return False
    return now - float(cache.get("checked_at") or 0) >= CHECK_INTERVAL


def available_update(cache):
    """The cached release if it's newer than this version, else None."""
    rel = cache.get("release")
    if isinstance(rel, dict) and is_newer(rel.get("version", "")):
        return rel
    return None


def rate_limit_backoff(headers, now=None):
    """Seconds-since-epoch to wait until after a 403/429 from GitHub."""
    now = time.time() if now is None else now
    reset = headers.get("x-ratelimit-reset")
    retry = headers.get("retry-after")
    try:
        if retry:
            return now + int(retry)
        if reset:
            return max(now + 60, float(reset))
    except ValueError:
        pass
    return now + 3600
