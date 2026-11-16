# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
import unittest
from unittest import mock

from catamount import bookmarks, discovery
from catamount.model import Drive

AVAHI = """+;eth0;IPv4;NAS;_smb._tcp;local
=;eth0;IPv4;NAS;_smb._tcp;local;nas.local;192.168.1.5;445;
=;eth0;IPv6;NAS;_smb._tcp;local;nas.local;fe80::1;445;
=;eth0;IPv4;NAS;_nfs._tcp;local;nas.local;192.168.1.5;2049;"path=/srv"
=;eth0;IPv4;Office\\032PC;_smb._tcp;local;office-pc.local;192.168.1.9;445;
"""


class DiscoveryTests(unittest.TestCase):
    def test_avahi(self):
        servers = discovery.parse_avahi(AVAHI)
        self.assertEqual([(s.name, s.host, s.address, sorted(s.kinds)) for s in servers],
                         [("NAS", "nas.local", "192.168.1.5", ["nfs", "smb"]),
                          ("Office PC", "office-pc.local", "192.168.1.9", ["smb"])])

    def test_smb_shares(self):
        out = "Disk|media|Movies\nDisk|IPC$|\nIPC|IPC$|IPC Service\nPrinter|hp|\nDisk|backup|\n"
        self.assertEqual(discovery.parse_smb_shares(out), [("backup", ""), ("media", "Movies")])
        self.assertEqual(len(discovery.parse_smb_shares(out, include_hidden=True)), 3)

    def test_errors(self):
        self.assertIn("password", discovery.explain_smb_error("session setup failed: NT_STATUS_LOGON_FAILURE"))
        self.assertTrue(discovery.needs_login("NT_STATUS_ACCESS_DENIED"))
        self.assertFalse(discovery.needs_login("NT_STATUS_BAD_NETWORK_NAME"))

    def test_showmount(self):
        self.assertEqual(discovery.parse_showmount("/srv/data 192.168.1.0/24\n/srv/pub *\n"),
                         [("/srv/data", "192.168.1.0/24"), ("/srv/pub", "*")])

    def test_auth_file_removed(self):
        with discovery._AuthFile("sam", "p w", "") as a:
            path = a.path
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            with open(path) as fh:
                self.assertIn("password=p w", fh.read())
            self.assertEqual(a.args, ["-A", path])
        self.assertFalse(os.path.exists(path))
        with discovery._AuthFile("", "", "") as a:
            self.assertEqual(a.args, ["-N"])


class FakeSettings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


KDE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">
 <bookmark href="file:///home/sam">
  <title>Home</title>
 </bookmark>
</xbel>
"""


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {"XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
               "XDG_DATA_HOME": os.path.join(self.tmp.name, "data")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.makedirs(env["XDG_DATA_HOME"])
        with open(bookmarks.kde_path(), "w") as fh:
            fh.write(KDE)
        os.makedirs(os.path.dirname(bookmarks.gtk_path()))
        with open(bookmarks.gtk_path(), "w") as fh:
            fh.write("file:///home/sam/Documents\n")

    def drive(self, mp, bookmark=True, name="My Share"):
        return Drive(spec={"id": "id-" + mp, "name": name, "mount_point": mp,
                           "bookmark": bookmark}, mount_unit="x.mount")

    def read(self, path):
        with open(path) as fh:
            return fh.read()

    def test_sync_add_move_remove(self):
        s = FakeSettings()
        self.assertEqual(bookmarks.sync([self.drive("/mnt/my share")], s), "")
        gtk = self.read(bookmarks.gtk_path())
        self.assertIn("file:///home/sam/Documents", gtk)
        self.assertIn("file:///mnt/my%20share My Share", gtk)
        self.assertIn('<bookmark href="file:///mnt/my%20share">', self.read(bookmarks.kde_path()))
        self.assertTrue(self.read(bookmarks.kde_path()).rstrip().endswith("</xbel>"))

        # User deletes it by hand: a later sync with no change must not re-add it.
        with open(bookmarks.gtk_path(), "w") as fh:
            fh.write("file:///home/sam/Documents\n")
        bookmarks.sync([self.drive("/mnt/my share")], s)
        self.assertNotIn("/mnt/", self.read(bookmarks.gtk_path()))

        # Mount point changes -> moves; bookmark turned off -> removed.
        bookmarks.sync([self.drive("/mnt/new")], s)
        self.assertIn("file:///mnt/new My Share", self.read(bookmarks.gtk_path()))
        bookmarks.sync([self.drive("/mnt/new", bookmark=False)], s)
        self.assertNotIn("/mnt/", self.read(bookmarks.gtk_path()))
        kde = self.read(bookmarks.kde_path())
        self.assertNotIn("/mnt/", kde)
        self.assertIn("<title>Home</title>", kde)
        self.assertEqual(json.loads(s["bookmarks"]), {})

    def test_no_kde_file_untouched(self):
        os.unlink(bookmarks.kde_path())
        bookmarks.sync([self.drive("/mnt/x")], FakeSettings())
        self.assertFalse(os.path.exists(bookmarks.kde_path()))
