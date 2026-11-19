# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Explorer look: built-in light/dark styling plus a user theme folder for custom icons,
fonts, colors and extra stylesheet rules.

Theme folder: ~/.local/share/caracal/explorer-theme/
  icons/<slot>.svg|png|ico   override any icon slot (see ICON_SLOTS) or file type
                             (ext-pdf.png, ext-docx.svg, ...)
  fonts/*.ttf|otf            loaded at startup; pick the family in theme.json or Settings
  theme.json                 colors, accent, font, sizes (see DEFAULT_THEME)
  style.qss                  extra Qt stylesheet rules applied last
"""

import copy
import json
import os

from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QFileIconProvider, QStyle
from PySide6.QtCore import QFileInfo

# slot -> (what it's for, system icon names tried in order)
ICON_SLOTS = {
    "home": ("Home in the navigation pane", ["user-home", "go-home"]),
    "quick-access": ("Quick access", ["starred", "emblem-favorite", "bookmarks"]),
    "this-pc": ("This PC", ["computer", "computer-laptop"]),
    "network": ("Network", ["network-workgroup", "folder-network", "network-server"]),
    "desktop": ("Desktop folder", ["user-desktop", "desktop"]),
    "downloads": ("Downloads folder", ["folder-download", "folder-downloads"]),
    "documents": ("Documents folder", ["folder-documents"]),
    "pictures": ("Pictures folder", ["folder-pictures", "folder-images"]),
    "music": ("Music folder", ["folder-music"]),
    "videos": ("Videos folder", ["folder-videos"]),
    "trash": ("Recycle bin / Trash", ["user-trash", "trash-empty"]),
    "drive": ("Local disk", ["drive-harddisk", "drive-harddisk-system"]),
    "drive-removable": ("USB drive / removable disk", ["drive-removable-media-usb", "drive-removable-media"]),
    "drive-network": ("Connected network drive", ["folder-remote", "network-server"]),
    "drive-network-offline": ("Disconnected network drive", ["folder-remote-symbolic", "network-offline"]),
    "folder": ("Folder", ["folder"]),
    "file": ("Generic file", ["text-x-generic", "unknown"]),
    "back": ("Back button", ["go-previous"]),
    "forward": ("Forward button", ["go-next"]),
    "up": ("Up one level", ["go-up"]),
    "refresh": ("Refresh", ["view-refresh"]),
    "search": ("Search", ["system-search", "edit-find"]),
    "new": ("New menu", ["list-add", "document-new"]),
    "new-folder": ("New folder", ["folder-new"]),
    "new-tab": ("New tab", ["tab-new", "list-add"]),
    "cut": ("Cut", ["edit-cut"]),
    "copy": ("Copy", ["edit-copy"]),
    "paste": ("Paste", ["edit-paste"]),
    "rename": ("Rename", ["edit-rename", "edit-entry"]),
    "delete": ("Delete", ["edit-delete", "user-trash"]),
    "sort": ("Sort menu", ["view-sort", "view-sort-ascending"]),
    "view": ("View menu", ["view-list-icons", "view-grid"]),
    "more": ("More (···) menu", ["view-more-horizontal", "application-menu", "open-menu"]),
    "details-view": ("Details view toggle", ["view-list-details"]),
    "icons-view": ("Large icons view toggle", ["view-list-icons"]),
    "preview-pane": ("Preview pane toggle", ["view-split-left-right", "view-preview"]),
    "pin": ("Pin to Quick access", ["pin", "window-pin", "view-pin", "view-pin-symbolic",
                                     "pin-symbolic", "emblem-pin"]),
    "properties": ("Properties", ["document-properties"]),
    "open": ("Open", ["document-open"]),
    "terminal": ("Open in terminal", ["utilities-terminal"]),
    "eject": ("Eject", ["media-eject"]),
    "add-location": ("Add network location", ["list-add", "folder-new"]),
    "connect": ("Connect", ["network-connect"]),
    "disconnect": ("Disconnect", ["network-disconnect"]),
    "settings": ("Settings", ["configure", "preferences-system"]),
}

DEFAULT_THEME = {
    "mode": "system",            # system | light | dark
    "icon_theme": "",            # "" = desktop's icon theme; else a freedesktop icon theme name
    "font_family": "",           # "" = system font; e.g. a family from fonts/
    "font_size": 9,              # points; 0 = system size. Explorer uses 9 pt Segoe UI
    "accent": "#0067C0",
    "row_height": 20,
    "nav_width": 180,
    "corner_radius": 4,
    "light": {"group": "#1E3287", "chrome": "#F3F3F3", "content": "#FFFFFF", "text": "#1B1B1B",
              "subtle": "#616161", "hover": "#E5F3FF", "selected": "#CCE8FF",
              "selected_border": "#99D1FF", "border": "#E5E5E5", "field": "#FFFFFF"},
    "dark": {"group": "#E6E6E6", "chrome": "#2B2B2B", "content": "#191919", "text": "#F2F2F2",
             "subtle": "#A8A8A8", "hover": "#2D2D2D", "selected": "#434343",
             "selected_border": "#666666", "border": "#3A3A3A", "field": "#191919"},
}


def theme_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "caracal", "explorer-theme")


README = """Caracal Explorer theme folder
================================

Drop files here to change how Caracal Explorer looks, then use
Settings -> Appearance -> Reload theme (or restart).

icons/   One file per slot, named <slot>.svg, .png or .ico. Examples:
           this-pc.png  network.svg  folder.ico  drive.png  back.svg
         File types: ext-<extension>, e.g. ext-pdf.png, ext-docx.svg, ext-zip.ico
         Slots:
{slots}

fonts/   Any .ttf / .otf / .ttc files. Set "font_family" in theme.json (or choose it
         in Settings -> Appearance) to the font's family name.

theme.json   Colors and sizes. Missing keys use the defaults:
{defaults}

style.qss    Extra Qt stylesheet rules, applied after the built-in style.
             Object names you can target: #NavPane, #CommandBar, #AddressBar,
             #SearchBox, #FileView, #PreviewPane, #StatusBar, #ThisPC
"""


def ensure_theme_dir():
    d = theme_dir()
    for sub in ("icons", "fonts"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    readme = os.path.join(d, "README.txt")
    if not os.path.exists(readme):
        slots = "\n".join("           %-22s %s" % (k, v[0]) for k, v in ICON_SLOTS.items())
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(README.format(slots=slots, defaults=json.dumps(DEFAULT_THEME, indent=2)))
    tj = os.path.join(d, "theme.json")
    if not os.path.exists(tj):
        with open(tj, "w", encoding="utf-8") as fh:
            json.dump({"mode": "system", "font_family": "", "accent": "#0067C0"}, fh, indent=2)
    return d


class Theme:
    def __init__(self):
        self.reload()

    def reload(self):
        self.dir = theme_dir()
        self.data = copy.deepcopy(DEFAULT_THEME)
        try:
            with open(os.path.join(self.dir, "theme.json"), encoding="utf-8") as fh:
                user = json.load(fh)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except (OSError, ValueError):
            pass
        self.apply_icon_theme()
        self._icons = {}
        self._ext_icons = {}
        self.provider = QFileIconProvider()
        self.fonts = []
        fdir = os.path.join(self.dir, "fonts")
        if os.path.isdir(fdir):
            for f in sorted(os.listdir(fdir)):
                if f.lower().endswith((".ttf", ".otf", ".ttc")):
                    fid = QFontDatabase.addApplicationFont(os.path.join(fdir, f))
                    if fid >= 0:
                        self.fonts += QFontDatabase.applicationFontFamilies(fid)

    # -- icon theme (KDE / freedesktop icon packs) ----------------------------------

    _desktop_icon_theme = None

    def apply_icon_theme(self):
        """Use the chosen icon theme for Caracal only (the desktop keeps its own)."""
        if Theme._desktop_icon_theme is None:
            Theme._desktop_icon_theme = QIcon.themeName()
        paths = QIcon.themeSearchPaths()
        extra = imported_icon_dir()
        if extra not in paths:
            QIcon.setThemeSearchPaths(paths + [extra])
        name = self.data.get("icon_theme") or Theme._desktop_icon_theme or ""
        if name and name != QIcon.themeName():
            QIcon.setThemeName(name)
        # Icons a Windows pack doesn't have still come from the desktop's theme.
        if Theme._desktop_icon_theme and hasattr(QIcon, "setFallbackThemeName"):
            QIcon.setFallbackThemeName(Theme._desktop_icon_theme
                                       if name != Theme._desktop_icon_theme else "hicolor")

    # -- icons ---------------------------------------------------------------

    def _file(self, name):
        for ext in (".svg", ".png", ".ico", ".xpm"):
            p = os.path.join(self.dir, "icons", name + ext)
            if os.path.isfile(p):
                return p
        return None

    def has_icon(self, slot):
        """True if the theme folder or the system icon theme provides this slot."""
        if self._file(slot):
            return True
        return any(not QIcon.fromTheme(n).isNull() for n in ICON_SLOTS.get(slot, ("", [slot]))[1])

    def icon(self, slot):
        if slot in self._icons:
            return self._icons[slot]
        p = self._file(slot)
        icon = QIcon(p) if p else QIcon()
        if icon.isNull():
            for name in ICON_SLOTS.get(slot, ("", [slot]))[1]:
                icon = QIcon.fromTheme(name)
                if not icon.isNull():
                    break
        if icon.isNull():
            style = QApplication.style()
            fallback = {"folder": QStyle.SP_DirIcon, "file": QStyle.SP_FileIcon,
                        "back": QStyle.SP_ArrowBack, "forward": QStyle.SP_ArrowForward,
                        "up": QStyle.SP_ArrowUp, "refresh": QStyle.SP_BrowserReload,
                        "this-pc": QStyle.SP_ComputerIcon, "drive": QStyle.SP_DriveHDIcon,
                        "network": QStyle.SP_DriveNetIcon, "drive-network": QStyle.SP_DriveNetIcon,
                        "trash": QStyle.SP_TrashIcon, "home": QStyle.SP_DirHomeIcon,
                        "delete": QStyle.SP_TrashIcon}.get(slot, QStyle.SP_FileIcon)
            icon = style.standardIcon(fallback)
        self._icons[slot] = icon
        return icon

    def file_icon(self, info):
        """Icon for a QFileInfo, honoring folder / ext-<ext> overrides."""
        if info.isDir():
            p = self._file("folder")
            if p:
                return self.icon("folder")
            return self.provider.icon(info)
        ext = info.suffix().lower()
        if ext not in self._ext_icons:
            p = self._file("ext-" + ext) if ext else None
            self._ext_icons[ext] = QIcon(p) if p else None
        return self._ext_icons[ext] or self.provider.icon(info)

    # -- colors / stylesheet ------------------------------------------------------

    def is_dark(self):
        mode = self.data.get("mode", "system")
        if mode in ("light", "dark"):
            return mode == "dark"
        hints = QApplication.styleHints()
        if hasattr(hints, "colorScheme"):
            from PySide6.QtCore import Qt
            scheme = hints.colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                return scheme == Qt.ColorScheme.Dark
        pal = QApplication.palette()
        return pal.color(QPalette.Window).lightness() < 128

    def palette(self):
        """A full palette for Explorer windows, so unstyled widgets (dialogs, inputs,
        buttons) follow Explorer's light/dark choice instead of the desktop's."""
        c = self.colors()
        accent = QColor(self.data.get("accent", "#0067C0"))
        p = QPalette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            p.setColor(group, QPalette.Window, QColor(c["chrome"]))
            p.setColor(group, QPalette.WindowText, QColor(c["text"]))
            p.setColor(group, QPalette.Base, QColor(c["content"]))
            p.setColor(group, QPalette.AlternateBase, QColor(c["chrome"]))
            p.setColor(group, QPalette.Text, QColor(c["text"]))
            p.setColor(group, QPalette.Button, QColor(c["chrome"]))
            p.setColor(group, QPalette.ButtonText, QColor(c["text"]))
            p.setColor(group, QPalette.ToolTipBase, QColor(c["field"]))
            p.setColor(group, QPalette.ToolTipText, QColor(c["text"]))
            p.setColor(group, QPalette.PlaceholderText, QColor(c["subtle"]))
            p.setColor(group, QPalette.Highlight, accent)
            p.setColor(group, QPalette.HighlightedText, QColor("#FFFFFF"))
            p.setColor(group, QPalette.Link, accent)
            p.setColor(group, QPalette.Mid, QColor(c["border"]))
            p.setColor(group, QPalette.Light, QColor(c["content"]))
            p.setColor(group, QPalette.Dark, QColor(c["border"]))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            p.setColor(QPalette.Disabled, role, QColor(c["subtle"]))
        return p

    def colors(self):
        return self.data["dark" if self.is_dark() else "light"]

    def font(self, base):
        f = base
        if self.data.get("font_family"):
            f.setFamily(self.data["font_family"])
        if self.data.get("font_size"):
            f.setPointSizeF(float(self.data["font_size"]))
        return f

    def glyphs(self):
        """Small arrow images in the current colors (QSS can only use image files)."""
        c = self.colors()
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        d = os.path.join(base, "caracal", "ui", "dark" if self.is_dark() else "light")
        os.makedirs(d, exist_ok=True)
        stroke = c["subtle"]
        shapes = {"chev-right": "M6 4 L10 8 L6 12", "chev-down": "M4 6 L8 10 L12 6",
                  "sort-up": "M4 9 L8 5 L12 9", "sort-down": "M4 6 L8 10 L12 6"}
        out = {}
        for name, path in shapes.items():
            p = os.path.join(d, name + ".svg")
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                   '<path d="%s" fill="none" stroke="%s" stroke-width="1.2" '
                   'stroke-linecap="round" stroke-linejoin="round"/></svg>' % (path, stroke))
            try:
                if not os.path.exists(p) or open(p).read() != svg:
                    with open(p, "w") as fh:
                        fh.write(svg)
            except OSError:
                pass
            out[name] = p
        return out

    def stylesheet(self):
        c = self.colors()
        g = self.glyphs()
        r = int(self.data.get("corner_radius", 4))
        rh = int(self.data.get("row_height", 30))
        accent = self.data.get("accent", "#0067C0")
        css = f"""
        QMainWindow, #ExplorerRoot {{ background: {c['chrome']}; color: {c['text']}; }}
        #CommandBar, #AddressRow, #TabRow {{ background: {c['chrome']}; }}
        #CommandBar QToolButton, #AddressRow QToolButton {{
            background: transparent; border: 1px solid transparent; border-radius: {r}px;
            padding: 4px 8px; color: {c['text']}; }}
        #CommandBar QToolButton:hover, #AddressRow QToolButton:hover {{ background: {c['hover']}; }}
        #CommandBar QToolButton:pressed {{ background: {c['selected']}; }}
        #CommandBar QToolButton:disabled {{ color: {c['subtle']}; }}
        #AddressBar, #SearchBox {{ background: {c['field']}; border: 1px solid {c['border']};
            border-radius: 0px; min-height: 22px; max-height: 24px; color: {c['text']}; }}
        #AddressBar QToolButton {{ padding: 0px 3px; }}
        #AddressBar QToolButton {{ border: none; padding: 2px 4px; border-radius: {r}px; }}
        #AddressBar QToolButton:hover {{ background: {c['hover']}; }}
        #SearchBox:focus, #AddressBar QLineEdit:focus {{ border-bottom: 2px solid {accent}; }}
        #NavPane {{ background: {c['content']}; border: none; border-right: 1px solid {c['border']};
            color: {c['text']}; outline: 0; }}
        #NavPane::branch {{ background: transparent; }}
        #NavPane::branch:has-children:closed {{ image: url({g['chev-right']}); }}
        #NavPane::branch:has-children:open {{ image: url({g['chev-down']}); }}
        #FileView QHeaderView::up-arrow {{ image: url({g['sort-up']}); subcontrol-position: top center;
            width: 10px; height: 6px; }}
        #FileView QHeaderView::down-arrow {{ image: url({g['sort-down']}); subcontrol-position: top center;
            width: 10px; height: 6px; }}
        #RibbonTabs {{ background: {c['chrome']}; }}
        #RibbonTabs QToolButton {{ border: none; padding: 2px 10px; color: {c['text']}; }}
        #RibbonTabs QToolButton:hover {{ background: {c['hover']}; }}
        #RibbonTabs QToolButton:checked {{ background: {c['content']}; border: 1px solid {c['border']};
            border-bottom: none; }}
        QToolButton#RibbonFile {{ background-color: {accent}; color: #FFFFFF; padding: 2px 12px;
            border: none; }}
        QToolButton#RibbonFile:hover {{ background-color: {accent}; color: #FFFFFF; }}
        #RibbonPanel {{ background: {c['content']}; border-top: 1px solid {c['border']};
            border-bottom: 1px solid {c['border']}; }}
        #RibbonPanel QToolButton {{ border: 1px solid transparent; border-radius: 2px; padding: 2px 4px;
            color: {c['text']}; }}
        #RibbonPanel QToolButton:hover {{ background: {c['hover']}; border-color: {c['selected_border']}; }}
        #RibbonPanel QCheckBox {{ color: {c['text']}; }}
        #NavPane::item {{ min-height: {rh - 2}px; border-radius: {r}px; padding-left: 2px; }}
        #NavPane::item:hover {{ background: {c['hover']}; }}
        #NavPane::item:selected {{ background: {c['selected']}; color: {c['text']}; }}
        #FileView, #ThisPC {{ background: {c['content']}; border: none; color: {c['text']};
            alternate-background-color: {c['content']}; outline: 0; }}
        #FileView::item {{ min-height: {rh - 4}px; border: 1px solid transparent; }}
        #FileView::item:hover {{ background: {c['hover']}; }}
        #FileView::item:selected {{ background: {c['selected']}; border: 1px solid {c['selected_border']};
            color: {c['text']}; }}
        #FileView QHeaderView {{ background: {c['content']}; }}
        #FileView QHeaderView::section {{ background: {c['content']}; color: {c['subtle']};
            border: none; border-right: 1px solid {c['border']}; padding: 6px 6px 2px 6px;
            min-height: 22px; }}
        #PreviewPane {{ background: {c['content']}; border-left: 1px solid {c['border']}; color: {c['text']}; }}
        #StatusBar {{ background: {c['content']}; color: {c['subtle']}; min-height: 20px;
            max-height: 22px; }}
        #StatusBar::item {{ border: none; }}
        #StatusBar QLabel {{ color: {c['subtle']}; }}
        #PreviewPane QLabel, #ThisPC QLabel, #ExplorerRoot QLabel {{ color: {c['text']}; }}
        #PreviewPane QPlainTextEdit {{ background: {c['content']}; color: {c['text']}; border: none; }}
        #StatusBar QToolButton {{ border: none; padding: 2px; border-radius: {r}px; }}
        #StatusBar QToolButton:checked {{ background: {c['selected']}; }}
        QTabBar#Tabs::tab {{ background: transparent; color: {c['text']}; padding: 6px 14px;
            border-top-left-radius: {r + 2}px; border-top-right-radius: {r + 2}px; min-width: 120px;
            max-width: 240px; }}
        QTabBar#Tabs::tab:selected {{ background: {c['content']}; }}
        QTabBar#Tabs::tab:hover:!selected {{ background: {c['hover']}; }}
        QSplitter::handle {{ background: {c['border']}; }}
        QScrollBar:vertical {{ background: {c['chrome']}; width: 17px; margin: 0; }}
        QScrollBar:horizontal {{ background: {c['chrome']}; height: 17px; margin: 0; }}
        QScrollBar::handle {{ background: {c['border'] if self.is_dark() else '#CDCDCD'};
            min-height: 24px; min-width: 24px; margin: 2px 4px; }}
        QScrollBar::handle:horizontal {{ margin: 4px 2px; }}
        QScrollBar::handle:hover {{ background: {c['subtle']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QDialog {{ background: {c['chrome']}; color: {c['text']}; }}
        QDialog QLineEdit, QDialog QSpinBox, QDialog QComboBox, QDialog QPlainTextEdit,
        QDialog QListWidget {{ background: {c['content']}; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: {r}px; padding: 2px 4px; }}
        QDialog QPushButton {{ background: {c['content']}; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: {r}px; padding: 4px 14px; }}
        QDialog QPushButton:hover {{ background: {c['hover']}; }}
        QDialog QLabel, QDialog QCheckBox, QDialog QGroupBox {{ color: {c['text']}; }}
        QDialog QTabWidget::pane {{ background: {c['content']}; border: 1px solid {c['border']};
            border-radius: {r}px; }}
        QDialog QTabWidget > QStackedWidget > QWidget {{ background: {c['content']}; }}
        QDialog QTabBar::tab {{ background: {c['chrome']}; color: {c['text']}; padding: 5px 12px;
            border: 1px solid {c['border']}; border-bottom: none; border-top-left-radius: {r}px;
            border-top-right-radius: {r}px; }}
        QDialog QTabBar::tab:selected {{ background: {c['content']}; }}
        QMenu {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['border']};
            border-radius: {r + 2}px; padding: 4px; }}
        QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: {r}px; }}
        QMenu::item:selected {{ background: {c['hover']}; }}
        QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
        """
        extra = os.path.join(self.dir, "style.qss")
        try:
            with open(extra, encoding="utf-8") as fh:
                css += "\n" + fh.read()
        except OSError:
            pass
        return css


_theme = None


def get():
    global _theme
    if _theme is None:
        _theme = Theme()
    return _theme


def reload():
    get().reload()
    return get()


def file_info_icon(path):
    return get().file_icon(QFileInfo(path))


def color(name):
    return QColor(get().colors()[name])


# --------------------------------------------------------------------------
# importing KDE / freedesktop theme downloads
# --------------------------------------------------------------------------

def imported_icon_dir():
    return os.path.join(theme_dir(), "icon-themes")


def _icon_theme_name(index_path):
    """Display name of an icon theme from its index.theme, or None if it isn't one."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(index_path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None
    if not cp.has_section("Icon Theme") or not cp.has_option("Icon Theme", "Directories"):
        return None
    return cp.get("Icon Theme", "Name", fallback=os.path.basename(os.path.dirname(index_path)))


def list_icon_themes():
    """[(theme folder name, display name)] for every installed and imported icon theme."""
    seen = {}
    for base in QIcon.themeSearchPaths() + [imported_icon_dir()]:
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            idx = os.path.join(base, d, "index.theme")
            if d in seen or d in ("hicolor", "default", "locolor") or not os.path.isfile(idx):
                continue
            name = _icon_theme_name(idx)
            if name:
                seen[d] = name
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def parse_kde_colors(path):
    """Map a KDE .colors scheme onto Explorer colors. Returns (mode, colors) or None."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None

    def col(section, key, default=None):
        try:
            parts = [int(x) for x in cp.get(section, key).split(",")[:3]]
            return "#%02X%02X%02X" % tuple(parts)
        except (configparser.Error, ValueError, TypeError):
            return default
    view_bg = col("Colors:View", "BackgroundNormal")
    if not view_bg:
        return None
    win_bg = col("Colors:Window", "BackgroundNormal", view_bg)
    text = col("Colors:View", "ForegroundNormal", "#000000")
    sel = col("Colors:Selection", "BackgroundNormal", "#0067C0")
    hover = col("Colors:View", "DecorationHover", sel)
    subtle = col("Colors:View", "ForegroundInactive", text)
    alt = col("Colors:View", "BackgroundAlternate", win_bg)
    dark = QColor(view_bg).lightness() < 128
    base = DEFAULT_THEME["dark" if dark else "light"]

    def mix(a, b, t):
        ca, cb = QColor(a), QColor(b)
        return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                      int(ca.green() + (cb.green() - ca.green()) * t),
                      int(ca.blue() + (cb.blue() - ca.blue()) * t)).name().upper()
    colors = dict(base)
    colors.update({"content": view_bg, "chrome": win_bg, "field": view_bg, "text": text,
                   "subtle": subtle, "border": mix(alt, text, 0.15),
                   "selected": mix(view_bg, sel, 0.45), "selected_border": sel,
                   "hover": mix(view_bg, hover, 0.2), "group": text if dark else mix(text, sel, 0.6)})
    return ("dark" if dark else "light"), colors, sel


MAX_IMPORT = 400 * 1024 * 1024


def _safe_extract(archive, dest):
    import tarfile
    import zipfile
    real = os.path.realpath(dest)
    low = archive.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            total = 0
            for m in zf.infolist():
                total += m.file_size
                t = os.path.realpath(os.path.join(dest, m.filename))
                if not (t == real or t.startswith(real + os.sep)):
                    raise OSError("Unsafe path in archive: %s" % m.filename)
            if total > MAX_IMPORT:
                raise OSError("The archive is too large to import.")
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            if sum(m.size for m in tf.getmembers()) > MAX_IMPORT:
                raise OSError("The archive is too large to import.")
            tf.extractall(dest, filter="data")


def import_theme(source):
    """Import a theme download (archive, .colors file or folder) into the theme folder.

    Picks up freedesktop icon themes (folders with index.theme), KDE color schemes
    (*.colors), fonts, and loose icons named after slots. Returns a summary dict:
    {"icon_themes": [names], "colors": [scheme files], "fonts": n, "icons": n}.
    """
    import shutil
    import tempfile
    d = ensure_theme_dir()
    summary = {"icon_themes": [], "colors": [], "fonts": 0, "icons": 0}
    with tempfile.TemporaryDirectory(prefix="caracal-theme-") as tmp:
        if os.path.isdir(source):
            root = source
        elif source.lower().endswith(".colors"):
            root = tmp
            shutil.copy2(source, tmp)
        else:
            _safe_extract(source, tmp)
            root = tmp
        icon_dst = imported_icon_dir()
        os.makedirs(icon_dst, exist_ok=True)
        theme_dirs = set()
        for dirpath, dirs, files in os.walk(root):
            if "index.theme" in files and _icon_theme_name(os.path.join(dirpath, "index.theme")):
                name = os.path.basename(dirpath.rstrip("/")) or "imported"
                target = os.path.join(icon_dst, name)
                if os.path.exists(target):
                    shutil.rmtree(target)
                shutil.copytree(dirpath, target, symlinks=True)
                summary["icon_themes"].append(name)
                theme_dirs.add(dirpath)
                dirs[:] = []         # don't descend into the icon theme itself
                continue
            if any(dirpath.startswith(t + os.sep) for t in theme_dirs):
                continue
            for f in files:
                p = os.path.join(dirpath, f)
                low = f.lower()
                if low.endswith(".colors"):
                    summary["colors"].append(p)
                elif low.endswith((".ttf", ".otf", ".ttc")):
                    shutil.copy2(p, os.path.join(d, "fonts", f))
                    summary["fonts"] += 1
                elif low.endswith((".svg", ".png", ".ico")) and \
                        (os.path.splitext(low)[0] in ICON_SLOTS or low.startswith("ext-")):
                    shutil.copy2(p, os.path.join(d, "icons", low))
                    summary["icons"] += 1
        # Apply: first icon theme, first color scheme.
        tj = os.path.join(d, "theme.json")
        try:
            with open(tj, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if summary["icon_themes"]:
            data["icon_theme"] = summary["icon_themes"][0]
        if summary["colors"]:
            parsed = parse_kde_colors(summary["colors"][0])
            if parsed:
                mode, colors, accent = parsed
                data["mode"] = mode
                data[mode] = colors
                data["accent"] = accent
            summary["colors"] = [os.path.basename(c) for c in summary["colors"]]
        with open(tj, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    return summary
