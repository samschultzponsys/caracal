# Changelog

All notable user-facing changes to Caracal (called CataMount before 0.7).

## [2.7] - 2026-11-25

- Fixed sorting search results: **Size** sorted as text ("1.1 GB" before "10.4 MB"); it now sorts by bytes, **Date modified** by time, and **Name** case-insensitively with folders first. Results that arrive while a search is still running keep the chosen order. Sizes are right-aligned.

## [2.6] - 2026-11-25

- Explorer's search box now searches when you **press Enter** (the box says "(Enter)"), so slow network folders aren't searched for every letter you type. **Preferences → Explorer → Search as I type** brings back live results. Clearing the box always returns to the folder.

## [2.5] - 2026-11-25

- **Files on demand** (like Windows' Files On-Demand) for cloud drives, WebDAV and FTP: the whole folder tree is listed without downloading anything; any program that reads a file — including browser and app upload dialogs — gets the real file, downloaded on first read (no placeholder files); opened files stay cached (20 GB / 30 days by default, least recently used removed first, still listed and re-downloaded when opened); saves upload in the background. On by default for every rclone-based location (existing ones switch over the next time they connect); a per-location switch turns it off.
- **Cloud drive** in Add network location: Nextcloud, FileRun, ownCloud (classic) and OpenCloud / oCIS. **Detect** reads the server's status.php; **Sign in with browser** (Nextcloud, FileRun) creates an app password for Caracal; ownCloud/OpenCloud use an app password / app token. A display name (e.g. "Spazcat Drive") is used in Explorer, the tray and other file managers' sidebars; the folder defaults to `~/SpazcatDrive`.
- Right-click a location: **Free up space** (refused while uploads are pending) and **Always keep on this device**. The tooltip shows how much is on this device and pending uploads; **Disconnect** and **Exit** warn about uploads in progress; at logout/shutdown the mount waits up to 2 minutes for them.
- **Import rclone mounts**: finds your own `rclone mount` systemd user services and takes them over (same remote, folder and cache settings; your service is disabled, not deleted). **Hand back to the original rclone setup** undoes it. Caracal mentions mounts it finds once.
- Locations keep retrying after sleep/resume and network changes (no "start limit" give-up). A non-empty folder is refused with a clear message and never touched.
- The dead-mount watchdog never detaches an rclone drive that's just offline (it may hold pending uploads), only one whose rclone process is gone.
- The tray menu lists SFTP/FTP/WebDAV/cloud locations too.

## [2.4] - 2026-11-22

- Fixed a crash when ticking **Names** or **Contents** in the search index window (and when answering "No" to "Stop indexing this drive?"). The list was rebuilt while Qt was still updating the row that was clicked.

## [2.3] - 2026-11-21

- **See what the index is doing for you.** Network folders show how long they took in the status bar: "⚡ opened from index in 29 ms · share listing 6.5 s" for indexed ones, "Share listing 2.1 s (not indexed)" otherwise (Preferences → Explorer to turn off).
- The **🔍** button in the status bar now also appears in network folders that aren't indexed ("🔍 Not indexed" — click to index the folder), and its tooltip shows averages for the last 20 folders opened ("from the index in 15 ms on average, reading the share took 2.3 s on average").
- **Searches say where their results came from and how long they took:** "159 results in 2 ms (⚡ from the index)" vs "11 results in 41.2 s (searched the share) — index this folder for instant results". The summary no longer disappears when you select a result.

## [2.2] - 2026-11-21

- **The search index is encrypted.** It holds the names (and, if you turned it on, the contents) of every file on your indexed drives. On disk it's now only an AES-256-GCM encrypted file — random data to anyone without your login — and the key lives in your desktop keyring (KWallet / GNOME Keyring), which unlocks when you log in, so there's nothing extra to type. While you're logged in, the working copy is kept in memory (your private runtime folder, wiped at logout); Caracal saves the encrypted copy after indexing and when it quits.
- Your existing index is encrypted automatically on upgrade, and the old unencrypted file is overwritten and deleted.
- Preferences → Search → **Index storage**: *Encrypted* (default), *In memory only* (nothing on disk at all; rebuilt after each login), or *Not encrypted*. If the keyring can't be used, Caracal keeps the index in memory for that session rather than writing it unencrypted, and says so on the index status page.
- If the key is ever lost (e.g. the keyring is reset), Caracal simply rebuilds the index — it's a cache of what's on your drives, so nothing is lost.
- Needs `python3-cryptography` and `secret-tool` (Fedora: `libsecret`; Debian/Ubuntu: `libsecret-tools`), now recommended dependencies.

## [2.1] - 2026-11-21

- **Indexed network folders open instantly.** Until now the index only sped up search; browsing always listed the share live. Now a folder inside an indexed network drive or folder is shown straight from the index (a local database read), with "from the search index — checking the share for changes…" in the status bar while the live listing loads in the background and replaces it. In a test with a slow share, all 3,000 items appeared in 0.25 s instead of trickling in over 9 s. If the share doesn't answer, Explorer keeps showing the index's copy and says so.
- **See whether indexing is done:** the status bar shows **🔍 Indexing… 40,123** while any index is being built, **🔍 Index queued** while waiting, and **🔍 Indexed** in a folder that's covered (hover for file count and when it was last updated). Click it — or **File → Search index status…** — for every indexed drive and folder. The right-click *Fast search index* menu shows the same status line.
- Fixed: the index status could fail on the very first index (the database had no tables yet).

## [2.0] - 2026-11-21

- **Check network mounts** (Explorer → File, or right-click the navigation pane): lists every network mount on the computer, not only Caracal's, with its health. *Disconnected* (transport endpoint is not connected), *Stale* and *Not responding* mounts are shown in red, and **Fix stuck mounts** detaches them. A **Flatpak start test** starts any installed Flatpak app's sandbox and shows the exact error if it won't open.
- **Caracal warns you about stuck mounts:** it checks every 2 minutes; if one is stuck you get a notification and a red "⚠ stuck network mount" button in Explorer's status bar that opens the check.
- **The background watchdog now sweeps all network mounts** for dead ones, not just Caracal's drives. Hard failures (disconnected FUSE connection, stale NFS handle, I/O error) are detached right away; mounts that are only slow must fail two checks in a row first, so a busy NAS isn't cut off. Never touches the Flatpak document portal, gvfs or other non-network mounts. Preferences → General → *Also other network mounts* limits it to Caracal's.
- **SFTP / FTP / WebDAV locations clean up after themselves:** when their connection process dies, the dead mount is removed and the location reconnects, instead of leaving a "Transport endpoint is not connected" folder behind. Existing locations are updated automatically.

## [1.9] - 2026-11-21

- **Flatpak apps no longer fail to install or open because of an offline drive.** Flatpak's sandbox setup touches the folders under `/mnt`; an automount drive whose server is gone made it hang or fail with "No such device" the moment it did. The watchdog now also checks idle automount drives' servers every minute and, while one is unreachable, swaps its automount for the locked empty folder (which Flatpak handles fine). It's restored automatically when the server answers again.

## [1.8] - 2026-11-21

- **Failed drives clean up after themselves.** When an SMB/NFS drive fails to connect, Caracal now stops its automount trap (which made every program touching the path wait), unmounts anything half-mounted, and **locks the empty folder** so no program can write into it and fill your local disk thinking it's the share. Mounting on top of the locked folder works normally. Preferences → General → *If a drive can't connect* can instead remove the folder or leave it as it is.
- **Automatic retry every 5 minutes** (Preferences → General → *Retry failed drives every*; "Never" turns it off). A small background timer (`caracal-watchdog.timer`, runs as root, works even when Caracal isn't open or at boot before the network is up) retries drives that should be connected, but only once the server answers, so a down server never causes hangs. Drives you disconnected yourself are left alone.
- **Dead shares are unmounted.** If a connected share stops responding (server rebooted, network dropped, "stale file handle"), it's unmounted within about a minute and comes back through the retry, instead of freezing every program that touches it (*Unmount shares that stop responding* in Preferences).
- A red dot's tooltip says what happened and when the next retry is ("Not connected — the server doesn't answer, retrying in about 3 min").

## [1.7] - 2026-11-21

- **Choose Caracal's app icon** (Preferences → Appearance → *App icon*): the cat-eared network folder (default), a plain **Folder**, a **Folder with cat ears**, **From my theme** (the File Explorer icon from a theme pack you imported, or your icon theme's file manager icon), or a **Custom image**. It changes the taskbar, app menu and window icons. Caracal does this with per-user copies of its menu entries, so it works on KDE Wayland; choosing the default again removes them.
- Importing a theme pack now picks up its File Explorer icon (`explorer.ico`, `file-explorer.png`, …) as the "app" icon. You can also drop `app.png`/`.svg`/`.ico` into the theme folder's icons/.

## [1.6] - 2026-11-21

- **Switching to the faster connection replaces the old drive.** Right-click an SMB drive → *Faster connection available: switch to NFS…* opens the dialog with the same name, local folder and options; OK removes the SMB drive and maps the NFS export in its place, so paths and bookmarks keep working.
- **No more typing the local folder.** The Map network drive dialog always suggests `/mnt/<share name>` (it was left empty when opened from Browse, the faster-connection tip or a typed address).
- SFTP / FTP / WebDAV locations also default to `/mnt/<name>` instead of `~/Network/<name>`; Caracal creates the folder for you (owned by you) the first time.

## [1.5] - 2026-11-21

- **7-Zip in the right-click menu**, laid out like 7-Zip on Windows: *Open archive*, *Extract files…*, *Extract Here*, *Extract to "name/"*, *Test archive*, *Add to archive…* (7z, zip, tar, wim, xz/gzip/bzip2; compression level; split into volumes; password with encrypted file names; delete files after compression), *Add to "name.7z"* / *"name.zip"*, *Compress and email* (when xdg-email is available), and *CRC SHA* (CRC-32, CRC-64, SHA-1, SHA-256, all). Jobs run in the background with progress and Cancel; encrypted archives ask for the password (passwords are passed to 7-Zip privately, not on its command line). Needs the **7zip** package (Fedora: `7zip` or `p7zip-plugins`; Debian/Ubuntu: `7zip`), which is now a recommended dependency. Preferences → Explorer → *7-Zip commands* chooses a submenu (default), items directly in the menu, or off.
- **Light Explorer on a dark desktop (and the reverse) looks right everywhere:** dialogs, dropdown lists (white-on-white text in Preferences), checkboxes (drawn black), tooltips and menus now use Explorer's colors. Icons switch to the matching light/dark version of your icon theme (e.g. Breeze ↔ Breeze-Dark, Papirus ↔ Papirus-Dark), so ribbon icons no longer vanish.
- **Home / Share / View tabs look clickable:** hover highlight with an accent bar, a pointing-hand cursor, and the open tab is marked.
- Ribbon menu buttons show a single ▾ (no more doubled arrows).

## [1.4] - 2026-11-21

- **No more lock-ups on network shares.** Explorer no longer waits on a slow or unreachable SMB/NFS/SFTP server: folder listings, the left pane, file icons, the status bar, menus and theme changes all stay responsive. A share that stops answering is shown as not responding instead of freezing the window, and is retried after 30 seconds.
- **Loading bar** at the bottom left while a folder loads, with a running count ("Loading… 1,200 items so far"). Big or slow folders fill in as they load. If a server doesn't answer, the status bar says so ("Waiting for the server (nas) to respond… 12 s"). The bar also runs while picture/music/document details are being read.
- **Connection dots** on network drives in the left pane: green when connected, amber while connecting, red when not mounted (including automount drives that haven't been opened yet) or not responding. Hover for details.
- **Faster connection tip:** if an SMB drive's server also shares the same folder over NFS (common on TrueNAS, Synology, Unraid, QNAP), the drive's tooltip says so and its right-click menu offers to map it over NFS, which is usually faster on Linux. The Map network drive dialog shows the same tip while you type an SMB address.
- **Quick Access Toolbar buttons** (New folder, Properties…) now grey out where they can't work, such as on This PC, instead of silently doing nothing, and show hover/pressed feedback.
- The left pane loads subfolders in the background and shows "Loading…" / "Not responding" instead of hanging.

## [1.3] - 2026-11-21

- **Updates take effect right away.** Caracal keeps running in the background (explorer mode, search, "Show in folder"), so after installing a new RPM/DEB the old version could keep answering and nothing seemed to change. Now the package tells running copies to restart, and Caracal reopens the same Explorer windows and tabs. If you're copying or moving files, it waits until they finish. Launching a newer Caracal while an older one is running also hands over to the new one. (Upgrading *to* 1.3 closes the old background copy instead; just open Caracal again.)
- Installing an update from **File → Check for updates…** restarts automatically when it's done (no extra question).
- **File → About Caracal** shows the version; the version is also at the bottom left of every Explorer window.

## [1.2] - 2026-11-21

- **All of Explorer's Details columns.** Name, Date modified, Type and Size stay the defaults; right-click the column header (or **View → Add columns**) to add Date created, Date accessed, File extension, Filename, Folder path, Path, Attributes, Owner, Group, Permissions, Link target, picture details (Dimensions, Width, Height, Date taken, Camera maker, Camera model), music/video details (Length, Bit rate, Title, Contributing artists, Album, Genre, Year, #) and document details (Authors, Pages, Content created). **More…** opens the full list. New columns appear on the right; drag headers to reorder.
- **Sort by and Group by any column**, from the header, **View → Sort by / Group by**, or the right-click menu (columns that aren't showing are under **More**).
- **Header filters like Explorer:** hover a column header and click the ▾ to tick the values to show (dates as Today / Yesterday / Last week…, sizes as Tiny / Small / Medium…, everything else by value). Filtered columns show a ✓, the status bar shows "N of M items (filtered)", and **View → Clear filters** resets them. Filters clear when you leave the folder.
- Picture, music and document details are read in the background only while one of their columns is in use. Music/video details need ffprobe (install ffmpeg); PDF details use pdfinfo.
- **View → Size all columns to fit.**

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
