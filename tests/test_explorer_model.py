# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import time
import unittest

import importlib.util

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from caracal.explorer import model as m

app = QApplication.instance() or QApplication([])


class BucketTests(unittest.TestCase):
    def test_date_buckets(self):
        # Wednesday 2026-11-18 15:00 local
        now = time.mktime((2026, 11, 18, 15, 0, 0, 0, 0, -1))
        day = 86400
        cases = [(now - 3600, "Today"), (now - day, "Yesterday"), (now - 2 * day + 1, "Earlier this week"),
                 (now - 4 * day, "Last week"), (now - 12 * day, "Earlier this month"),
                 (now - 30 * day, "Last month"), (now - 120 * day, "Earlier this year"),
                 (now - 400 * day, "A long time ago"), (now + 3 * day, "In the future")]
        for ts, label in cases:
            self.assertEqual(m.date_bucket(ts, now)[1], label, label)

    def test_sizes_and_names(self):
        self.assertEqual(m.win_size(0), "0 KB")
        self.assertEqual(m.win_size(1), "1 KB")
        self.assertEqual(m.win_size(159302656), "155,569 KB")
        names = ["file 10.txt", "File 2.txt", "file 1.txt"]
        self.assertEqual(sorted(names, key=m._natural), ["file 1.txt", "File 2.txt", "file 10.txt"])
        e = m.Entry("a", "/a", False, 5 * 1024 ** 3, 0, False)
        self.assertEqual(m.size_bucket(e)[1], "Gigantic (> 4 GB)")
        self.assertEqual(m.name_bucket(m.Entry("Zed", "", False, 0, 0, False))[1], "Q - Z")


class ModelTests(unittest.TestCase):
    def test_load_group_rename(self):
        with tempfile.TemporaryDirectory() as d:
            for name, age in (("b.txt", 0), ("a.txt", 2 * 86400), (".hidden", 0)):
                p = os.path.join(d, name)
                open(p, "w").close()
                t = time.time() - age
                os.utime(p, (t, t))
            os.mkdir(os.path.join(d, "folder"))
            s = QSettings(os.path.join(d, "s.ini"), QSettings.IniFormat)
            model = m.DirModel(s)
            loop = QEventLoop()
            model.directoryLoaded.connect(lambda _p: loop.quit())
            model.setRootPath(d)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            self.assertEqual([e.name for e in model.entries], ["folder", "a.txt", "b.txt"])
            model.set_show_hidden(True)
            self.assertEqual(model.item_count(), 4)
            model.set_show_hidden(False)
            model.set_group_by("date")
            self.assertTrue(model.grouped())
            labels = [g[0] for g in model.groups]
            self.assertEqual(labels[0], "Today")
            idx = model.index_for_path(os.path.join(d, "b.txt"))
            self.assertTrue(idx.isValid())
            self.assertEqual(model.data(idx.siblingAtColumn(m.SIZE)), "0 KB")
            renamed = []
            model.on_rename = lambda a, b: renamed.append((a, b))
            self.assertTrue(model.setData(idx, "c.txt"))
            self.assertTrue(os.path.exists(os.path.join(d, "c.txt")))
            self.assertEqual(renamed[0][1], os.path.join(d, "c.txt"))
            model.hide_ext = True
            idx = model.index_for_path(os.path.join(d, "c.txt"))
            self.assertEqual(model.data(idx), "c")
            self.assertTrue(model.setData(idx, "d"))
            self.assertTrue(os.path.exists(os.path.join(d, "d.txt")))


if __name__ == "__main__":
    unittest.main()


class DriveDialogSuggestTests(unittest.TestCase):
    def test_prefilled_address_still_suggests_mount_point(self):
        from caracal.dialogs import DriveDialog
        with tempfile.TemporaryDirectory() as d:
            s = QSettings(os.path.join(d, "s.ini"), QSettings.IniFormat)
            dlg = DriveDialog(s, template={"type": "nfs", "server": "nas",
                                           "share": "/mnt/tank/media"})
            self.assertEqual(dlg.mount_point.text(), "/mnt/media")
            dlg = DriveDialog(s, template={"type": "nfs", "server": "nas", "share": "/x/media",
                                           "name": "Media", "mount_point": "/mnt/Media"})
            self.assertEqual(dlg.mount_point.text(), "/mnt/Media")    # kept when given
