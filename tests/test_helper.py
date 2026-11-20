# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("caracal_helper",
                                              os.path.join(ROOT, "helper", "caracal-helper"))
spec_ = importlib.util.spec_from_loader("caracal_helper", loader)
helper = importlib.util.module_from_spec(spec_)
loader.exec_module(helper)

# Never touch the real /var/lib/caracal or /etc/caracal from tests.
_STATE_TMP = tempfile.mkdtemp(prefix="caracal-helper-test-")
helper.STATE_DIR = _STATE_TMP
helper.DESIRED_FILE = os.path.join(_STATE_TMP, "desired.json")
helper.WATCH_STATE_FILE = os.path.join(_STATE_TMP, "watchdog.json")
helper.WATCH_CONF_FILE = os.path.join(_STATE_TMP, "watchdog-conf.json")

from caracal import model  # noqa: E402

REAL_REQUIRE = helper.require_mount_helper

DRIVE_ID = "0b6f7c1e-3a2d-4c55-9a77-1f2e3d4c5b6a"


def smb_spec(**over):
    spec = {"id": DRIVE_ID, "name": "Media on nas", "type": "smb", "server": "nas.local",
            "share": "media", "mount_point": "/mnt/media", "automount": True,
            "idle_timeout": 600, "start_at_boot": True, "read_only": False,
            "extra_options": "", "smb": {"username": "sam", "domain": "", "guest": False,
                                         "version": "3.1.1", "uid": 1000, "gid": 1000}}
    spec.update(over)
    return spec


class EscapeTests(unittest.TestCase):
    CASES = ["/mnt/media", "/mnt/my share", "/srv/.hidden", "/mnt/a-b/c_d",
             "/mnt/ünï", "/mnt//double/", "/"]

    def test_matches_systemd_escape(self):
        if not shutil.which("systemd-escape"):
            self.skipTest("systemd-escape not available")
        for path in self.CASES:
            want = subprocess.run(["systemd-escape", "--path", path], capture_output=True,
                                  text=True).stdout.strip()
            self.assertEqual(helper.escape_path(path), want, path)
            self.assertEqual(model.escape_path(path), want, path)

    def test_known_values(self):
        self.assertEqual(helper.escape_path("/mnt/my share"), "mnt-my\\x20share")
        self.assertEqual(helper.escape_path("/srv/.hidden"), "srv-.hidden")
        self.assertEqual(helper.escape_path("/.x"), "\\x2ex")
        self.assertEqual(helper.escape_path("/mnt/a-b"), "mnt-a\\x2db")


class ValidationTests(unittest.TestCase):
    def test_valid_smb(self):
        spec = helper.validate_spec(smb_spec())
        self.assertEqual(spec["share"], "media")
        self.assertEqual(spec["smb"]["uid"], 1000)

    def bad(self, **over):
        with self.assertRaises(helper.HelperError):
            helper.validate_spec(smb_spec(**over))

    def test_rejects(self):
        self.bad(id="not-a-uuid")
        self.bad(name="")
        self.bad(name="a\nb")
        self.bad(type="ftp")
        self.bad(server="bad host")
        self.bad(server="-oops")
        self.bad(share="../etc")
        self.bad(share="a\\b")
        self.bad(mount_point="relative")
        self.bad(mount_point="/")
        self.bad(mount_point="/etc/x")
        self.bad(mount_point="/home/sam")
        self.bad(mount_point="/mnt/../etc")
        self.bad(mount_point="/run/media/x")
        self.bad(extra_options="credentials=/root/x")
        self.bad(extra_options="ro\nExecStart=/bin/sh")
        self.bad(extra_options="x-systemd.requires=foo")
        self.bad(idle_timeout=-1)
        self.bad(automount="yes")
        self.bad(smb={"username": "", "guest": False})

    def test_nfs(self):
        spec = helper.validate_spec({"id": DRIVE_ID, "name": "x", "type": "nfs",
                                     "server": "10.0.0.5", "share": "/srv/data",
                                     "mount_point": "/home/sam/data", "nfs": {"version": "4.2"}})
        self.assertEqual(helper.mount_source(spec), "10.0.0.5:/srv/data")
        self.assertIn("vers=4.2", helper.mount_options(spec))
        with self.assertRaises(helper.HelperError):
            helper.validate_spec({"id": DRIVE_ID, "name": "x", "type": "nfs", "server": "h",
                                  "share": "srv", "mount_point": "/mnt/x"})


