#!/bin/bash
# Fail if the version differs between the app, the RPM spec, debian/changelog,
# metainfo, CHANGELOG.md, or (optionally) the given tag (e.g. v0.1).
set -euo pipefail
app=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' catamount/__init__.py)
rpm=$(sed -n 's/^Version:[[:space:]]*//p' packaging/catamount.spec)
deb=$(head -1 debian/changelog | sed -n 's/^catamount (\([^)]*\)).*/\1/p')
meta=$(grep -o '<release version="[^"]*"' data/com.spazcat.CataMount.metainfo.xml | head -1 | cut -d'"' -f2)
cl=$(grep -m1 -o '^## \[[0-9.]*\]' CHANGELOG.md | tr -d '#[] ')
echo "app=$app rpm=$rpm deb=$deb metainfo=$meta changelog=$cl tag=${1:-none}"
for v in "$rpm" "$deb" "$meta" "$cl"; do [ "$v" = "$app" ] || { echo "Version mismatch"; exit 1; }; done
if [ -n "${1:-}" ] && [ "${1#v}" != "$app" ]; then echo "Tag $1 does not match $app"; exit 1; fi
