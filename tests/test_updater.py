# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest import mock

from caracal import updater

RELEASE = {
    "tag_name": "v99.2", "draft": False, "prerelease": False, "body": "Notes",
    "html_url": "https://github.com/x/y/releases/tag/v99.2",
    "assets": [
        {"name": "caracal-99.2-1.noarch.rpm", "browser_download_url": "https://e/rpm"},
        {"name": "caracal_99.2_all.deb", "browser_download_url": "https://e/deb"},
        {"name": "SHA256SUMS", "browser_download_url": "https://e/sums"},
    ],
}


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(updater.parse_version("v1.9"), (1, 9))
        self.assertIsNone(updater.parse_version("1.2.3"))
        self.assertTrue(updater.is_newer("2.0", "1.9"))
        self.assertTrue(updater.is_newer("v0.10", "0.9"))
        self.assertFalse(updater.is_newer("0.1", "0.1"))
        self.assertFalse(updater.is_newer("junk", "0.1"))


class ReleaseTests(unittest.TestCase):
    def test_parse_and_pick(self):
        rel = updater.parse_release(RELEASE)
        self.assertEqual(rel["version"], "99.2")
        self.assertEqual(updater.pick_assets(rel, "rpm"),
                         ("caracal-99.2-1.noarch.rpm", "https://e/rpm", "https://e/sums"))
        self.assertEqual(updater.pick_assets(rel, "deb")[0], "caracal_99.2_all.deb")
        self.assertIsNone(updater.pick_assets(rel, None))
        self.assertIsNone(updater.parse_release(dict(RELEASE, prerelease=True)))
        self.assertIsNone(updater.parse_release(dict(RELEASE, tag_name="nightly")))

    def test_sums(self):
        h = "a" * 64
        sums = updater.parse_sha256sums("%s  caracal_99.2_all.deb\n%s *dist/x.rpm\nbad\n" % (h, h))
        self.assertEqual(sums, {"caracal_99.2_all.deb": h, "x.rpm": h})


class CacheTests(unittest.TestCase):
    def test_due_and_backoff(self):
        t = 1_800_000_000
        self.assertTrue(updater.check_due({}, now=t))
        self.assertFalse(updater.check_due({"checked_at": t}, now=t + 3600))
        self.assertTrue(updater.check_due({"checked_at": t}, now=t + 86400))
        self.assertFalse(updater.check_due({"backoff_until": t + 5000}, now=t))
        self.assertEqual(updater.rate_limit_backoff({"retry-after": "120"}, now=10), 130)
        self.assertEqual(updater.rate_limit_backoff({"x-ratelimit-reset": "9999"}, now=10), 9999)

    def test_available_and_roundtrip(self):
        rel = updater.parse_release(RELEASE)
        self.assertEqual(updater.available_update({"release": rel}), rel)  # 99.2 > current
        self.assertIsNone(updater.available_update({"release": dict(rel, version="0.1")}))
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": d}):
            updater.save_cache({"release": rel, "checked_at": 5})
            self.assertEqual(updater.load_cache()["release"], rel)


if __name__ == "__main__":
    unittest.main()
