# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest import mock

from caracal import migrate, updater


class MigrateTests(unittest.TestCase):
    def test_moves_settings_and_data(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, data = os.path.join(d, "cfg"), os.path.join(d, "data")
            os.makedirs(os.path.join(cfg, "catamount"))
            os.makedirs(os.path.join(data, "catamount", "explorer-theme"))
            with open(os.path.join(cfg, "catamount", "catamount.conf"), "w") as fh:
                fh.write("[General]\ntheme=dark\n")
            open(os.path.join(data, "catamount", "index.sqlite"), "w").close()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": cfg, "XDG_DATA_HOME": data}), \
                    mock.patch.object(migrate.subprocess, "run", side_effect=OSError):
                done = migrate.run()
                self.assertIn("settings", done)
                self.assertIn("data", done)
                self.assertTrue(os.path.exists(os.path.join(cfg, "caracal", "caracal.conf")))
                self.assertTrue(os.path.exists(os.path.join(data, "caracal", "index.sqlite")))
                self.assertTrue(os.path.isdir(os.path.join(data, "caracal", "explorer-theme")))
                self.assertEqual(migrate.run(), [])   # idempotent

    def test_update_assets_either_name(self):
        rel = {"version": "0.7", "assets": {"catamount-0.7-1.noarch.rpm": "u1", "SHA256SUMS": "s"}}
        self.assertEqual(updater.pick_assets(rel, "rpm")[0], "catamount-0.7-1.noarch.rpm")
        rel["assets"]["caracal-0.7-1.noarch.rpm"] = "u2"
        self.assertEqual(updater.pick_assets(rel, "rpm")[0], "caracal-0.7-1.noarch.rpm")


if __name__ == "__main__":
    unittest.main()
