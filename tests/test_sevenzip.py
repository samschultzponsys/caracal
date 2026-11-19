# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import shutil
import subprocess
import tempfile
import unittest

import importlib.util

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from caracal.explorer import sevenzip as sz
from caracal.explorer.theme import icon_variant


class SevenZipTests(unittest.TestCase):
    def test_names(self):
        self.assertTrue(sz.is_archive("/x/a.7z"))
        self.assertTrue(sz.is_archive("/x/a.tar.gz"))
        self.assertTrue(sz.is_archive("/x/a.7z.001"))
        self.assertFalse(sz.is_archive("/x/a.txt"))
        self.assertEqual(sz.stem("/x/photos.tar.gz"), "photos")
        self.assertEqual(sz.stem("/x/a.7z.001"), "a")
        self.assertEqual(sz.stem("/x/b.part1.rar"), "b")
        self.assertEqual(sz.default_archive_base(["/x/notes.txt"], "/x"), "notes")
        self.assertEqual(sz.default_archive_base(["/x/a", "/x/b"], "/x"), "x")

    def test_args(self):
        a = sz.add_args("/x/a.7z", ["f"], "7z", "9", password=True, volume="100m",
                        delete_after=True)
        for flag in ("-t7z", "-mx9", "-p", "-mhe=on", "-v100m", "-sdel"):
            self.assertIn(flag, a)
        self.assertEqual(a[-3:], ["--", "/x/a.7z", "f"])
        self.assertNotIn("-mx5", sz.add_args("/x/a.tar", ["f"], "tar"))
        self.assertIn("-mem=AES256", sz.add_args("/x/a.zip", ["f"], "zip", password=True))
        self.assertEqual(sz.extract_args("/x/a.7z", "/y")[-1], "/x/a.7z")
        self.assertEqual(sz.last_percent("  3%\b\b\b\b 47%"), 47)
        self.assertTrue(sz.needs_password("Cannot open encrypted archive. Wrong password?"))
        self.assertFalse(sz.needs_password("Everything is Ok"))

    @unittest.skipUnless(sz.find_exe(), "7-Zip not installed")
    def test_password_via_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "f.txt"), "w").write("hi")
            exe = sz.find_exe()
            subprocess.run([exe] + sz.add_args(os.path.join(d, "s.7z"), ["f.txt"], password=True),
                           input=b"pw\npw\n", cwd=d, capture_output=True, check=True)
            bad = subprocess.run([exe] + sz.test_args(os.path.join(d, "s.7z")), input=b"no\n",
                                 cwd=d, capture_output=True)
            self.assertTrue(sz.needs_password(bad.stderr.decode() + bad.stdout.decode()))
            good = subprocess.run([exe] + sz.test_args(os.path.join(d, "s.7z")), input=b"pw\n",
                                  cwd=d, capture_output=True)
            self.assertEqual(good.returncode, 0)


class IconVariantTests(unittest.TestCase):
    def test_variants(self):
        inst = {"breeze": "breeze", "breeze-dark": "breeze-dark", "papirus": "Papirus",
                "papirus-dark": "Papirus-Dark", "adwaita": "Adwaita"}
        self.assertEqual(icon_variant("breeze-dark", False, inst), "breeze")
        self.assertEqual(icon_variant("breeze", True, inst), "breeze-dark")
        self.assertEqual(icon_variant("Papirus", True, inst), "Papirus-Dark")
        self.assertEqual(icon_variant("Adwaita", True, inst), "Adwaita")
        self.assertEqual(icon_variant("", True, inst), "")


if __name__ == "__main__":
    unittest.main()
