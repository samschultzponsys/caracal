# DEVLOG

Working notes for the next session. Newest first.

## 2026-11-21 — v1.6: replace on switch, /mnt/<share> defaults

Sam: "when switching to a faster method remove the old, also default the mount path to
/mnt/%sharename%" + "its making me type a path everytime".
- DriveDialog: _mp_touched/_name_touched now mean "a mount point / name was given in the
  spec", not "server is prefilled" — prefilled templates (Browse, address bar, faster tip)
  left the mount point empty. suggest_mount_point already gives <mount_root>/<share leaf>.
- MainWindow.switch_to_faster(d, "server:/export"): DriveDialog prefilled with d's name,
  mount point and options (not extra_options: SMB-only), then helper "remove"
  (remove_folder False) → _after_helper → "apply" of the NFS spec (same unit names since the
  mount point is the same, so it must be remove-then-apply). On apply failure the error box
  says the SMB version was removed and how to restore it (_switch_fallback).
- Helper "user-folder": creates /mnt/<name> or /media/<name> (one level, safe name, lstat,
  never a mount, never a non-empty folder owned by someone else) owned by PKEXEC_UID.
  LocationDialog suggests /mnt/<name> (next free -2, -3…); window.add_network_location calls
  user-folder when the folder is missing or not writable, then saves.

## 2026-11-21 — v1.5: 7-Zip menu, light-on-dark fixes, ribbon tabs

Sam (screenshots, light Explorer on KDE dark): Preferences combo list white-on-white, ribbon
icons invisible, black checkboxes, doubled ▾; wants Home/Share/View obviously clickable;
keep Caracal's own context menu and embed 7-Zip like Windows.
- Theme.apply_app_palette(): when Explorer mode is Light/Dark, QApplication palette =
  Explorer palette (restored to the saved system palette for "Follow system";
  is_dark() falls back to Theme._system_palette, not the app palette we replaced). Popups and
  QSS-styled widgets resolve from the app palette, which is why setPalette on the window
  wasn't enough. QSS adds QComboBox QAbstractItemView, QToolTip, dialog item views.
- icon_variant(name, dark): picks the installed light/dark sibling icon theme (suffixes
  -dark/_dark/-light/_light) in apply_icon_theme(); app-wide, Caracal only.
- Ribbon tabs: objectName RibbonTab, QSS accent top border on hover/checked, hand cursor.
  #RibbonPanel QToolButton::menu-indicator hidden (text already has ▾).
