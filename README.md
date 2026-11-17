# CataMount

Map SMB and NFS network drives on Linux, like **Map network drive** on Windows.

CataMount creates persistent kernel mounts as systemd `.mount` / `.automount`
units. It never edits `/etc/fstab`, and it shows existing fstab network mounts
read-only.

- **Browse** the network for SMB/NFS servers (mDNS for Linux/NAS boxes,
  WS-Discovery for Windows PCs) and pick a share, or
  **test the connection** before saving.
- Paste an address in any common form: `\\server\share`, `smb://server/share`,
  `//server/share`, `server:/export`, `nfs://server/export`.
- **Reconnect at startup** (enabled unit) and/or **Connect on first access**
  (automount, so boot never waits on the network), with optional idle disconnect.
- SMB sign-in with a password, as a guest, or with your Kerberos ticket (domain
  sign-in, no stored password); optional SMB 3 encryption; optionally let every
  user on the computer write to the drive.
- NFS soft mounts (optional) so apps don't freeze when a server disappears.
- SMB passwords are kept in root-only credential files, never in unit files.
- Optional file manager sidebar entry per drive (Files/Nautilus, Nemo, Thunar,
  Caja, Dolphin).
- Export your drives to a file and import them on another computer (passwords
  are never exported).
- Free space for connected drives, Connect all / Disconnect all, duplicate a
  drive, change a saved password (offered automatically when a connect fails on a
  bad password), and a per-drive log viewer for troubleshooting.
- Copy an existing `/etc/fstab` network mount into a CataMount drive (the fstab
  line itself is left for you to remove).
- Desktop notifications when a drive fails, drops, or comes back, even without a tray.
- Command line: `catamount --list`, `--connect`, `--disconnect`, `--connect-all`,
  `--disconnect-all`.
- Connect, disconnect, edit, remove and open drives; status refreshes live.
- Lives in the system tray: closing the window keeps it running, the tray menu
  connects/disconnects/opens drives, and you get a notification if a drive fails.
  Optional start at login (straight into the tray).
- Light, dark or follow-system theme.
- Updates itself from GitHub Releases: a green dot on the version number means
  an update is ready, and one click downloads, verifies and installs it.

## Install

