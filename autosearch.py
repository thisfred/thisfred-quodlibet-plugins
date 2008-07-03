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


class AutoRating(EventPlugin):
    PLUGIN_ID = "Automatic Searching"
    PLUGIN_NAME = _("Automatic Searching")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically do a search for the title of the"
                   "current song. (Helps to indentify covers & doubles.)")

    ignore_empty_queue = True
    def plugin_on_song_started(self, song):
        if song is not None and (
            self.ignore_empty_queue or len(main.playlist.q) > 0):
            title = song.comma("title").lower().replace("#", "")
            main.browser.set_text(title)
        else:
            if (main.browser.status ==
                "|(tag=favorites, &(#(skipcount < 1), #(playcount < 1)))"):
                print "[autosearch:] *new* already set"
                return
            main.browser.set_text(
                "|(tag=favorites, &(#(skipcount < 1), #(playcount < 1)))")
        main.browser.activate()
            
            