- explorer/sevenzip.py: find_exe (7zz/7z/7za/p7zip libexec), pure arg builders (tested),
  Job = QProcess with -bsp1 progress parsing, LC_ALL=C.UTF-8, password written to stdin
  (twice for `a -p`), stdin closed otherwise so a needed password fails fast ("Break
  signaled") → needs_password() → QInputDialog → retry. Progress dialog appears after
  600 ms. SevenZip.fill() builds the menu; window._sevenzip_menu() honors
  explorer/sevenzip_menu (submenu|flat|off). Not shown in Trash.
- Open with now sniffs MIME by content only for local files (extension for remote).
- Packaging: Recommends (7zip or p7zip-plugins) / 7zip | p7zip-full.

## 2026-11-21 — v1.4: responsiveness on network shares

Sam: QAT buttons "static"; red/green dots for mounts; detect faster NFS vs SMB; UI slow with
complete lock-ups on network shares; wants a loading bar; switching theme/colors to Adwaita
went "not responding".
- Root cause of the freezes: GUI-thread filesystem calls on network paths — every painted
  file icon (QFileInfo.isDir + QFileIconProvider), status bar (statvfs + getsize of the
  selection on every selection change), realpath() in folder prefs/in_trash, ismount, and the
  nav pane's synchronous expand (scandir of the folder + every subfolder) which
  _restore_expanded re-ran on each nav rebuild — so a theme change with an expanded share
  froze. (Theme switching itself measured ~0.2 s.)
- caracal/fsinfo.py: mount table from /proc/self/mountinfo (2 s cache; autofs counts as
  remote, is_mounted ignores autofs traps); call(fn, path) runs on remote paths in a daemon
  thread with a timeout and marks the mount unresponsive for 30 s (DEAD_FOR) so stuck threads
  don't pile up. Don't call mount_for() while holding _lock (deadlock found in testing).
  window.py now uses fsinfo.isdir/isfile/exists/realpath/is_mounted everywhere on the GUI
  thread; go() probes with timeout 0.3 and mark_dead=False and lets the scan report errors.
- Model: icons via Theme.name_icon(name, is_dir) (mime by extension, cached); _scan emits
  partial batches every 0.25 s (shown on first load; refreshes keep the old list until done),
  stops if navigated away; watcher added only after a successful load; progress signal →
  status. Window: busy QProgressBar (#BusyBar) in the status bar, free space via run_async
  cached 15 s, selection size from listing.
- NavPane: async _expand via run_async + QPersistentModelIndex; remote subfolders aren't
  peeked into; STATUS role + PinDelegate dot (up/busy/down); tooltip states.
- fastpath.py: port 2049 check + `showmount -e`, match_export() by last path component;
  MainWindow._probe_faster() per SMB drive once per session → self.faster → nav tooltip and
  "Faster connection available: map … over NFS…" (ctl.add_drive prefilled). DriveDialog shows
  the tip with a "Use NFS instead" link (900 ms debounce).
- QAT: Ribbon.update_qat_state() from _update_status; disabled on pseudo pages (This PC,
  Quick access home), where they used to do nothing.
- Test harness: scratchpad xp/hang.py fakes a hung NFS mount (os.stat/scandir/statvfs sleep
  20 s under ~/nas) and measures the worst event-loop gap: now <= 0.3 s for every action.

## 2026-11-21 — v1.3: upgrades restart running copies

Sam: upgrading the RPM (even uninstall + reinstall) didn't change anything / no version
visible. Cause: since 1.1 explorer mode keeps a headless process alive; a new launch just
forwards to it over QLocalSocket, so the old code kept running.
- Package scripts (RPM %posttrans, debian/caracal.postinst) run
  `pkill -USR1 -x caracal|catamount` (comm of the shebang script). Pre-1.3 processes die
  (default USR1 action); 1.3+ handles it: app.py sets a flag in the signal handler, a 1 s
  QTimer calls MainWindow.restart_for_update().
- restart_for_update waits while the helper is installing an update (_updating; killing the
  app could take pkexec/dnf with it) and while FileOpRunners are active (window.active_ops()).
- restart(): save_restore_state() → ~/.cache/caracal/restart.json (windows: tabs, current,
  geometry; plus a pending request), then `caracal --restore`. The new process skips the
  single-instance forward, takes the state (ignored if > 120 s old) and _reopen()s.
- Second launches now send "<version>\n<request>"; a running copy with a different version
  restarts and carries the request over (split_message() still accepts the old format).
- File menu: About Caracal <version>. Discover shows local RPMs as "caracal-1" (it uses
  QFileInfo.baseName of the file name) with no icon; that's Discover, not the package.

## 2026-11-21 — v1.2: every Details column, sort/group/filter by any of them

Sam: "need the full suite of filters and sort by like windows has… these [Name, Date
modified, Type, Size] are the defaults I want but we need all of them".
- columns.py: COLUMNS tuple of Column(key, title, kind, width, category, meta). Indices 0-3
  unchanged (stored per-folder sort [col, order] stays valid); group_by is stored as the
  column key ("date", "type", "ext"…; old values map via index_of). value()/display()/
  sort_key()/bucket() per column; missing values sort last, "Unspecified" group last.
- metadata.py: statx birth time (ctypes; struct buffer must be >= 256 bytes — a 240-byte
  buffer corrupted memory and caused random segfaults), JPEG/TIFF EXIF, QImageReader size,
  ffprobe JSON, OOXML/ODF props, pdfinfo. read() is called from DirModel._meta_pool only
  when meta_needed() (a meta column is visible/sorted/grouped/filtered); results cached by
  (path, mtime); sort/group/filter on a meta column re-runs _rebuild 400 ms after results.
- DirModel: filters {col: set(labels)} cleared by setRootPath; filter_values(col) counts
  respect other filters; total_count(). _Relay now holds the model by weakref and checks
  shiboken6.isValid, so late worker results for a closed window are dropped.
- headers.py: FilterHeader (▾ on hover, ✓ when filtered; press in the arrow area opens the
  popup without sorting), FilterPopup (Qt.Popup checkbox list, applies live, search box
  when > 12 values), ChooseColumnsDialog ("More…").
- window.py: header state now saved as explorer/columns2 (old 4-column state only donates
  widths via _restore_old_widths); fill_columns_menu (header right-click and View → Add
  columns), _column_choices for Sort by / Group by (visible columns + More by category).
- Packaging: Suggests ffmpeg-free (Fedora) / ffmpeg (Debian) for ffprobe.

## 2026-11-21 — v1.1: theme import, tray-less explorer mode, type-ahead

- theme.import_theme(): safe extract (zip-slip check, tar filter="data", 400 MB cap) into a
  temp dir; copies freedesktop icon themes (index.theme with Directories) to
  explorer-theme/icon-themes/, KDE .colors → parse_kde_colors() → theme.json mode/colors/accent,
  fonts → fonts/, slot-named images → icons/. Theme.apply_icon_theme() adds icon-themes to
  QIcon.themeSearchPaths and setThemeName (app-wide, Caracal only) with the desktop theme as
  fallback. list_icon_themes() for the Appearance combo ("Same as the desktop" = "").
- Explorer mode hides the tray unless `tray_in_explorer_mode` (MainWindow.tray_wanted()). The
  app keeps running headless; drive window close only hides it in explorer mode; File → Exit
  Caracal = ctl.quit.
- Type-ahead: _TypeAheadTree/_TypeAheadList override keyboardSearch → window._type_ahead():
  names only (no group rows), expands collapsed groups, same-letter cycling. Qt's default
  matched group headers and got "ca" wrong.

## 2026-11-20 — v1.0: updates reachable from Explorer

Sam (in Explorer mode): "I see no update tool". The version label / Help menu lived only in
the drives window, which Explorer mode never shows. Now: MainWindow.version_html() feeds an
Explorer status-bar version label (refresh_version, called from _refresh_version_label);
File/··· menus have Check for updates… and What's new; tray always offers Check for updates…;
update dialogs parent to dialog_parent() (an Explorer window when the drives window is
hidden); download/install progress mirrored to Explorer status. Version rolled 0.9 → 1.0 per
the Major.Minor rule.

## 2026-11-20 — v0.9: QAT, customizable nav pane, index any folder

Sam's spec (screenshot of his Win10 Explorer + notes): new folder button top-left; right-click
the left pane to hide/unhide nearly anything and get mounting options; right-click a path or
mount to index it manually; search stays top-right but uses the index when present; preview
pane toggle bottom-right; left pane vertical spacing.
- Ribbon: Quick Access Toolbar in the tab strip (explorer/qat JSON list; default
  properties + new-folder; ▾ to customize).
- NavPane: SPACER rows between sections; `hidden` set (explorer/nav_hidden JSON; key "trash"
  for Trash); `entries()` lists hideable items; right-click works on empty space (loc "").
  Rows = row_height + 3. Window `_nav_menu` rebuilt: item actions, index submenu, Hide, map /
  add / browse / manage, "Show in navigation pane" checklist, "Expand to open folder".
- IndexManager: FolderRoot (id "folder-<sha1>", state from isdir) stored in index/folders;
  add_folder/remove_folder/covering/indexed_root; update_drives merges folder roots. Window
  `_index_menu(m, path)` on item/background/nav menus; `_indexed_scope` uses covering().
- Status bar: preview toggle button (sb_preview) next to the view toggles.

## 2026-11-20 — v0.8: closer to Windows 10 Explorer; package renamed to caracal

- Repo renamed by Sam to samschultzponsys/caracal; REPO constant, links, metainfo and
  packaging URLs updated (the old URL redirects, so older installs still find updates).
  This cloud session's git remote still uses the old URL (GitHub redirects pushes).

Sam: "spacing and general look are not quite 1 to 1" (reference: his Win10 dark Explorer
screenshot). Changes: theme defaults font_size 9 / row_height 20 / nav_width 180; nav icons
16 px; ribbon strip and address bar slimmer (22-24 px, square); breadcrumb starts at the
home sub-folder with its icon (AddressBar._segments/_icon_for); history ⌄ + refresh inside
the address box (AddressBar.add_trailing); search magnifier trailing; QSS chevrons and
top-center sort arrows from SVGs generated per theme into ~/.cache/caracal/ui
(Theme.glyphs); group "group" color; children indented 10 px (GroupDelegate); columns sized
from font metrics; preview empty state is a centered hint; 17 px square scrollbars; status
bar on content color. `_rebuild_nav()` re-highlights the current folder after rebuilds.
- Package rename done: spec Name caracal + Obsoletes/Provides catamount; debian
  Source/Package caracal + Conflicts/Replaces (<< 0.8) + Provides catamount. 0.7's updater
  looks for caracal-* assets first and its helper accepts the caracal name.

## 2026-11-19 — v0.7: renamed CataMount → Caracal

Sam: "rename everything", except the GitHub repo for now (REPO constant and URLs still
point at samschultzponsys/catamount; GitHub redirects after a rename).
- Python package `catamount/` → `caracal/`, helper → caracal-helper, bin → caracal (+ `catamount`
  symlink), app ID com.spazcat.Caracal (desktop files, icons, metainfo, polkit
  com.spazcat.Caracal.manage, D-Bus com.spazcat.Caracal.SearchProvider, KRunner/GNOME/file
  manager integration files), env vars CARACAL_*, QSettings ("caracal","caracal"), paths
  /etc/caracal, /usr/share/caracal, ~/.local/share/caracal, ~/.cache/caracal, pseudo locations
  `caracal:home` etc.
- **Package name transition:** 0.6's updater/helper only accept a package named "catamount",
  so 0.7 keeps `Name: catamount` (spec) / `Package: catamount` (debian). 0.7's updater and
  helper accept both names (PACKAGE_NAMES). **For 0.8: set spec Name: caracal with
  `Obsoletes: catamount < 0.8` and `Provides: catamount = %{version}`; debian Source/Package
  caracal with `Replaces: catamount (<< 0.8)`, `Breaks: catamount (<< 0.8)`,
  `Provides: catamount`; rename debian/changelog entries' package name for new entries.**
  build-rpm.sh reads Name from the spec; build-deb.sh copies *_all.deb; check-version accepts
  any package name.
- Compatibility: helper/model/user_mounts accept the old "# Managed by CataMount" /
  "# CataMount-Id:" / "# CataMount-Spec:" headers; helper reads creds from
  /etc/catamount/creds when /etc/caracal has none, and moves them on the next save; user
  locations keep their `catamount-<id>.service` unit names and old secret/rclone paths until
  re-saved.
- `caracal/migrate.py` runs at startup: copies ~/.config/catamount/catamount.conf, moves
  ~/.local/share/catamount (index, theme), rewrites autostart, re-points the xdg-mime default
  and the FileManager1 service file. Idempotent.
- Tests: 61.

## 2026-11-19 — v0.6: Explorer polish toward v1

Sam: "just want to get a good v1 of the explorer built" (tray icon parked).
- Reload no longer loses state: modelAboutToBeReset saves selected paths/current/scroll;
  _after_reset restores them when the root is unchanged; collapsed group labels kept per
  folder in `_collapsed` (expanded/collapsed signals, ignored while restoring).
- Model reports `error` (permission/missing/other) from the scan thread; overlay labels in
  both views' viewports show it, or "This folder is empty."
- eventFilter on viewports: Back/Forward mouse buttons, middle-click → background tab,
  Ctrl+wheel steps details→small→medium→large→xl. Window mousePressEvent handles
  back/forward too.
- AddressBar edit has a QCompleter over a dirs-only QFileSystemModel.
- NavPane.reveal(loc) expands to the folder (prefers expandable This PC entries over pins);
  setting explorer/nav_follow (default on).
- Header context menu hides/shows columns (saved with the header state).
- explorer/visits counts → "Frequent folders" on the Home page.
- fileops: trash_original/restore_from_trash (handles relative paths in .Trash-uid),
  extract_archive (zip with zip-slip check, tarfile filter="data", 7z/bsdtar for 7z/rar/zst),
  make_zip. Window: in_trash, restore, extract, compress.
- Not done yet: rubber-band selection in Details view; grouping in icon views; Trash
  "Original location"/"Date deleted" as real columns (tooltip for now).

## 2026-11-19 — v0.5: CataMount Explorer (preview)

Sam: Dolphin can't be made to look enough like Windows Explorer; wants an Explorer-like
file manager, a slot for his own icons and fonts, and SMB/NFS/SFTP/SCP/FTP mounting in
the left bar. Combined into CataMount with a toggle (explorer_mode). Layout target:
his Windows 10 dark Explorer screenshot (ribbon, date groups, compact rows, preview pane).

### Structure (catamount/explorer/)
- `model.py` DirModel: own QAbstractItemModel (not QFileSystemModel) so it can do groups
  (top level = groups, internalId = group+1 for children), Windows KB sizes, M/d/yyyy dates,
  natural sort with folders first, async scandir thread, QFileSystemWatcher reload, async
  thumbnails, rename via setData (hide-extension aware), drops go to FileOperation.
- `fileops.py`: FileOperation thread with progress/conflict signals, rename-fast moves on the
  same device, `.catamount-part` temp files, never deletes sources after a failed copy.
  Own freedesktop trash (`trash_item`) because QFile.moveToTrash failed in testing;
  undo for rename/mkdir/create/move/copy/trash.
- `window.py` ExplorerWindow: tabs (state per tab: history/index/loc), pseudo locations
  catamount:home/thispc/network, net:sys:<id>, net:user:<id>, device:<dev>, connect:<addr>.
  Per-folder prefs in QSettings `explorer/folder_prefs` (sort/group). FileOpRunner shows the
  progress dialog after 500 ms.
- `ribbon.py` (Win10 ribbon + GroupDelegate), `navpane.py` (Win10/Win11 trees, PinDelegate,
  configurable labels), `widgets.py` (AddressBar, TilePage/Tile, PreviewPane),
  `dialogs.py` (OperationDialog, ConflictDialog, PropertiesDialog, LocationDialog,
  OpenWithDialog; `_adopt` gives each dialog Explorer's palette), `theme.py` (ICON_SLOTS,
  theme folder, palette() + stylesheet() per light/dark), `places.py` (user dirs,
  mountinfo volumes, lsblk + udisksctl), `openwith.py` (.desktop parsing, mimeapps.list),
  `fm1.py` (xdg-mime default + org.freedesktop.FileManager1 via Gio).
- `catamount/user_mounts.py`: sftp (sshfs -f, password_stdin from a 0600 secret file) and
  ftp/ftps/webdav (rclone mount, Type=notify, obscured pass in a 0600 rclone conf) as systemd
  user units with the "# Managed by CataMount" header.
- MainWindow: drive_listeners, open_explorer, show_default, browse_and_add, add_drive(address,
  kind), edit/remove_drive_obj, apply_explorer_settings. app.py: `--explorer [path]`,
  `--explorer-service`, single-instance message `explore\t<path>`.
- Tests: 57 (Qt tests skip on the CI runner, which has no PySide6).

### Known gaps / next
- Not tried on a real KDE desktop yet; icons in screenshots are blank only because the
  container has no icon theme.
- Grouping only applies in Details view (icon views are flat).
- Sam asked whether to rename the app now that it does more: name options are pending.

## 2026-11-18 — v0.4: tray icon grey → green in every style

- Sam: "looks worse when connected than disconnected; should be greyed out until connected
  then reflect a color". The mono (white/dark) styles only faded when unlit and stayed
  white/dark when lit. Now `tray_icon` recolors the mono glyph (CompositionMode_SourceIn):
  grey when unlit, green when lit (MONO_TINTS). The color style was already grey/green.
  "ready" (automount armed) now counts as lit. Style names: "Color tile", "Simple (for dark
  panels)", "Simple (for light panels)".

