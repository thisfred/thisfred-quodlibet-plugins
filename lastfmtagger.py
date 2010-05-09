"""
LastFMTagger: a Last.fm tagging plugin for Quod Libet.
version 0.1 (infrastructure copied from QLScrobbler 0.8)
(C) 2005-2007 by Eric Casteleijn <thisfred@gmail.com>
                 Joshua Kwan <joshk@triplehelix.org>,
                 Joe Wreschnig <piman@sacredchao.net>,
Licensed under GPLv2. See Quod Libet's COPYING for more information.
"""

import md5, urllib
import config, widgets
import gobject, gtk, xmlrpclib
from time import time
from xml.dom import minidom
from qltk.msg import Message
from quodlibet.util import copool
from plugins.events import EventPlugin

# Set this to True to enable logging
VERBOSE = True

def log(msg):
    """logging function"""
    if VERBOSE:
        print "[lastfmtagger]", msg


class LastFMTagger(EventPlugin):
    """Main plugin class"""
    # session invariants
    PLUGIN_ID = "LastFMTagger"
    PLUGIN_NAME = _("Last.fm Tagger")
    PLUGIN_DESC = "Synchronize tags between local files and last.fm"
    PLUGIN_ICON = gtk.STOCK_CONNECT
    PLUGIN_VERSION = "0.3"
    CLIENT = "lmt"
    CLIENT_VERSION = "0.1"
    PROTOCOL_VERSION = "1.2"
    TRACK_TAG_URL = \
        "http://ws.audioscrobbler.com/1.0/user/%s/tracktags.xml" \
        "?artist=%s&track=%s"
    ARTIST_TAG_URL = \
        "http://ws.audioscrobbler.com/1.0/user/%s/artisttags.xml?artist=%s"
    ALBUM_TAG_URL = \
        "http://ws.audioscrobbler.com/1.0/user/%s/albumtags.xml?" \
        "artist=%s&album=%s"

    # things that could change

    username = ""
    password = ""
    session = ""

    tag = 'grouping'

    # state management
    need_config = True
    __enabled = False

    lastfm_cache = {}

    def __init__(self):
        # Read configuration
        self.read_config()

    def plugin_on_song_started(self, song):
        """Triggered when song starts"""
        if song is None:
            return
        log("===> DOWN")
        copool.add(self.sync_down, song)

    def plugin_on_song_ended(self, song, skipped):
        """Triggered when song ends/is skipped"""
        if song is None:
            return
        log("===> UP")
        copool.add(self.sync_up, song)

    def read_config(self):
        """Read the options from the configuration file"""
        username = ""
        password = ""
        session = ""
        try:
            username = config.get("plugins", "lastfmtagger_username")
            password = config.get("plugins", "lastfmtagger_password")
            session = config.get("plugins", "lastfmtagger_session")
        except:
            if (self.need_config == False and
                getattr(self, 'PMEnFlag', False)):
                self.quick_dialog(
                    "Please visit the Preferences window to set LastFMTagger"
                    " up. Until then, tags will not be synchronized.",
                    gtk.MESSAGE_INFO)
                self.need_config = True
                return

        self.username = username
        self.session = session
        hasher = md5.new()
        hasher.update(password)
        self.password = hasher.hexdigest()
        self.tag = 'grouping'
        self.need_config = False

    def __destroy_cb(self, dialog, response_id):
        dialog.destroy()

    def quick_dialog_helper(self, dtype, dstr):
        dialog = Message(gtk.MESSAGE_INFO, widgets.main, "LastFMTagger", dstr)
        dialog.connect('response', self.__destroy_cb)
        dialog.show()

    def quick_dialog(self, dstr, dtype):
        gobject.idle_add(self.quick_dialog_helper, dtype, dstr)

    def enabled(self):
        self.__enabled = True

    def disabled(self):
        self.__enabled = False

    def get_lastfm_tags(self, title, artist, album):
        """Get the user's tags for the current track, album and artist
        from the audioscrobbler web service.
        """
        log("get lastfm tags")
        tags = set()
        if artist and title:
            url = self.TRACK_TAG_URL % (self.username, artist, title)
            tags |= self.get_lastfm_tags_from_url(url)
        if artist and album:
            url = self.ALBUM_TAG_URL % (self.username, artist, album)
            tags |= self.get_lastfm_tags_from_url(url, prefix='album')
        if artist:
            url = self.ARTIST_TAG_URL % (self.username, artist)
            tags |= self.get_lastfm_tags_from_url(url, prefix='artist')
        return tags

    def get_lastfm_tags_from_url(self, url, prefix=None):
        """Get the tags from the audioscrobbler webservice, if they
        aren't already in the cache.
        """
        log("get tags from url: %s " % url)
        tags = set()
        cached_tags = self.lastfm_cache.get(url, None)
        if not cached_tags is None:
            return cached_tags
        try:
            stream = urllib.urlopen(url)
            xmldoc = minidom.parse(stream).documentElement
        except:
            self.lastfm_cache[url] = tags
            return tags
        tagnodes = xmldoc.getElementsByTagName("tag")
        for tagnode in tagnodes:
            if prefix is not None:
                tag = "%s:%s" % (
                    prefix,
                    tagnode.getElementsByTagName(
                    "name")[0].firstChild.nodeValue)
                tags.add(tag.lower())
            else:
                tag = tagnode.getElementsByTagName(
                    "name")[0].firstChild.nodeValue
                tags.add(tag.lower())
        self.lastfm_cache[url] = tags
        return tags

    def submit_track_tags(self, song, tags):
        """Submit the tags to last.fm if locally changes are detected.
        """
        log("submitting track tags: %s " % ', '.join(tags))
        random_string, md5hash = self.get_timestamp()
        title = song.comma("title")
        if "version" in song:
            title += " (%s)" % song.comma("version").encode("utf-8")
        self._submit_track_tags(
            self.username,
            random_string,
            md5hash,
            song["artist"],
            title,
            list(tags),
            'set')

    def _submit_track_tags(self, *args):
        log("submitting track tags: %s " % repr(args))
        try:
            server = xmlrpclib.ServerProxy(
                "http://ws.audioscrobbler.com/1.0/rw/xmlrpc.php")
            server.tagTrack(*args)
        except:
            pass

    def get_tags_for(self, tags, for_=""):
        if for_:
            return set(':'.join(tag.lower().split(':')[1:]) for tag in tags
                       if tag.startswith('%s:' % for_))
        return set(tag.lower() for tag in tags if not (
            tag.startswith('album:') or tag.startswith('artist:')))

    def submit_artist_tags(self, song, tags):
        log("submitting artist tags: %s " % ', '.join(tags))
        random_string, md5hash = self.get_timestamp()
        self._submit_artist_tags(
            self.username, random_string, md5hash, song["artist"], list(tags),
            'set')

    def _submit_artist_tags(self, *args):
        log("submitting artist tags: %s " % repr(args))
        try:
            server = xmlrpclib.ServerProxy(
                "http://ws.audioscrobbler.com/1.0/rw/xmlrpc.php")
            server.tagArtist(*args)
        except:
            pass

    def submit_album_tags(self, song, tags):
        log("submitting album tags: %s " % ', '.join(tags))
        random_string, md5hash = self.get_timestamp()
        self._submit_album_tags(
            self.username, random_string, md5hash, song["artist"],
            song["album"], list(tags), 'set')

    def _submit_album_tags(self, *args):
        log("submitting album tags: %s " % repr(args))
        try:
            server = xmlrpclib.ServerProxy(
                "http://ws.audioscrobbler.com/1.0/rw/xmlrpc.php")
            server.tagAlbum(*args)
        except:
            pass

    def save_tags(self, song, tags):
        log("saving tags: %s" % ', '.join(tags))
        gtk.gdk.threads_enter()
        try:
            song[self.tag] = '\n'.join(tags)
            log("saved tags")
        finally:
            gtk.gdk.threads_leave()

    def submit_tags(self, song, artist, album, title, all_tags, lastfm_tags):
        log("submitting tags: %s" % ', '.join(all_tags))
        track_tags = self.get_tags_for(all_tags)
        lastfm_track_tags = self.get_tags_for(lastfm_tags)
        if track_tags != lastfm_track_tags:
            self.submit_track_tags(song, track_tags)
        self.lastfm_cache[
            self.TRACK_TAG_URL % (self.username, artist, title)] = track_tags
        artist_tags = self.get_tags_for(all_tags, for_="artist")
        lastfm_artist_tags = self.get_tags_for(lastfm_tags, for_="artist")
        if artist_tags != lastfm_artist_tags:
            self.submit_artist_tags(song, artist_tags)
        self.lastfm_cache[
            self.ARTIST_TAG_URL % (self.username, artist)] = artist_tags
        album_tags = self.get_tags_for(all_tags, for_="album")
        lastfm_album_tags = self.get_tags_for(lastfm_tags, for_="album")
        if album_tags != lastfm_album_tags:
            self.submit_album_tags(song, album_tags)
        self.lastfm_cache[
            self.ALBUM_TAG_URL % (self.username, artist, album)] = album_tags

    def sync_up(self, song):
        yield
        self.sync_tags(song, 'up')

    def sync_down(self, song):
        yield
        self.sync_tags(song, 'down')

    def sync_tags(self, song, direction):
        """
        This is the meat of the plugin. What it does is get the user's
        tags for the current track, album and artist from the in
        memory cache, and if it can't find them there it asks the
        audioscrobbler webservice for them. It then compares them to
        the tags saved in the 'tag' field of the actual file, and if
        there are differences, they are processed as followed: tags
        that are on last.fm but not on the file are saved to the file,
        and tags that are on the file but but not on last.fm are
        submitted.
        """
        log("syncing tags")
        title = urllib.quote_plus(song.comma("title").encode("utf-8"))
        if "version" in song:
            title += urllib.quote_plus(
                " (%s)" % song.comma("version").encode("utf-8"))
        artist = urllib.quote_plus(song.comma("artist").encode("utf-8"))
        album =  urllib.quote_plus(song.comma("album").encode("utf-8"))
        ql_tags = set()
        ql_tag_comma = song.comma(self.tag)

        log("local tags: %s" % ql_tag_comma)
        if ql_tag_comma:
            ql_tags = set([
                tag.lower().strip() for tag in ql_tag_comma.split(",")])
        lastfm_tags = self.get_lastfm_tags(title, artist, album)
        if direction == 'down':
            all_tags = ql_tags | lastfm_tags
        else:
            all_tags = ql_tags
        if direction == 'up':
            if all_tags != lastfm_tags:
                self.submit_tags(
                    song, artist, album, title, all_tags, lastfm_tags)
        if direction == 'down':
            if all_tags:
                self.save_tags(song, all_tags)

    def get_timestamp(self):
        timestamp = str(int(time()))
        return timestamp, md5.md5(self.password + timestamp).hexdigest()

    def PluginPreferences(self, parent):

        def changed(entry, key):
            # having a function for each entry is unnecessary..
            config.set("plugins", "lastfmtagger_" + key, entry.get_text())

        def destroyed(*args):
            # if changed, let's say that things just got better and we should
            # try everything again
            newu = None
            newp = None
            try:
                newu = config.get("plugins", "lastfmtagger_username")
                newp = config.get("plugins", "lastfmtagger_password")
            except:
                return

            if self.username != newu or self.password != newp:
                self.broken = False

        table = gtk.Table(6, 3)
        table.set_col_spacings(3)
        lt = gtk.Label(
            _("Please enter your Audioscrobbler\nusername and password."))
        lu = gtk.Label(_("Username:"))
        lp = gtk.Label(_("Password:"))
        ls = gtk.Label(_("Session:"))

        for l in [lt, lu, lp, ls]:
            l.set_line_wrap(True)
            l.set_alignment(0.0, 0.5)
        table.attach(lt, 0, 2, 0, 1, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(lu, 0, 1, 1, 2, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(lp, 0, 1, 2, 3, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(ls, 0, 1, 3, 4, xoptions=gtk.FILL | gtk.SHRINK)

        userent = gtk.Entry()
        pwent = gtk.Entry()
        session = gtk.Entry()
        pwent.set_visibility(False)
        pwent.set_invisible_char('*')

        table.set_border_width(6)
        try:
            userent.set_text(config.get("plugins", "lastfmtagger_username"))
        except:
            pass
        try:
            pwent.set_text(config.get("plugins", "lastfmtagger_password"))
        except:
            pass
        try:
            session.set_text(config.get("plugins", "lastfmtagger_session"))
        except:
            pass

        table.attach(userent, 1, 2, 1, 2, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(pwent, 1, 2, 2, 3, xoptions=gtk.FILL | gtk.SHRINK)
        table.attach(session, 1, 2, 3, 4, xoptions=gtk.FILL | gtk.SHRINK)
        pwent.connect('changed', changed, 'password')
        userent.connect('changed', changed, 'username')
        session.connect('changed', changed, 'session')

        table.connect('destroy', destroyed)
        return table
