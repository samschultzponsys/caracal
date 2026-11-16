# DEVLOG

Working notes for the next session. Newest first.

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
- **Icon**: not chosen. The .desktop uses the `folder-remote` theme icon as a placeholder.
  Present options (see chat on kickoff) and then add `data/icons/hicolor/...` +
  the polkit `<icon_name>`.
- **Auto-update** (second pass): check GitHub Releases at most daily (cached), download
  RPM/DEB + SHA256SUMS, verify the hash, add a helper `install-update` command
  (dnf/apt install of a verified file in a root-owned temp dir), and show a dot on the
  version label plus newer notes in the changelog dialog.
- Maintainer email in `debian/control` / `debian/changelog` is a placeholder
  (`noreply@spazcat.com`). Confirm with Sam.
- Verify on real hosts: Fedora (python3-pyside6) and Ubuntu 24.04 (does noble ship
  `python3-pyside6.qtwidgets`? If not, 24.04 support needs another route).
- Ideas: Kerberos (sec=krb5 + cruid) option; "Browse network" (SMB discovery via
  avahi/wsdd); per-drive "show in file manager sidebar" (GTK bookmarks).
