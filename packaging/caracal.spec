Name:           caracal
Version:        2.0
Release:        1
Summary:        Map SMB and NFS network drives as persistent systemd mounts
License:        Apache-2.0
URL:            https://github.com/samschultzponsys/caracal
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
# Renamed from CataMount (package "catamount", up to 0.7): replace it on upgrade.
Obsoletes:      catamount < 0.8
Provides:       catamount = %{version}-%{release}

BuildRequires:  make
# GUI: Python 3 + PySide6 (QtCore/QtGui/QtWidgets/QtNetwork) + SVG icon support
Requires:       python3
Requires:       python3-pyside6
Requires:       qt6-qtsvg
# Privileged changes (pkexec + polkit policy) and systemd mount units
Requires:       polkit
Requires:       systemd
# pkill, to restart running copies after an upgrade
Requires(posttrans): procps-ng
# Kernel mount helpers for SMB and NFS
Requires:       cifs-utils
Requires:       nfs-utils
# Desktop search service (KRunner / GNOME Shell) and PDF text for content search
Recommends:     python3-gobject
Recommends:     poppler-utils
Suggests:       nautilus-python
# Music/video details columns in Explorer (ffprobe)
Suggests:       ffmpeg-free
# 7-Zip commands in Explorer's right-click menu (runs the 7-Zip command-line program)
Recommends:     (7zip or p7zip-plugins)
# Explorer: SFTP/SCP and FTP/WebDAV locations, USB drives, default file manager
Recommends:     fuse-sshfs
Recommends:     rclone
Recommends:     udisks2
Recommends:     xdg-utils
# Resolve .local (mDNS) server names
Recommends:     nss-mdns
# Browse the network and test connections (optional)
Recommends:     avahi-tools
Recommends:     samba-client
# GNOME has no tray without this extension (KDE and most others have one built in)
Recommends:     (gnome-shell-extension-appindicator if gnome-shell)

%description
Caracal works like "Map network drive" on Windows. It creates persistent
systemd .mount/.automount units for SMB and NFS shares, stores SMB passwords
in root-only credential files, and never edits /etc/fstab.

%prep
%autosetup

%build

%install
make install DESTDIR=%{buildroot} PREFIX=%{_prefix}

%post
# Watchdog: retries failed network drives every few minutes and unmounts dead shares.
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl enable --now caracal-watchdog.timer >/dev/null 2>&1 || :

%preun
if [ $1 -eq 0 ]; then
    systemctl disable --now caracal-watchdog.timer >/dev/null 2>&1 || :
fi

%posttrans
# Caracal keeps running in the background (explorer mode, search, "Show in folder"), so a
# running copy still has the old code loaded. Tell it to restart into the new version
# (1.3+ restarts itself and reopens its windows; older versions simply exit).
pkill -USR1 -x caracal >/dev/null 2>&1 || :
pkill -USR1 -x catamount >/dev/null 2>&1 || :

%files
%license LICENSE NOTICE THIRD_PARTY_LICENSES.md
%doc README.md CHANGELOG.md
%{_bindir}/caracal
%{_bindir}/catamount
%{_libexecdir}/caracal/
%{_prefix}/lib/systemd/system/caracal-watchdog.service
%{_prefix}/lib/systemd/system/caracal-watchdog.timer
%{_datadir}/caracal/
%{_datadir}/polkit-1/actions/com.spazcat.Caracal.policy
%{_datadir}/applications/com.spazcat.Caracal.desktop
%{_datadir}/applications/com.spazcat.Caracal.Explorer.desktop
%{_metainfodir}/com.spazcat.Caracal.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/com.spazcat.Caracal.svg
%{_datadir}/icons/hicolor/symbolic/apps/com.spazcat.Caracal-symbolic.svg
%{_datadir}/dbus-1/services/com.spazcat.Caracal.SearchProvider.service
%{_datadir}/krunner/dbusplugins/com.spazcat.Caracal.desktop
%{_datadir}/gnome-shell/search-providers/com.spazcat.Caracal.search-provider.ini
%{_datadir}/kio/servicemenus/com.spazcat.Caracal.search.desktop
%{_datadir}/nemo/actions/caracal-search.nemo_action
%{_datadir}/nemo/actions/caracal-search-background.nemo_action
%{_datadir}/nautilus-python/extensions/caracal_search.py
%{_datadir}/caja-python/extensions/caracal_search.py
