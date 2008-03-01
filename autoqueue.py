# AutoQueue: an automatic queueing plugin for Quod Libet.
# version 0.1
# Copyright 2007 Eric Casteleijn <thisfred@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from datetime import datetime, timedelta
from time import strptime, sleep
import urllib, threading
import random, os
from xml.dom import minidom
from cPickle import Pickler, Unpickler
from sets import Set

try:
    import sqlite3
    SQL = True
except:
    SQL = False
    
import const, gtk
from plugins.events import EventPlugin
from widgets import main
from parse import Query
from qltk import Frame
from library import library
import config

TRACK_URL = "http://ws.audioscrobbler.com/1.0/track/%s/%s/similar.xml"
ARTIST_URL = "http://ws.audioscrobbler.com/1.0/artist/%s/similar.xml"

# Set this to True to enable logging
verbose = True

INT_SETTINGS = {
    "artist_block_time": 2,
    "track_block_time": 14,
    "desired_queue_length": -1,
    "to_add": 2,
    "cache_time": 90,}

BOOL_SETTINGS = {
    "cache": SQL and True,
    "include_rating": True,
    "by_tracks": True,
    "by_artists": True,
    "by_tags": True,
    "queue_similarity": False,
    "reorder": True,
    "random_skip": True}

STR_SETTINGS = {
    "pick": "best",}

