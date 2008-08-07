"""AutoQueue: an automatic queueing plugin for Quod Libet.
version 0.3
Copyright 2007-2008 Eric Casteleijn <thisfred@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License version 2 as
published by the Free Software Foundation"""

from collections import deque
from datetime import datetime, timedelta
from time import strptime, sleep
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

# If you change even a single character of code, I would ask that you
# get and use your own (free) api key from last.fm here:
# http://www.last.fm/api/account
API_KEY = "09d0975a99a4cab235b731d31abf0057"

TRACK_URL = "http://ws.audioscrobbler.com/2.0/?method=track.getsimilar" \
            "&artist=%s&track=%s&api_key=" + API_KEY
ARTIST_URL = "http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar" \
             "&artist=%s&api_key=" + API_KEY
INT_SETTINGS = {
    'artist_block_time': {
        'value': 1,
        'label': 'block artist (days)'},
    'track_block_time':  {
        'value': 90,
        'label': 'block track (days)'},
    'desired_queue_length': {
        'value': 4440,
        'label': 'queue (seconds)'},
    'cache_time': {
        'value': 90,
        'label': 'cache (days)'},}

BOOL_SETTINGS = {
    'cache': {
        'value': SQL and True,
        'label': 'caching'},
    'by_tracks': {
        'value': True,
        'label': 'by track'},
    'by_artists': {
        'value': True,
        'label': 'by artist'},
    'by_tags': {
        'value': True,
        'label': 'by tags'},
    'verbose': {
        'value': False,
        'label': 'log to console'},}

STR_SETTINGS = {
    'restrictors' : {
        'value': '',
        'label': 'restrict',},
    'relaxors' : {
        'value': '',
        'label': 'relax',},
    }

# be nice to last.fm
WAIT_BETWEEN_REQUESTS = timedelta(0, 0, 0, 5) 

def dictify(tups):
    """turn a list of n-tuples into a dict with a n-1-tuple as a key
    and the last item of the tuple as value""" 
    dictified = {}
    for tup in tups:
        key = tuple([item for item in tup][:-1])
        value = tup[-1]
        dictified[key] = value
    return dictified

def escape(the_string):
    """double escape quotes"""
    return the_string.replace('"', '\\"')

def construct_track_search(artist, title, restrictions):
    """construct a QL search string that looks for songs with this
    artist and title"""
    search = '&(artist = "%s", title = "%s")' % (
        escape(artist), escape(title))
    version = ""
    if "(" in title:
        version = '&(artist = "%s", title = "%s")' % (
            escape(artist),
            escape("(".join(title.split("(")[:-1]).strip()))
    if version:
        search = "|(%s, %s)" % (search, version)
    if search:
        search = "&(%s, %s)" % (search, restrictions)
    return search

def construct_tag_search(tags, exclude_artists, restrictions):
    """construct a QL search string that looks for songs with these
    tags"""
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
        ",".join(search_tags), exclude_artists, restrictions)
    return search

def construct_artist_search(artist, restrictions):
    """construct a QL search string that looks for songs with this
    artist"""
    search = 'artist = "%s"' % escape(artist)
    search = "&(%s, %s)" % (search, restrictions)
    return search

def construct_restrictions(track_block_time, relaxors, restrictors):
    """contstruct a QL search string to further modify the searches"""
    restrictions = "#(laststarted > %s days)" % track_block_time
    if relaxors:
        restrictions = "|(%s, %s)" % (restrictions, relaxors)
    if restrictors:
        restrictions = "&(%s, %s)" % (restrictions, restrictors)
    return restrictions

