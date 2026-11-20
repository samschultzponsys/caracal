# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""App and tray icons."""

import os

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

from caracal import APP_ID

_HERE = os.path.dirname(os.path.abspath(__file__))
# Installed location first, then the source checkout.
_ICON_ROOTS = ("/usr/share/caracal/icons", os.path.join(_HERE, "..", "data", "icons"))

TRAY_STYLES = (("color", "Color tile"), ("light", "Simple (for dark panels)"),
               ("dark", "Simple (for light panels)"))
BADGE_COLORS = {"failed": "#DC2626", "connected": "#16A34A"}


def _find(*parts):
    for root in _ICON_ROOTS:
        p = os.path.join(root, *parts)
        if os.path.isfile(p):
            return p
    return None


APP_ICON_CHOICES = (("caracal", "Caracal (cat-eared network folder)"),
                    ("folder", "Folder"),
                    ("folder-cat", "Folder with cat ears"),
                    ("theme", "From my theme (theme pack or icon theme)"),
                    ("custom", "Custom image…"))
DESKTOP_IDS = (APP_ID, APP_ID + ".Explorer")
OVERRIDE_MARK = "X-Caracal-Icon-Override=true"


def default_app_icon():
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull():
        p = _find("hicolor", "scalable", "apps", APP_ID + ".svg")
        icon = QIcon(p) if p else QIcon()
    if icon.isNull():
        style = QApplication.style()
        icon = QIcon.fromTheme("folder-remote", style.standardIcon(QStyle.SP_DriveNetIcon))
    return icon


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings("caracal", "caracal")


def app_icon_choice(settings=None):
    s = settings or _settings()
    choice = str(s.value("app_icon", "caracal") or "caracal")
    return choice if choice in dict(APP_ICON_CHOICES) else "caracal"


def icon_for_choice(choice, custom_file=""):
    """The app icon for a choice, or None if that choice has nothing to offer."""
    if choice in ("folder", "folder-cat"):
        p = _find("app", choice + ".svg")
        return QIcon(p) if p else None
    if choice == "theme":
        from caracal.explorer import theme
        t = theme.get()
        p = t._file("app")
        if p:
            return QIcon(p)
        for name in theme.ICON_SLOTS["app"][1]:
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
        return None
    if choice == "custom" and custom_file and os.path.isfile(custom_file):
        icon = QIcon(custom_file)
        return None if icon.isNull() else icon
    return None


def app_icon(settings=None):
    s = settings or _settings()
    choice = app_icon_choice(s)
    if choice != "caracal":
        icon = icon_for_choice(choice, str(s.value("app_icon_file", "") or ""))
        if icon is not None:
            return icon
    return default_app_icon()


def _system_desktop_file(desktop_id):
    for base in ("/usr/share/applications", "/usr/local/share/applications",
                 os.path.join(_HERE, "..", "data")):
        p = os.path.join(base, desktop_id + ".desktop")
        if os.path.isfile(p):
            return p
    return None


def _user_apps_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "applications")


def override_desktop_text(text, icon_path):
    """The desktop entry with the main group's Icon= pointing at icon_path (actions keep
    their own icons), marked so Caracal can find and remove its override later."""
    out, group, done = [], "", False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if group == "[Desktop Entry]" and not done:
                out.append("Icon=" + icon_path)
                done = True
            if group == "[Desktop Entry]":
                out.append(OVERRIDE_MARK)
            group = stripped
        elif group == "[Desktop Entry]" and stripped.startswith("Icon="):
            line, done = "Icon=" + icon_path, True
        elif stripped == OVERRIDE_MARK:
            continue
        out.append(line)
    if group == "[Desktop Entry]":
        if not done:
            out.append("Icon=" + icon_path)
        out.append(OVERRIDE_MARK)
    return "\n".join(out) + "\n"


