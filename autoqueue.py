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

try:
    import sqlite
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
    "queue_similarity": True,
    "reorder": True,
    "random_skip": True,
    "increasing_skip": False,}

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
        ## log("__init__")
        self.read_config()
        try:
            unpickler = Unpickler(open(self.DUMP, 'r'))
            self._blocked_artists, self._blocked_artists_times = unpickler.load()
        except IOError: pass
        if self.cache:
            try:
                os.stat(self.DB)
            except OSError:
                self.create_db()
            self.connection = sqlite.connect(self.DB)
            self.cursor = self.connection.cursor()
        self.queued = 0
        # Set up exit hook to dump cache
        gtk.quit_add(0, self.dump_stuff)
        
    def read_config(self):
        ## log("read_config")
        for key, value in INT_SETTINGS.items():
            try:
                setattr(self, key, config.getint(
                    "plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" % key, value)
        for key, value in BOOL_SETTINGS.items():
            try:
                setattr(self, key, config.getboolean(
                    "plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" % key, value)
        for key, value in STR_SETTINGS.items():
            try:
                setattr(self, key, config.get("plugins", "autoqueue_%s" % key))
            except:
                setattr(self, key, value)
                config.set("plugins", "autoqueue_%s" % key, value)
                
    def create_db(self):
        #log("create_db")
        """ Set up a database for the artist and track similarity scores
        """
        connection = sqlite.connect(self.DB)
        cursor = self.connection.cursor()
        cursor = self.cursor
        cursor.execute(
            'CREATE TABLE artists (id INTEGER PRIMARY KEY, name VARCHAR(100), updated DATE)')
        cursor.execute(
            'CREATE TABLE artist_2_artist (artist1 INTEGER, artist2 INTEGER, match INTEGER)')
        cursor.execute(
            'CREATE TABLE tracks (id INTEGER PRIMARY KEY, artist INTEGER, title VARCHAR(100), updated DATE)')
        cursor.execute(
            'CREATE TABLE track_2_track (track1 INTEGER, track2 INTEGER, match INTEGER)')
        connection.commit()
        
    def dump_stuff(self):
        """dump persistent data to pickles
        """
        #log("dump_stuff")
        try:
            os.remove(self.DUMP)
        except OSError: pass
        if len(self._blocked_artists) == 0: return 0
        pickler = Pickler(open(self.DUMP, 'w'), -1)
        to_dump = (self._blocked_artists,
                   self._blocked_artists_times)
        pickler.dump(to_dump)
        return 0

    def enabled(self):
        #log("enabled")
        self.__enabled = True

    def disabled(self):
        #log("disabled")
        self.__enabled = False

    
    def plugin_on_song_started(self, song):
        #log("plugin_on_song_started(%s)" % song)
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
        self.song = song
        # if there are enough songs in the queue, do not lookup new
        # ones to queue
        if self.desired_queue_length >= 0 and len(
            main.playlist.q) > self.desired_queue_length:
            if not self.reorder:
                return
            # but do reorder the queue (if so desired) by similarity
            # to the playing song
            bg = threading.Thread(
                None,
                self.reorder_queue,
                args=(self.song, main.playlist.q.get())) 
            bg.setDaemon(True)
            bg.start()
            return
        # look up songs and add them to the queue
        bg = threading.Thread(None, self.add_to_queue) 
        bg.setDaemon(True)
        bg.start()
        

    def block_artist(self, artist_name):
        # store artist name and current daytime so songs by that
        # artist can be blocked
        #log("block_artist(%s)" % artist_name)
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
        #log("unblock_artists")
        while self._blocked_artists_times:
            if self._blocked_artists_times[
                0] + timedelta(self.artist_block_time) > self.now:
                break
            log("Unblocked %s (%s)" % (
                self._blocked_artists.pop(0),
                self._blocked_artists_times.pop(0)))

    def is_blocked(self, artist_name):
        #log("is_blocked(%s)" % artist_name)
        return artist_name in self.get_blocked_artists()

    def get_blocked_artists(self):
        # prevent artists already in the queue from being queued
        #log("get_blocked_artists")
        return self._blocked_artists + [
            song.comma("artist").lower() for song in main.playlist.q.get()]

    def add_to_queue(self):
        # start blocking new threads
        #log("add_to_queue")
        # if true it is less likely similar tracks are queued the
        # lower rated a track is
        if self.random_skip:
            trigger = random.random()
            if self.increasing_skip and self.queued:
                trigger = trigger * ((-1.0/self.queued) + 1.0)
            rating = self.song["~#rating"]
            log("trigger: %s rating: %s" % (trigger, rating))
            if trigger > rating:
                self.queued = 0
                self.reorder_queue(self.song, main.playlist.q.get())
                return
        queue_length = len(main.playlist.q)
        self.unblock_artists()
        to_add = self.to_add
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
                self.queue(search, to_add, by="track")
            if len(main.playlist.q) > queue_length:
                to_add -= len(main.playlist.q) - queue_length
                queue_length = len(main.playlist.q)
                log("Similar track(s) added.")
        if self.by_artists and to_add:
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
                self.queue(search, to_add, by="artist")
            if len(main.playlist.q) > queue_length:
                to_add -= len(main.playlist.q) - queue_length
                queue_length = len(main.playlist.q)
                log("Similar artist(s) added.")
        if self.by_tags and to_add:
            tags = self.song.list("tag")
            exclude_artists = "&(%s)" % ",".join([
                '!artist = "%s"' %
                artist for artist in self.get_blocked_artists()])
            if tags:
                log("Searching for tags: %s" % tags)
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
                search = "&(|(%s),%s,%s)" % (
                    ",".join(search_tags),
                    exclude_artists,
                    "#(laststarted > %s days)" % self.track_block_time)
                self.queue(search, to_add, by="tag")
                if len(main.playlist.q) > queue_length:
                    to_add -= len(main.playlist.q) - queue_length
                    log("Tracks added by tag.")
        if to_add:
            self.queued = 0
        if self.reorder:
            self.reorder_queue(self.song, main.playlist.q.get())
            
    def reorder_queue(self, song, songs):
        #log("reorder_queue(%s, %s)" % (song, songs))
        unblock = False
        old = songs[:]
        if not len(songs) > 1: return
        if self.by_tags:
            songs = self._reorder_queue_helper(song, songs, by="tag")
        if self.by_artists:
            songs = self._reorder_queue_helper(song, songs, by="artist")
        if self.by_tracks:
            songs = self._reorder_queue_helper(song, songs, by="track")
        if songs == old:
            return
        main.playlist.q.clear()
        self._queue(songs)
        
    def _queue(self, songs):
        #log("_queue(%s)" % songs)
        songs = filter(lambda s: s.can_add, songs)
        old_length = len(main.playlist.q)
        main.playlist.enqueue(songs)
        ## while len(main.playlist.q) < len(songs) + old_length:
        ##     log("waiting to queue")
        
    ## def _unqueue(self, songs):
    ##     log("_unqueue(%s)" % songs)
    ##     main.playlist.q.clear()
        ## old_length = len(main.playlist.q)
        ## main.playlist.unqueue(songs)
        ## while len(main.playlist.q) + len(songs) > old_length:
        ##     log("waiting to unqueue")
        ## sleep(5)
        
    def _reorder_queue_helper(self, song, songs, by="track"):
        #log("_reorder_queue_helper(%s, %s, by=%s)" % (song, songs, by))
        tw, weighted_songs = self.get_weights([song], songs, by=by)
        if tw == 0: return songs
        log("unsorted: %s" % repr(
            [(score, i, song["artist"] + " - " + song["title"]) for score,
             i, song in weighted_songs]))
        weighted_songs.sort(reverse=True)
        log("sorted by %s: %s" % (by, repr(
            [(score, i, song["artist"] + " - " + song["title"]) for score,
             i, song in weighted_songs])))
        return [w_song[2] for w_song in weighted_songs]
            
    def queue(self, search, to_add, by="track"):
        #log("queue(%s, %s, by=%s)" % (search, to_add, by))
        try:
            myfilter = Query(search).search
            songs = filter(myfilter, library.itervalues())
        except (Query.error, RuntimeError): return
        log("%s songs found" % len(songs))
        n = min(to_add, len(songs))
        if self.pick =="random":
            self.enqueue_random_sample(songs, n)
        elif self.pick == "weighted":
            self.enqueue_weighted_sample(
                songs, n, by=by, queue_similarity=self.queue_similarity)
        else:
            self.enqueue_best_sample(
                songs, n, by=by, queue_similarity=self.queue_similarity)
        
    def enqueue_random_sample(self, songs, n):
        #log("enqueue_random_sample(%s, %s)" % (songs, n))
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
        self._queue(adds)

    def enqueue_weighted_sample(
        self, songs, n, by="track", queue_similarity=True):
        #log("enqueue_weighted_sample(%s, %s, by=%s, queue_similarity=%s)" % (
        #    songs, n, by, queue_similarity))
        by_songs = [self.song]
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
        self._queue(adds)
        
    def enqueue_best_sample(self, songs, n, by="track", queue_similarity=True):
        #log("enqueue_best_sample(%s, %s, by=%s, queue_similarity=%s)" % (
        #    songs, n, by, queue_similarity))
        by_songs = [self.song]
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
        self._queue(adds)
    
    def get_weights(self, by_songs, for_songs, by="track"):
        #log("get_weights(%s, %s, by=%s)" % (by_songs, for_songs, by))
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
        #log("get_artist_and_title(%s)" % song)
        title = song.comma("title").lower()
        if "version" in song:
            title += " (%s)" % song.comma("version").lower()
        artist_name = song.comma("artist").lower()
        return (artist_name, title)
    
    def get_match(self, by_songs, song, by="track"):
        #log("get_match(%s, %s, by=%s)" % (by_songs, song, by))
        artist_name, title = self.get_artist_and_title(song)
        match = 0
        for q_song in by_songs:
            q_artist_name, q_title = self.get_artist_and_title(q_song)
            if by == "track":
                match += self.get_track_match(
                    artist_name, title, q_artist_name, q_title)
            elif by == "tag":
                match += self.get_tag_match(song.list("tag"), q_song.list("tag"))
            else:
                match += self.get_artist_match(artist_name, q_artist_name)
        return match
    
    def get_track_match(self, a1, t1, a2, t2):
        #log("get_track_match(%s, %s, %s, %s)" % (a1, t1, a2, t2))
        id1 = self.get_track(a1,t1)[0]
        id2 = self.get_track(a2,t2)[0]
        return max(
            self._get_track_match(id1, id2),
            self._get_track_match(id2, id1))
        
    def get_artist_match(self, a1, a2):
        #log("get_artist_match(%s, %s)" % (a1, a2))
        id1 = self.get_artist(a1)[0]
        id2 = self.get_artist(a2)[0]
        return max(
            self._get_artist_match(id1, id2),
            self._get_artist_match(id2, id1))        

    def get_tag_match(self, tags1, tags2):
        #log("get_tag_match(%s, %s)" % (tags1, tags2))
        t1 = [tag.split(":")[-1] for tag in tags1]
        t2 = [tag.split(":")[-1] for tag in tags2]
        return len([tag for tag in t2 if tag in t1])
        
    def get_similar_tracks(self):
        #log("get_similar_tracks")
        artist_name = self.artist_name
        title = self.title
        log("Getting similar tracks from last.fm for: %s - %s" % (
            artist_name, title))
        enc_artist_name= artist_name.encode("utf-8")
        enc_title = title.encode("utf-8")
        if "&" in artist_name or "/" in artist_name or "?" in artist_name or "#" in artist_name or "&" in title or "/" in title or "?" in title or "#" in title:
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
                if similar_artist != '' and similar_title != '' and match is not None:
                    break
            tracks.append((similar_artist, similar_title, match))
        return tracks
            
    def get_similar_artists(self):
        #log("get_similar_artists")
        artist_name = self.artist_name
        log("Getting similar artists from last.fm for: %s " % artist_name)
        if "&" in artist_name or "/" in artist_name or "?" in artist_name or "#" in artist_name:
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
        #log("get_artist(%s)" % artist_name)
        cursor = self.cursor
        artist_name = artist_name.encode("UTF-8")
        cursor.execute("SELECT * FROM artists WHERE name = %s", artist_name)
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute("INSERT INTO artists (name) VALUES (%s)", artist_name)
        self.connection.commit()
        cursor.execute("SELECT * FROM artists WHERE name = %s", artist_name)
        return cursor.fetchone()

    def get_track(self, artist_name, title):
        #log("get_track(%s, %s)" % (artist_name, title))
        cursor = self.cursor
        title = title.encode("UTF-8")
        id = self.get_artist(artist_name)[0]
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = %s AND title = %s", (id, title))
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute(
            "INSERT INTO tracks (artist, title) VALUES (%s, %s)", (id, title))
        self.connection.commit()
        cursor.execute(
            "SELECT * FROM tracks WHERE artist = %s AND title = %s", (id, title))
        return cursor.fetchone()

    def get_cached_similar_artists(self):
        #log("get_cached_similar_artists")
        if not self.cache:
            return self.get_similar_artists()
        artist = self.get_artist(self.artist_name)
        id, updated = artist[0], artist[2]
        cursor = self.cursor
        cursor.execute(
            "SELECT name, match  FROM artist_2_artist INNER JOIN artists ON artist_2_artist.artist1 = artists.id WHERE artist_2_artist.artist2 = %s",
            id)
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                log(
                    "Getting similar artists from db for: %s " %
                    self.artist_name)
                cursor.execute(
                    "SELECT name, match  FROM artist_2_artist INNER JOIN artists ON artist_2_artist.artist2 = artists.id WHERE artist_2_artist.artist1 = %s",
                    id)
                return cursor.fetchall() + reverse_lookup
        similar_artists = self.get_similar_artists()
        self._update_similar_artists(id, similar_artists)
        return similar_artists + reverse_lookup

    def get_cached_similar_tracks(self):
        #log("get_cached_similar_tracks")
        if not self.cache:
            return self.get_similar_tracks()
        track = self.get_track(self.artist_name, self.title)
        id, updated = track[0], track[3]
        cursor = self.cursor
        cursor.execute(
            "SELECT artists.name, tracks.title, track_2_track.match  FROM track_2_track INNER JOIN tracks ON track_2_track.track1 = tracks.id INNER JOIN artists ON artists.id = tracks.artist WHERE track_2_track.track2 = %s",
            id)
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                log("Getting similar tracks from db for: %s - %s" % (
                    self.artist_name, self.title))
                cursor.execute(
                    "SELECT artists.name, tracks.title, track_2_track.match  FROM track_2_track INNER JOIN tracks ON track_2_track.track2 = tracks.id INNER JOIN artists ON artists.id = tracks.artist WHERE track_2_track.track1 = %s",
                    id)
                return cursor.fetchall() + reverse_lookup
        similar_tracks = self.get_similar_tracks()
        self._update_similar_tracks(id, similar_tracks)
        return similar_tracks + reverse_lookup

    def _get_artist_match(self, a1, a2):
        #log("_get_artist_match(%s, %s)" % (a1, a2))
        self.cursor.execute(
            "SELECT match FROM artist_2_artist WHERE artist1 = %s AND artist2 = %s",
            (a1, a2))
        row = self.cursor.fetchone()
        if not row: return 0
        return row[0]

    def _get_track_match(self, t1, t2):
        #log("_get_track_match(%s, %s)" % (t1, t2))
        self.cursor.execute(
            "SELECT match FROM track_2_track WHERE track1 = %s AND track2 = %s",
            (t1, t2))
        row = self.cursor.fetchone()
        if not row: return 0
        return row[0]

    def _update_artist_match(self, a1, a2, match):
        #log("_update_artist_match(a1, a2, match)" % (a1, a2, match))
        self.cursor.execute(
            "UPDATE artist_2_artist SET match = %s WHERE artist1 = %s AND artist2 = %s",
            (match, a1, a2))
        self.connection.commit()

    def _update_track_match(self, t1, t2, match):
        #log("_update_track_match(%s, %s, %s)" % (t1, t2, match))
        self.cursor.execute(
            "UPDATE track_2_track SET match = %s WHERE track1 = %s AND track2 = %s",
            (match, t1, t2))
        self.connection.commit()

    def _insert_artist_match(self, a1, a2, match):
        #log("_insert_artist_match(%s, %s, %s)" % (a1, a2, match))
        self.cursor.execute(
            "INSERT INTO artist_2_artist (artist1, artist2, match) VALUES (%s, %s, %s)",
            (a1, a2, match))
        self.connection.commit()

    def _insert_track_match(self, t1, t2, match):
        #log("_insert_track_match(%s, %s, %s)" % (t1, t2, match))
        self.cursor.execute(
            "INSERT INTO track_2_track (track1, track2, match) VALUES (%s, %s, %s)",
            (t1, t2, match))
        self.connection.commit()

    def _update_artist(self, id):
        #log("_update_artist(%s)" % id)
        self.cursor.execute(
            "UPDATE artists SET updated = DATETIME('now') WHERE id = %s", id)
        self.connection.commit()

    def _update_track(self, id):
        #log("_update_track(%s)" % id)
        self.cursor.execute(
            "UPDATE tracks SET updated = DATETIME('now') WHERE id = %s", id)
        self.connection.commit()
        
    def _update_similar_artists(self, id, similar_artists):
        #log("_update_similar_artists(%s, %s)" % (id, similar_artists))
        for artist_name, match in similar_artists:
            id2 = self.get_artist(artist_name)[0]
            if self._get_artist_match(id, id2):
                self._update_artist_match(id, id2, match)
                continue
            self._insert_artist_match(id, id2, match)
        self._update_artist(id)
        
    def _update_similar_tracks(self, id, similar_tracks):
        #log("_update_similar_tracks(%s, %s)" % (id, similar_tracks))
        for artist_name, title, match in similar_tracks:
            id2 = self.get_track(artist_name, title)[0]
            if self._get_track_match(id, id2):
                self._update_track_match(id, id2, match)
                continue
            self._insert_track_match(id, id2, match)
        self._update_track(id)

    def bool_changed(self, b):
        #log("bool_changed" % b)
        if b.get_active():
            setattr(self, b.get_name(), True)
        else:
            setattr(self, b.get_name(), False)
        config.set('plugins', "autoqueue_%s" % b.get_name(), b.get_active())

    def PluginPreferences(self, parent):
        #log("PluginPreferences(%s)" % parent)
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
                config.getboolean("plugins", "autoqueue_%s" % status))
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
