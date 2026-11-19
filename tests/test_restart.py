# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
import time
import unittest

import importlib.util

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from caracal import app


class RestartStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self.old
        self.tmp.cleanup()

    def test_roundtrip_once(self):
        app.save_restore_state([{"tabs": ["/a", "/b"], "current": "/b"}], "explore\t/c")
        state = app.take_restore_state()
        self.assertEqual(state["windows"][0]["tabs"], ["/a", "/b"])
        self.assertEqual(state["request"], "explore\t/c")
        self.assertIsNone(app.take_restore_state())     # consumed

    def test_stale_state_ignored(self):
        app.save_restore_state([{"tabs": ["/a"]}])
        path = app._restore_path()
        with open(path) as fh:
            data = json.load(fh)
        data["time"] = time.time() - 3600
        with open(path, "w") as fh:
            json.dump(data, fh)
        self.assertIsNone(app.take_restore_state())

    def test_messages(self):
        self.assertEqual(app.split_message("1.3\nexplore\t/x"), ("1.3", "explore\t/x"))
        self.assertEqual(app.split_message("show"), ("", "show"))      # pre-1.3 launcher


if __name__ == "__main__":
    unittest.main()
