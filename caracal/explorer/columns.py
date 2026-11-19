# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Every Details-view column Explorer offers that makes sense on Linux. Each column knows
how to read its value from an Entry, format it, sort it, and bucket it for Group by and the
header filters. The first four (Name, Date modified, Type, Size) keep indices 0-3 so saved
per-folder sort settings stay valid."""

import grp
import os
import pwd
import stat as stat_mod
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: str          # text, date, size, num, dur, name, type
    width: int         # default width in average character widths
    category: str = "General"
    meta: bool = False  # needs the background metadata reader


COLUMNS = (
    Column("name", "Name", "name", 50),
    Column("date", "Date modified", "date", 17),
    Column("type", "Type", "type", 14),
    Column("size", "Size", "size", 9),
    Column("created", "Date created", "date", 17, meta=True),
    Column("accessed", "Date accessed", "date", 17),
    Column("ext", "File extension", "text", 10),
    Column("filename", "Filename", "text", 30),
    Column("folder", "Folder path", "text", 30),
    Column("path", "Path", "text", 40),
    Column("attributes", "Attributes", "text", 9),
    Column("owner", "Owner", "text", 12),
    Column("group", "Group", "text", 12),
    Column("permissions", "Permissions", "text", 12),
    Column("link", "Link target", "text", 30),
    Column("dimensions", "Dimensions", "text", 13, "Image", True),
    Column("width", "Width", "num", 10, "Image", True),
    Column("height", "Height", "num", 10, "Image", True),
    Column("date_taken", "Date taken", "date", 17, "Image", True),
    Column("camera_maker", "Camera maker", "text", 14, "Image", True),
    Column("camera_model", "Camera model", "text", 16, "Image", True),
    Column("length", "Length", "dur", 10, "Media", True),
    Column("bitrate", "Bit rate", "num", 10, "Media", True),
    Column("title", "Title", "text", 25, "Media", True),
    Column("artist", "Contributing artists", "text", 22, "Media", True),
    Column("album", "Album", "text", 22, "Media", True),
    Column("genre", "Genre", "text", 14, "Media", True),
    Column("year", "Year", "num", 7, "Media", True),
    Column("track", "#", "num", 5, "Media", True),
    Column("authors", "Authors", "text", 20, "Document", True),
    Column("pages", "Pages", "num", 7, "Document", True),
    Column("content_created", "Content created", "date", 17, "Document", True),
)
NAME, DATE, TYPE, SIZE = range(4)
BY_KEY = {c.key: i for i, c in enumerate(COLUMNS)}
# group-by keys saved before 1.2 used these names
BY_KEY_ALIASES = {"date": DATE, "type": TYPE, "name": NAME, "size": SIZE}
CATEGORIES = ("General", "Image", "Media", "Document")
# offered straight in the header's right-click menu (the rest are under "More…")
COMMON = ("name", "date", "type", "size", "created", "accessed", "dimensions", "length",
          "title", "authors", "path")
UNSPECIFIED = "Unspecified"


def index_of(key):
    if key in BY_KEY:
        return BY_KEY[key]
    return BY_KEY_ALIASES.get(key, -1)


_users, _groups = {}, {}


def user_name(uid):
    if uid not in _users:
        try:
            _users[uid] = pwd.getpwuid(uid).pw_name
        except (KeyError, OverflowError):
            _users[uid] = str(uid)
    return _users[uid]


def group_name(gid):
    if gid not in _groups:
        try:
            _groups[gid] = grp.getgrgid(gid).gr_name
        except (KeyError, OverflowError):
            _groups[gid] = str(gid)
    return _groups[gid]


def extension(e):
    return ("." + e.name.rsplit(".", 1)[1].lower()) if not e.is_dir and "." in e.name.lstrip(".") else ""


def attributes(e):
    a = ""
    if not e.mode & stat_mod.S_IWUSR:
        a += "R"
    if e.hidden:
        a += "H"
    if e.is_dir:
        a += "D"
    if e.is_link:
        a += "L"
    return a


def value(col, e):
    """Raw value of column `col` for entry `e` (None when unknown)."""
    key = COLUMNS[col].key
    if key == "name":
        return e.name
    if key == "date":
        return e.mtime
    if key == "type":
        return e.type_name
    if key == "size":
        return None if e.is_dir else e.size
    if key == "accessed":
        return e.atime or None
    if key == "ext":
        return extension(e)
    if key == "filename":
        return e.name
    if key == "folder":
        return os.path.dirname(e.path)
    if key == "path":
        return e.path
    if key == "attributes":
        return attributes(e)
    if key == "owner":
        return user_name(e.uid)
    if key == "group":
        return group_name(e.gid)
    if key == "permissions":
        return stat_mod.filemode(e.mode)
    if key == "link":
        if not e.is_link:
            return ""
        if e.link is None:
            try:
                e.link = os.readlink(e.path)
            except OSError:
                e.link = ""
        return e.link
    m = e.meta or {}
    if key == "created":
        return m.get("date_created")
    if key == "dimensions":
        return ("%d x %d" % (m["width"], m["height"])) if m.get("width") else None
    return m.get(key)


def fmt_duration(sec):
    sec = int(round(sec))
    return "%02d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60)


def display(col, e, model):
    v = value(col, e)
    if v is None or v == "":
        return ""
    c = COLUMNS[col]
    if c.kind == "date":
        return model.format_date(v)
    if c.kind == "size":
        return model.format_size(v)
    if c.kind == "dur":
        return fmt_duration(v)
    if c.key == "bitrate":
        return "%dkbps" % v
    if c.key in ("width", "height"):
        return "%d pixels" % v
    return str(v)


def right_aligned(col):
    return COLUMNS[col].kind in ("size", "num", "dur")


def folders_first(col):
    return COLUMNS[col].kind != "date"


def sort_key(col, e):
    """Sort key for a present value (callers put missing values last)."""
    v = value(col, e)
    kind = COLUMNS[col].kind
    if kind in ("name",) or COLUMNS[col].key in ("filename",):
        from caracal.explorer.model import _natural
        return _natural(v)
    if kind in ("text", "type"):
        if COLUMNS[col].key == "dimensions":
            m = e.meta or {}
            return (m.get("width", 0) * m.get("height", 0), v)
        return (str(v).lower(), e.name.lower())
    return v


# --------------------------------------------------------------------------
# buckets for Group by and the header filters: (order key, label)
# --------------------------------------------------------------------------

def date_bucket(ts, now=None):
    now = time.time() if now is None else now
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    week_start = midnight - t.tm_wday * 86400            # Monday
    month_start = time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    prev_month = time.mktime((t.tm_year - (t.tm_mon == 1), (t.tm_mon - 2) % 12 + 1, 1,
                              0, 0, 0, 0, 0, -1))
    year_start = time.mktime((t.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))
    if ts >= midnight + 86400:
        return 0, "In the future"
    if ts >= midnight:
        return 1, "Today"
    if ts >= midnight - 86400:
        return 2, "Yesterday"
    if ts >= week_start:
        return 3, "Earlier this week"
    if ts >= week_start - 7 * 86400:
        return 4, "Last week"
    if ts >= month_start:
        return 5, "Earlier this month"
    if ts >= prev_month:
        return 6, "Last month"
    if ts >= year_start:
        return 7, "Earlier this year"
    return 8, "A long time ago"


def size_bucket(e):
    if e.is_dir:
        return 0, UNSPECIFIED
    s = e.size
    for i, (limit, label) in enumerate(((0, "Empty (0 KB)"), (16 * 1024, "Tiny (0 - 16 KB)"),
                                        (1024 ** 2, "Small (16 KB - 1 MB)"),
                                        (128 * 1024 ** 2, "Medium (1 - 128 MB)"),
                                        (1024 ** 3, "Large (128 MB - 1 GB)"),
                                        (4 * 1024 ** 3, "Huge (1 - 4 GB)")), 1):
        if s <= limit:
            return i, label
    return 7, "Gigantic (> 4 GB)"


def name_bucket(e):
    c = e.name[:1].upper()
    if "A" <= c <= "H":
        return 1, "A - H"
    if "I" <= c <= "P":
        return 2, "I - P"
    if "Q" <= c <= "Z":
        return 3, "Q - Z"
    if c.isdigit():
        return 0, "0 - 9"
    return 4, "Other"


DURATION_BUCKETS = ((60, "Very short (less than 1 minute)"), (300, "Short (1 - 5 minutes)"),
                    (1800, "Medium (5 - 30 minutes)"), (3600, "Long (30 - 60 minutes)"))


def bucket(col, e):
    c = COLUMNS[col]
    if c.kind == "name":
        return name_bucket(e)
    if c.kind == "size":
        return size_bucket(e)
    if c.kind == "type":
        return (0 if e.is_dir else 1, e.type_name.lower()), e.type_name
    v = value(col, e)
    if v is None or v == "":
        return (99,), UNSPECIFIED
    if c.kind == "date":
        return date_bucket(v)
    if c.kind == "dur":
        for i, (limit, label) in enumerate(DURATION_BUCKETS):
            if v < limit:
                return (i,), label
        return (len(DURATION_BUCKETS),), "Very long (over 60 minutes)"
    if c.kind == "num":
        return (0, v), display(col, e, _Plain)
    if c.key == "dimensions":
        m = e.meta or {}
        return (0, m.get("width", 0) * m.get("height", 0), str(v)), str(v)
    return (0, str(v).lower()), str(v)


class _Plain:
    """display() helper for bucket labels that need no date/size formatting."""
    @staticmethod
    def format_date(v):
        return str(v)

    @staticmethod
    def format_size(v):
        return str(v)
