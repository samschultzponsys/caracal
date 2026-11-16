# CataMount install rules, used by the RPM spec and debian/rules.
PREFIX ?= /usr
DESTDIR ?=
DATADIR = $(DESTDIR)$(PREFIX)/share
APPID = com.spazcat.CataMount

all:

test:
	python3 -m unittest -v

install:
	install -d $(DATADIR)/catamount/catamount
	install -m 0644 catamount/*.py $(DATADIR)/catamount/catamount/
	install -m 0644 CHANGELOG.md $(DATADIR)/catamount/CHANGELOG.md
	install -D -m 0755 bin/catamount $(DESTDIR)$(PREFIX)/bin/catamount
	install -D -m 0755 helper/catamount-helper $(DESTDIR)$(PREFIX)/libexec/catamount/catamount-helper
	install -D -m 0644 data/$(APPID).policy $(DATADIR)/polkit-1/actions/$(APPID).policy
	install -D -m 0644 data/$(APPID).desktop $(DATADIR)/applications/$(APPID).desktop
	install -D -m 0644 data/$(APPID).metainfo.xml $(DATADIR)/metainfo/$(APPID).metainfo.xml
	install -D -m 0644 data/icons/hicolor/scalable/apps/$(APPID).svg $(DATADIR)/icons/hicolor/scalable/apps/$(APPID).svg
	install -D -m 0644 data/icons/hicolor/symbolic/apps/$(APPID)-symbolic.svg $(DATADIR)/icons/hicolor/symbolic/apps/$(APPID)-symbolic.svg
	install -d $(DATADIR)/catamount/icons/tray
	install -m 0644 data/icons/tray/*.svg $(DATADIR)/catamount/icons/tray/

.PHONY: all test install
