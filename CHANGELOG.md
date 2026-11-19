# Changelog

All notable user-facing changes to Caracal (called CataMount before 0.7).

## [1.1] - 2026-11-21

- **Import KDE / Windows themes:** Preferences → Appearance → **Import theme…** takes a downloaded theme (.zip, .tar.gz, .tar.xz…), a KDE color scheme (.colors) or a folder, and picks out icon themes, color schemes, fonts and slot-named icons. The new **Icon theme** setting picks any installed or imported icon theme for Caracal only, or "Same as the desktop" (handy if you apply a Windows theme to all of KDE).
- **No tray icon in explorer mode** (Preferences → Explorer → "Keep the tray icon in explorer mode" brings it back). Caracal keeps running in the background for indexing, drive notifications and "Show in folder"; **File → Exit Caracal** stops it. Closing the drive-management window no longer quits Caracal in explorer mode.
- **Type to jump** works like Explorer: typing the start of a name selects it (group headers are skipped and collapsed groups open), and pressing the same letter again moves to the next item starting with it. Backspace goes back a folder.

## [1.0] - 2026-11-20

- Updates are reachable from Explorer: the version number sits at the bottom left of the Explorer window (click it for what's new; a green dot means an update is ready), and **File → Check for updates…** / **File → What's new** are in the ribbon (in the ··· menu with the Windows 11 layout). The tray menu always has **Check for updates…**.
- Update progress and results show in the Explorer window instead of the hidden drives window.
- Preferences → Appearance has a **Reload theme** button to pick up icons and fonts you just dropped into the theme folder.

## [0.9] - 2026-11-20

- **New folder at the top left:** a Quick Access Toolbar (Properties and New folder by default) in front of the ribbon. Use its ▾ to add Undo, Delete, Rename, Refresh or Open in terminal.
- **Navigation pane you can tailor:** right-click any item → *Hide from navigation pane*, or right-click anywhere → *Show in navigation pane* to hide or bring back Quick access, each folder, disks, network drives, Network and Trash. Sections are spaced apart like Explorer's.
- **Mount from the left pane:** its right-click menu has *Map network drive (SMB / NFS)…*, *Add network location (SFTP, FTP, WebDAV)…*, *Browse the network…*, and Connect / Disconnect / Edit / Remove on network drives.
- **Index any folder for fast search:** right-click a folder, drive or mount (left pane, file list, or empty space in a folder) → *Index this folder for fast search*. Searches from the top-right box inside indexed folders then come from the index (milliseconds); everywhere else they search the disk as before. Re-index, include file contents, or stop indexing from the same menu.
- **Preview pane on/off** button in the bottom right, next to the view buttons.
- The status bar no longer keeps showing the last search's result count after you clear the search.

## [0.8] - 2026-11-20

Explorer looks much closer to Windows Explorer (Windows 10 layout):

- Explorer-sized text (9 pt) and rows, 16 px icons in the navigation pane, slimmer ribbon tabs and address bar.
- The address bar shows the folder trail the way Explorer does ("Downloads › …" with the folder's own icon), with the history dropdown and refresh inside the box; the search box has its magnifier on the right.
- Thin chevrons in the navigation pane, the sort arrow centered at the top of the column header, items indented under group headers, Windows-sized columns.
- The preview pane just says "Select a file to preview." when nothing is selected.
- Square, Explorer-style scrollbars and a slimmer status bar.
- The navigation pane keeps the current folder highlighted.
- Text size is adjustable in Preferences → Appearance (set it to 0 for your desktop's size).

The package is now called **caracal** and replaces the old **catamount** package automatically when you update.

## [0.7] - 2026-11-19

**CataMount is now Caracal.** Same app, new name, now that it's a full file explorer as well as a network drive tool.

- The app, menu entries, commands (`caracal`; `catamount` still works), icons and settings folders are now called Caracal.
- Your settings, search index, Explorer theme folder, start-at-login entry and default-file-manager choice move over automatically the first time you start it.
- Drives, network locations and saved passwords you set up before keep working unchanged; they switch to the new names the next time you edit them.

## [0.6] - 2026-11-19

Explorer polish toward v1:

- When a folder changes on disk (a download finishing, a file saved), the view keeps your selection, scroll position and collapsed groups instead of resetting.
- Clear messages for empty folders, folders you don't have permission to open, and folders that no longer exist.
- Mouse back/forward buttons work; middle-click a folder to open it in a new background tab; Ctrl + mouse wheel changes the icon size.
- The address bar autocompletes folder paths as you type.
- The navigation pane follows the folder you're in (expands down to it).
- Right-click the column header to show or hide Date modified, Type and Size.
- Quick access now also shows Frequent folders.
- Trash: Restore (and Restore all items); hovering an item shows where it came from and when it was deleted.
- Right-click → Extract all… for ZIP, TAR (gz/bz2/xz) and, with p7zip installed, 7z/RAR; Compress to ZIP file.

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
