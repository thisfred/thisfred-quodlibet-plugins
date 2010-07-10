
# Copyright 2005-2007 Joe Wreschnig, Eric Casteleijn
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id: autorating.py 3819 2006-09-04 03:28:14Z piman $

from plugins.events import EventPlugin
from widgets import main


class AutoSearch(EventPlugin):
    PLUGIN_ID = "Automatic Searching"
    PLUGIN_NAME = _("Automatic Searching")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically do a search for the title of the"
                   "current song. (Helps to indentify covers & duplicates.)")

    ignore_empty_queue = True
    def plugin_on_song_started(self, song):
        if song is not None and (
            self.ignore_empty_queue or len(main.playlist.q) > 0):
            title = song.comma("title").lower()
            album = song.comma("album").lower()
            for bad_char in "/&|,'\"()!=\\<>":
                title = title.replace(bad_char, "#")
                album = album.replace(bad_char, "#")
            filename = title.replace(' ', '#')
            artists = get_artists(song)
            titles = split_filter(title)
            filenames = split_filter(filename)
            albums = split_filter(album)
            artist_search = ''
            title_search = ''
            filename_search = ''
            album_search = ''
            tag_search = ''
            if artists:
                aa = ','.join(
                    ["|(artist=%s,performer=%s)" % (a, a) for a in artists])
                if aa:
                    artist_search = "|(%s)" % aa
            if title:
                tt = ','.join(['title=%s' % t for t in titles if len(t) > 1])
                if tt:
                    title_search = "&(%s)" % tt
                tt = ','.join(['grouping=%s' % t for t in titles if len(t) > 2])
                if tt:
                    tag_search ="&(%s)" % tt
            if filename:
                ff = ','.join(
                    ['~filename=%s' % f for f in filenames if len(f) > 3])
                if ff:
                    filename_search = "&(%s)" % ff
            if album:
                aa = ','.join(["album=%s" % a for a in albums if len(a) > 2])
                if aa:
                    album_search = "&(%s)" % aa
            search = ("|(%s)" % ','.join([s for s in [
                artist_search, title_search, filename_search, album_search,
                tag_search] if s]))
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
        #main.browser.do_grab_focus()
        main.browser.activate()

def get_artists(song):
    """return lowercase UNICODE name of artists and performers."""
    artists = []
    performers = [remove_role(artist) for artist in song.list("performer")]
    for tag in song._song:
        if tag.startswith('performer:'):
            performers.extend(
                [artist for artist in song.list(tag)])
    for artist in song.list("artist") + performers:
        for bad_char in "/&|,'\"()!=\\<>":
            artist = artist.replace(bad_char, "#")
        artists.extend([a.lower().strip() for a in artist.split('#') if a])
    return set(artists)

def split_filter(value):
    return [v for v in value.split('#') if v.strip()]

def remove_role(artist):
    if not artist.endswith(')'):
        return artist
    return artist.split('(')[0]
