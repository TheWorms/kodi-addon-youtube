# -*- coding: utf-8 -*-
"""Routage des appels plugin://plugin.video.wtube/?action=..."""
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import feed, ui, widgets
from .extractor import DEFAULT_CLIENTS, Extractor, ExtractorError, find_js_runtime
from .playback import PlaybackMixin
from .storage import Storage
from .ui import L
from .utils import parse_target

ADDON_NAME = 'Wtube'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[wtube] %s' % msg, level)


class Router(PlaybackMixin):
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
                hls=self.addon.getSetting('stream_mode') != '0',  # nouvel identifiant : l'ancien réglage gardait « 360p » enregistré
                js_runtime=find_js_runtime(self._profile, self.addon.getSetting('js_runtime')),
                clients=self.addon.getSetting('yt_clients') or DEFAULT_CLIENTS,
            )
            log('moteur JavaScript : %s' % (self._extractor.js_runtime and ' '.join(self._extractor.js_runtime)
                                             or 'aucun (YouTube risque de bloquer la lecture)'))
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
            blocked = 'not a bot' in str(exc) or 'format' in str(exc).lower()
            if action == 'play' and blocked and not self.ex.js_runtime:
                self._offer_js_install()
            elif action == 'play':
                self.notify(L(30284) if blocked else L(30033), error=True)
            else:
                self.notify(str(exc)[:120], error=True)
            if action != 'play':
                self._end(ok=False)
        finally:
            self.db.close()

    # --- écrans ----------------------------------------------------------
    def a_root(self):
        # Interface plein écran seulement quand l'utilisateur ouvre l'addon depuis
        # Vidéos ; un widget ou le sélecteur de chemins du skin reçoit le menu.
        user_open = not xbmc.getCondVisibility(
            'Window.IsActive(home) | Window.IsActive(filebrowser) | Window.IsActive(selectdialog)'
            ' | Window.IsActive(addoninformation) | Window.IsActive(script-skinshortcuts.xml)'
            ' | Window.IsActive(script-skinshortcuts-menu.xml)')
        if self.addon.getSetting('ui_fullscreen') != 'false' and user_open:
            log('ouverture : interface plein écran')
            # L'invocation plugin rend la main à Kodi, puis la fenêtre s'ouvre
            xbmcplugin.endOfDirectory(self.handle, succeeded=False, cacheToDisc=False)
            try:
                from . import gui
                gui.open_main()
                return
            except Exception as exc:
                log('interface plein écran indisponible : %s' % exc, xbmc.LOGERROR)
                xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % self.url(action='classic'))
                return
        self.a_classic()

    def a_classic(self):
        entries = [
            (30001, dict(action='search_menu')),
            (30002, dict(action='subscriptions')),
            (30003, dict(action='feed')),
            (30004, dict(action='history')),
            (30005, dict(action='open_url')),
            (30270, dict(action='widgets')),
            (30006, dict(action='settings')),
        ]
        self._add([ui.folder_item(L(i), self.url(**p)) for i, p in entries])
        self._end(content='files', cache=True)

    # --- widgets --------------------------------------------------------
    def a_widgets(self):
        """Dossier parcouru par le sélecteur de widgets du skin."""
        items = [ui.folder_item(L(sid), self.url(action=action)) for sid, action in (
            (30271, 'w_new'), (30272, 'w_per_channel'), (30273, 'w_resume'),
            (30206, 'w_recent'), (30250, 'w_favorites'), (30274, 'w_channels'))]
        items.append(ui.folder_item(L(30275), self.url(action='widgets_channels')))
        items.append(ui.folder_item(L(30276), self.url(action='widgets_searches')))
        self._add(items)
        self._end(content='files')

    def a_widgets_channels(self):
        self._add([ui.folder_item(L(30277) % s['name'], self.url(action='w_channel', channel=s['channel_id']),
                                  s.get('thumb') or '')
                   for s in self.db.list_subs()])
        self._end(content='files')

    def a_widgets_searches(self):
        self._add([ui.folder_item(L(30278) % q, self.url(action='w_search', q=q))
                   for q in self.db.list_searches()])
        self._end(content='files')

    def _feed(self):
        """Nouveautés pour les widgets et la liste classique : le cache tenu à
        jour par le service, quel que soit son âge ; YouTube n'est interrogé
        ici que s'il n'existe pas encore (premier lancement)."""
        subs = [s['channel_id'] for s in self.db.list_subs()]
        entries = feed.cached_entries(self._profile, subs) if subs else []
        if entries is None:
            entries = feed.fetch_all_cached(subs, self._profile, widgets.feed_ttl(self.addon))
        return feed.without_shorts(entries, widgets.hide_shorts(self.addon))

    def _widget(self, entries, limit=30):
        entries = [e for e in entries if e.get('id')][:limit]
        resume = self.db.resume_map(e['id'] for e in entries)
        favs = self.db.favorite_ids()
        self._add([widgets.video_li(e, self.url, L, resume.get(e['id']), e['id'] in favs) for e in entries])
        self._end()

    def a_w_new(self):
        self._widget(self._feed())

    def a_w_per_channel(self):
        self._widget(widgets.latest_per_channel(self._feed()), limit=50)

    def a_w_channel(self):
        cid = self.params.get('channel', '')
        self._widget([e for e in self._feed() if e['channel_id'] == cid])

    def a_w_resume(self):
        hist = self.db.list_history(0, 200)
        resume = self.db.resume_map(e['id'] for e in hist)
        self._widget([e for e in hist if e['id'] in resume])

    def a_w_recent(self):
        self._widget(self.db.list_history(0, 30))

    def a_w_favorites(self):
        self._widget(self.db.list_favorites(0, 50), limit=50)

    def a_w_channels(self):
        self._add([widgets.channel_li(s, self.url) for s in self.db.list_subs()])
        self._end(content='files')

    def a_w_search(self):
        query = self.params.get('q', '')
        entries = widgets.cached_search(self.ex, self._profile, query, 20) if query else []
        self._widget(entries)

    # --- moteur JavaScript ----------------------------------------------
    def _offer_js_install(self):
        if xbmcgui.Dialog().yesno(ADDON_NAME, L(30281)):
            self._install_js()

    def a_js_install(self):
        self._install_js()

    def _install_js(self):
        """Le téléchargement est confié à l'addon « Moteur JavaScript »."""
        try:
            import jsruntime
        except ImportError:
            if xbmcgui.Dialog().yesno(ADDON_NAME, L(30287)):
                xbmc.executebuiltin('InstallAddon(script.module.jsruntime)')
            return
        if jsruntime.install_with_dialog(ADDON_NAME):
            self._extractor = None  # le moteur sera pris en compte dès la prochaine extraction

    def a_fav_toggle(self):
        vid = self.params['id']
        if vid in self.db.favorite_ids():
            self.db.remove_favorite(vid)
            self.notify(L(30262))
        else:
            self.db.add_favorite(vid, self.params.get('title', vid), self.params.get('channel', ''),
                                 self.params.get('channel_id', ''), '', int(self.params.get('duration') or 0))
            self.notify(L(30261))
        xbmc.executebuiltin('Container.Refresh')

    def a_mark_watched(self):
        self.db.clear_resume(self.params['id'])
        xbmc.executebuiltin('Container.Refresh')

    def a_settings(self):
        self.addon.openSettings()
        xbmcplugin.endOfDirectory(self.handle, succeeded=False, cacheToDisc=False)

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
        everything = self._feed()
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