Download the latest package from
[Releases](https://github.com/samschultzponsys/catamount/releases).

Fedora:

```bash
sudo dnf install ./catamount-*.noarch.rpm
```

Debian 13+ / Ubuntu 26.04+:

```bash
sudo apt install ./catamount_*_all.deb
```

Ubuntu 24.04 and 25.x are not supported because they don't package PySide6.
Only Fedora and Debian/Ubuntu are supported. There is no Flatpak: CataMount
needs root access to the host to write systemd units, run `mount`, and install
a polkit policy, and a sandbox blocks all of that.

## Dependencies

The package manager installs everything below automatically when you install
the RPM or DEB. CataMount also checks at startup and tells you exactly what to
install if something was removed.

| Needed for | Fedora | Debian / Ubuntu | Kind |
|---|---|---|---|
| Python runtime | `python3` | `python3` | required |
| GUI toolkit (Qt 6 via PySide6) | `python3-pyside6` | `python3-pyside6.qtcore`, `.qtgui`, `.qtwidgets`, `.qtnetwork` | required |
| SVG icons | `qt6-qtsvg` | `qt6-svg-plugins` | required |
| Admin prompt for changes | `polkit` (pkexec) | `pkexec` | required |
| Mount units | `systemd` | `systemd` | required |
| SMB / Windows shares | `cifs-utils` | `cifs-utils` | required |
| NFS exports | `nfs-utils` | `nfs-common` | required |
| `.local` server names (mDNS) | `nss-mdns` | `libnss-mdns` | recommended |
| Find servers on the network | `avahi-tools` | `avahi-utils` | recommended |
| List SMB shares, test SMB connections | `samba-client` | `smbclient` | recommended |
| Tray icon on GNOME | `gnome-shell-extension-appindicator` | `gnome-shell-extension-appindicator` | recommended on Fedora GNOME, suggested on Debian |

You also need a polkit authentication agent. Every major desktop includes one.

### System tray on GNOME

KDE, Cinnamon, XFCE, Budgie and Ubuntu's GNOME have a tray built in. Plain
GNOME (Fedora Workstation, Debian GNOME) needs the AppIndicator extension. On
Fedora it's installed with CataMount but has to be turned on once. Use
**Preferences → Turn on the tray extension**, or the Extensions app, then log
out and back in. On Debian:

```bash
sudo apt install gnome-shell-extension-appindicator
```

Without a tray, CataMount works normally and closing the window quits it.

## Command line

```bash
catamount --list
```

```bash
catamount --connect "Media on nas"
```

```bash
catamount --disconnect-all
```

A drive can be named by its name, its local folder, or its id. Connect and
disconnect ask for your admin password through polkit, just like the window does.

## Kerberos

Choose **Sign in with: Kerberos ticket** for domain shares. The mount uses
the ticket of the user who created the drive (`sec=krb5,cruid=<uid>`), so get a
ticket first (`kinit`, or sign in to the domain). No password is stored. With
automount, the drive connects whenever you have a valid ticket.

## How it works

| What | Where |
|---|---|
| GUI | `/usr/bin/catamount` (Python 3 + PySide6) |
| Privileged helper | `/usr/libexec/catamount/catamount-helper`, run through `pkexec` |
| polkit action | `com.spazcat.CataMount.manage` (admin password, remembered briefly) |
| Mount units | `/etc/systemd/system/<escaped-path>.mount` and `.automount` |
| SMB credentials | `/etc/catamount/creds/<drive-id>.cred` (root, 0600) |
| Preferences | `~/.config/catamount/catamount.conf` |
| Sidebar bookmarks | `~/.config/gtk-3.0/bookmarks` and, if it exists, `~/.local/share/user-places.xbel` |
| Start at login | `~/.config/autostart/com.spazcat.CataMount.desktop` (runs `catamount --tray`) |

Every unit CataMount writes starts with `# Managed by CataMount`, plus the
drive's id and settings as a JSON comment. The unit files are the only record:
there's no database. CataMount refuses to overwrite a unit it didn't create,
to use a mount folder that's already in `/etc/fstab`, or to mount over a
non-empty folder.

SMB mounts are owned by the user who created them (`uid`/`gid`), so that user
can read and write files normally.

Uninstalling the package leaves your drives mounted and working. Remove them in
the app first if you want them gone.

## Updates

CataMount checks [GitHub Releases](https://github.com/samschultzponsys/catamount/releases)
at most once a day. You can turn this off in Preferences, and **Help → Check
for updates** checks on demand. Results are cached in
`~/.cache/catamount/update.json`. When an update exists:

- the version number in the status bar shows a green dot, and the tray menu offers it;
- clicking the version number opens the changelog with the new version's notes
  on top and an **Install** button;
- CataMount downloads the RPM or DEB and `SHA256SUMS`, and checks the hash. The helper
  then copies the package to a root-owned folder, re-checks the hash, confirms it's a newer
  CataMount package, and installs it with `dnf` or `apt`. You enter your admin
  password once. CataMount then offers to restart.

If you run CataMount from source, the button opens the download page instead.
If GitHub's API rate limit is hit, CataMount waits until the limit resets and
says so in the changelog window.

## Preferences

**File → Preferences**:

- Theme: follow system, light, or dark
- Default parent folder for new drives (default `/mnt`)
- Status refresh interval
- Defaults for new drives: connect on first access, reconnect at startup, idle disconnect
- Ask before removing a drive
- Check for updates automatically (once a day)
- System tray: show tray icon, close to tray, failure/disconnect notifications,
  start in the tray at login, tray icon style (color, white or dark)

Only one copy runs at a time. Launching CataMount again brings up the existing
window.

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
2. Push a `vX.Y` tag, or run the **Release** workflow manually with
   **publish** ticked (it creates the tag from the app version). The workflow
   builds the RPM and DEB, creates the GitHub Release with the changelog section
   as notes, and attaches the packages and `SHA256SUMS`. A manual run without
   **publish** is a test build only.

## License

Apache License 2.0. Copyright 2026 Spazcat Enterprises. See `LICENSE`,
`NOTICE` and `THIRD_PARTY_LICENSES.md`.
