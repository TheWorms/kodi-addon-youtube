# -*- coding: utf-8 -*-
"""Routage des appels plugin://plugin.video.ytlocal/?action=..."""
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import feed, ui
from .extractor import Extractor, ExtractorError
from .storage import Storage
from .ui import L
from .utils import parse_target

ADDON_NAME = 'Kodi YouTube'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[ytlocal] %s' % msg, level)


class Router:
    def __init__(self, argv):
        self.base = argv[0]
        self.handle = int(argv[1])
        self.params = dict(parse_qsl(argv[2][1:])) if len(argv) > 2 else {}

        # Instance fraîche à chaque appel : les réglages sont toujours à jour.
        self.addon = xbmcaddon.Addon()
        profile = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
        self.db = Storage(profile)
        self.page_size = self.addon.getSettingInt('page_size') or 20
        self.page = int(self.params.get('page', 1))
        self._extractor = None
        self._profile = profile

    # --- helpers ---------------------------------------------------------
    @property
    def ex(self):
        if self._extractor is None:
            self._extractor = Extractor(
                self._profile,
                cookies=self.addon.getSettingString('cookies_file'),
                max_height=self.addon.getSettingInt('max_height') or 1080,
                hls=self.addon.getSettingInt('playback_mode') == 1,
            )
        return self._extractor

    def url(self, **kwargs):
        return '%s?%s' % (self.base, urlencode(kwargs))

    def notify(self, message, error=False):
        icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
        xbmcgui.Dialog().notification(ADDON_NAME, message, icon)

    def _end(self, content='videos', cache=False, ok=True):
        xbmcplugin.setContent(self.handle, content)
        xbmcplugin.endOfDirectory(self.handle, succeeded=ok, cacheToDisc=cache)

    def _add(self, items):
        xbmcplugin.addDirectoryItems(self.handle, items, len(items))

    def _render(self, entries, next_params=None, in_history=False):
        if not entries and self.page == 1:
            self.notify(L(30032))
        subs = self.db.sub_ids()
        items = [ui.make_item(e, self.url, subs, in_history) for e in entries]
        if next_params and len(entries) >= self.page_size:
            items.append(ui.folder_item(L(30010), self.url(**next_params)))
        self._add(items)
        self._end()

    # --- dispatch --------------------------------------------------------
    def dispatch(self):
        action = self.params.get('action', 'root')
        handler = getattr(self, 'a_' + action, None)
        if handler is None:
            log('action inconnue : %s' % action, xbmc.LOGWARNING)
            return self.a_root()
        try:
            handler()
        except ExtractorError as exc:
            log('erreur extraction (%s) : %s' % (action, exc), xbmc.LOGERROR)
            self.notify(L(30033) if action == 'play' else str(exc)[:120], error=True)
            if action != 'play':
                self._end(ok=False)
        finally:
            self.db.close()

    # --- écrans ----------------------------------------------------------
    def a_root(self):
        entries = [
            (30001, dict(action='search_menu')),
            (30002, dict(action='subscriptions')),
            (30003, dict(action='feed')),
            (30004, dict(action='history')),
            (30005, dict(action='open_url')),
            (30006, dict(action='settings')),
        ]
        self._add([ui.folder_item(L(i), self.url(**p)) for i, p in entries])
        self._end(content='files', cache=True)

    def a_settings(self):
        self.addon.openSettings()

    # recherche -----------------------------------------------------------
    def a_search_menu(self):
        items = [
            ui.folder_item(L(30011), self.url(action='search', kind='video')),
            ui.folder_item(L(30012), self.url(action='search', kind='channel')),
            ui.folder_item(L(30013), self.url(action='search', kind='playlist')),
            ui.folder_item(L(30007), self.url(action='search_history')),
        ]
        self._add(items)
        self._end(content='files')

    def a_search_history(self):
        items = [
            ui.folder_item(q, self.url(action='search', kind='video', q=q))
            for q in self.db.list_searches()
        ]
        if items:
            li = xbmcgui.ListItem(label=L(30025))
            items.append((self.url(action='searches_clear'), li, False))
        self._add(items)
        self._end(content='files')

    def a_searches_clear(self):
        self.db.clear_searches()
        xbmc.executebuiltin('Container.Refresh')

    def a_search(self):
        kind = self.params.get('kind', 'video')
        query = self.params.get('q')
        if not query:
            kb = xbmc.Keyboard('', L(30030))
            kb.doModal()
            if not kb.isConfirmed() or not kb.getText().strip():
                return self._end(ok=False)
            query = kb.getText().strip()
            self.db.add_search(query)
        entries = self.ex.search(query, self.page, self.page_size, kind)
        self._render(entries, dict(action='search', kind=kind, q=query, page=self.page + 1))

    # chaînes / playlists ---------------------------------------------------
    def a_channel(self):
        channel = self.params['channel']
        entries = self.ex.channel_videos(channel, self.page, self.page_size)
        self._render(entries, dict(action='channel', channel=channel, page=self.page + 1))

    def a_playlist(self):
        pid = self.params['id']
        entries = self.ex.playlist(pid, self.page, self.page_size)
        self._render(entries, dict(action='playlist', id=pid, page=self.page + 1))

    # abonnements ---------------------------------------------------------
    def a_subscriptions(self):
        subs = self.db.list_subs()
        if not subs:
            self.notify(L(30037))
        items = []
        for s in subs:
            url, li, folder = ui.folder_item(s['name'], self.url(action='channel', channel=s['channel_id']), s['thumb'])
            li.addContextMenuItems([(L(30021), 'RunPlugin(%s)' % self.url(action='sub_remove', id=s['channel_id']))])
            items.append((url, li, folder))
        self._add(items)
        self._end(content='files')

    def a_sub_add(self):
        self.db.add_sub(self.params['id'], self.params.get('name') or self.params['id'])
        self.notify(L(30034))

    def a_sub_remove(self):
        self.db.remove_sub(self.params['id'])
        self.notify(L(30035))
        xbmc.executebuiltin('Container.Refresh')

    def a_feed(self):
        ids = list(self.db.sub_ids())
        if not ids:
            self.notify(L(30037))
            return self._end(ok=False)
        # TODO : mettre en cache (fichier + durée de vie) pour ne pas refaire N requêtes par page
        everything = feed.fetch_all(ids)
        start = (self.page - 1) * self.page_size
        entries = everything[start:start + self.page_size]
        self._render(entries, dict(action='feed', page=self.page + 1))

    # historique ----------------------------------------------------------
    def a_history(self):
        offset = (self.page - 1) * self.page_size
        entries = self.db.list_history(offset, self.page_size)
        items = [ui.make_item(e, self.url, self.db.sub_ids(), in_history=True) for e in entries]
        if len(entries) >= self.page_size:
            items.append(ui.folder_item(L(30010), self.url(action='history', page=self.page + 1)))
        if entries and self.page == 1:
            items.append((self.url(action='history_clear'), xbmcgui.ListItem(label=L(30024)), False))
        self._add(items)
        self._end()

    def a_history_remove(self):
        self.db.remove_history(self.params['id'])
        xbmc.executebuiltin('Container.Refresh')

    def a_history_clear(self):
        self.db.clear_history()
        xbmc.executebuiltin('Container.Refresh')

    # ouvrir une URL ------------------------------------------------------
    def a_open_url(self):
        text = xbmcgui.Dialog().input(L(30031), type=xbmcgui.INPUT_ALPHANUM)
        kind, value = parse_target(text)
        if kind is None:
            if text:
                self.notify(L(30032))
            return self._end(ok=False)
        if kind == 'video':
            xbmc.executebuiltin('PlayMedia(%s)' % self.url(action='play', id=value))
            return self._end(ok=False)
        action = {'playlist': 'playlist', 'channel': 'channel'}[kind]
        key = 'id' if kind == 'playlist' else 'channel'
        xbmc.executebuiltin('Container.Update(%s)' % self.url(action=action, **{key: value}))
        self._end(ok=False)

    # lecture -------------------------------------------------------------
    def a_play(self):
        video_id = self.params['id']
        try:
            stream = self.ex.resolve(video_id)
        except ExtractorError:
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            raise
        xbmcplugin.setResolvedUrl(self.handle, True, ui.playable_item(stream))
        self.db.add_history(video_id, stream.title, stream.channel, stream.channel_id,
                            stream.thumb, stream.duration)
        if self.addon.getSettingBool('sponsorblock') and not stream.is_live:
            self._sponsorblock(video_id)

    def _sponsorblock(self, video_id):
        from .sponsorblock import get_segments

        segments = get_segments(video_id)
        if not segments:
            return
        monitor, player = xbmc.Monitor(), xbmc.Player()
        waited = 0.0
        while not player.isPlayingVideo():  # attendre le démarrage de la lecture
            if monitor.waitForAbort(0.5) or waited > 30:
                return
            waited += 0.5
        done = set()
        while player.isPlayingVideo() and not monitor.abortRequested():
            try:
                now = player.getTime()
            except RuntimeError:
                break
            for i, (start, end) in enumerate(segments):
                if i not in done and start <= now < end - 0.5:
                    done.add(i)
                    player.seekTime(end)
                    self.notify(L(30036))
            if monitor.waitForAbort(0.5):
                break
