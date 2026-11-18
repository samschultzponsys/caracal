# Changelog

All notable user-facing changes to CataMount.

## [0.5] - 2026-11-19

**CataMount Explorer (preview)**: a Windows Explorer-style file manager. Turn it on in Preferences → Explorer → "Use CataMount as my file explorer".

- Windows 10 layout (File / Home / Share / View ribbon) or Windows 11 layout (command bar), compact rows, optional tabs.
- Details view grouped like Explorer ("Today", "Yesterday", "Earlier this week"… or by type, name, size), Windows-style sizes (1,234 KB) and dates; each folder remembers its sort and grouping (Downloads defaults to newest first, grouped by date).
- Navigation pane with Quick access (pinned folders), This PC (your folders, disks, USB drives, and network drives shown as "name (\\server)"), Network and Trash. The names are configurable (e.g. call Trash "Recycle Bin").
- Breadcrumb address bar, back/forward/recent/up, instant search box (uses the search index on indexed drives), preview pane, thumbnails, Open with, Properties, New folder/file and templates, copy/cut/paste with other file managers, drag and drop, Trash with undo (Ctrl+Z), and Explorer's keyboard shortcuts.
- Copying and moving show progress and ask Replace / Skip / Keep both on conflicts.
- Light or dark just for Explorer (View → Theme), independent of the desktop.
- Your own skin: drop icons, fonts and colors into the theme folder (Preferences → Appearance → Open theme folder).
- Can be made the default file manager, so folders and "Show in folder" open in it.

**New network location types**: SFTP/SCP (via sshfs) and FTP, FTPS and WebDAV (via rclone), mounted per user without an admin password, from Explorer's "Add network location".

## [0.4] - 2026-11-18

- Tray icon fix: it's now grey until a drive is connected and turns green when one is, in every tray icon style (the simple styles used to stay white/dark and only fade). Drives that connect on first access count as connected while they're ready. A red dot still means a drive failed.

## [0.3] - 2026-11-18

Instant search for network drives, like Everything on Windows.

- New **Search index** tab: choose which drives to index (file names, and optionally the text inside documents, PDFs, e-books and text files). The index lives on this computer; searching never touches the network, and results still show while a drive is offline.
- New **search window** (toolbar, tray menu, Ctrl+F, or the "Search network drives" launcher action you can bind to a hotkey): results as you type, sortable columns, open the file or its folder, copy paths.
- Everything-style filters: `ext:pdf`, `type:pic`, `size:>100mb`, `modified:week`, `folder:projects`, `drive:nas`, `content:"exact phrase"`, wildcards like `*.iso` or `report?.docx`, and paths like `projects/2026`.
- Results in KRunner / the Plasma launcher (KDE) and in GNOME's Activities search.
- "Search here (CataMount, instant)" in the right-click menu of Dolphin, Files (Nautilus), Nemo and Caja.
- The index stays current: only changed folders are re-read every 15 minutes, a full refresh runs daily, and changes you make from this computer show up within seconds. Indexing pauses on battery (optional).
- `catamount --search QUERY` prints matching paths for scripts.
- Preferences are now split into General, System tray and Search tabs.

## [0.2] - 2026-11-17

- The tray icon now shows whether you're connected: it's grey when no drive is connected and lights up green (glowing network) as soon as one is. A red dot still means a drive failed. You can turn this off in Preferences → System tray.

## [0.1] - 2026-11-16

First release.

- Map SMB (Windows/Samba) and NFS shares to a local folder, like Windows' "Map network drive".
- Paste addresses in any common form: `\\server\share`, `smb://server/share`, `server:/export`, `nfs://server/export`.
- Drives are persistent systemd mounts: reconnect at startup, or connect on first access (automount) with optional idle disconnect.
- SMB passwords are stored in root-only credential files, never in the unit files.
- Browse the network for SMB and NFS servers (including Windows PCs) and their shares, and test a connection before saving.
- SMB sign-in with a password, as a guest, or with a Kerberos ticket; optional SMB 3 encryption.
- Optional file manager sidebar entry for each drive (Files, Nemo, Thunar, Caja, Dolphin).
- Export drives to a file and import them on another computer (passwords are never exported).
- Free space column for connected drives, and a connected/failed summary in the status bar.
- Connect all / Disconnect all (one password prompt), from the Drive menu, the tray, or the command line.
- Duplicate a drive, change a saved SMB password (offered when a connect fails on a bad password), and view a drive's log.
- Copy an /etc/fstab network mount into a CataMount drive.
- Optional NFS soft mounts (apps don't freeze if the server goes away) and an SMB option to let every user on the computer write to the drive.
- Notifications when a drive fails, drops or reconnects, also on desktops without a tray.
- Show-password button in password fields; the window remembers its size and column widths.
- Command line control: `--list`, `--connect`, `--disconnect`, `--connect-all`, `--disconnect-all`.
- Connect, disconnect, edit, remove and open drives from the main window; status refreshes automatically.
- Read-only view of existing network mounts in `/etc/fstab` (CataMount never edits it).
- System tray: close to tray, tray menu to connect/disconnect/open drives, notifications when a drive fails or drops, optional start at login in the tray, and a choice of color, white or dark tray icon.
- Only one copy runs at a time; opening it again brings the window forward.
- Warns at startup if a needed system tool (cifs-utils, nfs-utils, pkexec) is missing and shows how to install it.
- New app icon: a cat-eared network folder.
- Automatic updates from GitHub Releases (checked at most once a day). A green dot on the version number means an update is ready; the changelog window shows its notes and installs it after checking its SHA-256 checksum.
- Light, dark and follow-system themes.
- RPM (Fedora) and DEB (Debian 13+, Ubuntu 26.04+) packages. All dependencies are installed automatically.
