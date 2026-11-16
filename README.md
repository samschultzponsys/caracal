# CataMount

Map SMB and NFS network drives on Linux, like **Map network drive** on Windows.

CataMount creates persistent kernel mounts as systemd `.mount` / `.automount`
units. It never edits `/etc/fstab`, and it shows existing fstab network mounts
read-only.

- Paste an address in any common form: `\\server\share`, `smb://server/share`,
  `//server/share`, `server:/export`, `nfs://server/export`.
- **Reconnect at startup** (enabled unit) and/or **Connect on first access**
  (automount, so boot never waits on the network), with optional idle disconnect.
- SMB passwords are kept in root-only credential files, never in unit files.
- Connect, disconnect, edit, remove and open drives; status refreshes live.
- Light, dark or follow-system theme.

## Install

Download the latest package from
[Releases](https://github.com/samschultzponsys/catamount/releases).

Fedora:

```bash
sudo dnf install ./catamount-*.noarch.rpm
```

Debian 13+ / Ubuntu 24.04+:

```bash
sudo apt install ./catamount_*_all.deb
```

Only Fedora and Debian/Ubuntu are supported. There is no Flatpak: CataMount
needs root access to the host to write systemd units, run `mount`, and install
a polkit policy, and a sandbox blocks all of that.

## How it works

| What | Where |
|---|---|
| GUI | `/usr/bin/catamount` (Python 3 + PySide6) |
| Privileged helper | `/usr/libexec/catamount/catamount-helper`, run through `pkexec` |
| polkit action | `com.spazcat.CataMount.manage` (admin password, remembered briefly) |
| Mount units | `/etc/systemd/system/<escaped-path>.mount` and `.automount` |
| SMB credentials | `/etc/catamount/creds/<drive-id>.cred` (root, 0600) |
| Preferences | `~/.config/catamount/catamount.conf` |

Every unit CataMount writes starts with `# Managed by CataMount`, plus the
drive's id and settings as a JSON comment. The unit files are the only record:
there's no database. CataMount refuses to overwrite a unit it didn't create,
to use a mount folder that's already in `/etc/fstab`, or to mount over a
non-empty folder.

SMB mounts are owned by the user who created them (`uid`/`gid`), so that user
can read and write files normally.

Uninstalling the package leaves your drives mounted and working. Remove them in
the app first if you want them gone.

## Preferences

**File → Preferences**:

- Theme: follow system, light, or dark
- Default parent folder for new drives (default `/mnt`)
- Status refresh interval
- Defaults for new drives: connect on first access, reconnect at startup, idle disconnect
- Ask before removing a drive

## Troubleshooting

- **Could not connect**: open *Show Details* in the error dialog to see the
  kernel's mount error. `mount error(13)` means the user name or password is
  wrong. `(112)` / `(115)` means the server is unreachable.
- Logs: `journalctl -u 'mnt-*.mount'`
- Check what CataMount wrote: `grep -l "Managed by CataMount" /etc/systemd/system/*.mount`

## Development

```bash
python3 -m unittest -v
```

```bash
PYTHONPATH=. python3 -m catamount
```

`CATAMOUNT_UNIT_DIR`, `CATAMOUNT_FSTAB` and `CATAMOUNT_HELPER` override paths
for testing the GUI. Packages are built by GitHub Actions
(`.github/workflows/release.yml`) when a `v*` tag is pushed. A manual run does
a test build only.

### Releasing

1. Bump the version in `catamount/__init__.py`, `packaging/catamount.spec`,
   `debian/changelog`, `data/com.spazcat.CataMount.metainfo.xml`, and add a
   `CHANGELOG.md` section. CI checks that they all match.
2. Create a release with tag `vX.Y` on GitHub. The workflow attaches the RPM,
   DEB and `SHA256SUMS`.

## License

Apache License 2.0. Copyright 2026 Spazcat Enterprises. See `LICENSE`,
`NOTICE` and `THIRD_PARTY_LICENSES.md`.