## 2026-11-18 — v0.3: instant search (Everything-style)

Sam: Windows share search was always slow; wants an index for fast search, "the full
deal", with other desktops supported where possible; search is always local.

### Design
- Dolphin/Nautilus search can't be pointed at another index (Baloo and Tracker skip
  network filesystems), so CataMount keeps its own index and plugs into the places users
  search: its own window, KRunner, GNOME Shell, and file-manager right-click entries.
- `indexdb.py` (no Qt): SQLite WAL, tables drives/dirs/files, FTS5 `names` + `dirfts`
  (trigram, substring match), `contents` (unicode61). `files.ext` is indexed. Query parser
  handles words, ext:/type:/size:/modified:/folder:/drive:/content:, wildcards and paths.
  All long words go in ONE trigram MATCH (one FTS join per word took 16 s on 1M rows).
  Words under 3 chars fall back to LIKE. COUNT_CAP 20000.
- Benchmark (1M synthetic entries, this container): 0.2–176 ms per query, 341 MB DB.
- `crawler.py` (no Qt): a full scan lists everything; an incremental scan stats every
  known dir and re-lists those with a changed mtime. Skips recycle/snapshot/NAS system
  folders. Content pass: extract.py (text/code, OOXML, ODF, EPUB via zipfile, PDF via
  pdftotext; 256K chars per file, size cap from prefs); re-extracts when content_mtime !=
  mtime. Watcher = ctypes inotify, budget of half of max_user_watches, shallow dirs first.
