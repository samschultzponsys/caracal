# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Extra file details for Explorer's columns: creation time, picture size and EXIF,
music/video tags (ffprobe), document properties (OOXML/ODF/PDF). No Qt imports except
QImageReader for picture dimensions. Every reader is best effort: missing tools or
unreadable files just leave the fields empty."""

import ctypes
import ctypes.util
import json
import os
import re
import shutil
import struct
import subprocess
import time
import zipfile

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "heic", "avif", "ico", "svg"}
MEDIA_EXTS = {"mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "aac", "wma", "mp4", "m4v", "mkv",
              "webm", "avi", "mov", "wmv", "mpg", "mpeg", "3gp", "ts"}
OFFICE_EXTS = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}


# --------------------------------------------------------------------------
# creation time (statx), which os.stat doesn't expose on Linux
# --------------------------------------------------------------------------

class _StatxTimestamp(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("_pad", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    _fields_ = [("stx_mask", ctypes.c_uint32), ("stx_blksize", ctypes.c_uint32),
                ("stx_attributes", ctypes.c_uint64), ("stx_nlink", ctypes.c_uint32),
                ("stx_uid", ctypes.c_uint32), ("stx_gid", ctypes.c_uint32),
                ("stx_mode", ctypes.c_uint16), ("_spare0", ctypes.c_uint16),
                ("stx_ino", ctypes.c_uint64), ("stx_size", ctypes.c_uint64),
                ("stx_blocks", ctypes.c_uint64), ("stx_attributes_mask", ctypes.c_uint64),
                ("stx_atime", _StatxTimestamp), ("stx_btime", _StatxTimestamp),
                ("stx_ctime", _StatxTimestamp), ("stx_mtime", _StatxTimestamp),
                ("_rest", ctypes.c_uint64 * 32)]   # kernel struct is 256 bytes; leave room


_libc = None
STATX_BTIME = 0x800
AT_FDCWD = -100


def birth_time(path):
    """File creation time (seconds) if the filesystem records it, else None."""
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        if not hasattr(_libc, "statx"):
            return None
        buf = _Statx()
        if _libc.statx(AT_FDCWD, os.fsencode(path), 0, STATX_BTIME, ctypes.byref(buf)) != 0:
            return None
        if not buf.stx_mask & STATX_BTIME:
            return None
        return buf.stx_btime.tv_sec + buf.stx_btime.tv_nsec / 1e9
    except (OSError, AttributeError, ValueError):
        return None


# --------------------------------------------------------------------------
# pictures
# --------------------------------------------------------------------------

EXIF_TAGS = {0x010F: "camera_maker", 0x0110: "camera_model", 0x9003: "date_taken",
             0x0132: "date_time"}


def _exif_from_tiff(data):
    """Parse a TIFF/EXIF block. Returns {name: value} for the tags we use."""
    out = {}
    if len(data) < 8:
        return out
    endian = "<" if data[:2] == b"II" else ">" if data[:2] == b"MM" else None
    if not endian:
        return out

    def read_ifd(offset, depth=0):
        if depth > 3 or offset + 2 > len(data):
            return
        count = struct.unpack_from(endian + "H", data, offset)[0]
        for i in range(min(count, 400)):
            p = offset + 2 + i * 12
            if p + 12 > len(data):
                return
            tag, typ, n, val = struct.unpack_from(endian + "HHII", data, p)
            if tag == 0x8769:                      # EXIF sub-IFD
                read_ifd(val, depth + 1)
            elif tag in EXIF_TAGS and typ == 2:    # ASCII
                start = p + 8 if n <= 4 else val
                raw = data[start:start + n].split(b"\0")[0]
                out[EXIF_TAGS[tag]] = raw.decode("latin-1", "replace").strip()
    read_ifd(struct.unpack_from(endian + "I", data, 4)[0])
    return out


def jpeg_exif(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)
    except OSError:
        return {}
    if head[:2] != b"\xff\xd8":
        return _exif_from_tiff(head) if head[:2] in (b"II", b"MM") else {}
    pos = 2
    while pos + 4 < len(head):
        if head[pos] != 0xFF:
            break
        marker, length = head[pos + 1], struct.unpack(">H", head[pos + 2:pos + 4])[0]
        if marker == 0xE1 and head[pos + 4:pos + 10] == b"Exif\0\0":
            return _exif_from_tiff(head[pos + 10:pos + 2 + length])
        if marker == 0xDA:
            break
        pos += 2 + length
    return {}


def _exif_time(text):
    try:
        return time.mktime(time.strptime(text[:19], "%Y:%m:%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def picture_info(path):
    info = {}
    try:
        from PySide6.QtGui import QImageReader
        size = QImageReader(path).size()
        if size.isValid():
            info["width"], info["height"] = size.width(), size.height()
    except ImportError:
        pass
    ex = jpeg_exif(path)
    if ex.get("date_taken") or ex.get("date_time"):
        info["date_taken"] = _exif_time(ex.get("date_taken") or ex.get("date_time"))
    for k in ("camera_maker", "camera_model"):
        if ex.get(k):
            info[k] = ex[k]
    return info


# --------------------------------------------------------------------------
# music and video (ffprobe)
# --------------------------------------------------------------------------

def media_info(path):
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        proc = subprocess.run([exe, "-v", "quiet", "-print_format", "json", "-show_format",
                               "-show_streams", path], capture_output=True, timeout=15)
        data = json.loads(proc.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    info = {}
    try:
        info["length"] = float(fmt.get("duration"))
    except (TypeError, ValueError):
        pass
    try:
        info["bitrate"] = int(fmt.get("bit_rate")) // 1000
    except (TypeError, ValueError):
        pass
    for src, dst in (("title", "title"), ("artist", "artist"), ("album_artist", "artist"),
                     ("album", "album"), ("genre", "genre")):
        if tags.get(src) and dst not in info:
            info[dst] = tags[src]
    m = re.match(r"(\d{4})", tags.get("date", "") or tags.get("year", ""))
    if m:
        info["year"] = int(m.group(1))
    m = re.match(r"(\d+)", tags.get("track", ""))
    if m:
        info["track"] = int(m.group(1))
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and st.get("width"):
            info["width"], info["height"] = st["width"], st["height"]
            break
    return info


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------

def _xml_text(xml, tag):
    m = re.search(r"<(?:\w+:)?%s[^>]*>([^<]*)</" % tag, xml)
    return m.group(1).strip() if m else ""


def _iso_time(text):
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def office_info(path):
    info = {}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "docProps/core.xml" in names:
                core = zf.read("docProps/core.xml").decode("utf-8", "replace")
                info["title"] = _xml_text(core, "title")
                info["authors"] = _xml_text(core, "creator")
                info["content_created"] = _iso_time(_xml_text(core, "created"))
            if "docProps/app.xml" in names:
                app = zf.read("docProps/app.xml").decode("utf-8", "replace")
                pages = _xml_text(app, "Pages") or _xml_text(app, "Slides")
                if pages.isdigit():
                    info["pages"] = int(pages)
            if "meta.xml" in names:
                meta = zf.read("meta.xml").decode("utf-8", "replace")
                info["title"] = info.get("title") or _xml_text(meta, "title")
                info["authors"] = info.get("authors") or _xml_text(meta, "initial-creator") \
                    or _xml_text(meta, "creator")
                info["content_created"] = info.get("content_created") or \
                    _iso_time(_xml_text(meta, "creation-date"))
                m = re.search(r'meta:page-count="(\d+)"', meta)
                if m:
                    info["pages"] = int(m.group(1))
    except (OSError, zipfile.BadZipFile, KeyError):
        return {}
    return {k: v for k, v in info.items() if v not in ("", None)}


def pdf_info(path):
    exe = shutil.which("pdfinfo")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, path], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    info = {}
    for line in out.splitlines():
        key, _, val = line.partition(":")
        val = val.strip()
        if key == "Title" and val:
            info["title"] = val
        elif key == "Author" and val:
            info["authors"] = val
        elif key == "Pages" and val.isdigit():
            info["pages"] = int(val)
        elif key == "CreationDate" and val:
            try:
                info["content_created"] = time.mktime(time.strptime(val[:24], "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                pass
    return info


def read(path, is_dir):
    """All extra details for one item (may take a while; call from a worker thread)."""
    info = {}
    bt = birth_time(path)
    if bt:
        info["date_created"] = bt
    if is_dir:
        return info
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    try:
        if ext in IMAGE_EXTS:
            info.update(picture_info(path))
        elif ext in MEDIA_EXTS:
            info.update(media_info(path))
        elif ext in OFFICE_EXTS:
            info.update(office_info(path))
        elif ext == "pdf":
            info.update(pdf_info(path))
    except Exception:   # never let one odd file break the folder
        pass
    return info
