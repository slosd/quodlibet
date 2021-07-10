# Copyright 2004-2007 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2009-2010 Steven Robertson
#           2012-2018 Nick Boultbee
#           2009-2014 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from __future__ import absolute_import

import os

from gi.repository import Gtk, Pango, Gdk, Gio, GLib

from .prefs import Preferences, DEFAULT_PATTERN_TEXT
from quodlibet.browsers.albums.models import (AlbumModel,
    AlbumFilterModel, AlbumSortModel)
from quodlibet.browsers.albums.main import (get_cover_size,
    AlbumTagCompletion, PreferencesButton as AlbumPreferencesButton)
from quodlibet.browsers.covergrid.model import AlbumListModel

import quodlibet
from quodlibet import app
from quodlibet import ngettext
from quodlibet import config
from quodlibet import qltk
from quodlibet import util
from quodlibet import _
from quodlibet.browsers import Browser
from quodlibet.browsers._base import DisplayPatternMixin
from quodlibet.query import Query
from quodlibet.qltk.information import Information
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.x import MenuItem, Align, ScrolledWindow, RadioMenuItem
from quodlibet.qltk.x import SymbolicIconImage
from quodlibet.qltk.searchbar import SearchBarBox
from quodlibet.qltk.menubutton import MenuButton
from quodlibet.qltk import Icons
from quodlibet.util import connect_destroy
from quodlibet.util.library import background_filter
from quodlibet.util import connect_obj
from quodlibet.qltk.cover import get_no_cover_pixbuf
from quodlibet.qltk.image import add_border_widget, get_surface_for_pixbuf
from quodlibet.qltk import popup_menu_at_widget


class PreferencesButton(AlbumPreferencesButton):
    def __init__(self, browser, model):
        Gtk.HBox.__init__(self)

        sort_orders = [
            (_("_Title"), self.__compare_title),
            (_("_Artist"), self.__compare_artist),
            (_("_Date"), self.__compare_date),
            (_("_Genre"), self.__compare_genre),
            (_("_Rating"), self.__compare_rating),
        ]

        menu = Gtk.Menu()

        sort_item = Gtk.MenuItem(
            label=_(u"Sort _by…"), use_underline=True)
        sort_menu = Gtk.Menu()

        active = config.getint('browsers', 'album_sort', 1)

        item = None
        for i, (label, func) in enumerate(sort_orders):
            item = RadioMenuItem(group=item, label=label,
                                 use_underline=True)
            model.set_sort_func(100 + i, func)
            if i == active:
                model.set_sort_column_id(100 + i, Gtk.SortType.ASCENDING)
                item.set_active(True)
            item.connect("toggled",
                         util.DeferredSignal(self.__sort_toggled_cb),
                         model, i)
            sort_menu.append(item)

        sort_item.set_submenu(sort_menu)
        menu.append(sort_item)

        pref_item = MenuItem(_("_Preferences"), Icons.PREFERENCES_SYSTEM)
        menu.append(pref_item)
        connect_obj(pref_item, "activate", Preferences, browser)

        menu.show_all()

        button = MenuButton(
                SymbolicIconImage(Icons.EMBLEM_SYSTEM, Gtk.IconSize.MENU),
                arrow=True)
        button.set_menu(menu)
        self.pack_start(button, True, True, 0)


class AlbumWidget(Gtk.Box):

    def __init__(self, cover_width, padding, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
              hexpand=True, vexpand=True, margin=padding, **kwargs)

        self.__width = cover_width + 2 * padding

        self._image = Gtk.Image(
            width_request=cover_width,
            height_request=cover_width)
        self._label = Gtk.Label(
            ellipsize=Pango.EllipsizeMode.END,
            justify=Gtk.Justification.CENTER)

        self.pack_start(self._image, True, True, 0)
        self.pack_start(self._label, True, True, 0)

    def set_width(self, width):
        self.__width = width + 2 * self.props.margin

    def set_markup(self, markup):
        self._label.set_markup(markup)

    def set_cover(self, surface):
        self._image.props.surface = surface

    def do_get_preferred_width(self):
        return (self.__width, self.__width)