- `index_manager.py` (Qt, in the tray app): one worker thread, job queue (scan/full/dirs/
  content/delete/clear_content), 30 s scheduler (rescan_minutes, full_hours, pause on
  battery via /sys/class/power_supply, mount_ready). Watcher events are debounced 2 s into a
  "dirs" job. Per-drive prefs live in QSettings `index/drives` (JSON); index prefs are
  per user, not in the unit spec.
- `search_window.py`: QTableView + model, 60 ms debounce, synchronous queries (fast
  enough), content searches show the snippet column second, ShowItems via FileManager1.
- `search_service.py`: GLib/Gio only (no Qt), D-Bus activated
  (`com.spazcat.CataMount.SearchProvider`), exposes org.kde.krunner1 at /krunner and
  org.gnome.Shell.SearchProvider2, exits after 5 min idle. Tested with gdbus under
  dbus-run-session (python3.12 + gi here).
- Integration files in data/search/ (dbus service, krunner dbusplugin, gnome-shell ini,
  kio servicemenu, nemo actions, nautilus/caja python extension). The .desktop got a
  "search" action for hotkeys. Thunar: no system-wide action location (uca.xml is per
  user), so not integrated.
- Single-instance message `search\t<folder>\t<query>` opens the window in the running app.

### Not verified on real hardware
- KRunner and GNOME Shell actually loading the providers, the file-manager entries, and
  inotify on real CIFS/NFS mounts. Sam to test.

## 2026-11-17 — v0.2: tray icon shows connection state

- Sam: 0.1 "worked well", but the tray icon didn't light up when a connection was made
  (before, only failures changed it).
- `tray_icon(style, state, lit)`: color style uses `data/icons/tray/tray-color-idle.svg`
  (grey tile, unlit nodes) when no drive is connected and the normal glowing icon when any
  is; white/dark styles fade to 40% opacity when unlit. Red badge for failed as before.
  "ready" (automount armed, not mounted) counts as unlit. Pref `tray_show_status`
  (default on). Prefs changes refresh the tray icon right away.
- Released 0.2 through the workflow `publish` input. This is also the first end-to-end
  auto-update test (0.1 → 0.2) for Sam.

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
