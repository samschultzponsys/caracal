# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest import mock

from catamount import user_mounts as um


class UserMountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = os.path.join(self.tmp.name, "home")
        os.makedirs(home)
        for p in (mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(home, ".config"),
                                               "HOME": home}),
                  mock.patch.object(um.shutil, "which", lambda t: "/usr/bin/" + t),
                  mock.patch.object(um, "obscure", lambda pw: "OBSCURED"),
                  mock.patch.object(um, "systemctl", self.fake_systemctl)):
            p.start()
            self.addCleanup(p.stop)
        self.home = home
        self.calls = []

    def fake_systemctl(self, *args, check=True):
        self.calls.append(args)
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_sftp_key_and_password(self):
        spec = {"type": "sftp", "name": "Web server", "host": "web.example.com", "user": "sam",
                "path": "/var/www", "mount_point": self.home + "/Network/Web server",
                "auth": "key", "key_file": self.home + "/.ssh/id_ed25519"}
        loc = um.save(spec)
        unit = open(os.path.join(um.unit_dir(), loc.unit)).read()
        self.assertTrue(unit.startswith(um.MANAGED_HEADER))
        self.assertIn('ExecStart=/usr/bin/sshfs -f -o reconnect,', unit)
        self.assertIn("BatchMode=yes", unit)
        self.assertIn("IdentityFile=", unit)
        self.assertIn('"%s/Network/Web server"' % self.home, unit)
        self.assertIn(("enable", loc.unit), self.calls)
        self.assertIn(("start", loc.unit), self.calls)
        self.assertEqual(um.load_locations()[0].spec, loc.spec)
        self.assertEqual(loc.remote, "sftp://sam@web.example.com/var/www")

        spec.update(id=loc.id, auth="password")
        um.save(spec, password="s3cr$t%x")
        unit = open(os.path.join(um.unit_dir(), loc.unit)).read()
        self.assertIn("ExecStart=/bin/sh -c ", unit)
        self.assertIn("password_stdin", unit)
        self.assertIn("< ", unit)
        secret = um.secret_path(loc.id)
        self.assertEqual(open(secret).read(), "s3cr$t%x")
        self.assertEqual(oct(os.stat(secret).st_mode & 0o777), "0o600")
        um.save(spec)  # keeps stored password
        um.remove(loc)
        self.assertEqual(um.load_locations(), [])
        self.assertFalse(os.path.exists(secret))

    def test_ftp_and_webdav(self):
        loc = um.save({"type": "ftps", "name": "Old FTP", "host": "ftp.example.com",
                       "user": "bob", "mount_point": self.home + "/Network/ftp"}, password="pw")
        conf = open(um.rclone_conf(loc.id)).read()
        self.assertIn("type = ftp", conf)
        self.assertIn("explicit_tls = true", conf)
        self.assertIn("pass = OBSCURED", conf)
        self.assertNotIn("pw\n", conf)
        unit = open(os.path.join(um.unit_dir(), loc.unit)).read()
        self.assertIn("Type=notify", unit)
        self.assertIn("rclone mount loc:/ ", unit)
        dav = um.save({"type": "webdav", "name": "Cloud", "url": "https://c.example/dav/",
                       "user": "me", "auth": "anonymous",
                       "mount_point": self.home + "/Network/cloud"})
        self.assertIn("url = https://c.example/dav/", open(um.rclone_conf(dav.id)).read())

    def test_validation(self):
        base = {"type": "sftp", "name": "x", "host": "h", "mount_point": self.home + "/n"}
        for bad in ({"type": "gopher"}, {"host": "bad host"}, {"mount_point": "/etc/x"},
                    {"mount_point": "rel"}, {"auth": "anonymous"}, {"port": 70000},
                    {"name": ""}, {"user": "a b"}):
            with self.assertRaises(um.LocationError):
                um.validate(dict(base, **bad))
        self.assertEqual(um.validate(base)["port"], 22)
        self.assertEqual(um._sd_quote("a b%c$d"), '"a b%%c$$d"')


if __name__ == "__main__":
    unittest.main()
