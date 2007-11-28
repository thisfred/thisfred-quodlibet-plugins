# AutoQueue: a Last.fm tagging plugin for Quod Libet.
# version 0.1
# Copyright 2007 Eric Casteleijn <thisfred@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from datetime import datetime
import urllib, threading
import random
from xml.dom import minidom
from sets import Set

from plugins.events import EventPlugin
from widgets import main
from parse import Query
from qltk.songlist import SongList

TRACK_URL = "http://ws.audioscrobbler.com/1.0/track/%s/%s/similar.xml"
ARTIST_URL = "http://ws.audioscrobbler.com/1.0/artist/%s/similar.xml"

# Set this to True to enable logging
verbose = False

def log(msg):
    if verbose:
        print "[autoqueue]", msg

class AutoQueue(EventPlugin):
    PLUGIN_ID = "AutoQueue"
    PLUGIN_NAME = _("Auto Queue")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically queue new tracks that are similar to "
                   "the track being played.")

    blocked_artists = []
    desired_queue_length = 10
    to_add = 4

    def plugin_on_song_started(self, song):
        if song is None: return
        if len(main.playlist.q) >= self.desired_queue_length: return
        self.song = song
        bg = threading.Thread(None, self.add_to_queue) 
        bg.setDaemon(True)
        bg.start()
        
    def add_to_queue(self):
        queue_length = len(main.playlist.q)
        to_add = min(self.to_add, self.desired_queue_length - queue_length)
        title = self.song.comma("title").lower()
        if "version" in self.song:
            title += " (%s)" % self.song.comma("version").lower()
        artist_name = self.song.comma("artist").lower()
        similar_tracks = self.get_similar_tracks(artist_name, title)
        search_tracks = []
        search = ''
        search_tracks = [
            '&(artist = "%s", title = "%s")' % (artist, title)
            for (artist, title) in similar_tracks
            if not artist in self.blocked_artists]
        if search_tracks:
            search = "|(%s)" % ",".join(search_tracks)
            self.queue(search, to_add)
        if len(main.playlist.q) > queue_length:
            log(
                "Similar tracks added. Blocked artists: %s" %
                self.blocked_artists)
            return
        similar_artists = self.get_similar_artists(artist_name)
        search_artists = []
        search = ''
        search_artists = [
            'artist = "%s"' % artist for artist in similar_artists
            if not artist in self.blocked_artists]
        if search_artists:
            search = "|(%s)" % ",".join(search_artists)
            self.queue(search, to_add)
        if len(main.playlist.q) > queue_length:
            log(
                "Similar artists added. Blocked artists: %s" %
                self.blocked_artists)
            return
        tags = self.song.list("tag")
        log("Searching for tags: %s" % tags)
        if tags:
            search = ''
            search_tags = []
            for tag in tags:
                if tag.startswith("artist:") or tag.startswith("album:"):
                    stripped = ":".join(tag.split(":")[1:])
                else:
                    stripped = tag
                search_tags.extend([
                    'tag = "%s"' % stripped,
                    'tag = "artist:%s"' % stripped,
                    'tag = "album:%s"' % stripped])
            search = "|(%s)" % ",".join(search_tags)
            self.queue(search, to_add)
            if len(main.playlist.q) > queue_length:
                log(
                    "Tracks added by tag. Blocked artists: %s" %
                    self.blocked_artists)
                return
        #if queue_length == 0:
        #    self.blocked_artists = [artist_name]

    def queue(self, search, to_add):
        try: myfilter = Query(search).search
        except Query.error: return
        songs = filter(myfilter, main.browser._library.itervalues())
        log("%s songs found" % len(songs))
        picks = random.sample(songs, min(to_add, len(songs)))
        adds = []
        for pick in picks:
            pick_artist = pick.comma("artist").lower()
            if not pick_artist in self.blocked_artists:
                adds.append(pick)
                self.blocked_artists.append(pick_artist)
        main.playlist.enqueue(adds)

        
    def get_similar_tracks(self, artist_name, title):
        log("Getting similar tracks for: %s - %s" % (artist_name, title))
        url = TRACK_URL % (
            urllib.quote(artist_name.encode("utf-8")),
            urllib.quote(title.encode("utf-8")))
        try:
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
        except:
            return []
        tracks = []
        nodes = xmldoc.getElementsByTagName("track")
        for node in nodes:
            similar_artist = similar_title = '' 
            for child in node.childNodes:
                if child.nodeName == 'artist':
                    similar_artist = child.getElementsByTagName(
                        "name")[0].firstChild.nodeValue.lower()
                elif child.nodeName == 'name':
                    similar_title = child.firstChild.nodeValue.lower()
                if similar_artist and similar_title: break
            tracks.append((similar_artist, similar_title))
        return tracks
            
    def get_similar_artists(self, artist_name):
        log("Getting similar artists for: %s " % artist_name)
        url = ARTIST_URL % (urllib.quote(artist_name.encode("utf-8")))
        try:
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
        except:
            return []
        artists = []
        nodes = xmldoc.getElementsByTagName("artist")
        for node in nodes:
            artists.append(
                node.getElementsByTagName(
                "name")[0].firstChild.nodeValue.lower())
        return artists
        
