# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Local search index for network drives (SQLite + FTS5). No Qt imports.

One index per user at $XDG_DATA_HOME/caracal/index.sqlite (0600). The tray app
writes it (crawler); the search window, CLI and desktop search service read it.
WAL mode lets them run at the same time. Searching never touches the network.

Tables
  drives   one row per indexed drive (id = Caracal drive id)
  dirs     every folder, path relative to the drive's mount point ("" = root)
  files    every file and folder entry (is_dir), with size/mtime
  names    FTS5 trigram index over files.name   (rowid = files.id)
  dirfts   FTS5 trigram index over dirs.path    (rowid = dirs.id)
  contents FTS5 word index over extracted text  (rowid = files.id)
"""

import os
import re
import sqlite3
import time
from dataclasses import dataclass, field

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS drives(
    id TEXT PRIMARY KEY, name TEXT, mount_point TEXT,
    files INTEGER DEFAULT 0, dirs INTEGER DEFAULT 0, content_files INTEGER DEFAULT 0,
    last_full REAL DEFAULT 0, last_scan REAL DEFAULT 0, last_error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS dirs(
    id INTEGER PRIMARY KEY, drive TEXT NOT NULL, path TEXT NOT NULL, mtime REAL,
    UNIQUE(drive, path));
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY, drive TEXT NOT NULL, dir INTEGER NOT NULL, name TEXT NOT NULL,
    is_dir INTEGER NOT NULL, size INTEGER, mtime REAL, content_mtime REAL DEFAULT 0,
    ext TEXT, UNIQUE(dir, name));
CREATE INDEX IF NOT EXISTS files_ext ON files(ext);
CREATE INDEX IF NOT EXISTS files_drive ON files(drive);
CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE VIRTUAL TABLE IF NOT EXISTS names USING fts5(name, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS dirfts USING fts5(path, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS contents USING fts5(body,
    tokenize='unicode61 remove_diacritics 2');
"""


def default_path():
    """Where the index is opened: in RAM when it's encrypted or memory-only (see
    indexstore), else ~/.local/share/caracal/index.sqlite."""
    from caracal import indexstore
    return indexstore.active_path()


def connect(path=None, readonly=False):
    path = path or default_path()
    if readonly:
        if not os.path.exists(path):
            return None
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5,
                               check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        old_umask = os.umask(0o077)
        try:
            conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        finally:
            os.umask(old_umask)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


# --------------------------------------------------------------------------
# writing (used by the crawler)
# --------------------------------------------------------------------------

