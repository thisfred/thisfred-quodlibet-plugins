
# Copyright 2005-2007 Joe Wreschnig, Eric Casteleijn
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id: autorating.py 3819 2006-09-04 03:28:14Z piman $

import gtk
from plugins.events import EventPlugin
from widgets import main


class AutoSearch(EventPlugin):
    PLUGIN_ID = "Automatic Searching"
    PLUGIN_NAME = _("Automatic Searching")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically do a search for the title of the"
                   "current song. (Helps to indentify covers & doubles.)")

    ignore_empty_queue = True
    def plugin_on_song_started(self, song):
        if song is not None and (
            self.ignore_empty_queue or len(main.playlist.q) > 0):
            artist = song.comma("artist").lower()
            title = song.comma("title").lower()
            album = song.comma("album").lower()
            for bad_char in "/&|,'\"()!=\\":
                artist = artist.replace(bad_char, "#")
                title = title.replace(bad_char, "#")
                album = album.replace(bad_char, "#")
            filename = title.replace(' ', '#')
            artists = split_filter(artist)
            titles = split_filter(title)
            filenames = split_filter(filename)
            albums = split_filter(album)
            artist_search = ''
            title_search = ''
            filename_search = ''
            album_search = ''
            if artist:
                artist_search = "&(%s)" % (
                    ','.join(['|(artist=%s,performer=%s)' % (a, a) for a in
                          artists]))
            if title:
                title_search = "&(%s)" % (
                    ','.join(['title=%s' % t for t in titles]))
            if filename:
                filename_search = "&(%s)" % (
                    ','.join(['~filename=%s' % f for f in filenames]))
            if album:
                album_search = "&(%s)" % (
                    ','.join(["album=%s" % a for a in albums]))
            search = ("|(%s)" % ','.join([s for s in [
                artist_search, title_search, filename_search, album_search]
                                          if s]))
            main.browser.set_text(search)
        else:
            if (main.browser.status ==
                "|(grouping=favorites, &(#(skipcount < 1), #(playcount < 1)), "
                "#(added < 90 days))"):
                print "[autosearch:] *new* already set"
                return
            main.browser.set_text(
                "|(grouping=favorites, &(#(skipcount < 1), #(playcount < 1)), "
                "#(added < 90 days))")
        main.browser.activate()

def split_filter(value):
    return [v for v in value.split('#') if v.strip()]
