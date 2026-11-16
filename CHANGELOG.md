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
- Light, dark and follow-system themes.
- RPM (Fedora) and DEB (Debian 13+, Ubuntu 24.04+) packages.
