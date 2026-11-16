# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from catamount import model
from catamount.model import Drive, parse_address, suggest_mount_point


class AddressTests(unittest.TestCase):
    def check(self, text, kind, server, share, default="smb", **extra):
        a = parse_address(text, default)
        self.assertTrue(a.valid, (text, a.errors))
        self.assertEqual((a.kind, a.server, a.share), (kind, server, share), text)
        for k, v in extra.items():
            self.assertEqual(getattr(a, k), v)

    def test_smb_forms(self):
        self.check(r"\\nas\media", "smb", "nas", "media")
        self.check(r"\\nas\media\movies", "smb", "nas", "media/movies")
        self.check("smb://nas.local/media/", "smb", "nas.local", "media")
        self.check("//192.168.1.5/share", "smb", "192.168.1.5", "share")
        self.check("smb://WORK;sam@nas/home", "smb", "nas", "home", username="sam", domain="WORK")
        self.check("nas/media", "smb", "nas", "media")

    def test_nfs_forms(self):
        self.check("nas:/srv/export", "nfs", "nas", "/srv/export")
        self.check("nfs://nas/srv/export", "nfs", "nas", "/srv/export")
        self.check("[fd00::5]:/data", "nfs", "[fd00::5]", "/data")
        self.check("nas/srv/x", "nfs", "nas", "/srv/x", default="nfs")

    def test_invalid(self):
        self.assertFalse(parse_address(r"\\nas").valid)
        self.assertFalse(parse_address("").valid)
        self.assertFalse(parse_address(r"\\bad host\x").valid)
        self.assertFalse(parse_address(r"\\nas\..\x").valid)

    def test_suggest(self):
        a = parse_address(r"\\nas\My Media")
        self.assertEqual(suggest_mount_point("/mnt", a), "/mnt/My-Media")
        self.assertEqual(suggest_mount_point("/mnt/", a, {"/mnt/My-Media"}), "/mnt/My-Media-2")


class StatusTests(unittest.TestCase):
    def test_parse_show_and_state(self):
        out = ("Id=mnt-x.automount\nActiveState=active\nSubState=waiting\n"
               "UnitFileState=enabled\n\nId=mnt-x.mount\nActiveState=inactive\n"
               "SubState=dead\nUnitFileState=static\n")
        info = model.parse_systemctl_show(out)
        d = Drive(spec={}, mount_unit="mnt-x.mount", automount_unit="mnt-x.automount")
        self.assertEqual(model.drive_state(d, info), ("ready", True))
        info["mnt-x.mount"]["ActiveState"] = "active"
        self.assertEqual(model.drive_state(d, info)[0], "connected")
        info["mnt-x.mount"]["ActiveState"] = "failed"
        self.assertEqual(model.drive_state(d, info)[0], "failed")


class FstabTests(unittest.TestCase):
    def test_load(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# comment\nUUID=abc / ext4 defaults 0 1\n"
                     "//nas/My\\040Share /mnt/my\\040share cifs credentials=/x 0 0\n"
                     "nas:/srv /srv/nfs nfs4 defaults 0 0\n")
        try:
            entries = model.load_fstab(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual([(e.source, e.target, e.kind) for e in entries],
                         [("//nas/My Share", "/mnt/my share", "SMB"),
                          ("nas:/srv", "/srv/nfs", "NFS")])


if __name__ == "__main__":
    unittest.main()
