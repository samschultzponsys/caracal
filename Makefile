# Caracal install rules, used by the RPM spec and debian/rules.
PREFIX ?= /usr
DESTDIR ?=
DATADIR = $(DESTDIR)$(PREFIX)/share
APPID = com.spazcat.Caracal

all:

test:
	python3 -m unittest -v

install:
	install -d $(DATADIR)/caracal/caracal
	install -m 0644 caracal/*.py $(DATADIR)/caracal/caracal/
	install -d $(DATADIR)/caracal/caracal/explorer
	install -m 0644 caracal/explorer/*.py $(DATADIR)/caracal/caracal/explorer/
	install -m 0644 CHANGELOG.md $(DATADIR)/caracal/CHANGELOG.md
	install -D -m 0755 bin/caracal $(DESTDIR)$(PREFIX)/bin/caracal
	ln -sf caracal $(DESTDIR)$(PREFIX)/bin/catamount
	install -D -m 0755 helper/caracal-helper $(DESTDIR)$(PREFIX)/libexec/caracal/caracal-helper
	install -D -m 0644 data/$(APPID).policy $(DATADIR)/polkit-1/actions/$(APPID).policy
	install -D -m 0644 data/$(APPID).desktop $(DATADIR)/applications/$(APPID).desktop
	install -D -m 0644 data/$(APPID).Explorer.desktop $(DATADIR)/applications/$(APPID).Explorer.desktop
	install -D -m 0644 data/$(APPID).metainfo.xml $(DATADIR)/metainfo/$(APPID).metainfo.xml
	install -D -m 0644 data/icons/hicolor/scalable/apps/$(APPID).svg $(DATADIR)/icons/hicolor/scalable/apps/$(APPID).svg
	install -D -m 0644 data/icons/hicolor/symbolic/apps/$(APPID)-symbolic.svg $(DATADIR)/icons/hicolor/symbolic/apps/$(APPID)-symbolic.svg
	install -D -m 0644 data/search/com.spazcat.Caracal.SearchProvider.service $(DATADIR)/dbus-1/services/com.spazcat.Caracal.SearchProvider.service
	install -D -m 0644 data/search/com.spazcat.Caracal.krunner.desktop $(DATADIR)/krunner/dbusplugins/com.spazcat.Caracal.desktop
	install -D -m 0644 data/search/com.spazcat.Caracal.search-provider.ini $(DATADIR)/gnome-shell/search-providers/com.spazcat.Caracal.search-provider.ini
	install -D -m 0644 data/search/com.spazcat.Caracal.servicemenu.desktop $(DATADIR)/kio/servicemenus/com.spazcat.Caracal.search.desktop
	install -D -m 0644 data/search/caracal-search.nemo_action $(DATADIR)/nemo/actions/caracal-search.nemo_action
	install -D -m 0644 data/search/caracal-search-background.nemo_action $(DATADIR)/nemo/actions/caracal-search-background.nemo_action
	install -D -m 0644 data/search/caracal_search_nautilus.py $(DATADIR)/nautilus-python/extensions/caracal_search.py
	install -D -m 0644 data/search/caracal_search_nautilus.py $(DATADIR)/caja-python/extensions/caracal_search.py
	install -d $(DATADIR)/caracal/icons/tray
	install -m 0644 data/icons/tray/*.svg $(DATADIR)/caracal/icons/tray/
	install -d $(DATADIR)/caracal/icons/app
	install -m 0644 data/icons/app/*.svg $(DATADIR)/caracal/icons/app/

.PHONY: all test install
