# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Runs the privileged helper through pkexec without blocking the UI."""

import json
import os
import shutil

from PySide6.QtCore import QObject, QProcess, Signal

HELPER = os.environ.get("CATAMOUNT_HELPER", "/usr/libexec/catamount/catamount-helper")


class HelperClient(QObject):
    # ok, error message, reply data
    finished = Signal(bool, str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None

    @property
    def busy(self):
        return self._proc is not None

    def run(self, command, payload):
        if self._proc is not None:
            self.finished.emit(False, "Another operation is still running.", {})
            return
        pkexec = shutil.which("pkexec")
        if not pkexec:
            self.finished.emit(False, "pkexec was not found. Install polkit (pkexec).", {})
            return
        if not os.path.exists(HELPER):
            self.finished.emit(False, "The CataMount helper is missing (%s). "
                                      "Reinstall the package." % HELPER, {})
            return
        proc = QProcess(self)
        proc.setProgram(pkexec)
        proc.setArguments([HELPER, command])
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()
        proc.write(json.dumps(payload).encode("utf-8"))
        proc.closeWriteChannel()

    def _on_error(self, error):
        if error == QProcess.FailedToStart and self._proc is not None:
            self._proc = None
            self.finished.emit(False, "Could not start pkexec.", {})

    def _on_finished(self, code, _status):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
        err = bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
        proc.deleteLater()
        reply = None
        for line in reversed(out.splitlines()):
            try:
                reply = json.loads(line)
                break
            except ValueError:
                continue
        if isinstance(reply, dict):
            if reply.get("ok"):
                self.finished.emit(True, "", reply)
            else:
                self.finished.emit(False, str(reply.get("error") or "The operation failed."), reply)
            return
        if code in (126, 127):
            self.finished.emit(False, "Authorization was cancelled or denied.", {})
        else:
            self.finished.emit(False, err or "The helper failed (exit code %d)." % code, {})
