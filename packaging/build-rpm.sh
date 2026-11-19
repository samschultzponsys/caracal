#!/bin/bash
# Build the RPM inside a Fedora container. Run from the repo root.
set -euo pipefail
V=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' caracal/__init__.py)
N=$(sed -n 's/^Name:[[:space:]]*//p' packaging/caracal.spec)
echo "keepcache=True" >> /etc/dnf/dnf.conf
dnf -y -q install rpm-build make tar gzip
mkdir -p ~/rpmbuild/SOURCES dist
tar --exclude=./.git --exclude=./dist --exclude=./.cache \
    --transform="s,^\.,$N-$V," -czf ~/rpmbuild/SOURCES/$N-$V.tar.gz .
rpmbuild -bb packaging/caracal.spec
cp ~/rpmbuild/RPMS/noarch/$N-$V-*.noarch.rpm dist/
chmod -R a+rX /var/cache/dnf || true
