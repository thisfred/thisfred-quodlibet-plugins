# Copyright 2005-2007 Joe Wreschnig, Eric Casteleijn
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id: autorating.py 3819 2006-09-04 03:28:14Z piman $

from plugins.events import EventPlugin
from widgets import main

class AutoRating(EventPlugin):
    PLUGIN_ID = "Automatic Searching"
    PLUGIN_NAME = _("Automatic Searching")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Auromatically do a search for the artist of the"
                   "song that just ended.")

    def plugin_on_song_started(self, song):
        if song is not None and len(main.playlist.q) > 0:
            title = song.comma("title").lower()
            main.browser.set_text(title)
        else:
            main.browser.set_text("&(#(skipcount < 1), #(playcount < 1))")
        main.browser.activate()
            
            