# Changelog

All notable user-facing changes to CataMount.

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
- Connect, disconnect, edit, remove and open drives from the main window; status refreshes automatically.
- Read-only view of existing network mounts in `/etc/fstab` (CataMount never edits it).
- System tray: close to tray, tray menu to connect/disconnect/open drives, notifications when a drive fails or drops, optional start at login in the tray, and a choice of color, white or dark tray icon.
- Only one copy runs at a time; opening it again brings the window forward.
- Warns at startup if a needed system tool (cifs-utils, nfs-utils, pkexec) is missing and shows how to install it.
- New app icon: a cat-eared network folder.
- Automatic updates from GitHub Releases (checked at most once a day). A green dot on the version number means an update is ready; the changelog window shows its notes and installs it after checking its SHA-256 checksum.
- Light, dark and follow-system themes.
- RPM (Fedora) and DEB (Debian 13+, Ubuntu 26.04+) packages. All dependencies are installed automatically.
