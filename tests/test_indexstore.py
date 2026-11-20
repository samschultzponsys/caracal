# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from caracal import indexstore

FAKE_SECRET_TOOL = """#!/bin/sh
# stores one secret in $FAKE_KEYRING (stand-in for the desktop keyring)
case "$1" in
  store) cat > "$FAKE_KEYRING" ;;
  lookup) [ -s "$FAKE_KEYRING" ] && cat "$FAKE_KEYRING" || exit 1 ;;
  clear) rm -f "$FAKE_KEYRING" ;;
esac
"""


@unittest.skipUnless(indexstore.crypto_available(), "python3-cryptography not installed")
class IndexStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        tool = os.path.join(bindir, "secret-tool")
        with open(tool, "w") as fh:
            fh.write(FAKE_SECRET_TOOL)
        os.chmod(tool, 0o755)
        rt = os.path.join(self.tmp, "run")
        os.makedirs(rt, mode=0o700)
        self.env = mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": os.path.join(self.tmp, "data"),
            "XDG_CONFIG_HOME": os.path.join(self.tmp, "config"),
            "XDG_RUNTIME_DIR": rt,
            "FAKE_KEYRING": os.path.join(self.tmp, "keyring"),
            "PATH": bindir + os.pathsep + os.environ.get("PATH", "")})
        self.env.start()
        indexstore._state.update(mode=None, note="")

    def tearDown(self):
        self.env.stop()
        indexstore._state.update(mode=None, note="")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_plain_index(self):
        os.makedirs(indexstore.data_dir(), exist_ok=True)
        conn = sqlite3.connect(indexstore.plain_path())
        conn.execute("CREATE TABLE files(name TEXT)")
        conn.executemany("INSERT INTO files VALUES(?)",
                         [("Tax return 2025 SECRETNAME %d.pdf" % i,) for i in range(5000)])
        conn.commit()
        conn.close()

    def new_login(self):
        """Simulate logging out and in: RAM copy gone, state reset."""
        for f in os.listdir(indexstore.runtime_dir()):
            os.remove(os.path.join(indexstore.runtime_dir(), f))
        indexstore._state.update(mode=None, note="")

    def count(self):
        conn = sqlite3.connect(indexstore.active_path())
        try:
            return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            conn.close()

    def test_migrate_encrypt_and_reopen(self):
        self.make_plain_index()
        indexstore.prepare()
        self.assertEqual(indexstore.effective_mode(), "encrypted")
        self.assertFalse(os.path.exists(indexstore.plain_path()))          # plaintext gone
        self.assertTrue(indexstore.active_path().startswith(indexstore.runtime_dir()))
        self.assertEqual(self.count(), 5000)
        with open(indexstore.encrypted_path(), "rb") as fh:
            blob = fh.read()
        self.assertNotIn(b"SECRETNAME", blob)                               # junk on disk
        self.assertNotIn(b"SQLite format", blob)
        self.assertEqual(stat.S_IMODE(os.stat(indexstore.encrypted_path()).st_mode), 0o600)
        self.new_login()
        indexstore.prepare()
        self.assertEqual(self.count(), 5000)                               # decrypted again

    def test_tamper_truncate_wrong_key(self):
        self.make_plain_index()
        indexstore.prepare()
        key = indexstore.load_key()
        src, out = indexstore.encrypted_path(), os.path.join(self.tmp, "out.sqlite")
        data = bytearray(open(src, "rb").read())
        for bad in (data[:len(data) // 2],                                  # truncated
                    data[:200] + bytes([data[200] ^ 1]) + data[201:]):      # one bit flipped
            p = os.path.join(self.tmp, "bad.enc")
            open(p, "wb").write(bytes(bad))
            with self.assertRaises(indexstore.Damaged):
                indexstore.decrypt_file(p, out, key)
        with self.assertRaises(indexstore.Damaged):
            indexstore.decrypt_file(src, out, os.urandom(32))
        self.assertFalse(os.path.exists(out))

    def test_lost_key_rebuilds_instead_of_failing(self):
        self.make_plain_index()
        indexstore.prepare()
        os.remove(os.environ["FAKE_KEYRING"])           # e.g. keyring reset
        self.new_login()
        indexstore.prepare()
        self.assertEqual(indexstore.effective_mode(), "encrypted")       # new key, fresh index
        self.assertFalse(os.path.exists(indexstore.active_path()))       # to be rebuilt

    def test_no_keyring_means_memory_only_never_plaintext(self):
        self.make_plain_index()
        with mock.patch.object(indexstore, "keyring_available", lambda: False):
            indexstore.prepare()
        self.assertEqual(indexstore.effective_mode(), "memory")
        self.assertIn("keyring", indexstore.note())
        self.assertFalse(os.path.exists(indexstore.plain_path()))
        self.assertFalse(os.path.exists(indexstore.encrypted_path()))
        self.assertEqual(self.count(), 5000)

    def test_switch_modes(self):
        self.make_plain_index()
        indexstore.prepare()
        indexstore.switch("plain")
        self.assertTrue(os.path.exists(indexstore.plain_path()))
        self.assertFalse(os.path.exists(indexstore.encrypted_path()))
        self.assertIsNone(indexstore.load_key())                           # key removed
        self.assertEqual(self.count(), 5000)
        indexstore.switch("memory")
        self.assertFalse(os.path.exists(indexstore.plain_path()))
        self.assertEqual(self.count(), 5000)
        indexstore.switch("encrypted")
        self.assertTrue(os.path.exists(indexstore.encrypted_path()))


if __name__ == "__main__":
    unittest.main()
