# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Command-line control: list, connect and disconnect drives without the GUI."""

import json
import os
import shutil
import subprocess
import sys

from catamount.model import STATE_LABELS, load_drives, refresh_states

USAGE = """Usage: catamount [OPTION]

  (no option)            Open the CataMount window.
  --tray                 Start hidden in the system tray.
  --list                 List drives and their status.
  --connect DRIVE        Connect a drive (by name, local folder or id).
  --search QUERY         Search the local index and print matching paths.
  --explorer [FOLDER]    Open a File Explorer window.
  --search-window [--folder DIR] [QUERY]
                         Open the instant search window.
  --disconnect DRIVE     Disconnect a drive.
  --connect-all          Connect every drive.
  --disconnect-all       Disconnect every drive.
  --version              Print the version.
  --help                 Show this help.
"""

COMMANDS = ("--list", "--connect", "--disconnect", "--connect-all", "--disconnect-all",
            "--search")


def explorer_args(argv):
    """Folder to open in Explorer ('' = default start page), or None if not asked."""
    if "--explorer" not in argv:
        return None
    rest = [a for a in argv[argv.index("--explorer") + 1:] if not a.startswith("--")]
    if not rest:
        return ""
    target = rest[0]
    if target.startswith("file://"):
        from urllib.parse import unquote, urlparse
        target = unquote(urlparse(target).path)
    return os.path.abspath(os.path.expanduser(target))


def search_window_args(argv):
    """(query, folder) when the search window was asked for, else None."""
    if "--search-window" not in argv:
        return None
    args = argv[argv.index("--search-window") + 1:]
    folder = ""
    if "--folder" in args:
        i = args.index("--folder")
        if i + 1 < len(args):
            folder = args[i + 1]
            del args[i:i + 2]
        else:
            del args[i]
    return " ".join(a for a in args if not a.startswith("--")), folder


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


def search_cli(args):
    from catamount import indexdb
    i = args.index("--search")
    query = " ".join(args[i + 1:])
    as_json = "--json" in args
    query = query.replace("--json", "").strip()
    conn = indexdb.connect(readonly=True)
    if conn is None:
        print("No search index yet. Turn on indexing in CataMount's Search index tab.",
              file=sys.stderr)
        return 1
    results, total, _ms = indexdb.search(conn, query, limit=10000)
    if as_json:
        print(json.dumps([{"path": r.path, "name": r.name, "is_dir": r.is_dir, "size": r.size,
                           "mtime": r.mtime, "drive": r.drive_name} for r in results]))
    else:
        for r in results:
            print(r.path)
    return 0 if results else 1


def main(argv):
    if "--search" in argv:
        return search_cli(argv[1:])
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
