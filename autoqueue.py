# AutoQueue: an automatic queueing plugin for Quod Libet.
# version 0.1
# Copyright 2007 Eric Casteleijn <thisfred@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from datetime import datetime, timedelta
from time import strptime
import urllib, threading
import random, os
from xml.dom import minidom
from cPickle import Pickler, Unpickler

try:
    import sqlite3
    SQL = True
except ImportError:
    SQL = False
    
import const, gtk
from plugins.events import EventPlugin
from widgets import main
from parse import Query
from qltk import Frame
from library import library
import config
import util

TRACK_URL = "http://ws.audioscrobbler.com/1.0/track/%s/%s/similar.xml"
ARTIST_URL = "http://ws.audioscrobbler.com/1.0/artist/%s/similar.xml"

# Set this to True to enable logging
verbose = True

INT_SETTINGS = {
    'artist_block_time': {
        'value': 1,
        'label': 'block artist (days)'},
    'track_block_time':  {
        'value': 90,
        'label': 'block track (days)'},
    'desired_queue_length': {
        'value': 4400,
        'label': 'queue (seconds)'},
    'cache_time': {
        'value': 90,
        'label': 'cache (days)'},}

BOOL_SETTINGS = {
    'cache': {
        'value': SQL and True,
        'label': 'caching'},
    'include_rating': {
        'value': True,
        'label': 'include rating'},
    'by_tracks': {
        'value': True,
        'label': 'by track'},
    'by_artists': {
        'value': True,
        'label': 'by artist'},
    'by_tags': {
        'value': True,
        'label': 'by tags'},}

STR_SETTINGS = {
    'pick': {
        'value': 'best',
        'label': 'pick method'},}

def log(msg):
    if not verbose: return
    print "[autoqueue]", msg

def dictify(tups):
    dictified = {}
    for tup in tups:
        key = tuple([item for item in tup][:-1])
        value = tup[-1]
        dictified[key] = value
    return dictified

def escape(foo):
    return foo.replace('"', '\\"')

