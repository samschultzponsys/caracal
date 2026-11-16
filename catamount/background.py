# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Run a blocking function in a thread and get the result back on the UI thread."""

import threading

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object, object)  # result, exception


def run_async(parent, fn, callback):
    """Call fn() in a worker thread; callback(result, error) runs on the UI thread."""
    relay = _Relay(parent)
    relay.done.connect(callback)
    relay.done.connect(lambda *_: relay.deleteLater())

    def work():
        try:
            result, error = fn(), None
        except Exception as exc:  # report to the UI instead of dying silently
            result, error = None, exc
        relay.done.emit(result, error)

    threading.Thread(target=work, daemon=True).start()
    return relay
