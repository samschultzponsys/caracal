# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Command-line control: list, connect and disconnect drives without the GUI."""

import json
import shutil
import subprocess
import sys

from catamount.model import STATE_LABELS, load_drives, refresh_states

USAGE = """Usage: catamount [OPTION]

  (no option)            Open the CataMount window.
  --tray                 Start hidden in the system tray.
  --list                 List drives and their status.
  --connect DRIVE        Connect a drive (by name, local folder or id).
  --disconnect DRIVE     Disconnect a drive.
  --connect-all          Connect every drive.
  --disconnect-all       Disconnect every drive.
  --version              Print the version.
  --help                 Show this help.
"""

COMMANDS = ("--list", "--connect", "--disconnect", "--connect-all", "--disconnect-all")


def wants_cli(argv):
    return any(a in COMMANDS for a in argv[1:])


def _find(drives, key):
    key_l = key.lower().rstrip("/")
    for d in drives:
        if key_l in (d.id.lower(), d.name.lower(), d.mount_point.lower()):
            return d
    matches = [d for d in drives if key_l in d.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _helper(command, payload):
    from catamount.helper_client import HELPER as helper_path
    if not shutil.which("pkexec"):
        return False, "pkexec was not found. Install polkit."
    proc = subprocess.run(["pkexec", helper_path, command], input=json.dumps(payload),
                          capture_output=True, text=True)
    for line in reversed(proc.stdout.splitlines()):
        try:
            reply = json.loads(line)
            break
        except ValueError:
            continue
    else:
        if proc.returncode in (126, 127):
            return False, "Authorization was cancelled or denied."
        return False, proc.stderr.strip() or "The helper failed."
    if not reply.get("ok"):
        return False, reply.get("error", "Failed.")
    failed = reply.get("failed") or {}
    return (not failed), "\n".join(failed.values())


def main(argv):
    drives = load_drives()
    refresh_states(drives)
    args = argv[1:]
    if "--list" in args:
        if not drives:
            print("No CataMount drives.")
            return 0
        width = max(len(d.name) for d in drives)
        for d in drives:
            print("%-*s  %-30s  %-24s  %s" % (width, d.name, d.remote, d.mount_point,
                                              STATE_LABELS.get(d.state, d.state)))
        return 0
    for flag, command in (("--connect-all", "connect"), ("--disconnect-all", "disconnect")):
        if flag in args:
            if not drives:
                print("No CataMount drives.")
                return 0
            ok, msg = _helper(command, {"ids": [d.id for d in drives]})
            if msg:
                print(msg, file=sys.stderr)
            return 0 if ok else 1
    for flag, command in (("--connect", "connect"), ("--disconnect", "disconnect")):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print(USAGE, file=sys.stderr)
                return 2
            d = _find(drives, args[i + 1])
            if d is None:
                print("No drive matches '%s'. Use --list to see drives." % args[i + 1],
                      file=sys.stderr)
                return 1
            ok, msg = _helper(command, {"id": d.id})
            print(("%s: %s" % (d.name, "done" if ok else msg)), file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
    print(USAGE, file=sys.stderr)
    return 2
