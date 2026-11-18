# Copyright 2026 Spazcat Enterprises
# SPDX-License-Identifier: Apache-2.0
"""Explorer look: built-in light/dark styling plus a user theme folder for custom icons,
fonts, colors and extra stylesheet rules.

Theme folder: ~/.local/share/catamount/explorer-theme/
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
    "font_family": "",           # "" = system font; e.g. a family from fonts/
    "font_size": 0,              # 0 = system size (points)
    "accent": "#0067C0",
    "row_height": 22,
    "nav_width": 210,
    "corner_radius": 4,
    "light": {"chrome": "#F3F3F3", "content": "#FFFFFF", "text": "#1B1B1B",
              "subtle": "#616161", "hover": "#E5F3FF", "selected": "#CCE8FF",
              "selected_border": "#99D1FF", "border": "#E5E5E5", "field": "#FFFFFF"},
    "dark": {"chrome": "#2B2B2B", "content": "#191919", "text": "#F2F2F2",
             "subtle": "#A8A8A8", "hover": "#2D2D2D", "selected": "#434343",
             "selected_border": "#666666", "border": "#3A3A3A", "field": "#191919"},
}


def theme_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "catamount", "explorer-theme")


README = """CataMount Explorer theme folder
================================

Drop files here to change how CataMount Explorer looks, then use
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

    def stylesheet(self):
        c = self.colors()
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
            border-radius: {r}px; min-height: 28px; color: {c['text']}; }}
        #AddressBar QToolButton {{ border: none; padding: 2px 4px; border-radius: {r}px; }}
        #AddressBar QToolButton:hover {{ background: {c['hover']}; }}
        #SearchBox:focus, #AddressBar QLineEdit:focus {{ border-bottom: 2px solid {accent}; }}
        #NavPane {{ background: {c['content']}; border: none; border-right: 1px solid {c['border']};
            color: {c['text']}; outline: 0; }}
        #RibbonTabs {{ background: {c['chrome']}; }}
        #RibbonTabs QToolButton {{ border: none; padding: 4px 14px; color: {c['text']}; }}
        #RibbonTabs QToolButton:hover {{ background: {c['hover']}; }}
        #RibbonTabs QToolButton:checked {{ background: {c['content']}; border: 1px solid {c['border']};
            border-bottom: none; }}
        QToolButton#RibbonFile {{ background-color: {accent}; color: #FFFFFF; padding: 4px 14px;
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
            border: none; border-right: 1px solid {c['border']}; padding: 4px 8px; }}
        #PreviewPane {{ background: {c['content']}; border-left: 1px solid {c['border']}; color: {c['text']}; }}
        #StatusBar {{ background: {c['chrome']}; color: {c['subtle']}; }}
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
        QScrollBar:vertical {{ background: {c['content']}; width: 12px; margin: 0; }}
        QScrollBar:horizontal {{ background: {c['content']}; height: 12px; margin: 0; }}
        QScrollBar::handle {{ background: {c['subtle']}; border-radius: 3px; min-height: 24px;
            min-width: 24px; margin: 3px; }}
        QScrollBar::handle:hover {{ background: {c['text']}; }}
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