class Cache(object):
    """
    >>> dec_cache = Cache(10)
    >>> @dec_cache
    ... def identity(f):
    ...     pass
    >>> dummy = [identity(x) for x in range(20) + range(11,15) + range(20) +
    ... range(11,40) + [39, 38, 37, 36, 35, 34, 33, 32, 16, 17, 11, 41]] 
    >>> dec_cache.t1
    deque([(41,)])
    >>> dec_cache.t2
    deque([(11,), (17,), (16,), (32,), (33,), (34,), (35,), (36,), (37,)])
    >>> dec_cache.b1
    deque([(31,), (30,)])
    >>> dec_cache.b2
    deque([(38,), (39,), (19,), (18,), (15,), (14,), (13,), (12,)])
    >>> dec_cache.p
    5
    >>> identity(41)
    41
    >>> identity(32)
    32
    >>> identity(16)
    16
    """
    def __init__(self, size):
        self.cached = {}
        self.c = size
        self.p = 0
        self.t1 = deque()
        self.t2 = deque()
        self.b1 = deque()
        self.b2 = deque()

    def replace(self, args):
        if self.t1 and (
            (args in self.b2 and len(self.t1) == self.p) or
            (len(self.t1) > self.p)):
            old = self.t1.pop()
            self.b1.appendleft(old)
        else:
            old = self.t2.pop()
            self.b2.appendleft(old)
        del(self.cached[old])
        
    def __call__(self, func):
        def wrapper(*orig_args):
            args = orig_args[:]
            if args in self.t1: 
                self.t1.remove(args)
                self.t2.appendleft(args)
                return self.cached[args]
            if args in self.t2: 
                self.t2.remove(args)
                self.t2.appendleft(args)
                return self.cached[args]
            result = func(*orig_args)
            self.cached[args] = result
            if args in self.b1:
                self.p = min(
                    self.c, self.p + max(len(self.b2) / len(self.b1) , 1))
                self.replace(args)
                self.b1.remove(args)
                self.t2.appendleft(args)
                print "%s:: t1:%s b1:%s t2:%s b2:%s p:%s" % (
                    repr(func)[10:30], len(self.t1),len(self.b1),len(self.t2),
                    len(self.b2), self.p)
                return result            
            if args in self.b2:
                self.p = max(0, self.p - max(len(self.b1)/len(self.b2) , 1))
                self.replace(args)
                self.b2.remove(args)
                self.t2.appendleft(args)
                print "%s:: t1:%s b1:%s t2:%s b2:%s p:%s" % (
                   repr(func)[10:30], len(self.t1),len(self.b1),len(self.t2),
                   len(self.b2), self.p)
                return result
            if len(self.t1) + len(self.b1) == self.c:
                if len(self.t1) < self.c:
                    self.b1.pop()
                    self.replace(args)
                else:
                    del(self.cached[self.t1.pop()])
            else:
                total = len(self.t1) + len(self.b1) + len(
                    self.t2) + len(self.b2)
                if total >= self.c:
                    if total == (2 * self.c):
                        self.b2.pop()
                    self.replace(args)
            self.t1.appendleft(args)
            return result
        return wrapper


