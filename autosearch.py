# Copyright 2005-2007 Joe Wreschnig, Eric Casteleijn
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id: autorating.py 3819 2006-09-04 03:28:14Z piman $

"""Automatic search plugin."""

from plugins.events import EventPlugin
from widgets import main


class AutoSearch(EventPlugin):          # pylint: disable=W0232
    """Automatic search plugin."""

    PLUGIN_ID = "Automatic Searching"
    PLUGIN_NAME = _("Automatic Searching")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically do a search for the title of the"
                   "current song. (Helps to indentify covers & duplicates.)")

    ignore_empty_queue = True

    def plugin_on_song_started(self, song):
        """Main functionality."""
        if song is not None and (
            self.ignore_empty_queue or len(main.playlist.q) > 0):
            title = song.comma("title").lower()
            album = song.comma("album").lower()
            for bad_char in "?/&|,'\"()[]!=\\<> *+.":
                title = title.replace(bad_char, "#")
                album = album.replace(bad_char, "#")
            filename = title
            artists = get_artists(song)
            artist_search = ''
            title_search = ''
            filename_search = ''
            album_search = ''
            tag_search = ''
            if artists:
                aa = ','.join(
                    ["|(artist=/%s/,performer=/%s/)" %
                     (a, a) for a in artists])
                if aa:
                    artist_search = "|(%s)" % aa
            if title:
                title_search = "title=/%s/" % title
                tag_search = "grouping=/%s/" % title
            if filename:
                filename_search = "~basename=/%s/" % filename
            if album:
                album_search = "album=/%s/" % album
            search = ("|(%s)" % ','.join([s for s in [
                artist_search, title_search, filename_search, album_search,
                tag_search] if s]))
            search = ".*".join(
                [sub_string for sub_string in search.split('#') if sub_string])
            main.browser.set_text(search)
        else:
            if (main.browser.status ==
                "|(#(playcount < 1), #(added < 90 days))"):
                print "[autosearch:] *new* already set"
                return
            main.browser.set_text(
                "|(#(playcount < 1), #(added < 90 days))")


def get_artists(song):
    """Return lowercase UNICODE name of artists and performers."""
    artists = []
    performers = [remove_role(artist) for artist in song.list("performer")]
    for tag in song._song:              # pylint: disable=W0212
        if tag.startswith('performer:'):
            performers.extend(
                [artist.lower() for artist in song.list(tag)])
    for artist in song.list("artist") + performers:
        artist = artist.lower()
        for bad_char in "/&|,'\"()[]!=\\<> *+.":
            artist = artist.replace(bad_char, "#")
        artists.append(artist)
    return set(artists)


def remove_role(artist):
    """Remove role part of performer name."""
    if not artist.endswith(')'):
        return artist
    return artist.split('(')[0]
