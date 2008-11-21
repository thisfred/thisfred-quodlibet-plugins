import os
import const
from plugins.songsmenu import SongsMenuPlugin

class AddToListPlugin(SongsMenuPlugin):
    PLUGIN_ID = "Add to list"
    PLUGIN_NAME = _("Add To List")
    PLUGIN_DESC = _("Add filename of the song to file list.txt.")
    PLUGIN_ICON = "gtk-find-and-replace"
    PLUGIN_VERSION = "0.1"

    def player_get_userdir(self):
        """get the application user directory to store files"""
        try:
            return const.USERDIR
        except AttributeError:
            return const.DIR

    def plugin_songs(self, songs):
        f = open(os.path.join(self.player_get_userdir(), "list.txt"), 'a')
        for song in songs:
            f.write(song("~filename") + '\n')
