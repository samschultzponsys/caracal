# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""7-Zip in Explorer's right-click menu, laid out like 7-Zip's Windows shell menu.

Runs the 7-Zip command-line program (7zz / 7z / 7za from the distro's 7zip or p7zip package)
as a separate process; nothing of 7-Zip is bundled. Jobs run in the background with a progress
window and Cancel. Passwords go to 7-Zip on stdin, never on the command line (where other
users could see them in the process list)."""

import os
import re
import shutil

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

ARCHIVE_EXTS = ("7z", "zip", "rar", "tar", "gz", "tgz", "bz2", "tbz", "tbz2", "xz", "txz", "zst",
                "tzst", "lz", "lzma", "z", "cab", "arj", "lzh", "lha", "iso", "img", "wim", "cpio",
                "rpm", "deb", "apk", "jar", "war", "xpi", "dmg", "vhd", "vhdx", "vmdk", "squashfs",
                "chm", "msi", "cbz", "cbr", "zipx", "001")
FORMATS = (("7z", "7z"), ("zip", "zip"), ("tar", "tar"), ("wim", "wim"),
           ("xz", "xz (one file)"), ("gzip", "gzip (one file)"), ("bzip2", "bzip2 (one file)"))
EXT_FOR = {"7z": ".7z", "zip": ".zip", "tar": ".tar", "wim": ".wim", "xz": ".xz",
           "gzip": ".gz", "bzip2": ".bz2"}
LEVELS = (("0", "Store"), ("1", "Fastest"), ("3", "Fast"), ("5", "Normal"), ("7", "Maximum"),
          ("9", "Ultra"))
HASHES = (("CRC32", "CRC-32"), ("CRC64", "CRC-64"), ("SHA1", "SHA-1"), ("SHA256", "SHA-256"),
          ("*", "*"))
UPDATE_MODES = (("-aou", "Rename extracted files"), ("-aoa", "Overwrite without asking"),
                ("-aos", "Skip existing files"), ("-aot", "Rename existing files"))


def find_exe():
    for name in ("7zz", "7z", "7za"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/usr/libexec/p7zip/7z", "/usr/lib/p7zip/7z"):
        if os.access(p, os.X_OK):
            return p
    return None


def is_archive(path):
    name = os.path.basename(path).lower()
    return "." in name and name.rsplit(".", 1)[1] in ARCHIVE_EXTS


def stem(path):
    """Name without archive extensions: photos.tar.gz -> photos, a.7z.001 -> a."""
    name = os.path.basename(path.rstrip("/"))
    if os.path.isdir(path):
        return name
    name = re.sub(r"\.\d{3}$", "", name)
    name = re.sub(r"\.part\d+(?=\.rar$)", "", name, flags=re.I)
    low = name.lower()
    for double in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz"):
        if low.endswith(double):
            return name[:-len(double)]
    return name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name


def default_archive_base(paths, folder):
    if len(paths) == 1:
        return stem(paths[0]) if not os.path.isdir(paths[0]) else os.path.basename(paths[0].rstrip("/"))
    return os.path.basename(folder.rstrip("/")) or "Archive"


def unique(path):
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    if root.endswith(".tar"):
        root, ext = root[:-4], ".tar" + ext
    n = 2
    while os.path.exists("%s (%d)%s" % (root, n, ext)):
        n += 1
    return "%s (%d)%s" % (root, n, ext)


# --------------------------------------------------------------------------
# command lines (pure functions, unit tested)
# --------------------------------------------------------------------------

def add_args(archive, names, fmt="7z", level="5", password=False, encrypt_names=True,
             volume="", delete_after=False):
    args = ["a", "-bsp1", "-bso0", "-y", "-t" + fmt]
    if fmt not in ("tar", "wim"):
        args.append("-mx" + level)
    if password:
        args.append("-p")
        if fmt == "7z" and encrypt_names:
            args.append("-mhe=on")
        if fmt == "zip":
            args.append("-mem=AES256")
    if volume:
        args.append("-v" + volume)
    if delete_after:
        args.append("-sdel")
    return args + ["--", archive] + list(names)


def extract_args(archive, dest, mode="-aou"):
    return ["x", "-bsp1", "-bso0", mode, "-o" + dest, "--", archive]


def test_args(archive):
    return ["t", "-bsp1", "-bso0", "--", archive]


def hash_args(names, alg):
    return ["h", "-scrc" + alg, "--"] + list(names)


def last_percent(text):
    found = re.findall(r"(\d{1,3})%", text)
    return int(found[-1]) if found else None


def needs_password(err):
    e = err.lower()
    return "wrong password" in e or "break signaled" in e or "enter password" in e or \
        "encrypted" in e and "can not open" in e


# --------------------------------------------------------------------------
# running a job
# --------------------------------------------------------------------------

class Job(QObject):
    """One 7-Zip run with a small progress window (non-modal) and Cancel."""
    finished = Signal(bool, str)        # ok, output (stdout + stderr)

    def __init__(self, title, args, cwd, password=None, parent=None, quiet=False):
        super().__init__(parent)
        self.args, self.cwd, self.password = args, cwd, password
        self.out, self.err = "", ""
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LC_ALL", "C.UTF-8")
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.readyReadStandardOutput.connect(self._stdout)
        self.proc.readyReadStandardError.connect(self._stderr)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(self._failed)
        self.dialog = None
        if not quiet:
            self.dialog = QDialog(parent)
            self.dialog.setWindowTitle("7-Zip")
            self.dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            lay = QVBoxLayout(self.dialog)
            self.label = QLabel(title)
            self.label.setWordWrap(True)
            lay.addWidget(self.label)
            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            lay.addWidget(self.bar)
            row = QHBoxLayout()
            row.addStretch(1)
            cancel = QPushButton("Cancel")
            cancel.clicked.connect(self.cancel)
            row.addWidget(cancel)
            lay.addLayout(row)
            self.dialog.resize(380, 0)
            self.dialog.rejected.connect(self.cancel)
            # only show the window for jobs that take a moment
            QTimer.singleShot(600, lambda: self.proc.state() != QProcess.NotRunning
                              and self.dialog.show())
        self.cancelled = False

    def start(self):
        self.proc.start(find_exe() or "7z", self.args)
        if self.password is not None:
            data = self.password + "\n"
            if "-p" in self.args and self.args and self.args[0] == "a":
                data += self.password + "\n"      # 7-Zip asks to verify a new password
            self.proc.write(data.encode())
        self.proc.closeWriteChannel()

    def cancel(self):
        if self.proc.state() != QProcess.NotRunning:
            self.cancelled = True
            self.proc.kill()

    def _stdout(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.out += text
        pct = last_percent(text)
        if pct is not None and self.dialog is not None:
            self.bar.setValue(pct)

    def _stderr(self):
        self.err += bytes(self.proc.readAllStandardError()).decode(errors="replace")

    def _failed(self, error):
        if error == QProcess.FailedToStart:
            self._finish(False, "7-Zip couldn't be started.")

    def _done(self, code, _status):
        if self.cancelled:
            self._finish(False, "Cancelled.")
            return
        clean = re.sub(r"[\b]+", "\n", self.out)
        self._finish(code in (0, 1), (clean + "\n" + self.err).strip(), code)

    def _finish(self, ok, text, code=None):
        if self.dialog is not None:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.code = code
        self.finished.emit(ok, text)


# --------------------------------------------------------------------------
# dialogs
# --------------------------------------------------------------------------

class AddDialog(QDialog):
    """7-Zip's "Add to archive" with the options people actually use."""

    def __init__(self, paths, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to Archive")
        self.folder = folder
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(default_archive_base(paths, folder) + ".7z")
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.name, 1)
        rl.addWidget(browse)
        form.addRow("Archive:", row)
        self.fmt = QComboBox()
        single_file = len(paths) == 1 and not os.path.isdir(paths[0])
        for key, label in FORMATS:
            if "one file" in label and not single_file:
                continue
            self.fmt.addItem(label, key)
        self.fmt.currentIndexChanged.connect(self._format_changed)
        form.addRow("Archive format:", self.fmt)
        self.level = QComboBox()
        for key, label in LEVELS:
            self.level.addItem(label, key)
        self.level.setCurrentIndex(3)
        form.addRow("Compression level:", self.level)
        self.volume = QComboBox()
        self.volume.setEditable(True)
        for v in ("", "10M", "100M", "650M - CD", "700M - CD", "4092M - FAT", "4480M - DVD",
                  "8128M - DVD DL", "23040M - BD"):
            self.volume.addItem(v)
        self.volume.setToolTip("Split the archive into parts of this size (e.g. 100M, 4G).")
        form.addRow("Split to volumes, bytes:", self.volume)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        form.addRow("Enter password:", self.pw)
        form.addRow("Reenter password:", self.pw2)
        self.enc_names = QCheckBox("Encrypt file names")
        self.enc_names.setChecked(True)
        form.addRow("", self.enc_names)
        self.delete_after = QCheckBox("Delete files after compression")
        form.addRow("", self.delete_after)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._format_changed()

    def _format_changed(self, *_):
        fmt = self.fmt.currentData()
        name = self.name.text()
        base = name
        for ext in sorted(EXT_FOR.values(), key=len, reverse=True):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
                break
        self.name.setText(base + EXT_FOR[fmt])
        can_pw = fmt in ("7z", "zip")
        self.pw.setEnabled(can_pw)
        self.pw2.setEnabled(can_pw)
        self.enc_names.setEnabled(fmt == "7z")
        self.level.setEnabled(fmt not in ("tar", "wim"))

    def _browse(self):
        p, _f = QFileDialog.getSaveFileName(self, "Archive", self.archive_path())
        if p:
            self.name.setText(p)

    def archive_path(self):
        n = self.name.text().strip()
        return n if os.path.isabs(n) else os.path.join(self.folder, n)

    def _ok(self):
        if not self.name.text().strip():
            return
        if self.pw.isEnabled() and self.pw.text() != self.pw2.text():
            QMessageBox.warning(self, self.windowTitle(), "The passwords don't match.")
            return
        self.accept()

    def options(self):
        vol = self.volume.currentText().split(" ")[0].strip().lower()
        return {"fmt": self.fmt.currentData(), "level": self.level.currentData(),
                "password": self.pw.text() if self.pw.isEnabled() and self.pw.text() else None,
                "encrypt_names": self.enc_names.isChecked(), "volume": vol,
                "delete_after": self.delete_after.isChecked()}


class ExtractDialog(QDialog):
    def __init__(self, archive, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extract : %s" % os.path.basename(archive))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.dest = QLineEdit(os.path.join(folder, stem(archive)))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.dest, 1)
        rl.addWidget(browse)
        form.addRow("Extract to:", row)
        self.mode = QComboBox()
        for key, label in UPDATE_MODES:
            self.mode.addItem(label, key)
        form.addRow("Existing files:", self.mode)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Only if the archive is encrypted")
        form.addRow("Password:", self.pw)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, 0)

    def _browse(self):
        p = QFileDialog.getExistingDirectory(self, "Extract to", self.dest.text())
        if p:
            self.dest.setText(p)


class TextDialog(QDialog):
    """Monospace output (checksums, test results) with Copy."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        box.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        copy = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.resize(720, 360)


# --------------------------------------------------------------------------
# the menu and its actions
# --------------------------------------------------------------------------

def menu_icon():
    for name in ("7zip", "p7zip", "application-x-7z-compressed", "package-x-generic", "ark"):
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


class SevenZip:
    """Builds the 7-Zip submenu for a selection and runs its commands for an Explorer
    window (`win` provides status, current_location(), model, select_paths())."""

    def __init__(self, win):
        self.win = win
        self.jobs = []

    # -- menu ------------------------------------------------------------------------

    def fill(self, m, paths):
        if find_exe() is None:
            a = m.addAction("7-Zip isn't installed (install the “7zip” package)")
            a.setEnabled(False)
            return
        folder = os.path.dirname(paths[0])
        one = paths[0] if len(paths) == 1 else None
        archives = [p for p in paths if is_archive(p) and not os.path.isdir(p)]
        if one and archives:
            m.addAction("Open archive", lambda: self.win.open_file(one))
        if archives:
            m.addAction("Extract files…", lambda: self.extract_dialog(archives[0]))
            m.addAction("Extract Here", lambda: [self.extract(a, folder) for a in archives])
            if one:
                m.addAction("Extract to “%s/”" % stem(one),
                            lambda: self.extract(one, os.path.join(folder, stem(one))))
            else:
                m.addAction("Extract to “*/”", lambda: [
                    self.extract(a, os.path.join(folder, stem(a))) for a in archives])
            m.addAction("Test archive", lambda: [self.test(a) for a in archives])
            m.addSeparator()
        base = default_archive_base(paths, folder)
        m.addAction("Add to archive…", lambda: self.add_dialog(paths))
        if shutil.which("xdg-email"):
            m.addAction("Compress and email…", lambda: self.add_dialog(paths, email=True))
        m.addAction("Add to “%s.7z”" % base, lambda: self.add(paths, base + ".7z"))
        if shutil.which("xdg-email"):
            m.addAction("Compress to “%s.7z” and email" % base,
                        lambda: self.add(paths, base + ".7z", email=True))
        m.addAction("Add to “%s.zip”" % base, lambda: self.add(paths, base + ".zip", fmt="zip"))
        if shutil.which("xdg-email"):
            m.addAction("Compress to “%s.zip” and email" % base,
                        lambda: self.add(paths, base + ".zip", fmt="zip", email=True))
        files = [p for p in paths if not os.path.isdir(p)] or paths
        crc = m.addMenu("CRC SHA")
        for alg, label in HASHES:
            crc.addAction(label, lambda a=alg: self.hash(files, a))

    # -- commands ------------------------------------------------------------------------

    def _run(self, title, args, cwd, password=None, done=None, quiet=False):
        job = Job(title, args, cwd, password, self.win, quiet=quiet)
        self.jobs.append(job)
        self.win.status.setText(title)

        def finished(ok, text):
            self.jobs.remove(job)
            if done:
                done(ok, text, job)
        job.finished.connect(finished)
        job.start()
        return job

    def _report(self, ok, text, job, what):
        if ok:
            self.win.status.setText("%s — done%s." % (what, " with warnings" if job.code == 1 else ""))
            if job.code == 1:
                TextDialog("7-Zip: warnings", text, self.win).exec()
        elif text != "Cancelled.":
            QMessageBox.warning(self.win, "7-Zip", "%s failed.\n\n%s" % (what, text[-1500:]))
            self.win.status.setText("%s failed." % what)
        else:
            self.win.status.setText("%s cancelled." % what)

    def add_dialog(self, paths, email=False):
        folder = os.path.dirname(paths[0])
        d = AddDialog(paths, folder, self.win)
        if d.exec():
            o = d.options()
            self.add(paths, d.archive_path(), email=email, **o)

    def add(self, paths, archive, fmt=None, level="5", password=None, encrypt_names=True,
            volume="", delete_after=False, email=False):
        folder = os.path.dirname(paths[0])
        archive = archive if os.path.isabs(archive) else os.path.join(folder, archive)
        if fmt is None:
            fmt = "zip" if archive.lower().endswith(".zip") else "7z"
        archive = unique(archive) if not volume else archive
        names = [os.path.relpath(p, folder) for p in paths]
        what = "Compressing to %s" % os.path.basename(archive)
        args = add_args(archive, names, fmt, level, bool(password), encrypt_names, volume,
                        delete_after)

        def done(ok, text, job):
            self._report(ok, text, job, what)
            if ok:
                out = archive if not volume else archive + ".001"
                QTimer.singleShot(400, lambda: self.win.select_paths([out]))
                if email:
                    QProcess.startDetached("xdg-email", ["--attach", out])
        self._run(what + "…", args, folder, password, done)

    def extract_dialog(self, archive):
        d = ExtractDialog(archive, os.path.dirname(archive), self.win)
        if d.exec():
            self.extract(archive, d.dest.text().strip(), d.mode.currentData(),
                         d.pw.text() or None)

    def extract(self, archive, dest, mode="-aou", password=None):
        what = "Extracting %s" % os.path.basename(archive)

        def done(ok, text, job):
            if not ok and needs_password(text) and not job.cancelled:
                pw, got = QInputDialog.getText(
                    self.win, "Enter password",
                    ("Wrong password. " if password else "") +
                    "Enter the password for %s:" % os.path.basename(archive),
                    QLineEdit.Password)
                if got:
                    self.extract(archive, dest, mode, pw)
                    return
                self.win.status.setText("%s cancelled." % what)
                return
            self._report(ok, text, job, what)
            if ok and os.path.dirname(dest.rstrip("/")) == os.path.dirname(archive):
                QTimer.singleShot(400, lambda: self.win.select_paths([dest]))
        self._run(what + "…", extract_args(archive, dest, mode), os.path.dirname(archive),
                  password, done)

    def test(self, archive, password=None):
        what = "Testing %s" % os.path.basename(archive)

        def done(ok, text, job):
            if not ok and needs_password(text) and not job.cancelled:
                pw, got = QInputDialog.getText(self.win, "Enter password",
                                               "Enter the password for %s:"
                                               % os.path.basename(archive), QLineEdit.Password)
                if got:
                    self.test(archive, pw)
                return
            if ok:
                QMessageBox.information(self.win, "7-Zip", "%s\n\nThere are no errors."
                                        % os.path.basename(archive))
                self.win.status.setText("")
            else:
                self._report(ok, text, job, what)
        self._run(what + "…", test_args(archive), os.path.dirname(archive), password, done)

    def hash(self, files, alg):
        folder = os.path.dirname(files[0])
        names = [os.path.relpath(p, folder) for p in files]
        label = dict(HASHES).get(alg, alg)

        def done(ok, text, job):
            if ok:
                lines = text.splitlines()
                while lines and (not lines[0].strip() or lines[0].startswith(
                        ("7-Zip", " 64-bit", "64-bit", "Scanning", "p7zip"))):
                    lines.pop(0)
                body = "\n".join(lines)
                TextDialog("Checksum information (%s)" % label, body.strip(), self.win).show()
                self.win.status.setText("")
            else:
                self._report(ok, text, job, "Checksum")
        self._run("Calculating %s…" % label, hash_args(names, alg), folder, None, done)
