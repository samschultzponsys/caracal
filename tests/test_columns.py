# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import struct
import tempfile
import time
import unittest
import zipfile

import importlib.util

if importlib.util.find_spec("PySide6") is None:  # CI runner without Qt
    raise unittest.SkipTest("PySide6 not installed")

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from caracal.explorer import columns as cols, metadata, model as m

app = QApplication.instance() or QApplication([])


def _wait(model, cond, ms=4000):
    end = time.time() + ms / 1000
    while time.time() < end and not cond():
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()


def _exif_jpeg(path, maker, model_name, taken):
    """Minimal JPEG with an EXIF block holding Make, Model and DateTimeOriginal."""
    def asc(s):
        return s.encode() + b"\0"
    vals = [(0x010F, asc(maker)), (0x0110, asc(model_name))]
    exif_vals = [(0x9003, asc(taken))]
    # layout: header(8) IFD0 at 8: count + entries*12 + next(4), then data, then EXIF IFD
    ifd0_size = 2 + (len(vals) + 1) * 12 + 4
    data_off = 8 + ifd0_size
    blob, entries = b"", []
    for tag, v in vals:
        entries.append(struct.pack("<HHII", tag, 2, len(v), data_off + len(blob)))
        blob += v
    exif_ifd = data_off + len(blob)
    entries.append(struct.pack("<HHII", 0x8769, 4, 1, exif_ifd))
    exif_data_off = exif_ifd + 2 + len(exif_vals) * 12 + 4
    sub = struct.pack("<H", len(exif_vals))
    for tag, v in exif_vals:
        sub += struct.pack("<HHII", tag, 2, len(v), exif_data_off)
    sub += b"\0\0\0\0" + exif_vals[0][1]
    tiff = b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(vals) + 1) + b"".join(entries) \
        + b"\0\0\0\0" + blob + sub
    app1 = b"Exif\0\0" + tiff
    img = QImage(32, 16, QImage.Format_RGB32)
    img.fill(QColor("red"))
    tmp = path + ".tmp.jpg"
    img.save(tmp, "JPEG")
    with open(tmp, "rb") as fh:
        raw = fh.read()
    os.remove(tmp)
    with open(path, "wb") as fh:
        fh.write(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + raw[2:])


class MetadataTests(unittest.TestCase):
    def test_jpeg_exif_and_size(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "photo.jpg")
            _exif_jpeg(p, "Canon", "EOS R6", "2024:05:06 07:08:09")
            info = metadata.read(p, False)
            self.assertEqual((info["width"], info["height"]), (32, 16))
            self.assertEqual(info["camera_maker"], "Canon")
            self.assertEqual(info["camera_model"], "EOS R6")
            self.assertEqual(time.localtime(info["date_taken"])[:6], (2024, 5, 6, 7, 8, 9))

    def test_office_props(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "doc.docx")
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("docProps/core.xml",
                            '<cp:coreProperties xmlns:cp="x" xmlns:dc="y" xmlns:dcterms="z">'
                            '<dc:title>Budget</dc:title><dc:creator>Sam</dc:creator>'
                            '<dcterms:created xsi:type="W3CDTF">2025-01-02T03:04:05Z</dcterms:created>'
                            '</cp:coreProperties>')
                zf.writestr("docProps/app.xml", "<Properties><Pages>7</Pages></Properties>")
            info = metadata.read(p, False)
            self.assertEqual(info["title"], "Budget")
            self.assertEqual(info["authors"], "Sam")
            self.assertEqual(info["pages"], 7)
            self.assertIsNotNone(info["content_created"])

    def test_broken_files_are_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("bad.jpg", "bad.docx", "bad.pdf", "bad.mp3"):
                p = os.path.join(d, name)
                with open(p, "wb") as fh:
                    fh.write(b"\xff\xd8\xff\xe1\x00")
                self.assertIsInstance(metadata.read(p, False), dict)