def upsert_drive(conn, drive_id, name, mount_point):
    conn.execute("INSERT INTO drives(id, name, mount_point) VALUES(?,?,?) "
                 "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                 "mount_point=excluded.mount_point", (drive_id, name, mount_point))


def get_dir(conn, drive_id, path):
    row = conn.execute("SELECT id, mtime FROM dirs WHERE drive=? AND path=?",
                       (drive_id, path)).fetchone()
    return row


def add_dir(conn, drive_id, path, mtime):
    row = get_dir(conn, drive_id, path)
    if row:
        conn.execute("UPDATE dirs SET mtime=? WHERE id=?", (mtime, row[0]))
        return row[0]
    cur = conn.execute("INSERT INTO dirs(drive, path, mtime) VALUES(?,?,?)",
                       (drive_id, path, mtime))
    conn.execute("INSERT INTO dirfts(rowid, path) VALUES(?,?)", (cur.lastrowid, path))
    return cur.lastrowid


def dir_entries(conn, dir_id):
    """{name: (file_id, is_dir, size, mtime)} for a folder's current index rows."""
    return {r[1]: (r[0], r[2], r[3], r[4]) for r in conn.execute(
        "SELECT id, name, is_dir, size, mtime FROM files WHERE dir=?", (dir_id,))}


def cached_listing(conn, drive_id, rel):
    """[(name, is_dir, size, mtime)] for one indexed folder, or None if it isn't indexed.
    Explorer shows this instantly for indexed network folders, then checks the live share."""
    row = conn.execute("SELECT id FROM dirs WHERE drive=? AND path=?", (drive_id, rel)).fetchone()
    if row is None:
        return None
    return [(r[0], bool(r[1]), r[2] or 0, r[3] or 0) for r in conn.execute(
        "SELECT name, is_dir, size, mtime FROM files WHERE dir=?", (row[0],))]


def file_ext(name, is_dir=False):
    if is_dir or "." not in name.lstrip("."):
        return ""
    return name.rsplit(".", 1)[1].lower()


def add_file(conn, drive_id, dir_id, name, is_dir, size, mtime):
    cur = conn.execute("INSERT INTO files(drive, dir, name, is_dir, size, mtime, ext) "
                       "VALUES(?,?,?,?,?,?,?)", (drive_id, dir_id, name, int(is_dir), size,
                                                 mtime, file_ext(name, is_dir)))
    conn.execute("INSERT INTO names(rowid, name) VALUES(?,?)", (cur.lastrowid, name))
    return cur.lastrowid


def update_file(conn, file_id, size, mtime):
    conn.execute("UPDATE files SET size=?, mtime=? WHERE id=?", (size, mtime, file_id))


def delete_file(conn, file_id):
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.execute("DELETE FROM names WHERE rowid=?", (file_id,))
    conn.execute("DELETE FROM contents WHERE rowid=?", (file_id,))


def delete_tree(conn, drive_id, path):
    """Remove a folder (path relative to the drive) and everything below it."""
    like = (path + "/%") if path else "%"
    rows = conn.execute("SELECT id FROM dirs WHERE drive=? AND (path=? OR path LIKE ?)",
                        (drive_id, path, like)).fetchall()
    for (dir_id,) in rows:
        for (fid,) in conn.execute("SELECT id FROM files WHERE dir=?", (dir_id,)).fetchall():
            delete_file(conn, fid)
        conn.execute("DELETE FROM dirs WHERE id=?", (dir_id,))
        conn.execute("DELETE FROM dirfts WHERE rowid=?", (dir_id,))


def delete_drive(conn, drive_id):
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE drive=?", (drive_id,))]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute("DELETE FROM names WHERE rowid IN (%s)" % marks, chunk)
        conn.execute("DELETE FROM contents WHERE rowid IN (%s)" % marks, chunk)
    conn.execute("DELETE FROM files WHERE drive=?", (drive_id,))
    dids = [r[0] for r in conn.execute("SELECT id FROM dirs WHERE drive=?", (drive_id,))]
    for i in range(0, len(dids), 500):
        chunk = dids[i:i + 500]
        conn.execute("DELETE FROM dirfts WHERE rowid IN (%s)" % ",".join("?" * len(chunk)), chunk)
    conn.execute("DELETE FROM dirs WHERE drive=?", (drive_id,))
    conn.execute("DELETE FROM drives WHERE id=?", (drive_id,))
    conn.commit()


def set_content(conn, file_id, text, mtime):
    conn.execute("DELETE FROM contents WHERE rowid=?", (file_id,))
    if text:
        conn.execute("INSERT INTO contents(rowid, body) VALUES(?,?)", (file_id, text))
    conn.execute("UPDATE files SET content_mtime=? WHERE id=?", (mtime, file_id))


def clear_contents(conn, drive_id):
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE drive=? AND content_mtime>0",
                                      (drive_id,))]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute("DELETE FROM contents WHERE rowid IN (%s)" % ",".join("?" * len(chunk)),
                     chunk)
    conn.execute("UPDATE files SET content_mtime=0 WHERE drive=?", (drive_id,))
    conn.commit()


def update_counts(conn, drive_id, **fields):
    files = conn.execute("SELECT COUNT(*) FROM files WHERE drive=? AND is_dir=0",
                         (drive_id,)).fetchone()[0]
    dirs = conn.execute("SELECT COUNT(*) FROM dirs WHERE drive=?", (drive_id,)).fetchone()[0]
    content = conn.execute("SELECT COUNT(*) FROM files WHERE drive=? AND content_mtime>0",
                           (drive_id,)).fetchone()[0]
    sets = ["files=?", "dirs=?", "content_files=?"] + ["%s=?" % k for k in fields]
    conn.execute("UPDATE drives SET %s WHERE id=?" % ",".join(sets),
                 [files, dirs, content] + list(fields.values()) + [drive_id])
    conn.commit()


def drive_stats(conn):
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT id, name, mount_point, files, dirs, content_files, "
                            "last_full, last_scan, last_error FROM drives").fetchall()
    except sqlite3.OperationalError:     # brand-new database the indexer hasn't set up yet
        return {}
    return {r[0]: {"name": r[1], "mount_point": r[2], "files": r[3], "dirs": r[4],
                   "content_files": r[5], "last_full": r[6], "last_scan": r[7],
                   "last_error": r[8]} for r in rows}


# --------------------------------------------------------------------------
# query language
# --------------------------------------------------------------------------

SIZE_UNITS = {"": 1, "b": 1, "k": 1000, "kb": 1000, "m": 10**6, "mb": 10**6,
              "g": 10**9, "gb": 10**9, "t": 10**12, "tb": 10**12}
