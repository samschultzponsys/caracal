# DEVLOG

Working notes for the next session. Newest first.

## 2026-11-16 — Tray, dependencies, icon (still v0.1, unreleased)

### Done
- **Tray** (`mainwindow.py`): QSystemTrayIcon with a menu rebuilt on `aboutToShow`
  (show/hide, per-drive open/connect/disconnect, map drive, quit). Left click toggles the
  window. Close hides to tray when a tray exists and `close_to_tray` is on; otherwise it
  quits. `--tray` starts hidden. It waits up to 15 s for a tray host at login, then
  falls back to a minimized window. The tooltip shows the connected count. The icon gets
  a red badge if any drive failed. Notifications fire on a change to failed, or when
  connected → disconnected happens outside our own action. If a tray appears later,
  refresh() picks it up.
- **Single instance** (`app.py`): QLocalServer `catamount-<uid>`. A second launch sends
  `show`/`tray` and exits.
- **Autostart** (`autostart.py`): writes `~/.config/autostart/com.spazcat.CataMount.desktop`.
- **Deps**: added `python3-pyside6.qtnetwork` and `qt6-svg-plugins` (Debian) and `qt6-qtsvg`
  (Fedora). Recommends nss-mdns. GNOME appindicator: Fedora rich Recommends
  `(… if gnome-shell)`, Debian Suggests (a plain Recommends would pull gnome-shell onto
  KDE). `deps.py` checks mount.cifs, mount.nfs, pkexec and systemctl at startup and shows
  the install command. The helper refuses `apply` with connect if the mount helper is
  missing. Preferences can turn on the GNOME AppIndicator extension (`gnome-extensions enable`).
- **Ubuntu**: only 26.04 LTS (resolute) packages PySide6; 24.04/24.10/25.04/25.10 do not.
  Supported = Debian 13+, Ubuntu 26.04+. CLAUDE.md Project Notes updated.
- **Icon**: chosen by Sam = riff "h" with ear style "4" (wide ears at the top
  corners, dark folder, glowing 3-node network, green tile). Files:
  `data/icons/hicolor/scalable/apps/com.spazcat.CataMount.svg`, `.../symbolic/...-symbolic.svg`,
  `data/icons/tray/tray-{light,dark}.svg` (installed to /usr/share/catamount/icons/tray).
  There are no PNGs (no generated files in git); scalable SVG is enough for GNOME/KDE.

### Not verified on real hardware yet
- The tray was only exercised offscreen with availability faked (menu, hide-on-close,
  badge, notifications). Needs a check on KDE and on GNOME with the extension.
- The RPM still hasn't been built (first CI run will be the test).

## 2026-11-16 — Kickoff scaffold (v0.1)

### State
- **Helper** (`helper/catamount-helper`): stdlib-only, runs via pkexec, uses a JSON
  request on stdin and replies with JSON on stdout. Commands: `apply`, `connect`,
  `disconnect`, `remove`. It validates everything and renders units itself; the GUI
  never sends raw unit text.
- **GUI** (`catamount/`): `model.py` (Qt-free: unit parsing, address parsing,
  systemctl status, fstab), `helper_client.py` (async QProcess → pkexec),
  `dialogs.py` (map/edit drive, preferences, changelog), `mainwindow.py`, `theme.py`.
- **Packaging**: `Makefile install` is shared by `packaging/catamount.spec` and `debian/`.
  Packages: `catamount-X.Y-1.noarch.rpm`, `catamount_X.Y_all.deb`. The DEB build was
  verified locally (Ubuntu container). The RPM build has not run yet (no rpmbuild here);
  the first CI run will check it.
- **CI**: one job, `release.yml`. Runs on `v*` tag or manual dispatch only, builds both
  packages in Fedora 42 / Debian trixie containers via `docker run`, then uploads to the
  GitHub Release. No artifacts are stored.
- **Tests**: `python3 -m unittest` (19 tests: escaping matches `systemd-escape`,
  validation, rendering, GUI round-trip parse, and apply/edit/move/remove against a temp dir).

### Decisions
- Maintainer contact for packaging: support@spazcat.com (confirmed).
- Unit = source of truth. `.mount` header: `# Managed by CataMount`, `# CataMount-Id: <uuid>`,
  `# CataMount-Spec: <json>`. `.automount` has the first two lines.
- When automount is on, the `.automount` is enabled (WantedBy=remote-fs.target) and the
  `.mount` has no [Install]. Otherwise the `.mount` is enabled.
- Connect / Map starts the `.mount` right away (even with automount) so bad passwords
  or an unreachable server show up immediately. On failure the helper adds the last
  journal lines to the error.
- Disconnect stops the units but leaves them enabled; they come back at boot.
  "Reconnect at startup" = enabled.
- Editing stops the drive first (the change needs a remount). If the mount point
  changed, the old units are removed. If the drive is busy, the edit fails with
  "close files".
- Safety: the helper refuses unmanaged units, fstab mount points, non-empty folders,
  system trees (/etc, /usr/*, /proc, /run, /tmp, ...), bare /home/<user>, and extra
  options that set credentials or x-systemd.*.
- The app is installed to `/usr/share/catamount/catamount` (not site-packages) to avoid
  Python build tooling in the packages.
- Theme: Qt ≥ 6.8 uses `styleHints().setColorScheme`. Older Qt falls back to Fusion
  plus a palette.

### Open / next
- **Auto-update** (second pass): check GitHub Releases at most daily (cached), download
  RPM/DEB + SHA256SUMS, verify the hash, add a helper `install-update` command
  (dnf/apt install of a verified file in a root-owned temp dir), and show a dot on the
  version label plus newer notes in the changelog dialog.
- Verify on real hosts: Fedora 42+ and Debian 13 / Ubuntu 26.04.
- Ideas: Kerberos (sec=krb5 + cruid) option; "Browse network" (SMB discovery via
  avahi/wsdd); per-drive "show in file manager sidebar" (GTK bookmarks).