class ColumnTests(unittest.TestCase):
    def test_first_four_are_stable(self):
        self.assertEqual([c.title for c in cols.COLUMNS[:4]], ["Name", "Date modified", "Type", "Size"])
        self.assertEqual(cols.index_of("date"), m.DATE)
        self.assertEqual(cols.index_of("none"), -1)
        self.assertEqual(len({c.key for c in cols.COLUMNS}), len(cols.COLUMNS))

    def test_values_and_buckets(self):
        e = m.Entry("Song.MP3", "/music/Song.MP3", False, 2048, time.time(), False,
                    mode=0o100444, meta={"length": 125.0, "bitrate": 320, "year": 1999})
        self.assertEqual(cols.value(cols.index_of("ext"), e), ".mp3")
        self.assertEqual(cols.value(cols.index_of("folder"), e), "/music")
        self.assertEqual(cols.value(cols.index_of("attributes"), e), "R")
        self.assertEqual(cols.value(cols.index_of("permissions"), e), "-r--r--r--")
        self.assertEqual(cols.display(cols.index_of("length"), e, None), "00:02:05")
        self.assertEqual(cols.display(cols.index_of("bitrate"), e, None), "320kbps")
        self.assertEqual(cols.bucket(cols.index_of("length"), e)[1], "Short (1 - 5 minutes)")
        self.assertEqual(cols.bucket(cols.index_of("year"), e)[1], "1999")
        self.assertEqual(cols.bucket(cols.index_of("album"), e)[1], cols.UNSPECIFIED)


class ModelColumnTests(unittest.TestCase):
    def test_sort_group_filter_any_column(self):
        with tempfile.TemporaryDirectory() as d:
            for name, size in (("a.txt", 10), ("b.log", 50000), ("c.txt", 3000000)):
                with open(os.path.join(d, name), "wb") as fh:
                    fh.write(b"x" * size)
            _exif_jpeg(os.path.join(d, "p.jpg"), "Nikon", "Z6", "2023:01:01 00:00:00")
            os.mkdir(os.path.join(d, "sub"))
            s = QSettings(os.path.join(d, "s.ini"), QSettings.IniFormat)
            model = m.DirModel(s)
            model.setRootPath(d)
            _wait(model, lambda: model.item_count() == 5)
            ext = cols.index_of("ext")
            model.sort(ext, Qt.AscendingOrder)
            self.assertEqual([e.name for e in model.entries], ["sub", "p.jpg", "b.log", "a.txt", "c.txt"])
            model.set_group_by("ext")
            self.assertEqual([g[0] for g in model.groups], [".jpg", ".log", ".txt", cols.UNSPECIFIED])
            model.set_group_by("none")
            # header filter on Size buckets
            vals = dict(model.filter_values(m.SIZE))
            self.assertEqual(vals["Tiny (0 - 16 KB)"], 2)   # a.txt, p.jpg
            model.set_filter(m.SIZE, ["Medium (1 - 128 MB)"])
            self.assertEqual([e.name for e in model.entries], ["c.txt"])
            self.assertEqual(model.total_count(), 5)
            model.clear_filters()
            self.assertEqual(model.item_count(), 5)
            # metadata column: loads in the background, then sorts
            maker = cols.index_of("camera_maker")
            model.set_visible_columns({m.NAME, maker})
            _wait(model, lambda: model._by_path[os.path.join(d, "p.jpg")].meta is not None)
            idx = model.index_for_path(os.path.join(d, "p.jpg"))
            self.assertEqual(model.data(idx.siblingAtColumn(maker)), "Nikon")
            model.sort(maker, Qt.AscendingOrder)
            self.assertEqual(model.entries[1].name, "p.jpg")   # after the folder, before blanks
            # filters are per folder: navigating clears them
            model.set_filter(ext, [".txt"])
            model.setRootPath(d)
            self.assertEqual(model.filters, {})


if __name__ == "__main__":
    unittest.main()
