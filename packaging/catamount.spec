Name:           catamount
Version:        0.1
Release:        1
Summary:        Map SMB and NFS network drives as persistent systemd mounts
License:        Apache-2.0
URL:            https://github.com/samschultzponsys/catamount
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
Requires:       python3
Requires:       python3-pyside6
Requires:       polkit
Requires:       systemd
Requires:       cifs-utils
Requires:       nfs-utils

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
