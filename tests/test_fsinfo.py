# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import time
import unittest
from unittest import mock

from caracal import fastpath, fsinfo


class FsInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.share = os.path.join(self.tmp.name, "share")
        os.mkdir(self.share)
        table = [(self.share, "cifs"), ("/", "ext4")]
        self.patch = mock.patch.object(fsinfo, "mounts", lambda max_age=2.0: table)
        self.patch.start()
        fsinfo._dead.clear()

    def tearDown(self):
        self.patch.stop()
        fsinfo._dead.clear()
        self.tmp.cleanup()

    def test_mount_lookup(self):
        self.assertTrue(fsinfo.is_remote(os.path.join(self.share, "a", "b")))
        self.assertFalse(fsinfo.is_remote(self.tmp.name))
        self.assertTrue(fsinfo.is_mounted(self.share + "/"))
        self.assertFalse(fsinfo.is_mounted(self.tmp.name))

    def test_hung_mount_times_out_and_is_skipped(self):
        slow = lambda p: time.sleep(2) or True  # noqa: E731
        t = time.monotonic()
        self.assertEqual(fsinfo.call(slow, self.share, timeout=0.2, default="x"), "x")
        self.assertLess(time.monotonic() - t, 1)
        self.assertTrue(fsinfo.is_unresponsive(self.share))
        t = time.monotonic()      # now fails fast without waiting at all
        self.assertFalse(fsinfo.isdir(self.share))
        self.assertLess(time.monotonic() - t, 0.1)
        fsinfo.mark_responsive(self.share)
        self.assertTrue(fsinfo.isdir(self.share))

    def test_no_mark_when_asked(self):
        slow = lambda p: time.sleep(1) or True  # noqa: E731
        fsinfo.call(slow, self.share, timeout=0.1, mark_dead=False)
        self.assertFalse(fsinfo.is_unresponsive(self.share))

    def test_listdirs_and_local_passthrough(self):
        os.mkdir(os.path.join(self.share, "B"))
        os.mkdir(os.path.join(self.share, "a"))
        os.mkdir(os.path.join(self.share, ".hidden"))
        self.assertEqual(fsinfo.listdirs(self.share), ["a", "B"])
        self.assertTrue(fsinfo.isdir(self.tmp.name))
        self.assertEqual(fsinfo.realpath(self.share + "/x/../y"), self.share + "/y")


class FastPathTests(unittest.TestCase):
    def test_match_export(self):
        exports = ["/mnt/tank/media", "/mnt/tank/backups", "/srv/Photos"]
        self.assertEqual(fastpath.match_export("media", exports), "/mnt/tank/media")
        self.assertEqual(fastpath.match_export("photos", exports), "/srv/Photos")
        self.assertIsNone(fastpath.match_export("docs", exports))
        self.assertIsNone(fastpath.match_export("media", []))


if __name__ == "__main__":
    unittest.main()
