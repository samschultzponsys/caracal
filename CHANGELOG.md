# Changelog

All notable user-facing changes to CataMount.

## [0.1] - 2026-11-16

First release.

- Map SMB (Windows/Samba) and NFS shares to a local folder, like Windows' "Map network drive".
- Paste addresses in any common form: `\\server\share`, `smb://server/share`, `server:/export`, `nfs://server/export`.
- Drives are persistent systemd mounts: reconnect at startup, or connect on first access (automount) with optional idle disconnect.
- SMB passwords are stored in root-only credential files, never in the unit files.
- Connect, disconnect, edit, remove and open drives from the main window; status refreshes automatically.
- Read-only view of existing network mounts in `/etc/fstab` (CataMount never edits it).
- System tray: close to tray, tray menu to connect/disconnect/open drives, notifications when a drive fails or drops, optional start at login in the tray, and a choice of color, white or dark tray icon.
- Only one copy runs at a time; opening it again brings the window forward.
- Warns at startup if a needed system tool (cifs-utils, nfs-utils, pkexec) is missing and shows how to install it.
- New app icon: a cat-eared network folder.
- Light, dark and follow-system themes.
- RPM (Fedora) and DEB (Debian 13+, Ubuntu 26.04+) packages. All dependencies are installed automatically.
