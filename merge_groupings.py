from plugins.songsmenu import SongsMenuPlugin

class MergeGroupingsPlugin(SongsMenuPlugin):
    """
    Merge the set of all groupings of selected songs and save them back
    to all songs.

    """
    PLUGIN_ID = "Merge Groupings"
    PLUGIN_NAME = _("Merge groupings")
    PLUGIN_DESC = _(
        "Merge the set of all groupings of selected songs and save them back "
        "to all songs.")
    PLUGIN_ICON = "gtk-find-and-replace"
    PLUGIN_VERSION = "0.1"

    def plugin_songs(self, songs):
        """Act on the songs."""
        groupings = set()
        for song in songs:
            if song.get('grouping'):
                for tag in song.comma('grouping').split(','):
                    groupings.add(tag.lower().strip())
        for song in songs:
            song['grouping'] = '\n'.join(groupings)