TYPE_EXT = {
    "doc": "doc docx odt rtf txt md pdf pages",
    "pic": "jpg jpeg png gif bmp tif tiff webp heic svg raw cr2 nef arw",
    "audio": "mp3 flac wav ogg m4a aac opus wma",
    "video": "mp4 mkv avi mov wmv m4v webm mpg mpeg",
    "zip": "zip 7z rar tar gz bz2 xz zst tgz iso",
    "sheet": "xls xlsx ods csv",
    "slides": "ppt pptx odp key",
    "code": "py js ts c h cpp cs java go rs sh php rb kt swift sql html css json yaml yml xml",
}


@dataclass
class Query:
    words: list = field(default_factory=list)      # substrings that must all be in the name
    path_words: list = field(default_factory=list)  # substrings of the folder path
    content: list = field(default_factory=list)     # words/phrases inside files
    exts: list = field(default_factory=list)
    kind: str = ""                                  # "file" | "folder" | ""
    size_min: int = None
    size_max: int = None
    mtime_min: float = None
    mtime_max: float = None
    drive: str = ""
    folder: str = ""                                # absolute path prefix scope
    patterns: list = field(default_factory=list)    # wildcard patterns on name

    @property
    def empty(self):
        return not (self.words or self.path_words or self.content or self.exts or self.kind
                    or self.patterns or self.size_min is not None or self.size_max is not None
                    or self.mtime_min is not None or self.mtime_max is not None or self.drive
                    or self.folder)


def _size(text):
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([kmgt]?b?)$", text.lower())
    if not m:
        raise ValueError(text)
    return int(float(m.group(1)) * SIZE_UNITS[m.group(2)])


def _time_range(text, now=None):
    now = time.time() if now is None else now
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    named = {"today": (midnight, None), "yesterday": (midnight - 86400, midnight),
             "week": (now - 7 * 86400, None), "month": (now - 31 * 86400, None),
             "year": (now - 366 * 86400, None)}
    if text in named:
        return named[text]
    m = re.match(r"^([<>]=?)?(\d{4})-(\d{2})(?:-(\d{2}))?$", text)
    if not m:
        raise ValueError(text)
    day = time.mktime((int(m.group(2)), int(m.group(3)), int(m.group(4) or 1), 0, 0, 0, 0, 0, -1))
    op = m.group(1) or ""
    if op.startswith(">"):
        return day, None
    if op.startswith("<"):
        return None, day
    return day, day + (86400 if m.group(4) else 31 * 86400)


def parse_query(text, now=None):
    """Parse an Everything-style query. Unknown or bad filters become plain words."""
    q = Query()
    for tok in re.findall(r'(?:\w+:)?"[^"]*"|\S+', text):
        key, sep, val = tok.partition(":")
        key_l = key.lower()
        val = val.strip('"')
        try:
            if sep and key_l == "ext" and val:
                q.exts += [e.lstrip(".").lower() for e in val.replace(";", ",").split(",") if e]
                continue
            if sep and key_l == "type" and val:
                v = val.lower()
                if v in ("folder", "dir", "folders"):
                    q.kind = "folder"
                elif v in ("file", "files"):
                    q.kind = "file"
                elif v in TYPE_EXT:
                    q.exts += TYPE_EXT[v].split()
                else:
                    raise ValueError(v)
                continue
            if sep and key_l == "size" and val:
                m = re.match(r"^([<>]=?)?(.+)$", val)
                n = _size(m.group(2))
                if (m.group(1) or "").startswith("<"):
                    q.size_max = n
                elif (m.group(1) or "").startswith(">"):
                    q.size_min = n
                else:
                    q.size_min, q.size_max = int(n * 0.9), int(n * 1.1)
                continue
            if sep and key_l in ("modified", "dm", "date") and val:
                q.mtime_min, q.mtime_max = _time_range(val.lower(), now)
                continue
            if sep and key_l == "drive" and val:
                q.drive = val.lower()
                continue
            if sep and key_l in ("folder", "path", "in") and val:
                q.path_words.append(val.strip("/").lower())
                continue
            if sep and key_l == "content" and val:
                q.content.append(val)
                continue
        except ValueError:
            pass
        word = tok.strip('"')
        if not word:
            continue
        if re.match(r"^\*\.[A-Za-z0-9]+$", word):
            q.exts.append(word[2:].lower())       # *.pdf is an extension filter
        elif "*" in word or "?" in word:
            q.patterns.append(word.lower())
        elif "/" in word or "\\" in word:
            q.path_words.append(word.replace("\\", "/").strip("/").lower())
        else:
            q.words.append(word.lower())
    return q


