# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
""""Check network mounts": every network mount with its health, a button to detach stuck
ones, and a Flatpak start test (one dead mount can stop every Flatpak app from opening)."""

import os

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLabel, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from caracal import mountcheck
from caracal.background import run_async


class MountCheckDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Check network mounts")
        self.resize(760, 480)
        lay = QVBoxLayout(self)
        intro = QLabel("A network mount that stopped answering can freeze programs that touch "
                       "it — and can stop <b>Flatpak apps</b> from opening, since Flatpak sets "
                       "up your folders for each app it starts. This lists every network mount "
                       "on this computer (not only Caracal's). The start test below shows the "
                       "exact error if a Flatpak app won't open.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Folder", "Type", "Source", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.tree, 1)
        self.summary = QLabel("Checking…")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        self.recheck = QPushButton("Check again")
        self.recheck.clicked.connect(self.refresh)
        self.fix = QPushButton("Fix stuck mounts")
        self.fix.setToolTip("Detach the stuck mounts so nothing waits on them any more. "
                            "Caracal's own drives and locations reconnect by themselves once "
                            "the server answers.")
        self.fix.clicked.connect(self._fix)
        self.fix.setEnabled(False)
        row.addWidget(self.recheck)
        row.addWidget(self.fix)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("<b>Flatpak start test</b> — starts an app's sandbox (not the app) "
                             "and shows any error:"))
        frow = QHBoxLayout()
        self.apps = QComboBox()
        self.apps.setMinimumWidth(280)
        self.test_btn = QPushButton("Test")
        self.test_btn.clicked.connect(self._test_flatpak)
        frow.addWidget(self.apps, 1)
        frow.addWidget(self.test_btn)
        lay.addLayout(frow)
        self.flatpak_out = QPlainTextEdit()
        self.flatpak_out.setReadOnly(True)
        self.flatpak_out.setMaximumHeight(90)
        self.flatpak_out.setPlaceholderText("Result appears here.")
        lay.addWidget(self.flatpak_out)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.results = []
        run_async(self, mountcheck.flatpak_apps, self._apps_loaded)
        self.refresh()

    # -- mounts ------------------------------------------------------------------------

    def refresh(self):
        self.recheck.setEnabled(False)
        self.summary.setText("Checking…")
        run_async(self, mountcheck.check, self._checked)

    def _checked(self, results, _err):
        self.recheck.setEnabled(True)
        self.results = results or []
        self.tree.clear()
        bad = QBrush(QColor("#d93025"))
        for m in self.results:
            it = QTreeWidgetItem([m["path"], m["fstype"], m["source"], m["message"]])
            if m["status"] != "ok":
                for c in range(4):
                    it.setForeground(c, bad)
            self.tree.addTopLevelItem(it)
        stuck = mountcheck.stuck(self.results)
        self.fix.setEnabled(bool(stuck))
        if not self.results:
            self.summary.setText("No network mounts right now.")
        elif stuck:
            self.summary.setText("<b>%d stuck mount%s.</b> Click <i>Fix stuck mounts</i>, then "
                                 "try your Flatpak apps again."
                                 % (len(stuck), "" if len(stuck) == 1 else "s"))
        else:
            self.summary.setText("All %d network mounts answer normally." % len(self.results))
        if hasattr(self.ctl, "set_stuck_mounts"):
            self.ctl.set_stuck_mounts(stuck)

    def _fix(self):
        paths = [m["path"] for m in mountcheck.stuck(self.results)]
        if not paths or self.ctl.helper.busy:
            return
        self.fix.setEnabled(False)
        self.summary.setText("Detaching %d mount%s…" % (len(paths), "" if len(paths) == 1 else "s"))
        self.ctl._after_helper = lambda _r: self.refresh()
        self.ctl._run("force-unmount", {"paths": paths}, "Detaching stuck mounts")

    # -- flatpak -----------------------------------------------------------------------

    def _apps_loaded(self, apps, _err):
        self.apps.clear()
        for app_id, name in apps or []:
            self.apps.addItem("%s (%s)" % (name, app_id), app_id)
        if not apps:
            self.apps.addItem("No Flatpak apps found", None)
        self.test_btn.setEnabled(bool(apps))

    def _test_flatpak(self):
        app_id = self.apps.currentData()
        if not app_id:
            return
        self.test_btn.setEnabled(False)
        self.flatpak_out.setPlainText("Starting %s's sandbox…" % app_id)

        def done(res, err):
            self.test_btn.setEnabled(True)
            ok, text = res if res else (False, str(err))
            self.flatpak_out.setPlainText(("✓ The sandbox starts fine.\n" if ok else
                                           "✗ It didn't start:\n") + (text if not ok else ""))
        run_async(self, lambda: mountcheck.test_flatpak(app_id), done)


def is_available():
    return os.path.exists("/proc/self/mountinfo")