class AutoQueue(EventPlugin):
    """The actual plugin class"""
    PLUGIN_ID = "AutoQueue"
    PLUGIN_NAME = _("Auto Queue")
    PLUGIN_VERSION = "0.1"
    try:
        DUMP = os.path.join(const.USERDIR, "autoqueue_block_cache")
    except AttributeError:
        DUMP = os.path.join(const.DIR, "autoqueue_block_cache")
    try:
        DB = os.path.join(const.USERDIR, "similarity.db")
    except AttributeError:
        DB = os.path.join(const.DIR, "similarity.db")
        
    __enabled = False
    
    def __init__(self):
        self.artist_block_time = 7
        self.track_block_time = 30
        self.desired_queue_length = 4440
        self.cache_time = 90
        self.cache = SQL and True
        self.by_tracks = True
        self.by_artists = True
        self.by_tags = True
        self.pick = "best"
        self.running = False
        self.verbose = False
        self.now = datetime.now()
        self.connection = None
        self.song = None
        self._songs = deque([])
        self._blocked_artists = deque([])
        self._blocked_artists_times = deque([])
        self.relaxors = ''
        self.restrictors = ''
        self.read_config()
        self._artists_to_update = {}
        self._tracks_to_update = {}
        self._last_call = datetime.now()
        try:
            pickle = open(self.DUMP, 'r')
            try:
                unpickler = Unpickler(pickle)
                artists, times = unpickler.load()
                if isinstance(artists, list):
                    artists = deque(artists)
                if isinstance(times, list):
                    times = deque(times)
                self._blocked_artists = artists
                self._blocked_artists_times = times
            finally:
                pickle.close()
        except IOError:
            pass
        if self.cache:
            try:
                os.stat(self.DB)
                self.prune_db()
            except OSError:
                self.create_db()
        # Set up exit hook to dump cache
        gtk.quit_add(0, self.dump_stuff)

    def log(self, msg):
        """print debug messages"""
        if not self.verbose:
            return
        print "[autoqueue]", msg

    def read_config(self):
        """Initialize user settings from the config file"""
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
        self.log("create_db")
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

    def prune_db(self):
        connection = sqlite3.connect(self.DB)
        cursor = connection.cursor()
        cursor.execute(
            'DELETE FROM tracks WHERE updated IS NULL AND tracks.id NOT IN'
            ' (SELECT track1 FROM track_2_track);')
        connection.commit()
        cursor.execute(
            'DELETE FROM track_2_track WHERE track2 NOT IN (SELECT '
            'id FROM tracks);')
        connection.commit()
        cursor.execute(
            'DELETE FROM artists WHERE updated IS NULL AND artists.id NOT '
            'IN (SELECT tracks.artist FROM tracks) AND artists.id NOT IN '
            '(SELECT artist1 FROM artist_2_artist);'
            )
        cursor.execute(
            'DELETE FROM artist_2_artist WHERE artist2 NOT IN (SELECT '
            'id FROM artists);'
            )
        connection.commit()
        
    def dump_stuff(self):
        """dump persistent data to pickles
        """
        try:
            os.remove(self.DUMP)
        except OSError:
            pass
        if len(self._blocked_artists) == 0:
            return 0
        pickle = open(self.DUMP, 'w')
        try:
            pickler = Pickler(pickle, -1)
            to_dump = (self._blocked_artists, self._blocked_artists_times)
            pickler.dump(to_dump)
        finally:
            pickle.close()
        return 0

    def enabled(self):
        """user enabled the plugin"""
        self.log("enabled")
        self.__enabled = True

    def disabled(self):
        """user disabled the plugin"""
        self.log("disabled")
        self.__enabled = False

    def plugin_on_song_started(self, song):
        """Triggered when a song start. If the right conditions apply,
        we start looking for new songs to queue."""
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
        if self.running:
            return
        if self.queue_needs_songs():
            background = threading.Thread(None, self.add_to_queue) 
            background.setDaemon(True)
            background.start()

    def queue_needs_songs(self):
        """determine whether the queue needs more songs added"""
        model = main.playlist.q.get()
        time = sum([row.get("~#length", 0) for row in model])
        return time < self.desired_queue_length

    def song_generator(self):
        """yield songs that match the last song in the queue"""
        restrictions = construct_restrictions(
            self.track_block_time, self.relaxors, self.restrictors)
        if self.by_tracks:
            for match, artist, title in self.get_sorted_similar_tracks():
                if self.is_blocked(artist):
                    continue
                self.log("looking for: %s, %s, %s" % (match, artist, title))
                search = construct_track_search(artist, title, restrictions)
                songs = self.search(search)
                if songs:
                    yield random.choice(songs)
        if self.by_artists:
            for match, artist in self.get_sorted_similar_artists():
                if self.is_blocked(artist):
                    continue
                self.log("looking for: %s, %s" % (match, artist))
                search = construct_artist_search(artist, restrictions)
                songs = self.search(search)
                if songs:
                    yield random.choice(songs)
        if self.by_tags:
            tags = self.get_last_song().list("tag")
            exclude_artists = "&(%s)" % ",".join([
                '!artist = "%s"' %
                escape(artist) for artist in self.get_blocked_artists()])
            if tags:
                self.log("Searching for tags: %s" % tags)
                search = construct_tag_search(
                    tags, exclude_artists, restrictions)
                for song in self.search(search):
                    yield song
        return
        
    def add_to_queue(self):
        """search for appropriate songs and put them in the queue"""
        self.running = True
        self.connection = sqlite3.connect(self.DB)
        if len(self._songs) >= 10:
            self._songs.pop()
        while self.queue_needs_songs():
            self.unblock_artists()
            generator = self.song_generator()
            song = None
            try:
                song = generator.next()
                self.log("found song")
            except StopIteration:
                if self._songs:
                    song = self._songs.popleft()
                    while self.is_blocked(
                        song.comma("artist").lower()) and self._songs:
                        song = self._songs.pop()
                    if self.is_blocked(song.comma("artist").lower()):
                        song = None
            try:
                song2 = generator.next()
            except StopIteration:
                song2 = None
            if (song2 and not (song is song2) and not
                self.is_blocked(song2.comma("artist").lower())
                and not song2 in self._songs):
                self._songs = deque([
                    bsong for bsong in list(self._songs) if not
                    self.is_blocked(bsong.comma("artist").lower())])
                self._songs.appendleft(song2)
                if len(self._songs) > 10:
                    self._songs.pop()
                if self._songs:
                    self.log("%s backup songs: \n%s" % (
                        len(self._songs),
                        "\n".join(["%s - %s" % (
                        bsong.comma("artist"),
                        bsong.comma("title")) for bsong in list(self._songs)])))
            if song:
                gtk.gdk.threads_enter()
                main.playlist.enqueue([song])
                gtk.gdk.threads_leave()
        for artist_id in self._artists_to_update:
            self._update_similar_artists(artist_id, self._artists_to_update[artist_id])
        self._artists_to_update = {}
        for track_id in self._tracks_to_update:
            self._update_similar_tracks(track_id, self._tracks_to_update[track_id])
        self.connection.commit()
        self._tracks_to_update = {}
        self.running = False
       
    def block_artist(self, artist_name):
        """store artist name and current daytime so songs by that
        artist can be blocked
        """
        self._blocked_artists.append(artist_name)
        self._blocked_artists_times.append(self.now)
        self.log("Blocked artist: %s (%s)" % (
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
        """release blocked artists when they've been in the penalty
        box for long enough
        """
        while self._blocked_artists_times:
            if self._blocked_artists_times[
                0] + timedelta(self.artist_block_time) > self.now:
                break
            self.log("Unblocked %s (%s)" % (
                self._blocked_artists.popleft(),
                self._blocked_artists_times.popleft()))

    def is_blocked(self, artist_name):
        """check if the artist was played too recently"""
        return artist_name in self.get_blocked_artists()

    def get_blocked_artists(self):
        """prevent artists already in the queue from being queued"""
        return list(self._blocked_artists) + [
            song.comma("artist").lower() for song in main.playlist.q.get()]

    def get_last_song(self):
        """return the last song in the queue or the currently playing
        song"""
        if len(main.playlist.q):
            return main.playlist.q.get()[-1]
        return self.song

    @Cache(2000)
    def search(self, search):
        """perform a quod libet search"""
        try:
            myfilter = Query(search).search
            songs = filter(myfilter, library.itervalues())
        except (Query.error, RuntimeError):
            self.log("error in: %s" % search)
            return []
        return songs

    def get_artist_and_title(self, song):
        title = song.comma("title").lower()
        if "version" in song:
            title += " (%s)" % song.comma("version").lower()
        artist_name = song.comma("artist").lower()
        return (artist_name, title)

    @Cache(1000)
    def get_track_match(self, a1, t1, a2, t2):
        id1 = self.get_track(a1,t1)[0]
        id2 = self.get_track(a2,t2)[0]
        return max(
            self._get_track_match(id1, id2),
            self._get_track_match(id2, id1))

    @Cache(1000)
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
        self.log("Getting similar tracks from last.fm for: %s - %s" % (
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
            while self._last_call + WAIT_BETWEEN_REQUESTS > datetime.now():
                sleep(5)
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
            self._last_call = datetime.now()
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
            tracks.append((match, similar_artist, similar_title))
        return tracks
            
    def get_similar_artists(self):
        artist_name = self.get_artist_and_title(self.get_last_song())[0]
        self.log("Getting similar artists from last.fm for: %s " % artist_name)
        if ("&" in artist_name or "/" in artist_name or "?" in artist_name
            or "#" in artist_name):
            artist_name = urllib.quote_plus(artist_name)
        url = ARTIST_URL % (
            urllib.quote(artist_name.encode("utf-8")))
        try:
            while self._last_call + WAIT_BETWEEN_REQUESTS > datetime.now():
                sleep(5)
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
            self._last_call = datetime.now()
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
            artists.append((match, name))
        return artists

    @Cache(1000)
    def get_artist(self, artist_name):
        self.connection.commit()
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

    @Cache(2000)
    def get_track(self, artist_name, title):
        self.connection.commit()
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

    def get_sorted_similar_artists(self):
        if not self.cache:
            return sorted(list(set(self.get_similar_artists())), reverse=True)
        artist = self.get_artist(self.get_last_song().comma("artist").lower())
        artist_id, updated = artist[0], artist[2]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT match, name  FROM artist_2_artist INNER JOIN artists"
            " ON artist_2_artist.artist1 = artists.id WHERE"
            " artist_2_artist.artist2 = ?",
            (artist_id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                artist_name = self.get_artist_and_title(
                    self.get_last_song())[0]
                self.log(
                    "Getting similar artists from db for: %s " %
                    artist_name)
                cursor.execute(
                    "SELECT match, name  FROM artist_2_artist INNER JOIN"
                    " artists ON artist_2_artist.artist2 = artists.id WHERE"
                    " artist_2_artist.artist1 = ?",
                    (artist_id,))
                return sorted(list(set(cursor.fetchall() + reverse_lookup)),
                            reverse=True)
        similar_artists = self.get_similar_artists()
        #self._update_similar_artists(artist_id, similar_artists)
        self._artists_to_update[artist_id] = similar_artists
        return sorted(list(set(similar_artists + reverse_lookup)), reverse=True)

    def get_sorted_similar_tracks(self):
        if not self.cache:
            return sorted(list(set(self.get_similar_tracks())),reverse=True)
        artist, title = self.get_artist_and_title(self.get_last_song())
        track = self.get_track(artist, title)
        track_id, updated = track[0], track[3]
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT track_2_track.match, artists.name, tracks.title  FROM"
            " track_2_track INNER JOIN tracks ON track_2_track.track1"
            " = tracks.id INNER JOIN artists ON artists.id = tracks.artist"
            " WHERE track_2_track.track2 = ?",
            (track_id,))
        reverse_lookup = cursor.fetchall()
        if updated:
            updated = datetime(*strptime(updated, "%Y-%m-%d %H:%M:%S")[0:6])
            if updated + timedelta(self.cache_time) > self.now:
                self.log("Getting similar tracks from db for: %s - %s" % (
                    artist, title))
                cursor.execute(
                    "SELECT track_2_track.match, artists.name, tracks.title"
                    " FROM track_2_track INNER JOIN tracks ON"
                    " track_2_track.track2 = tracks.id INNER JOIN artists ON"
                    " artists.id = tracks.artist WHERE track_2_track.track1"
                    " = ?",
                    (track_id,))
                return sorted(list(set(cursor.fetchall() + reverse_lookup)),
                              reverse=True)
        similar_tracks = self.get_similar_tracks()
        #self._update_similar_tracks(track_id, similar_tracks)
        self._tracks_to_update[track_id] = similar_tracks
        return sorted(list(set(similar_tracks + reverse_lookup)), reverse=True)

    @Cache(1000)
    def _get_artist_match(self, a1, a2):
        self.connection.commit()
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT match FROM artist_2_artist WHERE artist1 = ?"
            " AND artist2 = ?",
            (a1, a2))
        row = cursor.fetchone()
        if not row: return 0
        return row[0]

    @Cache(1000)
    def _get_track_match(self, t1, t2):
        self.connection.commit()
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

    def _update_track_match(self, t1, t2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE track_2_track SET match = ? WHERE track1 = ? AND"
            " track2 = ?",
            (match, t1, t2))

    def _insert_artist_match(self, a1, a2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO artist_2_artist (artist1, artist2, match) VALUES"
            " (?, ?, ?)",
            (a1, a2, match))

    def _insert_track_match(self, t1, t2, match):
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO track_2_track (track1, track2, match) VALUES"
            " (?, ?, ?)",
            (t1, t2, match))

    def _update_artist(self, artist_id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE artists SET updated = DATETIME('now') WHERE id = ?",
            (artist_id,))

    def _update_track(self, track_id):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE tracks SET updated = DATETIME('now') WHERE id = ?",
            (track_id,))
        
    def _update_similar_artists(self, artist_id, similar_artists):
        for match, artist_name in similar_artists:
            id2 = self.get_artist(artist_name)[0]
            if self._get_artist_match(artist_id, id2):
                self._update_artist_match(artist_id, id2, match)
                continue
            self._insert_artist_match(artist_id, id2, match)
        self._update_artist(artist_id)
        
    def _update_similar_tracks(self, track_id, similar_tracks):
        for match, artist_name, title in similar_tracks:
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
