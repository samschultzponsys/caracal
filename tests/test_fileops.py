# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
import unittest.mock

import importlib.util

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from PySide6.QtCore import QCoreApplication, Qt

from catamount.explorer import fileops

app = QCoreApplication.instance() or QCoreApplication([])


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class FileOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = os.path.join(self.tmp.name, "a")
        self.b = os.path.join(self.tmp.name, "b")
        write(os.path.join(self.a, "doc.txt"), "hello")
        write(os.path.join(self.a, "folder", "inner.txt"), "inner" * 1000)
        os.makedirs(self.b)

    def run_op(self, op, answer=None):
        result = {}
        if answer:
            op.conflict.connect(lambda info: op.answer(*answer), Qt.DirectConnection)
        op.done.connect(lambda r: result.update(r), Qt.DirectConnection)
        op.start()
        op.thread.join(10)
        return result

    def test_copy_conflict_and_undo(self):
        r = self.run_op(fileops.FileOperation("copy", [os.path.join(self.a, "doc.txt"),
                                                       os.path.join(self.a, "folder")], self.b))
        self.assertEqual(r["errors"], [])
        self.assertEqual(open(os.path.join(self.b, "folder", "inner.txt")).read(), "inner" * 1000)
        write(os.path.join(self.a, "doc.txt"), "new")
        r = self.run_op(fileops.FileOperation("copy", [os.path.join(self.a, "doc.txt")], self.b),
                        answer=(fileops.KEEP_BOTH, False))
        self.assertTrue(os.path.exists(os.path.join(self.b, "doc (2).txt")))
        r = self.run_op(fileops.FileOperation("copy", [os.path.join(self.a, "doc.txt")], self.b),
                        answer=(fileops.REPLACE, True))
        self.assertEqual(open(os.path.join(self.b, "doc.txt")).read(), "new")
        # Copy into the same folder -> "doc - Copy.txt"
        self.run_op(fileops.FileOperation("copy", [os.path.join(self.a, "doc.txt")], self.a))
        self.assertTrue(os.path.exists(os.path.join(self.a, "doc - Copy.txt")))

    def test_move_and_undo(self):
        r = self.run_op(fileops.FileOperation("move", [os.path.join(self.a, "folder")], self.b))
        self.assertTrue(os.path.exists(os.path.join(self.b, "folder", "inner.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.a, "folder")))
        self.assertEqual(fileops.undo(r["undo"]), [])
        self.assertTrue(os.path.exists(os.path.join(self.a, "folder", "inner.txt")))

    def test_into_itself_and_delete(self):
        r = self.run_op(fileops.FileOperation("copy", [self.a], os.path.join(self.a, "folder")))
        self.assertTrue(r["errors"])
        r = self.run_op(fileops.FileOperation("delete", [os.path.join(self.a, "doc.txt")]))
        self.assertFalse(os.path.exists(os.path.join(self.a, "doc.txt")))

    def test_trash_and_undo(self):
        data = os.path.join(self.tmp.name, "data")
        with unittest.mock.patch.dict(os.environ, {"XDG_DATA_HOME": data}):
            src = os.path.join(self.a, "doc.txt")
            r = self.run_op(fileops.FileOperation("trash", [src]))
            self.assertFalse(os.path.exists(src))
            info = os.path.join(data, "Trash", "info", "doc.txt.trashinfo")
            self.assertIn("Path=%s" % src.replace(" ", "%20"), open(info).read())
            self.assertTrue(os.path.exists(os.path.join(data, "Trash", "files", "doc.txt")))
            self.assertEqual(fileops.undo(r["undo"]), [])
            self.assertTrue(os.path.exists(src))
            self.assertFalse(os.path.exists(info))

    def test_restore_extract_zip(self):
        data = os.path.join(self.tmp.name, "data")
        with unittest.mock.patch.dict(os.environ, {"XDG_DATA_HOME": data}):
            src = os.path.join(self.a, "doc.txt")
            trashed = fileops.trash_item(src)
            orig, when = fileops.trash_original(trashed)
            self.assertEqual(orig, src)
            self.assertTrue(when)
            self.assertEqual(fileops.restore_from_trash(trashed), src)
            self.assertTrue(os.path.exists(src))
        z = fileops.make_zip([os.path.join(self.a, "doc.txt"), os.path.join(self.a, "folder")],
                             os.path.join(self.b, "a.zip"))
        self.assertTrue(fileops.is_archive(z))
        out = fileops.extract_archive(z)
        self.assertEqual(out, os.path.join(self.b, "a"))
        self.assertEqual(open(os.path.join(out, "folder", "inner.txt")).read(), "inner" * 1000)
        self.assertEqual(fileops.extract_archive(z), os.path.join(self.b, "a (2)"))
        import zipfile
        bad = os.path.join(self.b, "bad.zip")
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../escape.txt", "x")
        with self.assertRaises(OSError):
            fileops.extract_archive(bad)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.b, "bad")))

    def test_unique_names(self):
        write(os.path.join(self.b, "x.txt"))
        self.assertEqual(fileops.unique_name(self.b, "x.txt"), "x - Copy.txt")
        self.assertEqual(fileops.unique_name(self.b, "x.txt", "n"), "x (2).txt")
        self.assertEqual(fileops.unique_name(self.b, ".bashrc", "n"), ".bashrc (2)")


if __name__ == "__main__":
    unittest.main()
