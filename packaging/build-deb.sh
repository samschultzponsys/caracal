#!/bin/bash
# Build the DEB inside a Debian container. Run from the repo root.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update -q
apt-get install -y -q --no-install-recommends build-essential dpkg-dev debhelper make
rm -rf /build && mkdir -p /build/caracal dist
tar --exclude=./.git --exclude=./dist --exclude=./.cache -cf - . | tar -xf - -C /build/caracal
(cd /build/caracal && dpkg-buildpackage -us -uc -b)
cp /build/*_all.deb dist/
chmod -R a+rX /var/cache/apt/archives || true
