# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import time
import unittest
import zipfile

from caracal import crawler, extract, indexdb


def touch(path, text="", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "share")
        self.db = os.path.join(self.tmp.name, "idx", "index.sqlite")
        touch(os.path.join(self.root, "Projects", "Budget 2026.xlsx"), "")
        touch(os.path.join(self.root, "Projects", "notes.txt"), "the quarterly widget report")
        touch(os.path.join(self.root, "Photos", "Summer", "IMG_0001.jpg"), "x" * 5000)
        touch(os.path.join(self.root, "Photos", "Summer", "beach.png"), "x" * 10)
        touch(os.path.join(self.root, "#recycle", "old.txt"), "gone")
        touch(os.path.join(self.root, "readme.md"), "# Hello network drive")
        self.c = crawler.Crawler(self.db, "drive-1", "NAS media", self.root)

    def search(self, text, **kw):
        conn = indexdb.connect(self.db, readonly=True)
        try:
            res, total, _ms = indexdb.search(conn, text, **kw)
        finally:
            conn.close()
        return sorted(r.name for r in res)

    def test_full_and_queries(self):
        self.c.scan(full=True)
        self.assertEqual(oct(os.stat(self.db).st_mode & 0o777), "0o600")
        self.assertEqual(self.search("budget"), ["Budget 2026.xlsx"])
        self.assertEqual(self.search("bud 2026"), ["Budget 2026.xlsx"])
        self.assertEqual(self.search("img"), ["IMG_0001.jpg"])
        self.assertEqual(self.search("ext:jpg,png"), ["IMG_0001.jpg", "beach.png"])
        self.assertEqual(self.search("type:pic"), ["IMG_0001.jpg", "beach.png"])
        self.assertEqual(self.search("summer/"), ["IMG_0001.jpg", "beach.png"])
        self.assertEqual(self.search("folder:photos type:file"), ["IMG_0001.jpg", "beach.png"])
        self.assertEqual(self.search("*.md"), ["readme.md"])
        self.assertEqual(self.search("size:>1kb"), ["IMG_0001.jpg"])
        self.assertEqual(self.search("type:folder"), ["Photos", "Projects", "Summer"])
        self.assertEqual(self.search("modified:today ext:md"), ["readme.md"])
        self.assertEqual(self.search("old"), [])          # #recycle skipped
        self.assertEqual(self.search("drive:nas readme"), ["readme.md"])
        self.assertEqual(self.search("x"), ["Budget 2026.xlsx", "notes.txt"])  # 1-char LIKE
        self.assertEqual(self.search("zq"), [])
        self.assertEqual(self.search("ad"), ["readme.md"])  # 2-char LIKE path
        folder = os.path.join(self.root, "Photos")
        q = indexdb.parse_query("type:file")
        q.folder = folder
        conn = indexdb.connect(self.db, readonly=True)
        self.assertEqual(sorted(r.name for r in indexdb.search(conn, q)[0]),
                         ["IMG_0001.jpg", "beach.png"])
        r = indexdb.search(conn, "beach")[0][0]
        self.assertEqual(r.path, os.path.join(self.root, "Photos", "Summer", "beach.png"))
        conn.close()

    def test_incremental_and_contents(self):
        self.c.scan(full=True)
        # Add, remove and rename; bump folder mtimes like a real server would.
        touch(os.path.join(self.root, "Projects", "Invoice.pdf"), "")
        os.remove(os.path.join(self.root, "readme.md"))
        os.rename(os.path.join(self.root, "Photos", "Summer"), os.path.join(self.root, "Photos", "Winter"))
        touch(os.path.join(self.root, "Photos", "Winter", "snow.png"), "")
        c2 = crawler.Crawler(self.db, "drive-1", "NAS media", self.root)
        changes = c2.scan(full=False)
        self.assertGreater(changes, 0)
        self.assertEqual(self.search("invoice"), ["Invoice.pdf"])
        self.assertEqual(self.search("readme"), [])
        self.assertEqual(self.search("winter/"), ["IMG_0001.jpg", "beach.png", "snow.png"])
        self.assertEqual(self.search("summer"), [])
        # Unchanged tree: nothing to do.
        c3 = crawler.Crawler(self.db, "drive-1", "NAS media", self.root)
        self.assertEqual(c3.scan(), 0)
        # Contents.
        doc = os.path.join(self.root, "Projects", "plan.docx")
        with zipfile.ZipFile(doc, "w") as zf:
            zf.writestr("word/document.xml", "<w:p><w:t>Secret</w:t></w:p><w:p><w:t>roadmap</w:t></w:p>")
        crawler.Crawler(self.db, "drive-1", "NAS media", self.root).scan()
        n = crawler.Crawler(self.db, "drive-1", "NAS media", self.root).index_contents()
        self.assertGreaterEqual(n, 2)
        self.assertEqual(self.search("content:quarterly"), ["notes.txt"])
        self.assertEqual(self.search('content:"secret roadmap"'), ["plan.docx"])
        self.assertEqual(self.search("content:widget ext:txt"), ["notes.txt"])
        conn = indexdb.connect(self.db, readonly=True)
        res = indexdb.search(conn, "content:quarterly")[0]
        self.assertIn("[quarterly]", res[0].snippet)
        stats = indexdb.drive_stats(conn)["drive-1"]
        self.assertGreater(stats["files"], 4)
        self.assertGreaterEqual(stats["content_files"], 2)
        conn.close()
        # Unchanged files aren't extracted again.
        self.assertEqual(crawler.Crawler(self.db, "drive-1", "x", self.root).index_contents(), 0)
        # Delete the drive's index.
        conn = indexdb.connect(self.db)
        indexdb.delete_drive(conn, "drive-1")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM names").fetchone()[0], 0)
        conn.close()

    def test_stop(self):
        c = crawler.Crawler(self.db, "drive-1", "x", self.root, should_stop=lambda: True)
        with self.assertRaises(crawler.Stopped):
            c.scan(full=True)

    def test_watcher(self):
        self.c.scan(full=True)
        w = crawler.Watcher()
        self.assertTrue(w.add("drive-1", self.root, "Projects"))
        touch(os.path.join(self.root, "Projects", "new.txt"), "x")
        time.sleep(0.05)
        self.assertEqual(w.read(), {"drive-1": {"Projects"}})
        w.close()

    def test_extract_and_parse(self):
        self.assertTrue(extract.supported("a.DOCX"))
        self.assertFalse(extract.supported("a.exe"))
        p = os.path.join(self.tmp.name, "x.html")
        touch(p, "<html><body><p>Hello &amp; welcome</p></body></html>")
        self.assertEqual(extract.extract(p), "Hello & welcome")
        q = indexdb.parse_query('ext:pdf size:<2mb "annual report" modified:2026-01 bogus:x')
        self.assertEqual(q.exts, ["pdf"])
        self.assertEqual(q.size_max, 2_000_000)
        self.assertEqual(q.words, ["annual report", "bogus:x"])
        self.assertIsNotNone(q.mtime_min)


