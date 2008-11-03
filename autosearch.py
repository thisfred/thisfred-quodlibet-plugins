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
            artist = song.comma("artist").lower().replace("'", "")
            title = song.comma("title").lower().replace("'", "")
            for bad_char in ",'\")!=\\":
                artist = artist.replace(bad_char, "#")                
                title = title.replace(bad_char, "#")
            artists = artist.split('#')
            titles = title.split('#')
            artist_search = "&(%s)" % (
                ','.join(['artist=%s' % a for a in artists if a]))
            title_search = "&(%s)" % (
                ','.join(['title=%s' % t for t in titles if t]))
            filename_search = "&(%s)" % (
                ','.join(['~filename=%s' % t for t in titles if t]))
            search = ("|(%s,%s,%s)" % (
                artist_search, 
                title_search, 
                filename_search))
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
        