def _fts_phrase(text):
    return '"%s"' % text.replace('"', '""')


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@dataclass
class Result:
    file_id: int
    drive_id: str
    drive_name: str
    mount_point: str
    folder: str          # path relative to the drive
    name: str
    is_dir: bool
    size: int
    mtime: float
    snippet: str = ""

    @property
    def path(self):
        return os.path.join(self.mount_point, self.folder, self.name) if self.folder \
            else os.path.join(self.mount_point, self.name)

    @property
    def folder_path(self):
        return os.path.join(self.mount_point, self.folder) if self.folder else self.mount_point


ORDERS = {
    "name": "f.name COLLATE NOCASE", "folder": "d.path COLLATE NOCASE",
    "size": "f.size", "modified": "f.mtime", "drive": "dr.name COLLATE NOCASE",
}


COUNT_CAP = 20000


def search(conn, query, limit=500, order="name", descending=False, drives=None):
    """Run a Query (or query text). Returns (results, total_or_None, elapsed_ms).

    total is an exact count up to COUNT_CAP, else None (show "COUNT_CAP+").
    """
    t0 = time.perf_counter()
    if conn is None:
        return [], 0, 0.0
    q = parse_query(query) if isinstance(query, str) else query
    if q.empty:
        return [], 0, 0.0
    joins, join_args, where, where_args = [], [], [], []
    snippet_sql = "''"

    long_words = [w for w in q.words if len(w) >= 3]   # trigram index needs 3+ chars
    if long_words:
        # One MATCH for all words: far faster than one FTS join per word.
        joins.append("JOIN names ON names.rowid=f.id AND names MATCH ?")
        join_args.append(" AND ".join(_fts_phrase(w) for w in long_words))
    for w in q.words:
        if len(w) < 3:
            where.append("f.name LIKE ? ESCAPE '\\'")
            where_args.append(_like(w))
    if q.content:
        joins.append("JOIN contents ON contents.rowid=f.id AND contents MATCH ?")
        join_args.append(" AND ".join(_fts_phrase(c) for c in q.content))
        snippet_sql = "snippet(contents, 0, '[', ']', '…', 12)"
    for w in q.path_words:
        if len(w) >= 3:
            where.append("f.dir IN (SELECT rowid FROM dirfts WHERE dirfts MATCH ?)")
            where_args.append(_fts_phrase(w))
        else:
            where.append("d.path LIKE ? ESCAPE '\\'")
            where_args.append(_like(w))
    for pat in q.patterns:
        where.append("LOWER(f.name) GLOB ?")
        where_args.append(pat.replace("[", "[[]"))
    if q.exts:
        where.append("f.ext IN (%s)" % ",".join("?" * len(q.exts)))
        where_args += q.exts
    if q.kind == "folder":
        where.append("f.is_dir=1")
    elif q.kind == "file":
        where.append("f.is_dir=0")
    for col, op, val in (("f.size", ">=", q.size_min), ("f.size", "<=", q.size_max),
                         ("f.mtime", ">=", q.mtime_min), ("f.mtime", "<", q.mtime_max)):
        if val is not None:
            where.append("%s%s?" % (col, op))
            where_args.append(val)
    if q.drive:
        where.append("(LOWER(dr.name) LIKE ? OR LOWER(dr.mount_point) LIKE ?)")
        where_args += [_like(q.drive)] * 2
    if q.folder:
        where.append("(dr.mount_point || '/' || d.path || '/') LIKE ? ESCAPE '\\'")
        where_args.append(q.folder.rstrip("/").replace("%", "\\%").replace("_", "\\_")
                          + "/%")
    if drives is not None:
        where.append("f.drive IN (%s)" % ",".join("?" * len(drives)) if drives else "0")
        where_args += list(drives)

    args = join_args + where_args
    base = ("FROM files f JOIN dirs d ON d.id=f.dir JOIN drives dr ON dr.id=f.drive %s %s"
            % (" ".join(joins), ("WHERE " + " AND ".join(where)) if where else ""))
    sql = ("SELECT f.id, f.drive, dr.name, dr.mount_point, d.path, f.name, f.is_dir, f.size, "
           "f.mtime, %s %s ORDER BY %s %s LIMIT ?"
           % (snippet_sql, base, ORDERS.get(order, ORDERS["name"]),
              "DESC" if descending else "ASC"))
    rows = conn.execute(sql, args + [limit]).fetchall()
    results = [Result(*r[:9], snippet=r[9] or "") for r in rows]
    total = len(results)
    if total >= limit:
        cap = COUNT_CAP
        total = conn.execute("SELECT COUNT(*) FROM (SELECT 1 %s LIMIT %d)" % (base, cap + 1),
                             args).fetchone()[0]
        if total > cap:
            total = None
    return results, total, (time.perf_counter() - t0) * 1000
