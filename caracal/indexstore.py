# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Where the search index lives, and keeping it unreadable on disk.

Storage modes:
  encrypted  The working database lives in RAM ($XDG_RUNTIME_DIR, private to the user and
             wiped at logout). On disk there is only an AES-256-GCM encrypted snapshot whose
             key is kept in the desktop keyring (Secret Service: GNOME Keyring, KWallet).
  memory     RAM only; nothing on disk. Rebuilt from the shares after each login.
  plain      The old behavior: an unencrypted SQLite file in ~/.local/share/caracal.

The index is a cache of what's on the shares, so every failure path (no keyring, lost key,
damaged snapshot) just means "rebuild", never lost data. If encryption can't be used this
session, Caracal falls back to memory-only rather than writing plaintext to disk.
No Qt imports."""

import base64
import fcntl
import os
import shutil
import sqlite3
import struct
import subprocess
import time

MODES = ("encrypted", "memory", "plain")
DEFAULT_MODE = "encrypted"
MAGIC = b"CARACALIDX\x01"
CHUNK = 4 * 1024 * 1024
KEY_ATTRS = ["application", "com.spazcat.Caracal", "purpose", "search-index-key"]

_state = {"mode": None, "note": ""}      # effective mode this session, and why if degraded


# --------------------------------------------------------------------------
# paths and settings
# --------------------------------------------------------------------------

def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "caracal")


def config_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "caracal", "index-storage")


def plain_path():
    return os.path.join(data_dir(), "index.sqlite")


def encrypted_path():
    return os.path.join(data_dir(), "index.sqlite.enc")


def runtime_dir():
    rt = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(rt, "caracal") if os.path.isdir(rt) else None


def runtime_path():
    d = runtime_dir()
    return os.path.join(d, "index.sqlite") if d else None


def chosen_mode():
    try:
        with open(config_file(), encoding="utf-8") as fh:
            mode = fh.read().strip()
        return mode if mode in MODES else DEFAULT_MODE
    except OSError:
        return DEFAULT_MODE


def set_chosen_mode(mode):
    if mode not in MODES:
        raise ValueError(mode)
    os.makedirs(os.path.dirname(config_file()), mode=0o700, exist_ok=True)
    with open(config_file(), "w", encoding="utf-8") as fh:
        fh.write(mode + "\n")


def effective_mode():
    if _state["mode"] is None:
        prepare()
    return _state["mode"]


def note():
    """Why the effective mode differs from the chosen one ("" if it doesn't)."""
    return _state["note"]


def active_path():
    """The database file everyone opens (crawler, Explorer, search window, KRunner)."""
    mode = effective_mode()
    if mode == "plain" or runtime_path() is None:
        return plain_path()
    return runtime_path()


# --------------------------------------------------------------------------
# key (desktop keyring via secret-tool)
# --------------------------------------------------------------------------

def crypto_available():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except ImportError:
        return False


def keyring_available():
    return shutil.which("secret-tool") is not None


def _secret_tool(args, data=None, timeout=60):
    proc = subprocess.run(["secret-tool"] + args, input=data, capture_output=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout


def load_key(create=False):
    """The 32-byte index key from the keyring; a new one is made and stored if asked.
    Returns None if the keyring can't be used (locked, absent, refused)."""
    if not keyring_available():
        return None
    try:
        code, out = _secret_tool(["lookup"] + KEY_ATTRS)
        if code == 0 and out.strip():
            key = base64.b64decode(out.strip())
            if len(key) == 32:
                return key
        if not create:
            return None
        key = os.urandom(32)
        code, _ = _secret_tool(["store", "--label=Caracal search index key"] + KEY_ATTRS,
                               data=base64.b64encode(key))
        if code != 0:
            return None
        # read it back: proves the keyring really kept it before we encrypt with it
        code, out = _secret_tool(["lookup"] + KEY_ATTRS)
        return key if code == 0 and base64.b64decode(out.strip() or b"") == key else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def forget_key():
    if keyring_available():
        try:
            _secret_tool(["clear"] + KEY_ATTRS)
        except (OSError, subprocess.SubprocessError):
            pass


# --------------------------------------------------------------------------
# file format: header | chunks of AES-256-GCM, each authenticated with its position and
# whether it's the last one, so a snapshot can't be truncated, reordered or spliced.
# --------------------------------------------------------------------------

def _aad(header, index, final):
    return header + struct.pack(">IB", index, 1 if final else 0)


def encrypt_file(src, dst, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    gcm = AESGCM(key)
    prefix = os.urandom(8)
    header = MAGIC + prefix + struct.pack(">I", CHUNK)
    tmp = dst + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(src, "rb") as fin, os.fdopen(fd, "wb") as fout:
        fout.write(header)
        index = 0
        block = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            final = not nxt
            ct = gcm.encrypt(prefix + struct.pack(">I", index), block,
                             _aad(header, index, final))
            fout.write(struct.pack(">I", len(ct)) + ct)
            if final:
                break
            block, index = nxt, index + 1
        fout.flush()
        os.fsync(fout.fileno())
    os.replace(tmp, dst)


class Damaged(Exception):
    pass


def decrypt_file(src, dst, key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    gcm = AESGCM(key)
    tmp = dst + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with open(src, "rb") as fin, os.fdopen(fd, "wb") as fout:
            header = fin.read(len(MAGIC) + 12)
            if len(header) != len(MAGIC) + 12 or not header.startswith(MAGIC):
                raise Damaged("not a Caracal index snapshot")
            prefix = header[len(MAGIC):len(MAGIC) + 8]
            index, finished = 0, False
            while True:
                raw = fin.read(4)
                if not raw:
                    break
                if finished or len(raw) != 4:
                    raise Damaged("trailing data")
                (size,) = struct.unpack(">I", raw)
                if size > CHUNK + 16:
                    raise Damaged("bad chunk size")
                ct = fin.read(size)
                nonce = prefix + struct.pack(">I", index)
                for final in (False, True):
                    try:
                        block = gcm.decrypt(nonce, ct, _aad(header, index, final))
                        finished = final
                        break
                    except InvalidTag:
                        block = None
                if block is None:
                    raise Damaged("wrong key or damaged snapshot")
                fout.write(block)
                index += 1
            if not finished:
                raise Damaged("snapshot is incomplete")
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _wipe(path):
    """Overwrite then delete (best effort: SSDs and copy-on-write filesystems such as btrfs
    may keep old blocks; full-disk encryption covers that)."""
    for p in (path, path + "-wal", path + "-shm", path + "-journal"):
        try:
            size = os.path.getsize(p)
            with open(p, "r+b") as fh:
                remaining = size
                while remaining > 0:
                    n = min(remaining, 1024 * 1024)
                    fh.write(b"\0" * n)
                    remaining -= n
                fh.flush()
                os.fsync(fh.fileno())
            os.remove(p)
        except OSError:
            pass


def _snapshot(src, dst):
    """A consistent copy of a live SQLite database (WAL included)."""
    s = sqlite3.connect("file:%s?mode=ro" % src, uri=True, timeout=30)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

class _Lock:
    """Only one process (Caracal, the KRunner search service) sets up the index at once."""

    def __init__(self, directory):
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self.fd = os.open(os.path.join(directory, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)

    def __enter__(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_a):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def prepare():
    """Make the index available at active_path() for this session (call before opening it).
    Decrypts the snapshot into RAM, migrates an old plaintext index, or falls back."""
    chosen = chosen_mode()
    rt = runtime_path()
    if chosen != "plain" and rt is None:
        _state.update(mode="plain", note="No private memory folder ($XDG_RUNTIME_DIR) on "
                      "this system, so the index can't be kept out of the disk.")
        return
    if chosen == "plain":
        _state.update(mode="plain", note="")
        if rt and os.path.exists(rt) and not os.path.exists(plain_path()):
            os.makedirs(data_dir(), mode=0o700, exist_ok=True)
            _snapshot(rt, plain_path())
        return
    os.makedirs(os.path.dirname(rt), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(rt), 0o700)
    with _Lock(os.path.dirname(rt)):
        if chosen == "encrypted" and not crypto_available():
            _state.update(mode="memory", note="Encryption needs the python3-cryptography "
                          "package; until it's installed the index is kept in memory only.")
        elif chosen == "encrypted":
            # No key but a working keyring (e.g. it was reset): a new key is stored and the
            # old snapshot, now unreadable, is dropped below. A locked/absent keyring can't
            # store one either, so then we fall back to memory and keep the snapshot.
            key = load_key(create=True)
            if key is None:
                _state.update(mode="memory", note="The keyring couldn't be used (locked, not "
                              "running, or secret-tool missing), so this session keeps the "
                              "index in memory only. Nothing unencrypted is written to disk.")
            else:
                _state.update(mode="encrypted", note="")
                if not os.path.exists(rt) and os.path.exists(encrypted_path()):
                    try:
                        decrypt_file(encrypted_path(), rt, key)
                    except (Damaged, OSError):
                        # wrong key (e.g. keyring reset) or damage: start over, it's a cache
                        os.remove(encrypted_path())
        else:
            _state.update(mode="memory", note="")
        # an old plaintext index: bring it into memory, then remove it from the disk
        if os.path.exists(plain_path()):
            if not os.path.exists(rt):
                _snapshot(plain_path(), rt)
            _wipe(plain_path())
        if _state["mode"] == "memory" and chosen == "memory" and os.path.exists(encrypted_path()):
            os.remove(encrypted_path())
    if _state["mode"] == "encrypted" and os.path.exists(rt) and not os.path.exists(encrypted_path()):
        save()


def save():
    """Write the encrypted snapshot (encrypted mode only). Safe to call from a thread."""
    if _state["mode"] != "encrypted":
        return False
    rt = runtime_path()
    if not rt or not os.path.exists(rt):
        return False
    key = load_key()
    if key is None:
        return False
    tmp = os.path.join(os.path.dirname(rt), "snapshot.sqlite")
    try:
        _snapshot(rt, tmp)
        os.makedirs(data_dir(), mode=0o700, exist_ok=True)
        encrypt_file(tmp, encrypted_path(), key)
        return True
    except (OSError, sqlite3.Error):
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def switch(mode):
    """Change the storage mode now (call with nothing writing to the index)."""
    old = effective_mode()
    rt = runtime_path()
    set_chosen_mode(mode)
    if mode == "plain":
        if old != "plain" and rt and os.path.exists(rt):
            os.makedirs(data_dir(), mode=0o700, exist_ok=True)
            _snapshot(rt, plain_path())
            for p in (rt, rt + "-wal", rt + "-shm"):
                try:
                    os.remove(p)
                except OSError:
                    pass
        try:
            os.remove(encrypted_path())
        except OSError:
            pass
        forget_key()
        _state.update(mode="plain", note="")
        return
    _state["mode"] = None
    prepare()
    if mode == "memory":
        forget_key()


def describe():
    """One line for the UI about where the index is and how it's protected."""
    mode = effective_mode()
    text = {"encrypted": "Encrypted on disk (key in your keyring); working copy in memory",
            "memory": "In memory only — nothing on disk; rebuilt after each login",
            "plain": "Not encrypted (stored in %s)" % plain_path()}[mode]
    when = ""
    if mode == "encrypted" and os.path.exists(encrypted_path()):
        when = " · last saved %s" % time.strftime("%H:%M", time.localtime(
            os.path.getmtime(encrypted_path())))
    return text + when
