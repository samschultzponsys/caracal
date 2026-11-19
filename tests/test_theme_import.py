# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from caracal.explorer import theme  # noqa: E402

COLORS = """[Colors:View]
BackgroundNormal=25,25,25
BackgroundAlternate=32,32,32
ForegroundNormal=242,242,242
ForegroundInactive=160,160,160
DecorationHover=80,80,80
[Colors:Window]
BackgroundNormal=43,43,43
[Colors:Selection]
BackgroundNormal=0,120,215
"""
INDEX = """[Icon Theme]
Name=Windows 10 Dark
Directories=places/48
[places/48]
Size=48
Context=Places
"""


class ImportTests(unittest.TestCase):
    def test_zip_with_icons_colors_fonts(self):
        with tempfile.TemporaryDirectory() as d:
            z = os.path.join(d, "Win10.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("Windows-10-Dark/index.theme", INDEX)
                zf.writestr("Windows-10-Dark/places/48/folder.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
                zf.writestr("colors/Windows10Dark.colors", COLORS)
                zf.writestr("fonts/SegoeLike.ttf", "not really a font")
                zf.writestr("extra/this-pc.png", "png")
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(d, "data")}):
                summary = theme.import_theme(z)
                self.assertEqual(summary["icon_themes"], ["Windows-10-Dark"])
                self.assertEqual(summary["colors"], ["Windows10Dark.colors"])
                self.assertEqual(summary["fonts"], 1)
                self.assertEqual(summary["icons"], 1)
                tdir = theme.theme_dir()
                self.assertTrue(os.path.exists(os.path.join(tdir, "icon-themes", "Windows-10-Dark",
                                                            "places", "48", "folder.svg")))
                self.assertTrue(os.path.exists(os.path.join(tdir, "icons", "this-pc.png")))
                data = json.load(open(os.path.join(tdir, "theme.json")))
                self.assertEqual(data["icon_theme"], "Windows-10-Dark")
                self.assertEqual(data["mode"], "dark")
                self.assertEqual(data["dark"]["content"], "#191919")
                self.assertEqual(data["accent"], "#0078D7")
                names = dict(theme.list_icon_themes())
                self.assertEqual(names.get("Windows-10-Dark"), "Windows 10 Dark")

    def test_rejects_unsafe_zip(self):
        with tempfile.TemporaryDirectory() as d:
            z = os.path.join(d, "bad.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("../../escape.txt", "x")
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(d, "data")}):
                with self.assertRaises(OSError):
                    theme.import_theme(z)
            self.assertFalse(os.path.exists(os.path.join(d, "escape.txt")))

    def test_colors_light(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "light.colors")
            with open(p, "w") as fh:
                fh.write(COLORS.replace("25,25,25", "255,255,255").replace("242,242,242", "0,0,0"))
            mode, colors, accent = theme.parse_kde_colors(p)
            self.assertEqual(mode, "light")
            self.assertEqual(colors["content"], "#FFFFFF")


if __name__ == "__main__":
    unittest.main()
