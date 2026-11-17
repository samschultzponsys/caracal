# DEVLOG

Working notes for the next session. Newest first.

## 2026-11-16 — v0.1 release

- Published 2026-11-17: https://github.com/samschultzponsys/catamount/releases/tag/v0.1
  (run 44152213376). The first run failed: Debian's dpkg-buildpackage needs
  `build-essential`; now installed in build-deb.sh. Checked that the release assets
  verify against SHA256SUMS.
- Next: Sam installs and tests on real hardware; then fixes go into 0.2.

- Added NFS soft mount option (`nfs.soft` → soft,timeo=150,retrans=3) and SMB `all_users`
  (file_mode/dir_mode 0777).
- Built the RPM locally with Ubuntu's rpmbuild (`--nodeps`, `_metainfodir` defined) to check
  the spec before CI: file list, Requires and Recommends look right.
- Ran the app from the extracted DEB layout (launcher, --list, GUI, icons, changelog): OK.
- Releasing: this cloud session's git proxy drops tag pushes (branch pushes work). With
  Sam's OK, the Release workflow got a `publish` input for manual runs: it creates
  `v<__version__>` on the run's commit via `gh release create --target`. Claude starts it
  through the GitHub Actions API. Tag pushes still work too.

## 2026-11-16 — Everyday features batch

Sam: as many features as possible in the first build before release.

### Done
- Helper: `connect`/`disconnect` accept `ids` (batch, per-drive `failed` map, error only if
  all fail). New `log` command (journalctl -u for the drive's units, 200 lines) for users
  outside systemd-journal/wheel.
- Space column: `disk_usage` (statvfs) runs in a thread, only for drives in state
  "connected" (statvfs on an automount point would trigger it), with an in-flight guard
  so a hung NFS server can't pile up threads.
- Change password dialog (re-applies the same spec with a new password). The failure
  dialog offers "Update password…" when the error looks like an auth failure (error(13),
  Permission denied, LOGON_FAILURE).
- Duplicate (template with `-2`… mount point), Show log (user journalctl first, then an
  admin button that calls the helper), Connect/Disconnect all (menu + tray),
  `notify()` (tray, else notify-send), "connected again" notification, summary label,
  window geometry and column state saved in QSettings.
- fstab tab: rows are selectable; "Copy as CataMount drive…" uses
  `model.fstab_to_template` (maps vers/ro/username/domain/guest/sec=krb5/seal, keeps
  unknown options as extra options, suggests a new mount point).
- `cli.py`: --list/--connect/--disconnect/--connect-all/--disconnect-all (pkexec helper,
  no Qt). Also `widgets.py` (password reveal action).
- Bugs found by smoke tests and fixed: the tray only appeared ~5 s after a normal start
  (now created right after show); LogDialog used the invalid `textCursor().End`.
- Tests: 40.

## 2026-11-16 — Browse, test, Kerberos/seal, sidebar, export/import (still v0.1, unreleased)

Sam: "push all releases per CLAUDE.md", but keep building features before cutting a build.

### Done
- `discovery.py` (no Qt): avahi-browse -ptr parsing (_smb._tcp, _nfs._tcp), smbclient -g -L
  share listing, `smbclient -c ls` connection test, showmount -e. Credentials go through a
  0600 temp auth file in XDG_RUNTIME_DIR (never argv). NT_STATUS codes become plain messages.
  All of this is optional: features grey out with a hint when the tool is missing.
- `browse.py`: BrowseDialog (servers on the left, shares on the right, sign-in row when an
  anonymous listing is denied). It uses the IP if the .local name doesn't resolve
  (no nss-mdns).
- `background.py`: run_async(fn, callback) runs work in a thread and calls back on the UI thread.
- Map dialog: Browse… (network), Choose… (local folder), "Sign in with" password/guest/Kerberos,
  "Encrypt traffic" (seal), "Show in the file manager sidebar", Test connection.
- Helper: `smb.kerberos` → `sec=krb5,cruid=<uid>[,username=]` (no creds file; strict username
  charset because it goes into the options), `smb.seal` → `seal`, top-level `bookmark` stored
  in the spec.
- `bookmarks.py`: sync() on every refresh, but only diffs against the record in QSettings
  `bookmarks`, so entries the user deletes by hand aren't re-added. GTK bookmarks file
  always; KDE xbel only if it exists (text insert/remove by href, not an XML rewrite).
- Export/import: File → Export (JSON, format "catamount-drives", no id/uid/gid/passwords).
  Import skips server+share pairs that already exist, then opens the Map dialog per drive
  ("Import drive n of N") and chains the helper calls.
- Deps: Recommends avahi-tools + samba-client (Fedora) and avahi-utils + smbclient (Debian).
- WS-Discovery (`discover_wsd`, stdlib UDP multicast probe to 239.255.255.250:3702) finds
  Windows 10/11 PCs and Samba hosts running wsdd, which don't use mDNS. It keeps only
  ProbeMatches whose Types contain "Computer" (drops printers etc.), names them by reverse
  DNS or else shows the IP, and merges them with the avahi results by address.
  Not yet verified against a real Windows PC.
- Tests: 36.

## 2026-11-16 — Auto-update (still v0.1, unreleased)

### Done
- `updater.py` (no Qt): version compare, release parsing, picking assets
  (`catamount-X.Y-1.noarch.rpm` / `catamount_X.Y_all.deb` + `SHA256SUMS`), cache in
  `~/.cache/catamount/update.json` (checked_at, etag, release, error, backoff_until),
  24 h interval, rate-limit backoff from Retry-After / X-RateLimit-Reset.
  `install_kind()` uses `rpm -q` / `dpkg-query`; None means a source checkout, which
  only gets a download link.
- `update_client.py`: async QNetworkAccessManager. Sends ETag/If-None-Match, so a 304
  doesn't use rate limit. A 404 (no releases) counts as up to date. Other failures retry
  after 1 h. Downloads go to `~/.cache/catamount/updates/` and are verified against
  SHA256SUMS; a mismatch is deleted.
- Helper `install-update {path, sha256}`: checks the name pattern, reads the user file once
  (O_NOFOLLOW, 100 MB cap), checks sha256, writes a root-owned copy in
  /var/cache/catamount/update, checks package Name/Version (rpm -qp / dpkg-deb -f) and that
  it's newer than installed, then `dnf install -y` (retries with --nogpgcheck only on a
  GPG/signature error, since release packages are unsigned) or `apt-get install -y`, then
  removes the copy.
- UI: green dot on the version label, changelog dialog puts the new notes on top with
  Install / Open download page, Help → Check for updates, tray menu entry, a one-time tray
  notification per version, and a restart offer (releases the single-instance socket
  before spawning).
- Repo is public, so there's no PAT field (CLAUDE.md asks for one only for private repos).
- Tested: unit tests (27), live API check (404 → up to date), download+verify against a
  local HTTP server (good accepted, tampered rejected), helper install path with the real
  DEB on Ubuntu 24.04 (fails cleanly on missing PySide6 deps, as expected).

### Fixed
- Tray and symbolic icon ears extended to the outer edge of the box stroke (they sat
  ~1 px inside). The app icon was already flush.

### Next
- Cut v0.1 to exercise the RPM build and the first real release; then v0.2 to test an
  end-to-end update on Fedora and Debian.

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