def log(msg):
    if not verbose: return
    print "[autoqueue]", msg


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
        self.blocked = False
        self.read_config()
        pickle = open(self.DUMP, 'r')
        try:
            unpickler = Unpickler(pickle)
            self._blocked_artists, self._blocked_artists_times \
                = unpickler.load()
        except:
            pass
        finally:
            pickle.close()
        if self.cache:
            try:
                os.stat(self.DB)
            except OSError:
                self.create_db()
        self.songs = []
        self.queued = 0
        # Set up exit hook to dump cache
        gtk.quit_add(0, self.dump_stuff)
        
    def read_config(self):
        for key, value in INT_SETTINGS.items():
            try:
                setattr(self, key, config.getint(
                    "plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" % key, value)
        for key, value in BOOL_SETTINGS.items():
            try:
                setattr(self, key, config.get(
                    "plugins", "autoqueue_%s" % key).lower() == 'true')
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" %
                           key, value and 'true' or 'false')
        for key, value in STR_SETTINGS.items():
            try:
                setattr(self, key, config.get("plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" % key, value)
                
    def create_db(self):
        """ Set up a database for the artist and track similarity scores
        """
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
        except OSError: pass
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
        # if another thread of this plugin is still active we do
        # nothing, since having two threads mess with the queue
        # results in crashes.
        if song is None: return
        self.now = datetime.now()
        self.added = 0
        self.artist_name, self.title = self.get_artist_and_title(song)
        if not self.artist_name or not self.title: return
        # add the artist to the blocked list, so their songs won't be
        # played for a determined number of days
        self.block_artist(self.artist_name)
        self.still_to_add = 0
        self.songs.append(song)
        if self.blocked: return
        bg = threading.Thread(None, self.add_to_queue) 
        bg.setDaemon(True)
        bg.start()
        
    def add_to_queue(self):
        self.blocked = True
        self.connection = sqlite3.connect(self.DB)
        if self.desired_queue_length >= 0 and len(
            main.playlist.q) > self.desired_queue_length:
            if not self.reorder:
                self.blocked = False
                return
            self.reorder_queue()
            self.blocked = False
            self.songs.pop(0)
            return
        while self.songs:
            if self.random_skip:
                trigger = random.random()
                rating = self.songs[0].get("~#rating", 0.5)
                log("trigger: %s rating: %s" % (trigger, rating))
                if trigger > rating:
                    self.queued = 0
                    self.reorder_queue()
                    self.blocked = False
                    self.songs.pop(0)
                    return
            queue_length = len(main.playlist.q)
            self.unblock_artists()
            self.still_to_add = min(
                self.to_add, max(0, self.desired_queue_length - queue_length))
            if self.by_tracks:
                similar_tracks = self.get_cached_similar_tracks()
                search_tracks = []
                search = ''
                search_tracks = [
                    '&(artist = "%s", title = "%s")' % (artist, title)
                    for (artist, title, match) in similar_tracks
                    if not self.is_blocked(artist)]
                version_tracks = [
                    '&(artist = "%s", title = "%s")' %
                    (artist,
                     "(".join(title.split("(")[:-1]).strip())
                    for (artist, title, match) in similar_tracks
                    if "(" in title and not self.is_blocked(artist)]
                if version_tracks:
                    search_tracks += version_tracks
                if search_tracks:
                    search = "&(|(%s),%s)" % (
                        ",".join(search_tracks),
                        "#(laststarted > %s days)" % self.track_block_time)
                    self.pick_and_queue(search,  by="track")
                if len(main.playlist.q) > queue_length:
                    self.still_to_add -= len(main.playlist.q) - queue_length
                    queue_length = len(main.playlist.q)
                    log("Similar track(s) added.")
            if self.by_artists and self.still_to_add:
                similar_artists = self.get_cached_similar_artists()
                search_artists = []
                search = ''
                search_artists = [
                    'artist = "%s"' % artist[0] for artist in similar_artists
                    if not self.is_blocked(artist[0])]
                if search_artists:
                    search = "&(|(%s),%s)" % (
                        ",".join(search_artists),
                        "#(laststarted > %s days)" % self.track_block_time)
                    self.pick_and_queue(search, by="artist")
                if len(main.playlist.q) > queue_length:
                    self.still_to_add -= len(main.playlist.q) - queue_length
                    queue_length = len(main.playlist.q)
                    log("Similar artist(s) added.")
            if self.by_tags and self.still_to_add:
                tags = self.songs[0].list("tag")
                exclude_artists = "&(%s)" % ",".join([
                    '!artist = "%s"' %
                    artist for artist in self.get_blocked_artists()])
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
                        search_tags.extend([
                            'tag = "%s"' % stripped,
                            'tag = "artist:%s"' % stripped,
                            'tag = "album:%s"' % stripped])
                    search = "&(|(%s),%s,%s)" % (
                        ",".join(search_tags),
                        exclude_artists,
                        "#(laststarted > %s days)" % self.track_block_time)
                    self.pick_and_queue(search, by="tag")
                    if len(main.playlist.q) > queue_length:
                        self.still_to_add -= len(
                            main.playlist.q) - queue_length
                        log("Tracks added by tag.")
            if self.still_to_add:
                self.queued = 0
            if self.reorder:
                self.reorder_queue()
            self.songs.pop(0)
        self.blocked = False
            
    def block_artist(self, artist_name):
        # store artist name and current daytime so songs by that
        # artist can be blocked
        self._blocked_artists.append(artist_name)
        self._blocked_artists_times.append(self.now)
        log("Blocked artist: %s (%s)" % (
            artist_name,
            len(self._blocked_artists)))
        try:
            os.remove(self.DUMP)
        except OSError: pass
        if len(self._blocked_artists) == 0: return
        # XXX: once the plugin is stable, remove this, but while there
        # are crashes I like to preserve persistent data as often as
        # possible
        pickler = Pickler(open(self.DUMP, 'w'), -1)
        to_dump = (self._blocked_artists,
                   self._blocked_artists_times)
        pickler.dump(to_dump)

    def unblock_artists(self):
        # release blocked artists when they've been in the penalty box
        # for long enough
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
        # prevent artists already in the queue from being queued
        return self._blocked_artists + [
            song.comma("artist").lower() for song in main.playlist.q.get()]

    def reorder_queue(self):
        if not len(main.playlist.q) > 1:
            return
        new_order = old_order = main.playlist.q.get()[:]
        if self.by_tags:
            new_order = self._reorder_queue_helper(
                self.songs[-1], new_order, by="tag")
        if self.by_artists:
            new_order = self._reorder_queue_helper(
                self.songs[-1], new_order, by="artist")
        if self.by_tracks:
            new_order = self._reorder_queue_helper(
                self.songs[-1], new_order, by="track")
        if new_order == old_order:
            return
        self.queue(new_order)
        
    def queue(self, songs):
        main.playlist.q.clear()
        log("queuing songs: [%s]" % len(songs))
        main.playlist.enqueue(
            [song for song in songs if not
             self.is_blocked(song.comma("artist").lower())])
                
    def _reorder_queue_helper(self, song, songs, by="track"):
        tw, weighted_songs = self.get_weights([song], songs, by=by)
        if tw == 0:
            log("already sorted by %s" % by)
            return songs
        weighted_songs.sort(reverse=True)
        log("sorted by %s: \n%s" % (by, "\n".join(["%05d %03d %s - %s" % (
            score, len(weighted_songs) + 1 - i, song.comma(
            "artist"), song.comma("title"))
            for score, i, song in weighted_songs])))
        return [w_song[2] for w_song in weighted_songs]
            
    def pick_and_queue(self, search, by="track"):
        try:
            myfilter = Query(search).search
            songs = filter(myfilter, library.itervalues())
        except (Query.error, RuntimeError): return
        log("%s songs found" % len(songs))
        n = min(self.still_to_add, len(songs))
        if self.pick =="random":
            queue_songs = self.get_random_sample(songs, n)
        elif self.pick == "weighted":
            queue_songs = self.get_weighted_sample(
                songs, n, by=by, queue_similarity=self.queue_similarity)
        else:
            queue_songs = self.get_best_sample(
                songs, n, by=by, queue_similarity=self.queue_similarity)
        queue_songs = main.playlist.q.get()[:] + queue_songs
        self.queue(queue_songs)
        
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
                self.added += 1
                self.queued += 1
        return adds

    def get_weighted_sample(
        self, songs, n, by="track", queue_similarity=True):
        by_songs = [self.songs[-1]]
        if queue_similarity:
            by_songs.extend(main.playlist.q.get())
        total_weight, weighted_songs = self.get_weights(
            by_songs, songs, by=by)
        adds = []
        while n and weighted_songs:
            r = random.randint(0, total_weight)
            running = 0
            index = 0
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
            self.added += 1
            self.queued += 1
        return adds
        
    def get_best_sample(self, songs, n, by="track", queue_similarity=True):
        by_songs = [self.songs[-1]]
        if queue_similarity:
            by_songs.extend(main.playlist.q.get())
        weighted_songs = self.get_weights(by_songs, songs, by=by)[1]
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
            self.added += 1
            self.queued += 1
        return adds
    
    def get_weights(self, by_songs, for_songs, by="track"):
        weighted_songs = []
        total_weight = 0
        len_songs = len(for_songs)
        for i, song in enumerate(for_songs):
            weight = self.get_match(by_songs, song, by=by)
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
    
    def get_match(self, by_songs, song, by="track"):
        artist_name, title = self.get_artist_and_title(song)
        match = 0
        for q_song in by_songs:
            q_artist_name, q_title = self.get_artist_and_title(q_song)
            if by == "track":
                match += self.get_track_match(
                    artist_name, title, q_artist_name, q_title)
            elif by == "tag":
                match += self.get_tag_match(
                    song.list("tag"), q_song.list("tag"))
            else:
                match += self.get_artist_match(artist_name, q_artist_name)
        return match

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
        t1 = list(Set([tag.split(":")[-1] for tag in tags1]))
        t2 = list(Set([tag.split(":")[-1] for tag in tags2]))
        return len([tag for tag in t2 if tag in t1])
        
    def get_similar_tracks(self):
        artist_name = self.artist_name
        title = self.title
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
        artist_name = self.artist_name
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
        id = self.get_artist(artist_name)[0]
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = ? AND title = ?", (id, title))
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute(
            "INSERT INTO tracks (artist, title) VALUES (?, ?)", (id, title))
        self.connection.commit()
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = ? AND title = ?", (id, title))
        return cursor.fetchone()

    def get_cached_similar_artists(self):
        if not self.cache:
            return self.get_similar_artists()
        artist = self.get_artist(self.artist_name)
        id, updated = artist[0], artist[2]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT name, match  FROM artist_2_artist INNER JOIN artists"
            " ON artist_2_artist.artist1 = artists.id WHERE"
            " artist_2_artist.artist2 = ?",
            (id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                log(
                    "Getting similar artists from db for: %s " %
                    self.artist_name)
                cursor.execute(
                    "SELECT name, match  FROM artist_2_artist INNER JOIN"
                    " artists ON artist_2_artist.artist2 = artists.id WHERE"
                    " artist_2_artist.artist1 = ?",
                    (id,))
                return cursor.fetchall() + reverse_lookup
        similar_artists = self.get_similar_artists()
        self._update_similar_artists(id, similar_artists)
        return similar_artists + reverse_lookup

    def get_cached_similar_tracks(self):
        if not self.cache:
            return self.get_similar_tracks()
        track = self.get_track(self.artist_name, self.title)
        id, updated = track[0], track[3]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT artists.name, tracks.title, track_2_track.match FROM"
            " track_2_track INNER JOIN tracks ON track_2_track.track1"
            " = tracks.id INNER JOIN artists ON artists.id = tracks.artist"
            " WHERE track_2_track.track2 = ?",
            (id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                log("Getting similar tracks from db for: %s - %s" % (
                    self.artist_name, self.title))
                cursor.execute(
                    "SELECT artists.name, tracks.title, track_2_track.match"
                    " FROM track_2_track INNER JOIN tracks ON"
                    " track_2_track.track2 = tracks.id INNER JOIN artists ON"
                    " artists.id = tracks.artist WHERE track_2_track.track1"
                    " = ?",
                    (id,))
                return cursor.fetchall() + reverse_lookup
        similar_tracks = self.get_similar_tracks()
        self._update_similar_tracks(id, similar_tracks)
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

    def _update_artist(self, id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE artists SET updated = DATETIME('now') WHERE id = ?", (id,))
        self.connection.commit()

    def _update_track(self, id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE tracks SET updated = DATETIME('now') WHERE id = ?", (id,))
        self.connection.commit()
        
    def _update_similar_artists(self, id, similar_artists):
        for artist_name, match in similar_artists:
            id2 = self.get_artist(artist_name)[0]
            if self._get_artist_match(id, id2):
                self._update_artist_match(id, id2, match)
                continue
            self._insert_artist_match(id, id2, match)
        self._update_artist(id)
        
    def _update_similar_tracks(self, id, similar_tracks):
        for artist_name, title, match in similar_tracks:
            id2 = self.get_track(artist_name, title)[0]
            if self._get_track_match(id, id2):
                self._update_track_match(id, id2, match)
                continue
            self._insert_track_match(id, id2, match)
        self._update_track(id)

    def bool_changed(self, b):
        if b.get_active():
            setattr(self, b.get_name(), True)
        else:
            setattr(self, b.get_name(), False)
        config.set('plugins', "autoqueue_%s" % b.get_name(), b.get_active())

    def PluginPreferences(self, parent):
        vb = gtk.VBox(spacing = 3)
        tooltips = gtk.Tooltips().set_tip

        ## pattern_box = gtk.HBox(spacing = 3)
        ## pattern_box.set_border_width(3)

        ## pattern = gtk.Entry()
        ## pattern.set_text(self.pattern)
        ## pattern.connect('changed', self.pattern_changed)
        ## pattern_box.pack_start(gtk.Label("Pattern:"), expand = False)
        ## pattern_box.pack_start(pattern)

        ## accounts_box = gtk.HBox(spacing = 3)
        ## accounts_box.set_border_width(3)
        ## accounts = gtk.Entry()
        ## accounts.set_text(join(self.accounts))
        ## accounts.connect('changed', self.accounts_changed)
        ## tooltips(accounts, "List accounts, separated by spaces, for "
        ##                      "changing status message. If none are specified, "
        ##                      "status message of all accounts will be changed.")
        ## accounts_box.pack_start(gtk.Label("Accounts:"), expand = False)
        ## accounts_box.pack_start(accounts)

        ## c = gtk.CheckButton(label="Add '[paused]'")
        ## c.set_active(self.paused)
        ## c.connect('toggled', self.paused_changed)
        ## tooltips(c, "If checked, '[paused]' will be added to "
        ##             "status message on pause.")

        table = gtk.Table()
        self.list = []
        i = 0
        j = 0
        for status in BOOL_SETTINGS.keys():
            button = gtk.CheckButton(label=status)
            button.set_name(status)
            button.set_active(
                config.get(
                "plugins", "autoqueue_%s" % status).lower() == 'true')
            button.connect('toggled', self.bool_changed)
            self.list.append(button)
            table.attach(button, i, i+1, j, j+1)
            if i == 2:
                i = 0
                j += 1
            else:
                i += 1

        ## vb.pack_start(pattern_box)
        ## vb.pack_start(accounts_box)
        ## vb.pack_start(c)
        vb.pack_start(Frame(label="Thingum"))
        vb.pack_start(table)

        return vb
