# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from caracal import cloud, user_mounts as um


class FakeNextcloud(http.server.BaseHTTPRequestHandler):
    polls = 0

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/nc/status.php":
            self._json(200, {"installed": True, "productname": "Nextcloud",
                             "versionstring": "31.0.2"})
        else:
            self._json(404, {})

    def do_POST(self):
        base = "http://127.0.0.1:%d" % self.server.server_port
        if self.path == "/nc/index.php/login/v2":
            self._json(200, {"poll": {"token": "tok", "endpoint": base + "/nc/login/v2/poll"},
                             "login": base + "/nc/login/v2/flow/abc"})
        elif self.path == "/nc/login/v2/poll":
            FakeNextcloud.polls += 1
            if FakeNextcloud.polls < 3:
                self._json(404, {})
            else:
                self._json(200, {"server": base + "/nc", "loginName": "sam",
                                 "appPassword": "app-pass-123"})
        else:
            self._json(404, {})


class CloudTests(unittest.TestCase):
    def test_products_and_urls(self):
        self.assertEqual(cloud.product_from_status({"productname": "Nextcloud"}), "nextcloud")
        self.assertEqual(cloud.product_from_status({"productname": "FileRun"}), "filerun")
        self.assertEqual(cloud.product_from_status({"productname": "ownCloud"}), "owncloud")
        self.assertEqual(cloud.product_from_status({"productname": "Infinite Scale"}), "ocis")
        self.assertEqual(cloud.product_from_status({"product": "OpenCloud"}), "ocis")
        self.assertIsNone(cloud.product_from_status({"productname": "Apache"}))
        self.assertEqual(cloud.normalize_server("drive.spazcat.com/remote.php/dav/files/x/"),
                         "https://drive.spazcat.com")
        self.assertEqual(cloud.webdav_url("filerun", "https://drive.spazcat.com", "sam@x.com"),
                         "https://drive.spazcat.com/remote.php/dav/files/sam@x.com/")
        self.assertEqual(cloud.webdav_url("ocis", "https://oc.example.com/", "u"),
                         "https://oc.example.com/remote.php/webdav/")

    def test_detect_and_login_flow(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeNextcloud)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d/nc" % srv.server_port
            product, status = cloud.detect(base)
            self.assertEqual(product, "nextcloud")
            flow = cloud.login_flow_start(base)
            self.assertTrue(flow["login"].endswith("/flow/abc"))
            res = cloud.login_flow_poll(flow, interval=0.05)
            self.assertEqual(res, {"server": base, "user": "sam", "password": "app-pass-123"})
            with self.assertRaises(cloud.CloudError):
                cloud.login_flow_poll(dict(flow, endpoint=base + "/nope"), interval=0.05, timeout=0.3)
        finally:
            srv.shutdown()


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp + "/config",
                                                "XDG_CACHE_HOME": self.tmp + "/cache",
                                                "XDG_RUNTIME_DIR": self.tmp + "/run",
                                                "HOME": self.tmp + "/home"})
        self.env.start()
        os.makedirs(self.tmp + "/home")
        self.calls = []
        self.sys = mock.patch.object(um, "systemctl", self._systemctl)
        self.sys.start()

    def tearDown(self):
        self.sys.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _systemctl(self, *args, check=True):
        self.calls.append(args)
        import subprocess
        return subprocess.CompletedProcess(args, 0, "inactive\n" if args[0] == "is-active" else "", "")

    def cloud_spec(self, **kw):
        raw = {"type": "cloud", "name": "Spazcat Drive", "server": "https://drive.spazcat.com",
               "product": "filerun", "user": "sam", "mount_point": self.tmp + "/home/SpazcatDrive"}
        raw.update(kw)
        return um.validate(raw)

    def test_cloud_spec_flags_and_unit(self):
        spec = self.cloud_spec()
        self.assertEqual(spec["url"], "https://drive.spazcat.com/remote.php/dav/files/sam/")
        self.assertEqual((spec["vfs_full"], spec["cache_gb"], spec["cache_days"]), (True, 20, 30))
        conf = um.render_rclone(spec, "OBSCURED")
        self.assertIn("vendor = nextcloud", conf)
        with mock.patch.object(um, "rclone_supports_unix_rc", lambda: True):
            _t, cmd = um.exec_lines(spec)
            unit = um.render_unit(spec)
        for flag in ("--vfs-cache-mode full", "--vfs-cache-max-size 20G",
                     "--vfs-cache-max-age 720h", "--vfs-write-back 5s", "--dir-cache-time 30s",
                     "--rc-addr unix://"):
            self.assertIn(flag, cmd)
        self.assertIn(um.cache_dir(spec["id"]), cmd)
        self.assertIn("--wait-uploads " + spec["id"], unit)
        self.assertIn("StartLimitIntervalSec=0", unit)
        self.assertIn("TimeoutStopSec=150", unit)
        off = self.cloud_spec(vfs_full=False)
        self.assertIn("--vfs-cache-mode writes", um.exec_lines(off)[1])
        with self.assertRaises(um.LocationError):
            self.cloud_spec(cache_gb=0)
        self.assertEqual(um.render_rclone(um.validate({
            "type": "webdav", "name": "x", "url": "https://h/", "mount_point": self.tmp + "/home/x"}),
            "").count("vendor = other"), 1)

    def test_tcp_rc_keeps_password_off_the_command_line(self):
        spec = self.cloud_spec()
        with mock.patch.object(um, "rclone_supports_unix_rc", lambda: False):
            _t, cmd = um.exec_lines(spec)
        info = json.load(open(um.rc_info_path(spec["id"])))
        self.assertNotIn(info["password"], cmd)
        self.assertIn("--rc-htpasswd", cmd)
        ht = os.path.join(um.rclone_dir(), spec["id"] + ".htpasswd")
        self.assertEqual(oct(os.stat(ht).st_mode & 0o777), "0o600")
        self.assertTrue(open(ht).read().startswith("caracal:{SHA}"))

    def test_non_empty_mount_point_refused(self):
        mp = self.tmp + "/home/Busy"
        os.makedirs(mp)
        open(mp + "/keep.txt", "w").close()
        with mock.patch("shutil.which", lambda n: "/usr/bin/" + n), \
                mock.patch.object(um, "obscure", lambda p: "x"):
            with self.assertRaises(um.LocationError) as cm:
                um.save({"type": "cloud", "name": "x", "server": "https://h", "user": "u",
                         "mount_point": mp}, password="p")
        self.assertIn("isn't empty", str(cm.exception))
        self.assertTrue(os.path.exists(mp + "/keep.txt"))      # nothing touched

    def test_find_take_over_and_hand_back(self):
        home = os.environ["HOME"]
        rc = os.path.join(um.config_home(), "rclone")
        os.makedirs(rc)
        with open(os.path.join(rc, "rclone.conf"), "w") as fh:
            fh.write("[filerun]\ntype = webdav\nurl = https://drive.spazcat.com/remote.php/dav/files/sam/\n"
                     "vendor = nextcloud\nuser = sam\npass = OBSCUREDPASS\n")
        os.makedirs(um.unit_dir())
        with open(os.path.join(um.unit_dir(), "rclone-filerun.service"), "w") as fh:
            fh.write("[Service]\nType=notify\nExecStart=/usr/bin/rclone mount filerun: %h/SpazcatDrive "
                     "--vfs-cache-mode full --vfs-cache-max-size 20G --vfs-cache-max-age 720h "
                     "--vfs-write-back 5s --dir-cache-time 30s --cache-dir %h/.cache/rclone\n")
        found = um.find_rclone_mounts()
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["remote"], f["mount_point"], f["cache_gb"], f["cache_days"], f["vfs_full"]),
                         ("filerun", home + "/SpazcatDrive", 20, 30, True))
        self.assertEqual(um.nice_name(f["mount_point"]), "Spazcat Drive")
        with mock.patch("shutil.which", lambda n: "/usr/bin/" + n):
            loc = um.take_over(f)
        self.assertIn(("disable", "--now", "rclone-filerun.service"), self.calls)
        conf = open(um.rclone_conf(loc.id)).read()
        self.assertIn("[loc]", conf)
        self.assertIn("pass = OBSCUREDPASS", conf)
        self.assertEqual(loc.spec["imported_from"], "rclone-filerun.service")
        self.assertNotIn("OBSCUREDPASS", open(os.path.join(um.unit_dir(), loc.unit)).read())
        self.assertTrue(os.path.exists(os.path.join(um.unit_dir(), "rclone-filerun.service")))
        with mock.patch.object(um, "pending_uploads", lambda i: 0):
            um.hand_back(loc)
        self.assertIn(("enable", "--now", "rclone-filerun.service"), self.calls)
        self.assertFalse(os.path.exists(os.path.join(um.unit_dir(), loc.unit)))

    def test_free_up_space_refuses_with_pending_uploads(self):
        spec = self.cloud_spec()
        loc = um.Location(spec)
        os.makedirs(um.cache_dir(loc.id) + "/vfs")
        with mock.patch.object(um, "pending_uploads", lambda i: 2):
            with self.assertRaises(um.LocationError):
                um.free_up_space(loc)
        self.assertTrue(os.path.isdir(um.cache_dir(loc.id)))
        # a dirty file in rclone's metadata also blocks it
        meta = um.cache_dir(loc.id) + "/vfsMeta/loc"
        os.makedirs(meta)
        json.dump({"Dirty": True}, open(meta + "/a.txt", "w"))
        with mock.patch.object(um, "pending_uploads", lambda i: 0):
            with self.assertRaises(um.LocationError):
                um.free_up_space(loc)
        os.remove(meta + "/a.txt")
        with mock.patch.object(um, "pending_uploads", lambda i: 0):
            um.free_up_space(loc)
        self.assertFalse(os.path.exists(um.cache_dir(loc.id)))


if __name__ == "__main__":
    unittest.main()
