import os
import const
from plugins.songsmenu import SongsMenuPlugin

class AddToListPlugin(SongsMenuPlugin):
    PLUGIN_ID = "Export title list"
    PLUGIN_NAME = _("Export title list")
    PLUGIN_DESC = _("Add title name to titles.txt.")
    PLUGIN_ICON = "gtk-find-and-replace"
    PLUGIN_VERSION = "0.1"

    def player_get_userdir(self):
        """get the application user directory to store files"""
        try:
            return const.USERDIR
        except AttributeError:
            return const.DIR

    def plugin_songs(self, songs):
        f = open(os.path.join(self.player_get_userdir(), "titles.txt"), 'a')
        titles = set()
        for song in songs:
            title = song("title")
            if title in titles:
                continue
            titles.add(title)
            f.write('%s\n' % title)