class CoverGrid(Browser, util.InstanceTracker, DisplayPatternMixin):
    __gsignals__ = Browser.__gsignals__
    __model = None

    _PATTERN_FN = os.path.join(quodlibet.get_user_dir(), "album_pattern")
    _DEFAULT_PATTERN_TEXT = DEFAULT_PATTERN_TEXT
    STAR = ["~people", "album"]

    name = _("Cover Grid")
    accelerated_name = _("_Cover Grid")
    keys = ["CoverGrid"]
    priority = 5

    def pack(self, songpane):
        container = self.songcontainer
        container.pack1(self, True, False)
        container.pack2(songpane, True, False)
        return container

    def unpack(self, container, songpane):
        container.remove(songpane)
        container.remove(self)

    @classmethod
    def init(klass, library):
        super(CoverGrid, klass).load_pattern()

    def finalize(self, restored):
        if not restored:
            # Select the "All Albums" album, which is None
            self.select_by_func(lambda r: r[0].album is None, one=True)

    @classmethod
    def _destroy_model(klass):
        klass.__model.destroy()
        klass.__model = None

    @classmethod
    def toggle_text(klass):
        on = config.getboolean("browsers", "album_text", True)
        for covergrid in klass.instances():
            covergrid.__text_cells.set_visible(on)
            covergrid.view.queue_resize()

    @classmethod
    def toggle_wide(klass):
        wide = config.getboolean("browsers", "covergrid_wide", False)
        for covergrid in klass.instances():
            covergrid.songcontainer.set_orientation(
                Gtk.Orientation.HORIZONTAL if wide
                else Gtk.Orientation.VERTICAL)

    @classmethod
    def update_mag(klass):
        mag = config.getfloat("browsers", "covergrid_magnification", 3.)
        for covergrid in klass.instances():
            covergrid.update_covergrid_magnification(mag)

    def redraw(self):
        model = self.__model
        for iter_, item in model.iterrows():
            album = item.album
            if album is not None:
                item.scanned = False
                model.row_changed(model.get_path(iter_), iter_)

    @classmethod
    def _init_model(klass, library):
        klass.__model = AlbumModel(library)
        klass.__library = library

    @classmethod
    def _refresh_albums(klass, albums):
        """We signal all other open album views that we changed something
        (Only needed for the cover atm) so they redraw as well."""
        if klass.__library:
            klass.__library.albums.refresh(albums)

    @util.cached_property
    def _no_cover(self):
        """Returns a cairo surface representing a missing cover"""

        mag = config.getfloat("browsers", "covergrid_magnification", 3.)

        cover_size = get_cover_size()
        scale_factor = self.get_scale_factor() * mag
        pb = get_no_cover_pixbuf(cover_size, cover_size, scale_factor)
        return get_surface_for_pixbuf(self, pb)

    def __init__(self, library):
        Browser.__init__(self, spacing=6)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.songcontainer = qltk.paned.ConfigRVPaned(
            "browsers", "covergrid_pos", 0.4)
        if config.getboolean("browsers", "covergrid_wide", False):
            self.songcontainer.set_orientation(Gtk.Orientation.HORIZONTAL)

        self._register_instance()
        if self.__model is None:
            self._init_model(library)

        self._cover_cancel = Gio.Cancellable()

        self.scrollwin = sw = ScrolledWindow()
        sw.set_shadow_type(Gtk.ShadowType.IN)
        model_sort = AlbumSortModel(model=self.__model)
        model_filter = AlbumFilterModel(child_model=model_sort)
        model_list = AlbumListModel(model_filter)
        self.__bg_filter = background_filter()
        self.__filter = None
        model_filter.set_visible_func(self.__parse_query)

        mag = config.getfloat("browsers", "covergrid_magnification", 3.)
        cover_size = get_cover_size() * mag
        item_padding = config.getint("browsers", "item_padding", 6)

        def create_widget(item):
            widget = AlbumWidget(cover_size, item_padding, has_tooltip=True)

            def setup():
                if not item.album:
                    surface = None
                elif item.cover:
                    pixbuf = item.cover
                    pixbuf = add_border_widget(pixbuf, self.view)
                    surface = get_surface_for_pixbuf(self, pixbuf)
                else:
                    surface = self._no_cover
                widget.set_cover(surface)
                widget.set_markup(self.__album_markup(item.album))

            def scan_cover(widget, cr):
                widget.disconnect(draw_handler_id)
                item.scan_cover(
                    scale_factor=self.get_scale_factor() * mag,
                    cancel=self._cover_cancel)

            def get_tooltip(tooltip):
                tooltip.set_markup(self.__album_markup(item.album))
                return True

            setup()
            item.connect('notify', lambda _, __: setup())
            draw_handler_id = widget.connect('draw',
                util.DeferredSignal(scan_cover, timeout=50, priority=GLib.PRIORITY_LOW))
            widget.connect("query-tooltip",
                lambda widget, x, y, keyboard_tip, tooltip: get_tooltip(tooltip))
            eb = Gtk.EventBox()
            eb.connect('button-press-event', self.__rightclick, library)
            eb.connect('popup-menu', self.__popup, library)
            eb.add(widget)
            eb.show_all()
            return eb

        self.view = view = Gtk.FlowBox()
        view.get_model = lambda: model_filter
        view.bind_model(model_list, create_widget)

        view.props.activate_on_single_click = False
        view.props.homogeneous = True
        view.props.min_children_per_line = 1
        view.props.max_children_per_line = 10
        view.props.row_spacing = config.getint("browsers", "row_spacing", 6)
        view.props.column_spacing = config.getint("browsers", "column_spacing", 6)

        #TODO: this seems to be bugged; clicks always add to selection
        #self.view.props.selection_mode = Gtk.SelectionMode.MULTIPLE

        view.set_hadjustment(self.scrollwin.get_hadjustment())
        view.set_vadjustment(self.scrollwin.get_vadjustment())

        sw.props.hscrollbar_policy = Gtk.PolicyType.NEVER
        sw.props.vscrollbar_policy = Gtk.PolicyType.AUTOMATIC
        sw.add(view)

        view.connect('child-activated', self.__play_selection, None)

        self.__sig = connect_destroy(
            view, 'selected-children-changed',
            util.DeferredSignal(self.__update_songs, owner=self))

        targets = [("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, 1),
                   ("text/uri-list", 0, 2)]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        view.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY)
        view.connect("drag-data-get", self.__drag_data_get) # NOT WORKING

        self.accelerators = Gtk.AccelGroup()
        search = SearchBarBox(completion=AlbumTagCompletion(),
                              accel_group=self.accelerators)
        search.connect('query-changed', self.__update_filter)
        connect_obj(search, 'focus-out', lambda w: w.grab_focus(), view)
        self.__search = search

        prefs = PreferencesButton(self, model_sort)
        search.pack_start(prefs, False, True, 0)
        self.pack_start(Align(search, left=6, top=6), False, True, 0)
        self.pack_start(sw, True, True, 0)

        self.connect("destroy", self.__destroy)

        self.__update_filter()

        self.connect('key-press-event', self.__key_pressed, library.librarian)

        if app.cover_manager:
            connect_destroy(
                app.cover_manager, "cover-changed", self._cover_changed)

        self.show_all()

    def update_covergrid_magnification(self, mag):
        size = get_cover_size() * mag

        def update(widget):
            widget.set_width(size)

        self.view.foreach(lambda child: update(child.get_child().get_child()))
        self.view.emit('check-resize')

    def __album_markup(self, album):
        if album:
            return self.display_pattern % album
        else:
            albums_len = len(self.view.get_model()) - 1
            text = "<b>%s</b>" % _("All Albums")
            text += "\n" + ngettext(
                "%d album", "%d albums", albums_len) % albums_len
            return text

    def _cover_changed(self, manager, songs):
        model = self.__model
        songs = set(songs)
        for iter_, item in model.iterrows():
            album = item.album
            if album is not None and songs & album.songs:
                item.scanned = False
                model.row_changed(model.get_path(iter_), iter_)

    def __key_pressed(self, widget, event, librarian):
        if qltk.is_accel(event, "<Primary>I"):
            songs = self.__get_selected_songs()
            if songs:
                window = Information(librarian, songs, self)
                window.show()
            return True
        elif qltk.is_accel(event, "<Primary>Return", "<Primary>KP_Enter"):
            qltk.enqueue(self.__get_selected_songs())
            return True
        elif qltk.is_accel(event, "<alt>Return"):
            songs = self.__get_selected_songs()
            if songs:
                window = SongProperties(librarian, songs, self)
                window.show()
            return True
        return False

    def __destroy(self, browser):
        self._cover_cancel.cancel()

        klass = type(browser)
        if not klass.instances():
            klass._destroy_model()

    def __update_filter(self, entry=None, text=None, scroll_up=True,
                        restore=False):
        model = self.view.get_model()

        self.__filter = None
        query = self.__search.get_query(self.STAR)
        if not query.matches_all:
            self.__filter = query.search
        self.__bg_filter = background_filter()

        self.__inhibit()

        # If we're hiding "All Albums", then there will always
        # be something to filter ­— probably there's a better
        # way to implement this

        if (not restore or self.__filter or self.__bg_filter) or (not
            config.getboolean("browsers", "covergrid_all", True)):
            model.refilter()

        self.__uninhibit()

    def __parse_query(self, model, iter_, data):
        f, b = self.__filter, self.__bg_filter
        album = model.get_album(iter_)

        if f is None and b is None and album is not None:
            return True
        else:
            if album is None:
                return config.getboolean("browsers", "covergrid_all", True)
            elif b is None:
                return f(album)
            elif f is None:
                return b(album)
            else:
                return b(album) and f(album)

    def __search_func(self, model, column, key, iter_, data):
        album = model.get_album(iter_)
        if album is None:
            return config.getboolean("browsers", "covergrid_all", True)
        key = key.lower()
        title = album.title.lower()
        if key in title:
            return False
        if config.getboolean("browsers", "album_substrings"):
            people = (p.lower() for p in album.list("~people"))
            for person in people:
                if key in person:
                    return False
        return True

    def __rightclick(self, widget, event, library):
        if event.button == Gdk.BUTTON_SECONDARY:
            #TODO: select item
            #if not widget.is_selected():
            #    self.view.unselect_all()
            #view.select_path(current_path)
            self.__popup(widget, library)

    def __popup(self, widget, library):
        items = []
        num = len(self.view.get_selected_children())
        button = MenuItem(
            ngettext("Reload album _cover", "Reload album _covers", num),
            Icons.VIEW_REFRESH)
        button.connect('activate', self.__refresh_album, widget)
        items.append(button)

        songs = list(self.__get_selected_songs())
        menu = SongsMenu(library, songs, items=[items])
        menu.show_all()
        popup_menu_at_widget(menu, widget,
            Gdk.BUTTON_SECONDARY,
            Gtk.get_current_event_time())

    @property
    def __selected_rows(self):
        view = self.view
        return (view.get_model()[c.get_index()]
                for c in view.get_selected_children())

    @property
    def __selected_paths(self):
        return (r.path for r in self.__selected_rows)

    def __refresh_album(self, menuitem, view):
        items = self.__get_selected_items()
        for item in items:
            item.scanned = False
        model = self.view.get_model()
        for iter_, item in model.iterrows():
            if item in items:
                model.row_changed(model.get_path(iter_), iter_)

    def __get_selected_items(self):
        return self.view.get_model().get_items(self.__selected_paths)

    def __get_selected_albums(self):
        return self.view.get_model().get_albums(self.__selected_paths)

    def __get_songs_from_albums(self, albums, sort=True):
        # Sort first by how the albums appear in the model itself,
        # then within the album using the default order.
        songs = []
        if sort:
            for album in albums:
                songs.extend(sorted(album.songs, key=lambda s: s.sort_key))
        else:
            for album in albums:
                songs.extend(album.songs)
        return songs

    def __get_selected_songs(self, sort=True):
        albums = self.__get_selected_albums()
        return self.__get_songs_from_albums(albums, sort)

    def __drag_data_get(self, view, ctx, sel, tid, etime):
        songs = self.__get_selected_songs()
        if tid == 1:
            qltk.selection_set_songs(sel, songs)
        else:
            sel.set_uris([song("~uri") for song in songs])

    def __play_selection(self, view, indices, col):
        self.songs_activated()

    def active_filter(self, song):
        for album in self.__get_selected_albums():
            if song in album.songs:
                return True
        return False

    def can_filter_text(self):
        return True

    def filter_text(self, text):
        self.__search.set_text(text)
        if Query(text).is_parsable:
            self.__update_filter(self.__search, text)
            # self.__inhibit()
            #self.view.set_cursor((0,), None, False)
            # self.__uninhibit()
            self.activate()

    def get_filter_text(self):
        return self.__search.get_text()

    def can_filter(self, key):
        # Numerics are different for collections, and although title works,
        # it's not of much use here.
        if key is not None and (key.startswith("~#") or key == "title"):
            return False
        return super().can_filter(key)

    def can_filter_albums(self):
        return True

    def list_albums(self):
        model = self.view.get_model()
        return [row[0].album.key for row in model if row[0].album]

    def select_by_func(self, func, scroll=True, one=False):
        first = True
        for i, row in enumerate(self.view.get_model()):
            if func(row):
                if not first:
                    self.view.select_child(self.view.get_child_at_index(i))
                    continue
                self.view.unselect_all()
                child = self.view.get_child_at_index(i)
                self.view.select_child(child)
                if scroll:
                    self.__scroll_to_child(child)
                first = False
                if one:
                    break
        return not first

    def __scroll_to_child(self, child):
        va = self.scrollwin.get_vadjustment().props
        try:
            x, y = child.translate_coordinates(self.scrollwin, 0, va.value)
            h = child.get_allocation().height
            if y < va.value:
                va.value = y
            elif y + h > va.value + va.page_size:
                va.value = y - va.page_size + h
        except TypeError:
            pass

    def filter_albums(self, values):
        self.__inhibit()
        changed = self.select_by_func(
            lambda r: r[0].album and r[0].album.key in values)
        self.view.grab_focus()
        self.__uninhibit()
        if changed:
            self.activate()

    def unfilter(self):
        self.filter_text("")

    def activate(self):
        self.view.emit('selected-children-changed')

    def __inhibit(self):
        self.view.handler_block(self.__sig)

    def __uninhibit(self):
        self.view.handler_unblock(self.__sig)

    def restore(self):
        text = config.gettext("browsers", "query_text")
        entry = self.__search
        entry.set_text(text)

        # update_filter expects a parsable query
        if Query(text).is_parsable:
            self.__update_filter(entry, text, scroll_up=False, restore=True)

        keys = config.gettext("browsers", "covergrid", "").split("\n")

        self.__inhibit()
        if keys != [""]:
            def select_fun(row):
                album = row[0].album
                if not album:  # all
                    return False
                return album.str_key in keys
            self.select_by_func(select_fun)
        else:
            self.select_by_func(lambda r: r[0].album is None)
        self.__uninhibit()

    def scroll(self, song):
        album_key = song.album_key
        select = lambda r: r[0].album and r[0].album.key == album_key
        self.select_by_func(select, one=True)

    def __get_config_string(self):
        model = self.view.get_model()
        paths = self.__selected_paths

        # All is selected
        if model.contains_all(paths):
            return ""

        # All selected albums
        albums = model.get_albums(paths)

        confval = "\n".join((a.str_key for a in albums))
        # ConfigParser strips a trailing \n so we move it to the front
        if confval and confval[-1] == "\n":
            confval = "\n" + confval[:-1]
        return confval

    def save(self):
        conf = self.__get_config_string()
        config.settext("browsers", "covergrid", conf)
        text = self.__search.get_text()
        config.settext("browsers", "query_text", text)

    def __update_songs(self, selection):
        songs = self.__get_selected_songs(sort=False)
        self.songs_selected(songs)