def apply_app_icon(settings=None):
    """Use the chosen app icon for windows now, and for the taskbar / app menu / KDE's
    window matching through per-user overrides of Caracal's desktop entries (the taskbar
    on Wayland only looks at those). Choosing "Caracal" again removes the overrides."""
    s = settings or _settings()
    app = QApplication.instance()
    icon = app_icon(s)
    if app is not None:
        app.setWindowIcon(icon)
    choice = app_icon_choice(s)
    apps = _user_apps_dir()
    use_default = choice == "caracal" or icon_for_choice(
        choice, str(s.value("app_icon_file", "") or "")) is None
    if use_default:
        changed = False
        for did in DESKTOP_IDS:
            p = os.path.join(apps, did + ".desktop")
            try:
                with open(p, encoding="utf-8") as fh:
                    mine = OVERRIDE_MARK in fh.read()
                if mine:
                    os.remove(p)
                    changed = True
            except OSError:
                pass
        if changed:
            _refresh_desktop_cache(apps)
        return icon
    import hashlib
    data_dir = os.path.join(os.path.dirname(apps), "caracal", "app-icon")
    os.makedirs(data_dir, exist_ok=True)
    pm = icon.pixmap(256, 256)
    from PySide6.QtCore import QBuffer, QByteArray
    buf = QByteArray()
    qb = QBuffer(buf)
    qb.open(QBuffer.WriteOnly)
    pm.save(qb, "PNG")
    raw = bytes(buf)
    # a new file name whenever the picture changes, so icon caches can't show the old one
    png = os.path.join(data_dir, "icon-%s.png" % hashlib.sha1(raw).hexdigest()[:10])
    if not os.path.exists(png):
        for old in os.listdir(data_dir):
            if old.startswith("icon-") and old.endswith(".png"):
                try:
                    os.remove(os.path.join(data_dir, old))
                except OSError:
                    pass
        with open(png, "wb") as fh:
            fh.write(raw)
    os.makedirs(apps, exist_ok=True)
    changed = False
    for did in DESKTOP_IDS:
        src = _system_desktop_file(did)
        if not src:
            continue
        with open(src, encoding="utf-8") as fh:
            text = override_desktop_text(fh.read(), png)
        dst = os.path.join(apps, did + ".desktop")
        try:
            with open(dst, encoding="utf-8") as fh:
                current = fh.read()
        except OSError:
            current = None
        if current is not None and OVERRIDE_MARK not in current:
            continue          # the user's own customized entry: leave it alone
        if current != text:
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            changed = True
    if changed:
        _refresh_desktop_cache(apps)
    return icon


def _refresh_desktop_cache(apps):
    import shutil
    from PySide6.QtCore import QProcess
    for tool, args in (("update-desktop-database", [apps]), ("kbuildsycoca6", []),
                       ("kbuildsycoca5", [])):
        exe = shutil.which(tool)
        if exe:
            QProcess.startDetached(exe, args)


# Glyph colors for the monochrome tray styles: (not connected, connected)
MONO_TINTS = {"light": ("#9CA3AF", "#34D399"),   # for dark panels
              "dark": ("#6B7280", "#059669")}    # for light panels


def tray_icon(style="color", state=None, lit=True):
    """Tray icon in the chosen style.

    lit=False (nothing connected): grey in every style. lit=True: green (the color icon,
    or a green glyph for the white/dark styles). state="failed" adds a red badge.
    """
    base = QIcon()
    tint = None
    if style in MONO_TINTS:
        p = _find("tray", "tray-%s.svg" % style)
        if p:
            base = QIcon(p)
            tint = MONO_TINTS[style][1 if lit else 0]
    elif not lit:
        p = _find("tray", "tray-color-idle.svg")
        if p:
            base = QIcon(p)
    if base.isNull():
        base = default_app_icon()
    badge = BADGE_COLORS.get(state)
    if not tint and not badge:
        return base
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        src = base.pixmap(size, size)
        if src.isNull():
            continue
        pm = QPixmap(src)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if tint:
            # Recolor the glyph, keeping its shape (alpha).
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), QColor(tint))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if badge:
            r = max(3.0, size * 0.2)
            p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 24)))
            p.setBrush(QColor(badge))
            p.drawEllipse(QRectF(size - 2 * r - 0.5, size - 2 * r - 0.5, 2 * r, 2 * r))
        p.end()
        icon.addPixmap(pm)
    return icon if not icon.isNull() else base
