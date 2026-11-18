Name:           catamount
Version:        0.3
Release:        1
Summary:        Map SMB and NFS network drives as persistent systemd mounts
License:        Apache-2.0
URL:            https://github.com/samschultzponsys/catamount
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
# GUI: Python 3 + PySide6 (QtCore/QtGui/QtWidgets/QtNetwork) + SVG icon support
Requires:       python3
Requires:       python3-pyside6
Requires:       qt6-qtsvg
# Privileged changes (pkexec + polkit policy) and systemd mount units
Requires:       polkit
Requires:       systemd
# Kernel mount helpers for SMB and NFS
Requires:       cifs-utils
Requires:       nfs-utils
# Desktop search service (KRunner / GNOME Shell) and PDF text for content search
Recommends:     python3-gobject
Recommends:     poppler-utils
Suggests:       nautilus-python
# Resolve .local (mDNS) server names
Recommends:     nss-mdns
# Browse the network and test connections (optional)
Recommends:     avahi-tools
Recommends:     samba-client
# GNOME has no tray without this extension (KDE and most others have one built in)
Recommends:     (gnome-shell-extension-appindicator if gnome-shell)

%description
CataMount works like "Map network drive" on Windows. It creates persistent
systemd .mount/.automount units for SMB and NFS shares, stores SMB passwords
in root-only credential files, and never edits /etc/fstab.

%prep
%autosetup

%build

%install
make install DESTDIR=%{buildroot} PREFIX=%{_prefix}

%files
%license LICENSE NOTICE THIRD_PARTY_LICENSES.md
%doc README.md CHANGELOG.md
%{_bindir}/catamount
%{_libexecdir}/catamount/
%{_datadir}/catamount/
%{_datadir}/polkit-1/actions/com.spazcat.CataMount.policy
%{_datadir}/applications/com.spazcat.CataMount.desktop
%{_metainfodir}/com.spazcat.CataMount.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/com.spazcat.CataMount.svg
%{_datadir}/icons/hicolor/symbolic/apps/com.spazcat.CataMount-symbolic.svg
%{_datadir}/dbus-1/services/com.spazcat.CataMount.SearchProvider.service
%{_datadir}/krunner/dbusplugins/com.spazcat.CataMount.desktop
%{_datadir}/gnome-shell/search-providers/com.spazcat.CataMount.search-provider.ini
%{_datadir}/kio/servicemenus/com.spazcat.CataMount.search.desktop
%{_datadir}/nemo/actions/catamount-search.nemo_action
%{_datadir}/nemo/actions/catamount-search-background.nemo_action
%{_datadir}/nautilus-python/extensions/catamount_search.py
%{_datadir}/caja-python/extensions/catamount_search.py