if __name__ == "__main__":
    unittest.main()


class ServiceAndCliTests(unittest.TestCase):
    def test_searcher_and_cli(self):
        import io
        import contextlib
        from unittest import mock
        from caracal import cli, search_service
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "share")
            touch(os.path.join(root, "a", "budget.xlsx"))
            touch(os.path.join(root, "a", "Budget 2026 final.xlsx"))
            touch(os.path.join(root, "budget"))
            db = os.path.join(tmp, "data", "caracal", "index.sqlite")
            crawler.Crawler(db, "d1", "NAS", root).scan(full=True)
            s = search_service.Searcher(db)
            names = [m[1] for m in s.krunner_matches("budget")]
            self.assertEqual(names[0], "budget")             # exact match first
            self.assertEqual(s.krunner_matches("budget")[0][3], search_service.EXACT)
            self.assertEqual(s.krunner_matches("b"), [])      # too short
            self.assertIn(os.path.join(root, "a", "budget.xlsx"), s.paths(["budget", "xlsx"]))
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(tmp, "data")}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = cli.main(["caracal", "--search", "final"])
                self.assertEqual(rc, 0)
                self.assertEqual(out.getvalue().strip(),
                                 os.path.join(root, "a", "Budget 2026 final.xlsx"))
        self.assertEqual(cli.search_window_args(["x", "--search-window", "--folder", "/mnt/a",
                                                 "tax", "2026"]), ("tax 2026", "/mnt/a"))
        self.assertIsNone(cli.search_window_args(["x"]))


class CachedListingTests(unittest.TestCase):
    def test_listing_from_index_and_model_uses_it(self):
        import importlib.util
        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("PySide6 not installed")
        from PySide6.QtCore import QEventLoop, QSettings, QTimer
        from PySide6.QtWidgets import QApplication
        from caracal import crawler, indexdb
        from caracal.explorer import model as m
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "share")
            os.makedirs(os.path.join(root, "sub"))
            for n in ("a.txt", "b.txt"):
                open(os.path.join(root, n), "w").close()
            db = os.path.join(d, "i.sqlite")
            crawler.Crawler(db, "r1", "share", root).scan(full=True)
            conn = indexdb.connect(db, readonly=True)
            rows = indexdb.cached_listing(conn, "r1", "")
            self.assertEqual(sorted(r[0] for r in rows), ["a.txt", "b.txt", "sub"])
            self.assertIsNone(indexdb.cached_listing(conn, "r1", "nope"))
            # the model shows the cached rows at once, then the live listing replaces them
            model = m.DirModel(QSettings(os.path.join(d, "s.ini"), QSettings.IniFormat))
            open(os.path.join(root, "new.txt"), "w").close()      # not in the index yet
            model.cache_lookup = lambda p: (indexdb.cached_listing(conn, "r1", ""), "r1")
            model.setRootPath(root)
            self.assertTrue(model.from_index)
            self.assertEqual(model.item_count(), 3)
            loop = QEventLoop()
            model.directoryLoaded.connect(lambda _p: loop.quit())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            self.assertFalse(model.from_index)
            self.assertEqual(model.item_count(), 4)
            # live listing fails: keep the index's copy and report it
            model2 = m.DirModel(QSettings(os.path.join(d, "s2.ini"), QSettings.IniFormat))
            model2.cache_lookup = lambda p: (indexdb.cached_listing(conn, "r1", ""), "r1")
            model2._scan = lambda gen, r: model2._relay.loaded.emit(gen, r, [], "Host is down")
            model2.setRootPath(root)
            loop = QEventLoop()
            QTimer.singleShot(300, loop.quit)
            loop.exec()
            self.assertEqual(model2.item_count(), 3)
            self.assertEqual(model2.stale_error, "Host is down")
            conn.close()