class AutoQueue(EventPlugin):
    PLUGIN_ID = "AutoQueue"
    PLUGIN_NAME = _("Auto Queue")
    PLUGIN_VERSION = "0.1"
    PLUGIN_DESC = ("Automatically queue new tracks that are similar to "
                   "the track being played.")
    try: DUMP = os.path.join(const.USERDIR, "autoqueue_block_cache")
    except AttributeError:
        DUMP = os.path.join(const.DIR, "autoqueue_block_cache")
    try: DB = os.path.join(const.USERDIR, "similarity.db")
    except AttributeError:
        DB = os.path.join(const.DIR, "similarity.db")
        

    _blocked_artists = []
    _blocked_artists_times = []
    __enabled = False
    
    def __init__(self):
        self.artist_block_time = 7
        self.track_block_time = 30
        self.desired_queue_length = 5
        self.cache_time = 90

        self.cache = SQL and True
        self.include_rating = True
        self.by_tracks = True
        self.by_artists = True
        self.by_tags = True
        self.pick = "best"

        self.blocked = False
        self.read_config()
        self.now = datetime.now()
        self.connection = None
        self.song = None
        self._songs = []
        self.similar_artists = {}
        self.similar_tracks = {}
        try:
            pickle = open(self.DUMP, 'r')
            try:
                unpickler = Unpickler(pickle)
                self._blocked_artists, self._blocked_artists_times \
                                       = unpickler.load()
            finally:
                pickle.close()
        except IOError:
            self._blocked_artists = self._blocked_artists_times = []
        if self.cache:
            try:
                os.stat(self.DB)
            except OSError:
                self.create_db()
        # Set up exit hook to dump cache
        gtk.quit_add(0, self.dump_stuff)
        
    def read_config(self):
        for key, vdict in INT_SETTINGS.items():
            try:
                setattr(self, key, config.getint(
                    "plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, vdict['value'])
                config.set("plugins", "autoqueue_%s" % key, vdict['value'])
        for key, vdict in BOOL_SETTINGS.items():
            try:
                setattr(self, key, config.get(
                    "plugins", "autoqueue_%s" % key).lower() == 'true')
            except:
                setattr(self, key, vdict['value'])
                config.set("plugins", "autoqueue_%s" %
                           key, vdict['value'] and 'true' or 'false')
        for key, vdict in STR_SETTINGS.items():
            try:
                setattr(
                    self, key, config.get("plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, vdict['value'])
                config.set("plugins", "autoqueue_%s" % key, vdict['value'])
                
    def create_db(self):
        """ Set up a database for the artist and track similarity scores
        """
        log("create_db")
        connection = sqlite3.connect(self.DB)
        cursor = connection.cursor()
        cursor.execute(
            'CREATE TABLE artists (id INTEGER PRIMARY KEY, name'
            ' VARCHAR(100), updated DATE)')
        cursor.execute(
            'CREATE TABLE artist_2_artist (artist1 INTEGER, artist2 INTEGER,'
            ' match INTEGER)')
        cursor.execute(
            'CREATE TABLE tracks (id INTEGER PRIMARY KEY, artist INTEGER,'
            ' title VARCHAR(100), updated DATE)')
        cursor.execute(
            'CREATE TABLE track_2_track (track1 INTEGER, track2 INTEGER,'
            ' match INTEGER)')
        connection.commit()
        
    def dump_stuff(self):
        """dump persistent data to pickles
        """
        try:
            os.remove(self.DUMP)
        except OSError:
            pass
        if len(self._blocked_artists) == 0: return 0
        pickle = open(self.DUMP, 'w')
        try:
            pickler = Pickler(pickle, -1)
            to_dump = (self._blocked_artists, self._blocked_artists_times)
            pickler.dump(to_dump)
        finally:
            pickle.close()
        return 0

    def enabled(self):
        log("enabled")
        self.__enabled = True

    def disabled(self):
        log("disabled")
        self.__enabled = False

    def plugin_on_song_started(self, song):
        if song is None:
            return
        self.now = datetime.now()
        artist_name, title = self.get_artist_and_title(song)
        if not (artist_name and title):
            return
        self.song = song
        # add the artist to the blocked list, so their songs won't be
        # played for a determined number of days
        self.block_artist(artist_name)
        if self.blocked:
            return
        bg = threading.Thread(None, self.add_to_queue) 
        bg.setDaemon(True)
        bg.start()

    def need_songs(self):
        model = main.playlist.q.get()
        time = sum([row.get("~#length", 0) for row in model])
        return time < self.desired_queue_length
        
    def add_to_queue(self):
        self.blocked = True
        self.connection = sqlite3.connect(self.DB)
        if self.desired_queue_length >= 0 and not self.need_songs():
            self.blocked = False
            return
        while self.need_songs():
            self.unblock_artists()
            if self.by_tracks:
                similar_tracks = self.get_cached_similar_tracks()
                search_tracks = []
                search = ''
                search_tracks = [
                    '&(artist = "%s", title = "%s")' %
                    (escape(artist), escape(title))
                    for (artist, title, match) in similar_tracks
                    if not self.is_blocked(artist)
                    ]
                version_tracks = [
                    '&(artist = "%s", title = "%s")' %
                    (escape(artist),
                     escape("(".join(title.split("(")[:-1]).strip()))
                    for (artist, title, match) in similar_tracks
                    if "(" in title and not self.is_blocked(artist)
                    ]
                if version_tracks:
                    search_tracks += version_tracks
                if search_tracks:
                    search = "&(|(%s),%s)" % (
                        ",".join(search_tracks),
                        "#(laststarted > %s days)" % self.track_block_time)
                    self.similar_tracks = dictify(similar_tracks)
                    if self.pick_songs(search,  by="track"):
                        self.enqueue(self._songs.pop(0))
                        continue
            if self.by_artists:
                similar_artists = self.get_cached_similar_artists()
                search_artists = []
                search = ''
                search_artists = [
                    'artist = "%s"' % escape(artist[0])
                    for artist in similar_artists
                    if not self.is_blocked(artist[0])]
                if search_artists:
                    search = "&(|(%s),%s)" % (
                        ",".join(search_artists),
                        "#(laststarted > %s days)" % self.track_block_time)
                    self.similar_artists = dictify(similar_artists)
                    if self.pick_songs(search, by="artist"):
                        self.enqueue(self._songs.pop(0))
                        continue
            if self.by_tags:
                tags = self.get_last_song().list("tag")
                exclude_artists = "&(%s)" % ",".join([
                    '!artist = "%s"' %
                    escape(artist) for artist in self.get_blocked_artists()])
                if tags:
                    log("Searching for tags: %s" % tags)
                    search = ''
                    search_tags = []
                    for tag in tags:
                        if tag.startswith("artist:") or tag.startswith(
                            "album:"):
                            stripped = ":".join(tag.split(":")[1:])
                        else:
                            stripped = tag
                        stripped = escape(stripped)
                        search_tags.extend([
                            'tag = "%s"' % stripped,
                            'tag = "artist:%s"' % stripped,
                            'tag = "album:%s"' % stripped])
                    search = "&(|(%s),%s,%s)" % (
                        ",".join(search_tags),
                        exclude_artists,
                        "#(laststarted > %s days)" % self.track_block_time)
                    if self.pick_songs(search, by="tag"):
                        self.enqueue(self._songs.pop(0))
                        continue
            if self._songs:
                self.reorder_songs()
                self.enqueue(self._songs.pop(0))
        self.blocked = False
       
    def block_artist(self, artist_name):
        """store artist name and current daytime so songs by that
        artist can be blocked
        """
        self._blocked_artists.append(artist_name)
        self._blocked_artists_times.append(self.now)
        log("Blocked artist: %s (%s)" % (
            artist_name,
            len(self._blocked_artists)))
        try:
            os.remove(self.DUMP)
        except OSError:
            pass
        if len(self._blocked_artists) == 0:
            return
        pickler = Pickler(open(self.DUMP, 'w'), -1)
        to_dump = (self._blocked_artists,
                   self._blocked_artists_times)
        pickler.dump(to_dump)

    def unblock_artists(self):
        """ release blocked artists when they've been in the penalty
        box for long enough
        """
        while self._blocked_artists_times:
            if self._blocked_artists_times[
                0] + timedelta(self.artist_block_time) > self.now:
                break
            log("Unblocked %s (%s)" % (
                self._blocked_artists.pop(0),
                self._blocked_artists_times.pop(0)))

    def is_blocked(self, artist_name):
        return artist_name in self.get_blocked_artists()

    def get_blocked_artists(self):
        """prevent artists already in the queue from being queued"""
        return self._blocked_artists + [
            song.comma("artist").lower() for song in main.playlist.q.get()]

    def get_last_song(self):
        if len(main.playlist.q):
            return main.playlist.q.get()[-1]
        return self.song

    def reorder_songs(self):
        if not len(self._songs) > 1:
            return
        by_song = self.get_last_song()
        new_order = old_order = self._songs[:]
        if self.by_tags:
            new_order = self._reorder_queue_helper(
                by_song, new_order, by="tag")
        if self.by_artists:
            new_order = self._reorder_queue_helper(
                by_song, new_order, by="artist")
        if self.by_tracks:
            new_order = self._reorder_queue_helper(
                by_song, new_order, by="track")
        if new_order == old_order:
            return
        self._songs = new_order
        
    def enqueue(self, song):
        gtk.gdk.threads_enter()
        log("queuing song")
        if not self.is_blocked(song.comma("artist").lower()):
            main.playlist.enqueue([song])
        gtk.gdk.threads_leave()
        
    def _reorder_queue_helper(self, song, songs, by="track"):
        tw, weighted_songs = self.get_weights(song, songs, by=by)
        if tw == 0:
            log("already sorted by %s" % by)
            return songs
        weighted_songs.sort(reverse=True)
        log("sorted by %s: \n%s" % (by, "\n".join(["%05d %03d %s - %s" % (
            score, len(weighted_songs) + 1 - i, song.comma(
            "artist"), song.comma("title"))
            for score, i, song in weighted_songs])))
        return [w_song[2] for w_song in weighted_songs]
            
    def pick_songs(
        self, search, by="track"):
        try:
            myfilter = Query(search).search
            songs = filter(myfilter, library.itervalues())
        except (Query.error, RuntimeError):
            log("error in: %s" % search)
            return False
        log("%s songs found" % len(songs))
        if not songs:
            return False
        if self.pick =="random":
            queue_songs = self.get_random_sample(
                songs, self.desired_queue_length)
        elif self.pick == "weighted":
            queue_songs = self.get_weighted_sample(
                songs, self.desired_queue_length, by=by)
        else:
            queue_songs = self.get_best_sample(
                songs, self.desired_queue_length, by=by)
        self._songs = queue_songs
        return True
        
    def get_random_sample(self, songs, n):
        adds = []
        while n and songs:
            sample = random.sample(songs, n)
            for song in sample:
                songs.remove(song)
                artist = song.comma("artist").lower()
                if self.is_blocked(artist) or artist in [
                    add.comma("artist").lower() for add in adds]:
                    continue
                n -= 1
                adds.append(song)
        return adds

    def get_weighted_sample(
        self, songs, n, by="track", queue_similarity=True):
        total_weight, weighted_songs = self.get_weights(
            self.get_last_song(), songs, by=by)
        adds = []
        while n and weighted_songs:
            r = random.randint(0, total_weight)
            running = 0
            for weight, i, song in weighted_songs:
                running += weight
                if running >= r: break
            weighted_songs.remove((weight, i, song))
            total_weight -= weight
            artist = song.comma("artist").lower()
            if self.is_blocked(artist) or artist in [
                add.comma("artist").lower() for add in adds]:
                continue
            n -= 1
            adds.append(song)
        return adds
        
    def get_best_sample(self, songs, n, by="track"):
        weighted_songs = self.get_weights(
            self.get_last_song(), songs, by=by)[1]
        weighted_songs.sort()
        adds = []
        while n and weighted_songs:
            song = weighted_songs.pop()[2]
            artist = song.comma("artist").lower()
            if self.is_blocked(artist) or artist in [
                add.comma("artist").lower() for add in adds]:
                continue
            n -= 1
            adds.append(song)
        return adds
    
    def get_weights(self, by_song, for_songs, by="track"):
        weighted_songs = []
        total_weight = 0
        len_songs = len(for_songs)
        for i, song in enumerate(for_songs):
            weight = self.get_match(by_song, song, by=by)
            if self.include_rating:
                weight = int(weight * song["~#rating"])
            weighted_songs.append((weight, len_songs - i, song))
            total_weight += weight
        return total_weight, weighted_songs
        
    def get_artist_and_title(self, song):
        title = song.comma("title").lower()
        if "version" in song:
            title += " (%s)" % song.comma("version").lower()
        artist_name = song.comma("artist").lower()
        return (artist_name, title)
    
    def get_match(self, by_song, song, by="track"):
        artist_name, title = self.get_artist_and_title(song)
        q_artist_name, q_title = self.get_artist_and_title(by_song)
        if by == "track":
            match = self.similar_tracks.get((artist_name, title), 0)
            if match:
                return match
            return self.get_track_match(
                artist_name, title, q_artist_name, q_title)
        if by == "tag":
            return self.get_tag_match(song.list("tag"), by_song.list("tag"))
        match = self.similar_artists.get((artist_name), 0)
        if match:
            return match
        return self.get_artist_match(artist_name, q_artist_name)

    def get_track_match(self, a1, t1, a2, t2):
        id1 = self.get_track(a1,t1)[0]
        id2 = self.get_track(a2,t2)[0]
        return max(
            self._get_track_match(id1, id2),
            self._get_track_match(id2, id1))

    def get_artist_match(self, a1, a2):
        id1 = self.get_artist(a1)[0]
        id2 = self.get_artist(a2)[0]
        return max(
            self._get_artist_match(id1, id2),
            self._get_artist_match(id2, id1))        

    def get_tag_match(self, tags1, tags2):
        t1 = list(set([tag.split(":")[-1] for tag in tags1]))
        t2 = list(set([tag.split(":")[-1] for tag in tags2]))
        return len([tag for tag in t2 if tag in t1])
        
    def get_similar_tracks(self):
        artist_name, title = self.get_artist_and_title(self.get_last_song())
        log("Getting similar tracks from last.fm for: %s - %s" % (
            artist_name, title))
        enc_artist_name= artist_name.encode("utf-8")
        enc_title = title.encode("utf-8")
        if ("&" in artist_name or "/" in artist_name or "?" in artist_name
            or "#" in artist_name or "&" in title or "/" in title
            or "?" in title or "#" in title):
            enc_artist_name = urllib.quote_plus(enc_artist_name)
            enc_title = urllib.quote_plus(enc_title)
        url = TRACK_URL % (
            urllib.quote(enc_artist_name),
            urllib.quote(enc_title))
        try:
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
        except:
            return []
        tracks = []
        nodes = xmldoc.getElementsByTagName("track")
        for node in nodes:
            similar_artist = similar_title = ''
            match = None
            for child in node.childNodes:
                if child.nodeName == 'artist':
                    similar_artist = child.getElementsByTagName(
                        "name")[0].firstChild.nodeValue.lower()
                elif child.nodeName == 'name':
                    similar_title = child.firstChild.nodeValue.lower()
                elif child.nodeName == 'match':
                    match = int(float(child.firstChild.nodeValue) * 100)
                if (similar_artist != '' and similar_title != ''
                    and match is not None):
                    break
            tracks.append((similar_artist, similar_title, match))
        return tracks
            
    def get_similar_artists(self):
        artist_name = self.get_artist_and_title(self.get_last_song())[0]
        log("Getting similar artists from last.fm for: %s " % artist_name)
        if ("&" in artist_name or "/" in artist_name or "?" in artist_name
            or "#" in artist_name):
            artist_name = urllib.quote_plus(artist_name)
        url = ARTIST_URL % (
            urllib.quote(artist_name.encode("utf-8")))
        try:
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
        except:
            return []
        artists = []
        nodes = xmldoc.getElementsByTagName("artist")
        for node in nodes:
            name = node.getElementsByTagName(
                "name")[0].firstChild.nodeValue.lower()
            match = 0
            matchnode = node.getElementsByTagName("match")
            if matchnode:
                    match = int(float(matchnode[0].firstChild.nodeValue) * 100)
            artists.append((name, match))
        return artists

    def get_artist(self, artist_name):
        cursor = self.connection.cursor()
        artist_name = artist_name.encode("UTF-8")
        cursor.execute("SELECT * FROM artists WHERE name = ?", (artist_name,))
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute("INSERT INTO artists (name) VALUES (?)", (artist_name,))
        self.connection.commit()
        cursor.execute("SELECT * FROM artists WHERE name = ?", (artist_name,))
        return cursor.fetchone()

    def get_track(self, artist_name, title):
        cursor = self.connection.cursor()
        title = title.encode("UTF-8")
        artist_id = self.get_artist(artist_name)[0]
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = ? AND title = ?",
            (artist_id, title))
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute(
            "INSERT INTO tracks (artist, title) VALUES (?, ?)",
            (artist_id, title))
        self.connection.commit()
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = ? AND title = ?",
            (artist_id, title))
        return cursor.fetchone()

    def get_cached_similar_artists(self):
        if not self.cache:
            return self.get_similar_artists()
        artist = self.get_artist(self.get_last_song().comma("artist").lower())
        artist_id, updated = artist[0], artist[2]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT name, match  FROM artist_2_artist INNER JOIN artists"
            " ON artist_2_artist.artist1 = artists.id WHERE"
            " artist_2_artist.artist2 = ?",
            (artist_id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                artist_name = self.get_artist_and_title(
                    self.get_last_song())[0]
                log(
                    "Getting similar artists from db for: %s " %
                    artist_name)
                cursor.execute(
                    "SELECT name, match  FROM artist_2_artist INNER JOIN"
                    " artists ON artist_2_artist.artist2 = artists.id WHERE"
                    " artist_2_artist.artist1 = ?",
                    (artist_id,))
                return cursor.fetchall() + reverse_lookup
        similar_artists = self.get_similar_artists()
        self._update_similar_artists(artist_id, similar_artists)
        return similar_artists + reverse_lookup

    def get_cached_similar_tracks(self):
        if not self.cache:
            return self.get_similar_tracks()
        artist, title = self.get_artist_and_title(self.get_last_song())
        track = self.get_track(artist, title)
        track_id, updated = track[0], track[3]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT artists.name, tracks.title, track_2_track.match FROM"
            " track_2_track INNER JOIN tracks ON track_2_track.track1"
            " = tracks.id INNER JOIN artists ON artists.id = tracks.artist"
            " WHERE track_2_track.track2 = ?",
            (track_id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                log("Getting similar tracks from db for: %s - %s" % (
                    artist, title))
                cursor.execute(
                    "SELECT artists.name, tracks.title, track_2_track.match"
                    " FROM track_2_track INNER JOIN tracks ON"
                    " track_2_track.track2 = tracks.id INNER JOIN artists ON"
                    " artists.id = tracks.artist WHERE track_2_track.track1"
                    " = ?",
                    (track_id,))
                return cursor.fetchall() + reverse_lookup
        similar_tracks = self.get_similar_tracks()
        self._update_similar_tracks(track_id, similar_tracks)
        return similar_tracks + reverse_lookup

    def _get_artist_match(self, a1, a2):
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT match FROM artist_2_artist WHERE artist1 = ?"
            " AND artist2 = ?",
            (a1, a2))
        row = cursor.fetchone()
        if not row: return 0
        return row[0]

    def _get_track_match(self, t1, t2):
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT match FROM track_2_track WHERE track1 = ? AND track2 = ?",
            (t1, t2))
        row = cursor.fetchone()
        if not row: return 0
        return row[0]

    def _update_artist_match(self, a1, a2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE artist_2_artist SET match = ? WHERE artist1 = ? AND"
            " artist2 = ?",
            (match, a1, a2))
        self.connection.commit()

    def _update_track_match(self, t1, t2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE track_2_track SET match = ? WHERE track1 = ? AND"
            " track2 = ?",
            (match, t1, t2))
        self.connection.commit()

    def _insert_artist_match(self, a1, a2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO artist_2_artist (artist1, artist2, match) VALUES"
            " (?, ?, ?)",
            (a1, a2, match))
        self.connection.commit()

    def _insert_track_match(self, t1, t2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO track_2_track (track1, track2, match) VALUES"
            " (?, ?, ?)",
            (t1, t2, match))
        self.connection.commit()

    def _update_artist(self, artist_id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE artists SET updated = DATETIME('now') WHERE id = ?",
            (artist_id,))
        self.connection.commit()

    def _update_track(self, track_id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE tracks SET updated = DATETIME('now') WHERE id = ?",
            (track_id,))
        self.connection.commit()
        
    def _update_similar_artists(self, artist_id, similar_artists):
        for artist_name, match in similar_artists:
            id2 = self.get_artist(artist_name)[0]
            if self._get_artist_match(artist_id, id2):
                self._update_artist_match(artist_id, id2, match)
                continue
            self._insert_artist_match(artist_id, id2, match)
        self._update_artist(artist_id)
        
    def _update_similar_tracks(self, track_id, similar_tracks):
        for artist_name, title, match in similar_tracks:
            id2 = self.get_track(artist_name, title)[0]
            if self._get_track_match(track_id, id2):
                self._update_track_match(track_id, id2, match)
                continue
            self._insert_track_match(track_id, id2, match)
        self._update_track(track_id)

    def PluginPreferences(self, parent):

        def bool_changed(widget):
            if widget.get_active():
                setattr(self, widget.get_name(), True)
            else:
                setattr(self, widget.get_name(), False)
            config.set(
                'plugins',
                'autoqueue_%s' % widget.get_name(),
                widget.get_active() and 'true' or 'false')
            
        def str_changed(entry, key):
            value = entry.get_text()
            config.set('plugins', 'autoqueue_%s' % key, value)
            setattr(self, key, value)

        def int_changed(entry, key):
            value = entry.get_text()
            config.set('plugins', 'autoqueue_%s' % key, value)
            setattr(self, key, int(value))
            
        table = gtk.Table()
        table.set_col_spacings(3)
        i = 0
        j = 0
        for setting in BOOL_SETTINGS:
            button = gtk.CheckButton(label=BOOL_SETTINGS[setting]['label'])
            button.set_name(setting)
            button.set_active(
                config.get(
                "plugins", "autoqueue_%s" % setting).lower() == 'true')
            button.connect('toggled', bool_changed)
            table.attach(button, i, i+1, j, j+1)
            if i == 1:
                i = 0
                j += 1
            else:
                i += 1
        for setting in INT_SETTINGS:
            j += 1
            label = gtk.Label('%s:' % INT_SETTINGS[setting]['label'])
            entry = gtk.Entry()
            table.attach(label, 0, 1, j, j+1, xoptions=gtk.FILL | gtk.SHRINK)
            table.attach(entry, 1, 2 ,j, j+1, xoptions=gtk.FILL | gtk.SHRINK)
            entry.connect('changed', int_changed, setting)
            try:
                entry.set_text(
                    config.get('plugins', 'autoqueue_%s' % setting))
            except:
                pass
        for setting in STR_SETTINGS:
            j += 1
            label = gtk.Label('%s:' % STR_SETTINGS[setting]['label'])
            entry = gtk.Entry()
            table.attach(label, 0, 1, j, j+1, xoptions=gtk.FILL | gtk.SHRINK)
            table.attach(entry, 1, 2 ,j, j+1, xoptions=gtk.FILL | gtk.SHRINK)
            entry.connect('changed', str_changed, setting)
            try:
                entry.set_text(config.get('plugins', 'autoqueue_%s' % setting))
            except:
                pass
            
        return table
