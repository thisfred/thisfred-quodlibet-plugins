import os
import const
from plugins.songsmenu import SongsMenuPlugin

class AddToListPlugin(SongsMenuPlugin):
    PLUGIN_ID = "Export artist list"
    PLUGIN_NAME = _("Export artist list")
    PLUGIN_DESC = _("Add artist name to artists.txt.")
    PLUGIN_ICON = "gtk-find-and-replace"
    PLUGIN_VERSION = "0.1"

    def player_get_userdir(self):
        """get the application user directory to store files"""
        try:
            return const.USERDIR
        except AttributeError:
            return const.DIR

    def plugin_songs(self, songs):
        f = open(os.path.join(self.player_get_userdir(), "artists.txt"), 'a')
        artists = set()
        for song in songs:
            artist = song("artist")
            if artist in artists:
                continue
            artists.add(artist)
            f.write('%s\n' % artist)