class RenderTests(unittest.TestCase):
    def test_render_automount(self):
        spec = helper.validate_spec(smb_spec(name="100% media"))
        units = helper.render_units(spec)
        self.assertEqual(sorted(units), ["mnt-media.automount", "mnt-media.mount"])
        mount = units["mnt-media.mount"]
        self.assertTrue(mount.startswith(helper.MANAGED_HEADER + "\n" + helper.ID_PREFIX))
        self.assertIn("What=//nas.local/media\n", mount)
        self.assertIn("Type=cifs\n", mount)
        self.assertIn("credentials=/etc/caracal/creds/%s.cred" % DRIVE_ID, mount)
        self.assertIn("Description=Caracal: 100%% media", mount)
        self.assertNotIn("[Install]", mount)
        auto = units["mnt-media.automount"]
        self.assertIn("TimeoutIdleSec=600", auto)
        self.assertIn("WantedBy=remote-fs.target", auto)

    def test_render_plain_guest(self):
        spec = helper.validate_spec(smb_spec(automount=False, read_only=True,
                                             smb={"guest": True, "username": "", "uid": 1000}))
        units = helper.render_units(spec)
        self.assertEqual(list(units), ["mnt-media.mount"])
        mount = units["mnt-media.mount"]
        self.assertIn("guest", mount)
        self.assertNotIn("credentials=", mount)
        self.assertIn(",ro", mount)
        self.assertIn("WantedBy=remote-fs.target", mount)

    def test_render_kerberos_seal(self):
        spec = helper.validate_spec(smb_spec(smb={"username": "sam@CORP.EXAMPLE",
                                                  "kerberos": True, "seal": True, "uid": 1000}))
        mount = helper.render_units(spec)["mnt-media.mount"]
        self.assertIn("sec=krb5,cruid=1000,username=sam@CORP.EXAMPLE", mount)
        self.assertIn(",seal", mount)
        self.assertNotIn("credentials=", mount)
        for bad in ({"guest": True, "kerberos": True}, {"kerberos": True, "username": "a,b"}):
            with self.assertRaises(helper.HelperError):
                helper.validate_spec(smb_spec(smb=dict(bad, uid=1000)))

    def test_render_soft_and_all_users(self):
        nfs = helper.validate_spec({"id": DRIVE_ID, "name": "x", "type": "nfs", "server": "h",
                                    "share": "/d", "mount_point": "/mnt/d",
                                    "nfs": {"soft": True}})
        self.assertIn("soft,timeo=150,retrans=3", helper.mount_options(nfs))
        smb = helper.validate_spec(smb_spec(smb={"username": "s", "all_users": True, "uid": 1}))
        self.assertIn("file_mode=0777,dir_mode=0777", helper.mount_options(smb))

    def test_gui_parses_rendered_units(self):
        spec = helper.validate_spec(smb_spec())
        with tempfile.TemporaryDirectory() as d:
            for name, content in helper.render_units(spec).items():
                with open(os.path.join(d, name), "w") as fh:
                    fh.write(content)
            with open(os.path.join(d, "other.mount"), "w") as fh:
                fh.write("[Mount]\nWhat=/dev/sda1\n")
            drives = model.load_drives(d)
        self.assertEqual(len(drives), 1)
        drv = drives[0]
        self.assertEqual(drv.spec, spec)
        self.assertEqual(drv.automount_unit, "mnt-media.automount")
        self.assertEqual(drv.remote, "\\\\nas.local\\media")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.units = os.path.join(self.tmp, "units")
        os.makedirs(self.units)
        self.creds = os.path.join(self.tmp, "etc", "caracal", "creds")
        self.fstab = os.path.join(self.tmp, "fstab")
        with open(self.fstab, "w") as fh:
            fh.write("//old/share /mnt/fstabbed cifs defaults 0 0\n")
        self.calls = []
        patches = [mock.patch.object(helper, "UNIT_DIR", self.units),
                   mock.patch.object(helper, "CRED_DIR", self.creds),
                   mock.patch.object(helper, "FSTAB", self.fstab),
                   mock.patch.object(helper, "run", self.fake_run),
                   mock.patch.object(helper, "require_mount_helper", lambda kind: None),
                   # temp dirs live under /tmp, which real mounts may not use
                   mock.patch.object(helper, "DENY_TREES",
                                     tuple(t for t in helper.DENY_TREES if t != "/tmp"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp)

    def fake_run(self, *args, check=True):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def spec(self, **over):
        return smb_spec(mount_point=os.path.join(self.tmp, "mnt", "media"), **over)

    def test_apply_writes_units_and_creds(self):
        res = helper.cmd_apply({"spec": self.spec(), "password": "s3cret", "connect": True})
        self.assertEqual(len(res["units"]), 2)
        cred = os.path.join(self.creds, DRIVE_ID + ".cred")
        self.assertEqual(os.stat(cred).st_mode & 0o777, 0o600)
        with open(cred) as fh:
            self.assertEqual(fh.read(), "username=sam\npassword=s3cret\n")
        cmds = [c[1:3] for c in self.calls if c[0] == "systemctl"]
        self.assertIn(("daemon-reload",), [c[:1] for c in cmds])
        self.assertTrue(any(c[0] == "enable" and c[1].endswith(".automount") for c in cmds))

        # Edit without password keeps the old one; username change is applied.
        sp = self.spec()
        sp["smb"] = dict(sp["smb"], username="other")
        helper.cmd_apply({"spec": sp, "connect": False})
        with open(cred) as fh:
            self.assertEqual(fh.read(), "username=other\npassword=s3cret\n")

        # Move to a new folder: old units are removed.
        old = set(os.listdir(self.units))
        helper.cmd_apply({"spec": smb_spec(mount_point=os.path.join(self.tmp, "mnt", "new")),
                          "connect": False})
        new = set(os.listdir(self.units))
        self.assertFalse(old & new)
        self.assertEqual(len(new), 2)

        helper.cmd_remove({"id": DRIVE_ID})
        self.assertEqual(os.listdir(self.units), [])
        self.assertFalse(os.path.exists(cred))

    def test_new_smb_requires_password(self):
        with self.assertRaises(helper.HelperError):
            helper.cmd_apply({"spec": self.spec(), "connect": False})

    def test_refuses_unmanaged_unit(self):
        sp = self.spec()
        base = helper.escape_path(sp["mount_point"])
        with open(os.path.join(self.units, base + ".mount"), "w") as fh:
            fh.write("[Mount]\n")
        with self.assertRaises(helper.HelperError):
            helper.cmd_apply({"spec": sp, "password": "x", "connect": False})

    def test_refuses_fstab_and_nonempty(self):
        with mock.patch.object(helper, "fstab_mount_points", return_value={"/mnt/fstabbed"}):
            with self.assertRaises(helper.HelperError):
                helper.cmd_apply({"spec": smb_spec(mount_point="/mnt/fstabbed"),
                                  "password": "x", "connect": False})
        sp = self.spec()
        os.makedirs(sp["mount_point"])
        open(os.path.join(sp["mount_point"], "file"), "w").close()
        with self.assertRaises(helper.HelperError):
            helper.cmd_apply({"spec": sp, "password": "x", "connect": False})

    def test_batch_connect_reports_per_drive(self):
        helper.cmd_apply({"spec": self.spec(), "password": "x", "connect": False})
        res = helper.cmd_disconnect({"ids": [DRIVE_ID, "11111111-2222-3333-4444-555555555555"]})
        self.assertEqual(list(res["failed"]), ["11111111-2222-3333-4444-555555555555"])
        with self.assertRaises(helper.HelperError):
            helper.cmd_connect({"ids": ["11111111-2222-3333-4444-555555555555"]})
        self.assertIn("log", helper.cmd_log({"id": DRIVE_ID}))

    def test_legacy_catamount_units_and_creds(self):
        # A drive written by CataMount 0.6 (old header lines, old creds folder).
        sp = helper.validate_spec(self.spec())
        legacy_creds = os.path.join(self.tmp, "etc", "catamount", "creds")
        os.makedirs(legacy_creds)
        with open(os.path.join(legacy_creds, DRIVE_ID + ".cred"), "w") as fh:
            fh.write("username=sam\npassword=old-secret\n")
        for name, text in helper.render_units(sp).items():
            text = text.replace("# Managed by Caracal", "# Managed by CataMount") \
                .replace("# Caracal-Id: ", "# CataMount-Id: ").replace("# Caracal-Spec: ", "# CataMount-Spec: ")
            with open(os.path.join(self.units, name), "w") as fh:
                fh.write(text)
        with mock.patch.object(helper, "LEGACY_CRED_DIR", legacy_creds):
            self.assertEqual(sorted(helper.units_for(DRIVE_ID)), sorted(helper.render_units(sp)))
            drives = model.load_drives(self.units)
            self.assertEqual(drives[0].id, DRIVE_ID)
            # Re-saving without a password keeps the old one and moves it to the new folder.
            helper.cmd_apply({"spec": self.spec(), "connect": False})
            with open(os.path.join(self.creds, DRIVE_ID + ".cred")) as fh:
                self.assertIn("password=old-secret", fh.read())
            self.assertFalse(os.path.exists(os.path.join(legacy_creds, DRIVE_ID + ".cred")))
            with open(os.path.join(self.units, os.listdir(self.units)[0])) as fh:
                self.assertTrue(fh.readline().startswith("# Managed by Caracal"))

    def test_missing_mount_helper(self):
        with mock.patch.object(helper.os, "access", return_value=False):
            with self.assertRaises(helper.HelperError) as ctx:
                REAL_REQUIRE("smb")
        self.assertIn("cifs-utils", str(ctx.exception))

    def test_unknown_drive(self):
        with self.assertRaises(helper.HelperError):
            helper.cmd_remove({"id": DRIVE_ID})
        with self.assertRaises(helper.HelperError):
            helper.cmd_connect({"id": "../../etc"})


class InstallUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.calls = []
        self.pkg_info = "caracal 0.2"
        self.installed = "0.1"
        for p in (mock.patch.object(helper, "UPDATE_DIR", os.path.join(self.tmp, "cache")),
                  mock.patch.object(helper, "run", self.fake_run)):
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, *args, check=True):
        self.calls.append(args)
        out = ""
        if args[:2] == ("rpm", "-qp"):
            out = self.pkg_info
        elif args[:2] == ("rpm", "-q"):
            out = self.installed
        return subprocess.CompletedProcess(args, 0, out, "")

    def make(self, name, data=b"package"):
        import hashlib
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path, hashlib.sha256(data).hexdigest()

    def test_installs_verified_copy(self):
        path, sha = self.make("catamount-0.2-1.noarch.rpm")   # transition-era name works too
        self.assertEqual(helper.cmd_install_update({"path": path, "sha256": sha}),
                         {"version": "0.2"})
        dnf = [c for c in self.calls if c[0] == "dnf"]
        self.assertEqual(len(dnf), 1)
        self.assertTrue(dnf[0][-1].startswith(helper.UPDATE_DIR))  # our copy, not the user's
        self.assertEqual(os.listdir(helper.UPDATE_DIR), [])  # cleaned up

    def test_rejections(self):
        path, sha = self.make("caracal-0.2-1.noarch.rpm")
        for req in ({"path": path, "sha256": "0" * 64},
                    {"path": path, "sha256": "nothex"},
                    {"path": self.make("evil.rpm")[0], "sha256": sha},
                    {"path": "relative.rpm", "sha256": sha}):
            with self.assertRaises(helper.HelperError):
                helper.cmd_install_update(req)
        self.pkg_info = "otherpkg 0.2"
        with self.assertRaises(helper.HelperError):
            helper.cmd_install_update({"path": path, "sha256": sha})
        self.pkg_info, self.installed = "caracal 0.2", "0.2"
        with self.assertRaises(helper.HelperError):  # not newer
            helper.cmd_install_update({"path": path, "sha256": sha})
        self.assertFalse(any(c[0] == "dnf" for c in self.calls))


if __name__ == "__main__":
    unittest.main()


class UserFolderTests(unittest.TestCase):
    def test_rejects_anything_but_one_level_under_mnt_or_media(self):
        for bad in ("/etc/x", "/mnt", "/mnt/a/b", "/mnt/../etc", "/mnt/.hidden", "/media/a\nb",
                    "/home/u/x", "mnt/x", None, "/mnt/name "):
            with self.assertRaises(helper.HelperError, msg=repr(bad)):
                helper.cmd_user_folder({"path": bad})

    @unittest.skipUnless(os.geteuid() == 0, "needs root to chown")
    def test_creates_folder_owned_by_caller(self):
        name = "/mnt/caracal-test-%d" % os.getpid()
        try:
            with mock.patch.dict(os.environ, {"PKEXEC_UID": "65534"}):
                self.assertEqual(helper.cmd_user_folder({"path": name})["path"], name)
                self.assertEqual(os.stat(name).st_uid, 65534)
                helper.cmd_user_folder({"path": name})          # again: fine, already theirs
        finally:
            shutil.rmtree(name, ignore_errors=True)


class _WatchdogBase(unittest.TestCase):
    ID = "11111111-1111-4111-8111-111111111111"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mp = os.path.join(self.tmp, "mnt", "media")
        spec = {"id": self.ID, "name": "Media", "type": "smb", "server": "nas", "share": "media",
                "mount_point": self.mp, "automount": False}
        self.unit = "mnt-media.mount"
        self.calls = []
        self.mounted = set()
        self.up = {"reachable": True, "start_ok": True, "healthy": True}
        self.unit_states = {self.unit: ("inactive", "enabled")}
        p = [mock.patch.object(helper, "STATE_DIR", self.tmp),
             mock.patch.object(helper, "DESIRED_FILE", os.path.join(self.tmp, "desired.json")),
             mock.patch.object(helper, "WATCH_STATE_FILE", os.path.join(self.tmp, "watch.json")),
             mock.patch.object(helper, "WATCH_CONF_FILE", os.path.join(self.tmp, "conf.json")),
             mock.patch.object(helper, "drive_specs", lambda: {self.ID: (spec, [self.unit])}),
             mock.patch.object(helper, "mounted_points", lambda: set(self.mounted)),
             mock.patch.object(helper, "reachable", lambda s, timeout=3: self.up["reachable"]),
             mock.patch.object(helper, "healthy", lambda mp, timeout=10: self.up["healthy"]),
             mock.patch.object(helper, "unit_state", lambda n: self.unit_states.get(n, ("", ""))),
             mock.patch.object(helper, "systemctl", self._systemctl),
             mock.patch.object(helper, "run", self._run),
             mock.patch.object(helper, "start_unit", self._start),
             mock.patch.object(helper, "set_immutable", lambda p, on: self.calls.append(("lock", on)) or True),
             mock.patch.object(helper, "validate_mount_point", lambda p: p)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _systemctl(self, *args, check=True):
        self.calls.append(("systemctl",) + args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def _run(self, *args, check=True):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def _start(self, unit):
        self.calls.append(("start", unit))
        if not self.up["start_ok"]:
            raise helper.HelperError("Could not connect (mnt-media.mount).\n\nmount error(113)")

    def state(self):
        return helper._load_json(helper.WATCH_STATE_FILE, {})[self.ID]

class WatchdogTests(_WatchdogBase):
    def test_unreachable_then_back(self):
        self.up["reachable"] = False
        helper.cmd_watchdog({})
        st = self.state()
        self.assertEqual(st["state"], "unreachable")
        self.assertIn(("lock", True), self.calls)                 # folder locked
        self.assertNotIn(("start", self.unit), self.calls)        # no hanging mount attempt
        self.assertAlmostEqual(st["next_try"] - st["last_try"], 300, delta=1)
        # a minute later: not due yet, nothing tried
        self.calls.clear()
        self.up["reachable"] = True
        helper.cmd_watchdog({})
        self.assertNotIn(("start", self.unit), self.calls)
        # after the interval: server answers, mount comes back
        data = helper._load_json(helper.WATCH_STATE_FILE, {})
        data[self.ID]["last_try"] -= 301
        helper._save_json(helper.WATCH_STATE_FILE, data)
        helper.cmd_watchdog({})
        self.assertIn(("start", self.unit), self.calls)
        self.assertEqual(self.state()["state"], "ok")

    def test_failed_start_cleans_up(self):
        self.up["start_ok"] = False
        helper.cmd_watchdog({})
        st = self.state()
        self.assertEqual(st["state"], "retrying")
        self.assertTrue(st["error"].startswith("Could not connect"))   # first line kept
        self.assertIn(("systemctl", "stop", self.unit), self.calls)

    def test_dead_share_is_unmounted(self):
        self.mounted.add(self.mp)
        self.unit_states[self.unit] = ("active", "enabled")
        self.up["healthy"] = False
        helper.cmd_watchdog({})
        self.assertIn(("umount", "-l", "-f", self.mp), self.calls)
        self.assertEqual(self.state()["state"], "retrying")

    def test_disconnected_drive_left_alone(self):
        helper.set_desired(self.ID, "disconnected")
        helper.cmd_watchdog({})
        self.assertEqual(self.state()["state"], "off")
        self.assertNotIn(("start", self.unit), self.calls)

    def test_retry_zero_never_retries(self):
        helper._save_json(helper.WATCH_CONF_FILE, {"retry_minutes": 0, "on_fail": "lock",
                                                   "recover_dead": True})
        helper.cmd_watchdog({})
        self.assertNotIn(("start", self.unit), self.calls)
        self.assertEqual(self.state()["next_try"], 0)

    def test_set_watchdog_validates(self):
        with self.assertRaises(helper.HelperError):
            helper.cmd_set_watchdog({"retry_minutes": -1, "on_fail": "lock", "recover_dead": True})
        with self.assertRaises(helper.HelperError):
            helper.cmd_set_watchdog({"retry_minutes": 5, "on_fail": "rm -rf", "recover_dead": True})


class ImmutableTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, "needs root")
    def test_lock_empty_folder(self):
        d = tempfile.mkdtemp(dir="/var/tmp")
        try:
            with mock.patch.object(helper, "validate_mount_point", lambda p: p):
                if not helper.set_immutable(d, True):
                    self.skipTest("filesystem doesn't support the immutable flag")
                with self.assertRaises(OSError):
                    open(os.path.join(d, "x"), "w")
                self.assertTrue(helper.set_immutable(d, False))
                open(os.path.join(d, "x"), "w").close()
                self.assertFalse(helper.set_immutable(d, True))     # not empty: left alone
        finally:
            with mock.patch.object(helper, "validate_mount_point", lambda p: p):
                helper.set_immutable(d, False)
            shutil.rmtree(d, ignore_errors=True)


class IdleAutomountTests(_WatchdogBase):
    def setUp(self):
        super().setUp()
        self.auto = "mnt-media.automount"
        spec = helper.drive_specs()[self.ID][0]
        self._p = mock.patch.object(helper, "drive_specs",
                                    lambda: {self.ID: (spec, [self.auto, self.unit])})
        self._p.start()
        self.addCleanup(self._p.stop)
        self.unit_states = {self.auto: ("active", "enabled"), self.unit: ("inactive", "")}

    def test_idle_trap_removed_while_server_is_down(self):
        helper.cmd_watchdog({})
        self.assertEqual(self.state()["state"], "ready")          # server up: trap stays
        self.assertNotIn(("systemctl", "stop", self.auto), self.calls)
        self.up["reachable"] = False
        helper.cmd_watchdog({})
        self.assertEqual(self.state()["state"], "unreachable")
        self.assertIn(("systemctl", "stop", self.auto), self.calls)
        self.assertIn(("lock", True), self.calls)
