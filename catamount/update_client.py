# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Asynchronous update check and download (QtNetwork, no threads)."""

import os
import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkProxyFactory, QNetworkReply,
                               QNetworkRequest)

from catamount import updater

MAX_DOWNLOAD = 100 * 1024 * 1024


class UpdateClient(QObject):
    # Emitted after every check (automatic or manual). manual tells the UI to report the result.
    checked = Signal(bool)
    # ok, path-or-error, sha256
    downloaded = Signal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        QNetworkProxyFactory.setUseSystemConfiguration(True)
        self.nam = QNetworkAccessManager(self)
        self.cache = updater.load_cache()
        self.kind = updater.install_kind()
        self._checking = False
        self._downloading = False

    # -- state ------------------------------------------------------------

    @property
    def update(self):
        return updater.available_update(self.cache)

    @property
    def last_error(self):
        return self.cache.get("error", "")

    @property
    def busy(self):
        return self._checking or self._downloading

    def _request(self, url, accept="application/vnd.github+json"):
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, updater.USER_AGENT)
        req.setRawHeader(b"Accept", accept.encode())
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        req.setTransferTimeout(30000)
        return req

    def _save(self):
        try:
            updater.save_cache(self.cache)
        except OSError:
            pass

    # -- check ------------------------------------------------------------

    def check(self, manual=False):
        if self._checking:
            return
        if not manual and not updater.check_due(self.cache):
            self.checked.emit(False)
            return
        self._checking = True
        req = self._request(updater.API_URL)
        etag = self.cache.get("etag")
        if etag and self.cache.get("release"):
            req.setRawHeader(b"If-None-Match", etag.encode())
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._check_done(reply, manual))

    def _check_done(self, reply, manual):
        self._checking = False
        now = time.time()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        headers = {bytes(k).decode().lower(): bytes(v).decode()
                   for k, v in reply.rawHeaderPairs()}
        body = bytes(reply.readAll())
        error = reply.error()
        reply.deleteLater()

        if status == 304:
            self.cache.update(checked_at=now, error="")
        elif status == 200:
            import json
            try:
                rel = updater.parse_release(json.loads(body.decode("utf-8")))
            except ValueError:
                rel = None
            self.cache.update(checked_at=now, etag=headers.get("etag", ""), error="",
                              release=rel)
        elif status in (403, 429):
            until = updater.rate_limit_backoff(headers, now)
            self.cache.update(backoff_until=until, error="GitHub's rate limit was reached. "
                              "CataMount will try again after %s."
                              % time.strftime("%H:%M", time.localtime(until)))
        elif status == 404:
            self.cache.update(checked_at=now, error="", release=None)  # no releases yet
        else:
            msg = reply.errorString() if error != QNetworkReply.NoError else "HTTP %s" % status
            # Try again in an hour rather than waiting a whole day.
            self.cache.update(checked_at=now - updater.CHECK_INTERVAL + 3600,
                              error="Update check failed: %s" % msg)
        self._save()
        self.checked.emit(manual)

    # -- download ---------------------------------------------------------

    def download(self):
        """Download the package and SHA256SUMS for the available update, then verify."""
        rel = self.update
        assets = updater.pick_assets(rel, self.kind) if rel and self.kind else None
        if not assets:
            self.downloaded.emit(False, "This update has no package for this system.", "")
            return
        if self._downloading:
            return
        self._downloading = True
        name, pkg_url, sums_url = assets
        reply = self.nam.get(self._request(sums_url, "application/octet-stream"))
        reply.finished.connect(lambda: self._sums_done(reply, name, pkg_url))

    def _fail(self, msg):
        self._downloading = False
        self.downloaded.emit(False, msg, "")

    def _sums_done(self, reply, name, pkg_url):
        ok = reply.error() == QNetworkReply.NoError
        text = bytes(reply.readAll()).decode("utf-8", "replace")
        err = reply.errorString()
        reply.deleteLater()
        if not ok:
            return self._fail("Could not download SHA256SUMS: %s" % err)
        expected = updater.parse_sha256sums(text).get(name)
        if not expected:
            return self._fail("SHA256SUMS has no entry for %s." % name)
        reply = self.nam.get(self._request(pkg_url, "application/octet-stream"))
        reply.setReadBufferSize(0)
        reply.finished.connect(lambda: self._pkg_done(reply, name, expected))

    def _pkg_done(self, reply, name, expected):
        ok = reply.error() == QNetworkReply.NoError
        data = bytes(reply.readAll())
        err = reply.errorString()
        reply.deleteLater()
        if not ok:
            return self._fail("Download failed: %s" % err)
        if len(data) > MAX_DOWNLOAD:
            return self._fail("The download is unexpectedly large.")
        folder = os.path.join(updater.cache_dir(), "updates")
        try:
            os.makedirs(folder, exist_ok=True)
            for old in os.listdir(folder):  # keep only the newest download
                os.unlink(os.path.join(folder, old))
            path = os.path.join(folder, name)
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            return self._fail("Could not save the download: %s" % exc)
        actual = updater.sha256_file(path)
        if actual != expected:
            os.unlink(path)
            return self._fail("The download's checksum doesn't match SHA256SUMS, so it was "
                              "deleted. Try again later.")
        self._downloading = False
        self.downloaded.emit(True, path, actual)
