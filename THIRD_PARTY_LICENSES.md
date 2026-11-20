# Third-party licenses

Caracal itself is Apache-2.0 (see `LICENSE` and `NOTICE`). It bundles no
third-party code. At runtime it uses these system packages, installed and
updated by the distribution's package manager:

| Component | Used for | License | Notes |
|---|---|---|---|
| [Qt 6](https://www.qt.io/) | GUI toolkit | LGPL-3.0-only (also GPL / commercial) | Dynamically linked, unmodified system libraries. |
| [PySide6 / Shiboken6](https://doc.qt.io/qtforpython-6/) | Python bindings for Qt | LGPL-3.0-only (also GPL / commercial) | Imported from the system package, unmodified. Users may replace them with any compatible build. |
| Python 3 | Runtime | PSF-2.0 | System interpreter. |
| SQLite | Search index (via Python's `sqlite3`) | Public domain | Part of the system Python. |
| PyGObject | Desktop search service (optional) | LGPL-2.1-or-later | System package, unmodified. |

Tools Caracal runs as separate programs (not linked, not distributed with
Caracal): systemd (`systemctl`, `journalctl`), polkit (`pkexec`),
cifs-utils, nfs-utils / nfs-common, and optionally pdftotext (poppler-utils),
avahi-browse, smbclient. Their licenses do not affect Caracal's.

LGPL-3.0 and Apache-2.0 are compatible for this use: Caracal does not
modify or statically include Qt or PySide6.

Optional, used if installed (not bundled):

- **cryptography** (python3-cryptography) — Apache-2.0 OR BSD-3-Clause. Imported to
  encrypt the search index (AES-256-GCM).
- **secret-tool** (libsecret / libsecret-tools) — LGPL-2.1-or-later. Run as a separate
  program to keep the index key in the desktop keyring.
- **7-Zip** (7zip / p7zip) — LGPL-2.1-or-later with the unRAR restriction. Run as a
  separate program for the 7-Zip menu.
- **ffprobe** (ffmpeg) and **pdfinfo** (poppler-utils) — run as separate programs for
  media and PDF detail columns.
