# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Pull searchable text out of files for content search. No Qt imports.

Plain text and source code are read directly; Office Open XML (docx/xlsx/pptx),
OpenDocument (odt/ods/odp) and EPUB are unzipped with the standard library; PDFs use
`pdftotext` (poppler-utils) when it's installed. Everything else is skipped.
"""

import html
import os
import re
import shutil
import subprocess
import zipfile

MAX_TEXT = 256 * 1024          # characters kept per file

TEXT_EXTS = set("""
txt md markdown rst csv tsv log json xml html htm xhtml ini conf cfg toml yaml yml
py pyw js mjs ts tsx jsx c h cc cpp hpp cs java kt go rs rb php pl sh bash zsh fish ps1 bat
sql css scss less tex bib srt vtt sub nfo diff patch rtf org adoc properties gradle cmake
""".split())
ZIP_DOCS = {
    "docx": ("word/document.xml", "word/header", "word/footer"),
    "xlsx": ("xl/sharedStrings.xml",),
    "pptx": ("ppt/slides/slide",),
    "odt": ("content.xml",), "ods": ("content.xml",), "odp": ("content.xml",),
    "epub": (".xhtml", ".html", ".htm"),
}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def supported(name, pdf=None):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in TEXT_EXTS or ext in ZIP_DOCS:
        return True
    if ext == "pdf":
        return pdf if pdf is not None else bool(shutil.which("pdftotext"))
    return False


def _clean(text):
    return SPACE_RE.sub(" ", text).strip()[:MAX_TEXT]


def _read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TEXT * 4)
    if b"\x00" in raw[:4096]:
        return ""  # binary despite the extension
    for enc in ("utf-8", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    if path.lower().endswith((".html", ".htm", ".xhtml", ".xml")):
        text = html.unescape(TAG_RE.sub(" ", text))
    return _clean(text)


def _read_zip(path, ext):
    parts = []
    size = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename
            wanted = ZIP_DOCS[ext]
            if ext == "epub":
                ok = name.lower().endswith(wanted)
            else:
                ok = any(name == w or (name.startswith(w) and name.endswith(".xml")) for w in wanted)
            if not ok or info.file_size > 50 * 1024 * 1024:
                continue
            data = zf.read(info).decode("utf-8", "replace")
            # keep paragraph breaks as spaces so words don't run together
            data = re.sub(r"</(w:p|text:p|text:h|a:p|p|div|h\d|li)>", " ", data)
            parts.append(html.unescape(TAG_RE.sub("", data)))
            size += len(parts[-1])
            if size > MAX_TEXT:
                break
    return _clean(" ".join(parts))


def _read_pdf(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, "-q", "-enc", "UTF-8", "-l", "200", path, "-"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return _clean(proc.stdout.decode("utf-8", "replace"))


def extract(path):
    """Return searchable text for a file, or "" if unsupported/unreadable."""
    name = os.path.basename(path)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    try:
        if ext in TEXT_EXTS:
            return _read_text(path)
        if ext in ZIP_DOCS:
            return _read_zip(path, ext)
        if ext == "pdf":
            return _read_pdf(path)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError):
        return ""
    return ""
