# -*- coding: utf-8 -*-
#
# quodgkue getglue checkins for Quodlibet
# (C) 2010 Eric Casteleijn
# Based on QLScrobbler, (C) 2005-2007 Joshua Kwan, Joe Wreschnig
# and ScrobblerGluer Copyright (C) 2010 James Martin
# Which in turn is Based on Pylast -- Copyright (C) 2008-2010  Amr Hassan

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

from lxml import etree
import urllib
import gtk, config
from plugins.events import EventPlugin

# Set this to True to enable logging
VERBOSE = True

# GetGlue.com API URL
API = "http://api.getglue.com/v2/"

def login(user_id, password):
    """Logs in with userId and password, returns token"""
    url = API + "user/login?userId=%s&password=%s" % (user_id, password)
    response = urllib.urlopen(url).read()
    xlogin = etree.XML(response)
    return xlogin.getchildren()[1].getchildren()[0][2].text

class QuodGlue(EventPlugin):
    """Main plugin class"""

    need_config = True

    def __init__(self):
        # Read configuration
        self.read_config()
        self.last_artist = None

    def plugin_on_song_ended(self, song, skipped):
        """Triggered when song ends/is skipped"""
        if skipped or song is None:
            return
        artist = song['artist']
        if artist == self.last_artist:
            return
        self.last_artist = artist
        self.add_checkin(artist)

    def get_artist_url(self, artist):
        return "http://www.last.fm/music/" + urllib.quote(artist, safe=None)

    def add_checkin(self, name):
        """Takes name and token, and likes appropriate page on GetGlue."""
        url = self.getArtistUrl(name)

        app = "QuodGlue"
        source = url

        gluerl = API + "user/addCheckin?objectId=%s&source=%s&app=%s&token=%s" % (
            url, source, app, self.token)
        urllib.urlopen(gluerl).read()
        return name

    def read_config(self):
        """Read the options from the configuration file"""
        try:
            self.token = config.get("plugins", "quodglue_token")
        except:
            self.token = None
        if not self.token:
            username = ""
            password = ""
            try:
                username = config.get("plugins", "quodglue_username")
                password = config.get("plugins", "quodglue_password")
            except:
                if (self.need_config == False and
                    getattr(self, 'PMEnFlag', False)):
                    self.quick_dialog(
                        "Please visit the Preferences window to set QuodGlue"
                        " up. Until then, checkins will not be made.",
                        gtk.MESSAGE_INFO)
                    self.need_config = True
                    return
            self.token = login(username, password)
            config.set("plugins", "quodglue_token", self.token)
        self.need_config = False

    def PluginPreferences(self, parent):

        def changed(entry, key):
            # having a function for each entry is unnecessary..
            config.set("plugins", "quodglue_" + key, entry.get_text())

        def destroyed(*args):
            # if changed, let's say that things just got better and we should
            # try everything again
            newu = None
            newp = None
            try:
                newu = config.get("plugins", "quodglue_username")
                newp = config.get("plugins", "quodglue_password")
            except:
                return

            if self.username != newu or self.password != newp:
                self.broken = False

        table = gtk.Table(6, 3)
        table.set_col_spacings(3)
        lt = gtk.Label(
            _("Please enter your GetGlue\nusername and password."))
        lu = gtk.Label(_("UserId:"))
        lp = gtk.Label(_("Password:"))

        for l in [lt, lu, lp]:
            l.set_line_wrap(True)
            l.set_alignment(0.0, 0.5)
        table.attach(lt, 0, 2, 0, 1, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(lu, 0, 1, 1, 2, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(lp, 0, 1, 2, 3, xoptions=gtk.FILL | gtk.SHRINK)

        userent = gtk.Entry()
        pwent = gtk.Entry()
        pwent.set_visibility(False)
        pwent.set_invisible_char('*')

        table.set_border_width(6)
        try:
            userent.set_text(config.get("plugins", "quodglue_username"))
        except:
            pass
        try:
            pwent.set_text(config.get("plugins", "quodglue_password"))
        except:
            pass

        table.attach(userent, 1, 2, 1, 2, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(pwent, 1, 2, 2, 3, xoptions=gtk.FILL | gtk.SHRINK)
        pwent.connect('changed', changed, 'password')
        userent.connect('changed', changed, 'username')

        table.connect('destroy', destroyed)
        return table

