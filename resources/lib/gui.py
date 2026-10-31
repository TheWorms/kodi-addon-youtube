# -*- coding: utf-8 -*-
"""Interface plein écran (WindowXML 1080i).

Écrans : accueil (vidéo à la une, nouveautés des abonnements, vidéos
récentes), recherche, abonnements, historique, page chaîne, playlist.
La lecture passe par l'URL du plugin (action=play) : relais SponsorBlock,
repli HLS et historique restent gérés au même endroit.

Chargements dans des threads, avec un jeton de génération par écran : un
chargement dépassé par un plus récent n'écrit plus rien, et plus aucun
thread ne touche la fenêtre après sa fermeture.
"""
import datetime
import hashlib
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import feed, upnext, widgets
from .extractor import DEFAULT_CLIENTS, Extractor, ExtractorError, find_js_runtime
from .storage import Storage
from .utils import fmt_count, parse_target

ADDON_ID = 'plugin.video.wtube'
ADDON_PATH = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('path'))
PROFILE = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile'))
XML_FILE = 'script-wtube-main.xml'

GUARD_PROP = 'wtube.ui.window'
CLOSED_PROP = 'wtube.ui.closed_at'
REOPEN_GRACE = 3.0

NAV = 9000
ROW_NEW, ROW_RECENT = 301, 302
BTN_PLAY, BTN_CHANNEL, BTN_SUB = 311, 312, 313
SEARCH_FIELD, SEARCH_FILTERS, SEARCH_RECENT = 401, 402, 405
RESULTS = {'video': 406, 'channel': 407, 'playlist': 408}
SUBS_GRID, HISTORY_GRID, FAV_GRID = 501, 601, 901
CH_SUB, CH_BACK, CH_TABS, CH_GRID = 711, 712, 713, 701
PL_BACK, PL_GRID = 812, 801
ITEM_CONTAINERS = (ROW_NEW, ROW_RECENT, 406, 407, 408, SUBS_GRID, HISTORY_GRID, CH_GRID, PL_GRID, FAV_GRID)

A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
ACTION_BACK = (9, 10, 92, 216, 247, 275)
ACTION_CONTEXT = (101, 117)

NAV_ITEMS = (('home', 30200, 'home'), ('search', 30201, 'search'), ('subs', 30241, 'subs'),
             ('favorites', 30250, 'star'), ('history', 30203, 'history'), ('settings', 30204, 'settings'))
FILTERS = (('video', 30011), ('channel', 30012), ('playlist', 30013))
TABS = (('videos', 30011), ('playlists', 30013))
PALETTE = ('FF2F5878', 'FF7A5636', 'FF7C3448', 'FF4A3C80', 'FF2F7253', 'FF806B2C', 'FF6A2F6E', 'FF2C6B6B')


def L(sid):
    return xbmcaddon.Addon(ADDON_ID).getLocalizedString(sid)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[wtube] ui : %s' % msg, level)


def setting_int(key, default):
    # Lecture texte : getSettingInt s'est montré peu fiable avec les listes (cf. Radio)
    try:
        value = xbmcaddon.Addon(ADDON_ID).getSetting(key)
        return int(value) if value != '' else default
    except (ValueError, RuntimeError):
        return default


def media(name):
    return os.path.join(ADDON_PATH, 'resources', 'skins', 'Default', 'media', name)


def notify(msg, error=False):
    xbmcgui.Dialog().notification('Wtube', msg,
                                  xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO)


# --- mise en forme ------------------------------------------------------------
def fmt_duration(seconds):
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return ''
    if s <= 0:
        return ''
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


def hours_since(iso):
    try:
        published = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - published).total_seconds() / 3600.0


def fmt_age(hours):
    if hours is None:
        return ''
    if hours < 24:
        return L(30230) % max(1, int(hours))
    days = int(hours // 24)
    if days < 7:
        return L(30231) % days
    if days < 60:
        return L(30232) % (days // 7)
    return L(30238) % (days // 30)


def initials(name):
    words = [w for w in (name or '').replace('&', ' ').split() if w[:1].isalnum()]
    return (''.join(w[0] for w in words[:2]) or (name or '?')[:2]).upper()


def color_for(key):
    return PALETTE[int(hashlib.md5((key or '').encode('utf-8')).hexdigest(), 16) % len(PALETTE)]


def views_text(views):
    return L(30240) % fmt_count(views) if views else ''


# --- éléments de liste --------------------------------------------------------
def video_item(e, label2=None, new=False):
    channel = e.get('channel') or ''
    age = e.get('age') or ''
    li = xbmcgui.ListItem(e.get('title') or '')
    li.setLabel2(label2 if label2 is not None else '  ·  '.join(x for x in (channel, age) if x))
    li.setArt({'thumb': e.get('thumb') or ''})
    for key, value in (('kind', 'video'), ('ytid', e.get('id') or ''), ('channel', channel),
                       ('channel_id', e.get('channel_id') or ''), ('duration', fmt_duration(e.get('duration'))),
                       ('views', views_text(e.get('views'))), ('age', age),
                       ('plot', e.get('description') or ''), ('new', '1' if new else ''),
                       ('seconds', str(int(e.get('duration') or 0)))):
        li.setProperty(key, value)
    return li


def channel_item(e):
    name = e.get('title') or e.get('channel') or ''
    li = xbmcgui.ListItem(name)
    li.setLabel2(L(30220) % fmt_count(e['subs']) if e.get('subs') else '')
    li.setArt({'thumb': e.get('thumb') or ''})
    li.setProperty('kind', 'channel')
    li.setProperty('ytid', e.get('channel_id') or e.get('id') or e.get('url') or '')
    li.setProperty('initials', initials(name))
    li.setProperty('color', color_for(name))
    return li


def playlist_item(e):
    li = xbmcgui.ListItem(e.get('title') or '')
    li.setLabel2(e.get('channel') or L(30234))
    li.setArt({'thumb': e.get('thumb') or ''})
    li.setProperty('kind', 'playlist')
    li.setProperty('ytid', e.get('id') or '')
    li.setProperty('count', str(e['count']) if e.get('count') else '')
    return li


def entry_item(e):
    if e['kind'] == 'channel':
        return channel_item(e)
    if e['kind'] == 'playlist':
        return playlist_item(e)
    return video_item(e, label2='  ·  '.join(x for x in (e.get('channel'), views_text(e.get('views'))) if x))


def more_item():
    li = xbmcgui.ListItem(L(30212))
    li.setProperty('kind', 'more')
    return li


def chip_items(pairs, selected):
    items = []
    for key, label in pairs:
        li = xbmcgui.ListItem(label)
        li.setProperty('key', key)
        li.setProperty('selected', '1' if key == selected else '')
        items.append(li)
    return items


class MainWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        # Kodi construit la fenêtre dans __new__ avec ces arguments : ne pas les
        # repasser à __init__ (TypeError sous Python 3).
        self._ready = False
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._busy = 0
        self._gen = {}
        self._ex = None
        self.screen = 'home'
        self.root = 'home'
        self.stack = []
        self.subtitles = {}
        self.hero = {}
        self.feed = []
        self.home_dirty = True
        self.first_focus = True
        self.query = ''
        self.filter = 'video'
        self.search_state = {k: {'query': None, 'page': 0} for k, _ in FILTERS}
        self.ch = {}
        self.pl = {}
        self.history_page = 1
        self.loaded = {'channel': None, 'list': None}  # contenu affiché dans les grilles partagées
        self.page_size = max(10, setting_int('page_size', 20))

    # --- infrastructure ---------------------------------------------------
    @property
    def ex(self):
        if self._ex is None:
            self._ex = Extractor(
                PROFILE,
                cookies=xbmcaddon.Addon(ADDON_ID).getSetting('cookies_file'),
                max_height=setting_int('max_height', 1080),
                hls=setting_int('stream_mode', 1) == 1,
                js_runtime=find_js_runtime(PROFILE, xbmcaddon.Addon(ADDON_ID).getSetting('js_runtime')),
                clients=xbmcaddon.Addon(ADDON_ID).getSetting('yt_clients') or DEFAULT_CLIENTS,
            )
        return self._ex

    def _db(self):
        return Storage(PROFILE)  # une connexion par opération : SQLite reste dans son thread

    def _run(self, fn, *args):
        def task():
            with self._lock:
                self._busy += 1
            self._prop('busy', '1')
            try:
                fn(*args)
            except ExtractorError as exc:
                log('%s : %s' % (fn.__name__, exc), xbmc.LOGWARNING)
                if not self._closed.is_set():
                    notify(str(exc)[:120], error=True)
            except Exception as exc:  # une erreur ne doit jamais figer l'interface
                import traceback
                log('%s : %s\n%s' % (fn.__name__, exc, traceback.format_exc()), xbmc.LOGERROR)
            finally:
                with self._lock:
                    self._busy -= 1
                    idle = self._busy == 0
                if idle:
                    self._prop('busy', '')
        threading.Thread(target=task, daemon=True).start()

    def _next_gen(self, key):
        with self._lock:
            self._gen[key] = self._gen.get(key, 0) + 1
            return self._gen[key]

    def _stale(self, key, gen):
        return self._closed.is_set() or self._gen.get(key) != gen

    def _prop(self, key, value):
        if not self._closed.is_set():
            self.setProperty(key, value or '')

    def _ctrl(self, cid):
        try:
            return self.getControl(cid)
        except (RuntimeError, TypeError):
            return None

    def _size(self, cid):
        c = self._ctrl(cid)
        try:
            return c.size() if c else 0
        except RuntimeError:
            return 0

    def _focus_id(self):
        try:
            return self.getFocusId()
        except RuntimeError:
            return 0

    def _focus(self, cid, delay=True):
        if delay:
            xbmc.sleep(80)  # laisse Kodi rendre visible le groupe de l'écran
        try:
            self.setFocusId(cid)
        except RuntimeError:
            pass

    def _fill(self, cid, items, append=False, more=False):
        if self._closed.is_set():
            return
        c = self._ctrl(cid)
        if c is None:
            return
        if append:
            n = c.size()
            if n and c.getListItem(n - 1).getProperty('kind') == 'more':
                c.removeItem(n - 1)
        else:
            c.reset()
        if more:
            items = list(items) + [more_item()]
        if items:
            c.addItems(items)

    def _selected(self, cid):
        c = self._ctrl(cid)
        try:
            return c.getSelectedItem() if c else None
        except RuntimeError:
            return None

    # --- cycle de vie -----------------------------------------------------
    def onInit(self):
        if self._ready:
            # Retour de la lecture plein écran : l'historique a changé
            self._run(self._load_recent)
            return
        self._ready = True
        self._fill(NAV, self._nav_items())
        # La barre latérale est vide quand Kodi applique le focus par défaut
        # (« Control 9000 … can't focus ») : on le pose une fois remplie.
        self._focus(NAV, delay=False)
        self._fill(SEARCH_FILTERS, chip_items([(k, L(s)) for k, s in FILTERS], self.filter))
        self._prop('search.filter', self.filter)
        self._update_search_field()
        self.show('home', focus=False)
        self._run(self._load_home)

    def onClose(self):
        self._closed.set()

    def _quit(self):
        self._closed.set()
        self.close()

    # --- écrans -----------------------------------------------------------
    def _nav_items(self):
        items = []
        for key, sid, icon in NAV_ITEMS:
            li = xbmcgui.ListItem(L(sid))
            li.setArt({'icon': media('ytl_%s.png' % icon)})
            li.setProperty('key', key)
            li.setProperty('active', '1' if key == self.root else '')
            items.append(li)
        return items

    def _update_nav(self):
        c = self._ctrl(NAV)
        if c is None:
            return
        for i in range(c.size()):
            li = c.getListItem(i)
            li.setProperty('active', '1' if li.getProperty('key') == self.root else '')

    def _set_header(self):
        titles = {'home': L(30200), 'search': L(30201), 'subs': L(30202), 'history': L(30203),
                  'favorites': L(30250),
                  'channel': self.ch.get('name') or '', 'list': self.pl.get('title') or ''}
        self._prop('page.title', '' if self.screen == 'channel' else titles.get(self.screen, ''))
        self._prop('page.sub', self.subtitles.get(self.screen, ''))

    def show(self, screen, focus=True):
        self.screen = screen
        if screen in ('home', 'search', 'subs', 'favorites', 'history'):
            self.root = screen
        self._prop('screen', screen)
        self._update_nav()
        self._set_header()
        if focus:
            self._focus(self._main_control())

    def _main_control(self):
        s = self.screen
        if s == 'home':
            if self._size(ROW_NEW):
                return ROW_NEW
            if self.hero.get('id'):
                return BTN_PLAY
            return ROW_RECENT if self._size(ROW_RECENT) else NAV
        if s == 'search':
            cid = RESULTS[self.filter]
            return cid if self._size(cid) else SEARCH_FIELD
        if s == 'subs':
            return SUBS_GRID if self._size(SUBS_GRID) else NAV
        if s == 'history':
            return HISTORY_GRID if self._size(HISTORY_GRID) else NAV
        if s == 'favorites':
            return FAV_GRID if self._size(FAV_GRID) else NAV
        if s == 'channel':
            return CH_GRID if self._size(CH_GRID) else CH_SUB
        if s == 'list':
            return PL_GRID if self._size(PL_GRID) else PL_BACK
        return NAV

    def _push(self):
        fid = self._focus_id()
        c = self._ctrl(fid) if fid in ITEM_CONTAINERS else None
        self.stack.append({'screen': self.screen, 'focus': fid,
                           'pos': c.getSelectedPosition() if c else -1,
                           'ch': dict(self.ch), 'pl': dict(self.pl)})

    def _pop(self):
        state = self.stack.pop()
        self.ch, self.pl = state['ch'], state['pl']
        if state['screen'] == 'channel':
            self._set_channel_header()
        self.show(state['screen'], focus=False)
        target = state['focus'] if state['focus'] else self._main_control()
        self._focus(target)
        if state['pos'] >= 0:
            c = self._ctrl(target)
            if c:
                c.selectItem(state['pos'])
        # Grille chaîne/playlist réutilisée entre-temps par un autre contenu
        if state['screen'] == 'channel':
            self._fill(CH_TABS, chip_items([(k, L(s)) for k, s in TABS], self.ch.get('tab', 'videos')))
            if self.loaded['channel'] != (self.ch.get('key'), self.ch.get('tab')):
                self._fill(CH_GRID, [])
                self._run(self._load_channel, 1)
        elif state['screen'] == 'list' and self.loaded['list'] != self.pl.get('id'):
            self._fill(PL_GRID, [])
            self._run(self._load_playlist, 1)

    # --- actions ----------------------------------------------------------
    def onAction(self, action):
        aid = action.getId()
        if aid in ACTION_BACK:
            return self._back()
        if aid in ACTION_CONTEXT:
            return self._context_menu()
        fid = self._focus_id()
        # La navigation native a déjà eu lieu : on ne traite ici que les
        # déplacements dont la cible dépend de l'état.
        if fid == NAV and aid == A_RIGHT:
            self._focus(self._main_control(), delay=False)
        elif self.screen == 'search' and aid == A_DOWN and fid in (SEARCH_FIELD, SEARCH_FILTERS, SEARCH_RECENT):
            if fid != SEARCH_RECENT and self._size(SEARCH_RECENT):
                self._focus(SEARCH_RECENT, delay=False)
            elif self._size(RESULTS[self.filter]):
                self._focus(RESULTS[self.filter], delay=False)
        elif self.screen == 'home':
            if fid in (ROW_NEW, ROW_RECENT) and aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                li = self._selected(fid)
                if li is not None and li.getProperty('kind') == 'video':
                    self._set_hero(li)
            elif fid in (BTN_PLAY, BTN_CHANNEL, BTN_SUB) and aid == A_DOWN and not self._size(ROW_NEW):
                if self._size(ROW_RECENT):
                    self._focus(ROW_RECENT, delay=False)
            elif fid == ROW_RECENT and aid == A_UP and not self._size(ROW_NEW) and self.hero.get('id'):
                self._focus(BTN_PLAY, delay=False)

    def _back(self):
        if self.stack:
            return self._pop()
        if self._focus_id() != NAV:
            return self._focus(NAV, delay=False)
        self._quit()

    def onClick(self, cid):
        if cid == NAV:
            return self._nav_click()
        if cid in ITEM_CONTAINERS:
            return self._item_click(cid)
        if cid == BTN_PLAY and self.hero.get('id'):
            return self._play(self.hero['id'], self.hero.get('title'), self.hero.get('thumb_small'))
        if cid == BTN_CHANNEL and self.hero.get('channel_id'):
            return self.open_channel(self.hero['channel_id'], self.hero.get('channel'))
        if cid == BTN_SUB and self.hero.get('channel_id'):
            return self._toggle_sub(self.hero['channel_id'], self.hero.get('channel'))
        if cid == SEARCH_FIELD:
            return self._ask_search()
        if cid == SEARCH_FILTERS:
            li = self._selected(SEARCH_FILTERS)
            return self._set_filter(li.getProperty('key')) if li else None
        if cid == SEARCH_RECENT:
            li = self._selected(SEARCH_RECENT)
            if li:
                self.query = li.getLabel()
                self._update_search_field()
                self._run(self._search, 1)
            return None
        if cid == CH_SUB and self.ch.get('id'):
            return self._toggle_sub(self.ch['id'], self.ch.get('name'), self.ch.get('avatar', ''))
        if cid in (CH_BACK, PL_BACK):
            return self._back()
        if cid == CH_TABS:
            li = self._selected(CH_TABS)
            if li and li.getProperty('key') != self.ch.get('tab'):
                self.ch['tab'] = li.getProperty('key')
                self._fill(CH_TABS, chip_items([(k, L(s)) for k, s in TABS], self.ch['tab']))
                self._fill(CH_GRID, [])
                self._run(self._load_channel, 1)
        return None

    def _nav_click(self):
        li = self._selected(NAV)
        if li is None:
            return
        key = li.getProperty('key')
        if key == 'settings':
            xbmcaddon.Addon(ADDON_ID).openSettings()
            self._ex = None  # réglages de lecture relus au prochain appel
            self.page_size = max(10, setting_int('page_size', 20))
            return
        self.stack = []
        self.show(key)
        if key == 'home' and self.home_dirty:
            self._run(self._load_home)
        elif key == 'search':
            self._run(self._load_recent_searches)
        elif key == 'subs':
            self._run(self._load_subs)
        elif key == 'history':
            self._run(self._load_history, 1)
        elif key == 'favorites':
            self._run(self._load_favorites)

    def _item_click(self, cid):
        li = self._selected(cid)
        if li is None:
            return
        kind = li.getProperty('kind')
        if kind == 'more':
            self._load_more(cid)
        elif kind == 'video':
            self._play(li.getProperty('ytid'), li.getLabel(), li.getArt('thumb'))
        elif kind == 'channel':
            self.open_channel(li.getProperty('ytid'), li.getLabel(), li.getArt('thumb'))
        elif kind == 'playlist':
            self.open_playlist(li.getProperty('ytid'), li.getLabel())

    def _load_more(self, cid):
        if cid in RESULTS.values():
            self._run(self._search, self.search_state[self.filter]['page'] + 1)
        elif cid == CH_GRID:
            self._run(self._load_channel, self.ch.get('page', 1) + 1)
        elif cid == PL_GRID:
            self._run(self._load_playlist, self.pl.get('page', 1) + 1)
        elif cid == HISTORY_GRID:
            self._run(self._load_history, self.history_page + 1)

    def _following(self, video_id):
        """Vidéos qui suivent `video_id` dans la liste d'où elle est lancée."""
        fid = self._focus_id()
        cid = fid if fid in ITEM_CONTAINERS else (ROW_NEW if fid in (BTN_PLAY, BTN_CHANNEL, BTN_SUB) else 0)
        c = self._ctrl(cid) if cid else None
        if c is None:
            return []
        out, found = [], False
        try:
            for i in range(c.size()):
                li = c.getListItem(i)
                if li.getProperty('kind') != 'video':
                    continue
                vid = li.getProperty('ytid')
                if found and vid and vid != video_id:
                    out.append({'id': vid, 'title': li.getLabel(), 'thumb': li.getArt('thumb'),
                                'channel': li.getProperty('channel')})
                elif vid == video_id:
                    found = True
        except RuntimeError:
            return []
        return out

    def _play(self, video_id, title='', thumb=''):
        if not video_id:
            return
        upnext.save(PROFILE, video_id, self._following(video_id), from_gui=True)
        item = xbmcgui.ListItem(title or video_id)
        item.setArt({'thumb': thumb or '', 'icon': thumb or ''})
        item.getVideoInfoTag().setTitle(title or video_id)
        xbmc.Player().play('plugin://%s/?action=play&id=%s' % (ADDON_ID, video_id), item)

    def _context_menu(self):
        fid = self._focus_id()
        li = self._selected(fid) if fid in ITEM_CONTAINERS else None
        if li is None:
            return
        kind = li.getProperty('kind')
        options = []
        if kind == 'video':
            vid, title, thumb = li.getProperty('ytid'), li.getLabel(), li.getArt('thumb')
            cid, cname = li.getProperty('channel_id'), li.getProperty('channel')
            options.append((L(30207), lambda: self._play(vid, title, thumb)))
            videos = self._videos_of(fid)
            pos = next((i for i, v in enumerate(videos) if v['id'] == vid), -1)
            if len(videos) > 1:
                if 0 < pos < len(videos) - 1:
                    options.append((L(30296), lambda: self._play_all(videos[pos:])))
                options.append((L(30297), lambda: self._play_all(videos)))
            options.append((L(30251), lambda: self._queue(vid, title, thumb, play_next=True)))
            options.append((L(30252), lambda: self._queue(vid, title, thumb)))
            if self._is_favorite(vid):
                options.append((L(30254), lambda: self._toggle_favorite(li)))
            else:
                options.append((L(30253), lambda: self._toggle_favorite(li)))
            if cid and self.ch.get('id') != cid:
                options.append((L(30022), lambda: self.open_channel(cid, cname)))
            if cid:
                options.append((self._sub_label(cid, verb=True), lambda: self._toggle_sub(cid, cname)))
            if li.getProperty('progress'):
                options.append((L(30255), lambda: self._mark_watched(vid)))
            if fid in (ROW_RECENT, HISTORY_GRID):
                options.append((L(30023), lambda: self._remove_history(vid)))
        elif kind == 'playlist':
            pid, title = li.getProperty('ytid'), li.getLabel()
            options.append((L(30256), lambda: self.open_playlist(pid, title)))
            options.append((L(30257), lambda: self._run(self._play_playlist, pid)))
        elif kind == 'channel':
            cid = li.getProperty('ytid')
            options.append((L(30022), lambda: self.open_channel(cid, li.getLabel(), li.getArt('thumb'))))
            if cid.startswith('UC'):
                options.append((self._sub_label(cid, verb=True),
                                lambda: self._toggle_sub(cid, li.getLabel(), li.getArt('thumb'))))
        queue = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        if queue.size() and not xbmc.Player().isPlaying():
            options.append((L(30258) % queue.size(), lambda: xbmc.Player().play(queue)))
        if not options:
            return
        idx = xbmcgui.Dialog().contextmenu([label for label, _ in options])
        if idx >= 0:
            options[idx][1]()

    # --- file d'attente / favoris ------------------------------------------
    @staticmethod
    def _queue_item(video_id, title, thumb):
        url = 'plugin://%s/?action=play&id=%s' % (ADDON_ID, video_id)
        item = xbmcgui.ListItem(title or video_id)
        item.setArt({'thumb': thumb or '', 'icon': thumb or ''})
        item.setProperty('IsPlayable', 'true')
        item.getVideoInfoTag().setTitle(title or video_id)
        return url, item

    def _queue(self, video_id, title, thumb, play_next=False):
        """« Lire ensuite » : juste après la vidéo en cours (ou tout de suite si
        rien ne joue). « File d'attente » : à la fin de la liste de lecture."""
        player = xbmc.Player()
        queue = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        url, item = self._queue_item(video_id, title, thumb)
        if play_next:
            if player.isPlayingVideo():
                queue.add(url, item, queue.getposition() + 1)
                notify(L(30259))
            else:
                self._play(video_id, title, thumb)
            return
        queue.add(url, item)
        notify(L(30260) % queue.size())

    def _videos_of(self, cid):
        """Vidéos chargées dans un conteneur, dans l'ordre affiché."""
        c = self._ctrl(cid) if cid in ITEM_CONTAINERS else None
        out = []
        try:
            for i in range(c.size() if c else 0):
                li = c.getListItem(i)
                if li.getProperty('kind') == 'video' and li.getProperty('ytid'):
                    out.append({'id': li.getProperty('ytid'), 'title': li.getLabel(), 'thumb': li.getArt('thumb')})
        except RuntimeError:
            return []
        return out

    def _play_all(self, videos):
        """Lit la liste dans la file Kodi : Suivant / Précédent de la télécommande
        passent d'une vidéo à l'autre, sans question de reprise."""
        queue = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        queue.clear()
        for v in videos:
            url, item = self._queue_item(v['id'], v.get('title'), v.get('thumb'))
            queue.add(url + '&chain=1', item)
        xbmc.Player().play(queue)

    def _play_playlist(self, playlist_id):
        _title, entries = self.ex.playlist_page(playlist_id, 1, 100)
        entries = [e for e in entries if e.get('id')]
        if not entries or self._closed.is_set():
            return
        self._play_all(entries)

    def _is_favorite(self, video_id):
        db = self._db()
        try:
            return video_id in db.favorite_ids()
        finally:
            db.close()

    def _toggle_favorite(self, li):
        vid = li.getProperty('ytid')
        db = self._db()
        try:
            if vid in db.favorite_ids():
                db.remove_favorite(vid)
                notify(L(30262))
            else:
                db.add_favorite(vid, li.getLabel(), li.getProperty('channel'), li.getProperty('channel_id'),
                                li.getArt('thumb'), int(li.getProperty('seconds') or 0))
                notify(L(30261))
        finally:
            db.close()
        if self.screen == 'favorites':
            self._run(self._load_favorites)

    def _load_favorites(self):
        db = self._db()
        try:
            favs = db.list_favorites()
        finally:
            db.close()
        resume = self._resume_map(favs)
        items = []
        for e in favs:
            li = video_item(e)
            r = resume.get(e['id'])
            if r and r[1]:
                li.getVideoInfoTag().setResumePoint(r[0], r[1])
                li.setProperty('progress', '%d' % (100 * r[0] / r[1]))
            items.append(li)
        self._fill(FAV_GRID, items)
        self._prop('favorites.empty', '' if favs else L(30263))
        self.subtitles['favorites'] = L(30219) % len(favs) if favs else ''
        if self.screen == 'favorites':
            self._set_header()

    def _mark_watched(self, video_id):
        db = self._db()
        try:
            db.clear_resume(video_id)
        finally:
            db.close()
        self._run(self._load_recent)
        if self.screen == 'favorites':
            self._run(self._load_favorites)

    # --- abonnements ------------------------------------------------------
    def _is_sub(self, channel_id):
        db = self._db()
        try:
            return channel_id in db.sub_ids()
        finally:
            db.close()

    def _sub_label(self, channel_id, verb=False):
        if self._is_sub(channel_id):
            return L(30021) if verb else L(30210)
        return L(30020)

    def _toggle_sub(self, channel_id, name='', thumb=''):
        db = self._db()
        try:
            if channel_id in db.sub_ids():
                db.remove_sub(channel_id)
                notify(L(30035))
            else:
                db.add_sub(channel_id, name or channel_id, thumb or '')
                notify(L(30034))
        finally:
            db.close()
        self.home_dirty = True
        if self.hero.get('channel_id'):
            self._prop('hero.sub_label', self._sub_label(self.hero['channel_id']))
        if self.ch.get('id'):
            self._prop('ch.sub_label', self._sub_label(self.ch['id']))
        if self.screen == 'subs':
            self._run(self._load_subs)

    # --- accueil ----------------------------------------------------------
    def _set_hero(self, li):
        p = li.getProperty
        vid = p('ytid')
        self.hero = {'id': vid, 'title': li.getLabel(), 'channel': p('channel'),
                     'channel_id': p('channel_id'), 'thumb_small': li.getArt('thumb')}
        if p('new'):
            badge = '%s  ·  %s' % (L(30226), p('age')) if p('age') else L(30226)
        else:
            badge = p('badge') or L(30229)
        self._prop('hero.thumb', 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid if vid else '')
        self._prop('hero.title', li.getLabel())
        self._prop('hero.meta', '  ·  '.join(x for x in (p('channel'), p('views'), p('age')) if x))
        self._prop('hero.plot', p('plot'))
        self._prop('hero.duration', p('duration'))
        self._prop('hero.badge', badge.upper())
        self._prop('hero.sub_label', self._sub_label(p('channel_id')) if p('channel_id') else '')
        self._prop('hero.has', '1' if vid else '')

    def _set_welcome(self):
        self.hero = {}
        for key in ('hero.thumb', 'hero.meta', 'hero.duration', 'hero.has', 'hero.sub_label'):
            self._prop(key, '')
        self._prop('hero.badge', 'WTUBE')
        self._prop('hero.title', L(30218))
        self._prop('hero.plot', L(30217))

    def _load_home(self):
        gen = self._next_gen('home')
        self.home_dirty = False
        self._load_recent()
        db = self._db()
        try:
            subs = db.list_subs()
        finally:
            db.close()
        addon = xbmcaddon.Addon(ADDON_ID)
        entries = feed.fetch_all_cached([s['channel_id'] for s in subs], PROFILE,
                                        widgets.feed_ttl(addon)) if subs else []
        entries = feed.without_shorts(entries, widgets.hide_shorts(addon))
        if self._stale('home', gen):
            return
        for e in entries:
            e['hours'] = hours_since(e.get('published'))
            e['age'] = fmt_age(e['hours'])
        self.feed = entries
        fresh = [e for e in entries if e['hours'] is not None and e['hours'] < 48]
        items = [video_item(e, new=e in fresh) for e in entries[:40]]
        self._fill(ROW_NEW, items)
        self._prop('home.new_count', L(30219) % len(entries) if entries else '')
        self._prop('home.new_empty', '' if entries else (L(30217) if not subs else L(30032)))
        self.subtitles['home'] = L(30223) % len(fresh) if fresh else ''
        if items:
            self._set_hero(items[0])
        elif self._size(ROW_RECENT):
            self._set_hero(self._ctrl(ROW_RECENT).getListItem(0))
        else:
            self._set_welcome()
        if self.screen == 'home':
            self._set_header()
            if self.first_focus and self._focus_id() in (NAV, 0):
                self.first_focus = False
                self._focus(self._main_control(), delay=False)

    def _history_item(self, e, resume=None):
        when = ''
        if e.get('watched_at'):
            when = L(30228) % time.strftime('%d/%m', time.localtime(e['watched_at']))
        e = dict(e, thumb=e.get('thumb') or 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % e['id'])
        detail = when
        if resume and resume[1]:
            detail = L(30249) % fmt_duration(resume[1] - resume[0])
        li = video_item(e, label2='  ·  '.join(x for x in (e.get('channel'), detail) if x))
        li.setProperty('badge', when)
        if resume and resume[1]:
            # Barre de progression (ListItem.PercentPlayed) sur la vignette
            li.getVideoInfoTag().setResumePoint(resume[0], resume[1])
            li.setProperty('progress', '%d' % (100 * resume[0] / resume[1]))
        return li

    def _resume_map(self, entries):
        db = self._db()
        try:
            return db.resume_map(e['id'] for e in entries)
        finally:
            db.close()

    def _load_recent(self):
        db = self._db()
        try:
            hist = db.list_history(0, 20)
        finally:
            db.close()
        resume = self._resume_map(hist)
        self._fill(ROW_RECENT, [self._history_item(e, resume.get(e['id'])) for e in hist])
        self._prop('home.has_recent', '1' if hist else '')
        if self.screen == 'history':
            self._load_history(1)

    # --- recherche --------------------------------------------------------
    def _update_search_field(self):
        c = self._ctrl(SEARCH_FIELD)
        if c:
            c.setLabel(self.query or L(30216))

    def _load_recent_searches(self):
        db = self._db()
        try:
            recent = db.list_searches()[:10]
        finally:
            db.close()
        self._fill(SEARCH_RECENT, chip_items([(q, q) for q in recent], None))
        self._prop('search.has_recent', '1' if recent else '')
        if not self.query:
            self._prop('search.results', L(30214))

    def _ask_search(self):
        kb = xbmc.Keyboard(self.query, L(30030))
        kb.doModal()
        if not kb.isConfirmed():
            return
        text = kb.getText().strip()
        if not text:
            return
        kind, value = parse_target(text)
        if kind == 'video':
            return self._play(value)
        if kind == 'channel':
            return self.open_channel(value)
        if kind == 'playlist':
            return self.open_playlist(value)
        self.query = text
        db = self._db()
        try:
            db.add_search(text)
        finally:
            db.close()
        self._update_search_field()
        self._run(self._load_recent_searches)
        self._run(self._search, 1)

    def _set_filter(self, key):
        if not key or key == self.filter:
            return
        self.filter = key
        self._fill(SEARCH_FILTERS, chip_items([(k, L(s)) for k, s in FILTERS], key))
        self._prop('search.filter', key)
        state = self.search_state[key]
        if self.query and state['query'] != self.query:
            self._run(self._search, 1)
        elif self.query:
            self._prop('search.results', L(30215) % (state.get('count', 0), self.query))

    def _search(self, page):
        if not self.query:
            return
        kind = self.filter
        gen = self._next_gen('search')
        query = self.query
        if page == 1:
            self._prop('search.results', L(30213))
        entries = self.ex.search(query, page, self.page_size, kind)
        if self._stale('search', gen):
            return
        cid = RESULTS[kind]
        self._fill(cid, [entry_item(e) for e in entries], append=page > 1,
                   more=len(entries) >= self.page_size)
        state = self.search_state[kind]
        state.update(query=query, page=page,
                     count=(state.get('count', 0) if page > 1 else 0) + len(entries))
        self._prop('search.results', L(30215) % (state['count'], query) if state['count'] else L(30032))
        if page == 1 and self.screen == 'search' and self._focus_id() in (SEARCH_FIELD, SEARCH_RECENT, 0):
            if entries:
                self._focus(cid, delay=False)

    # --- abonnements / historique ----------------------------------------
    def _load_subs(self):
        db = self._db()
        try:
            subs = db.list_subs()
        finally:
            db.close()
        fresh = {}
        for e in self.feed:
            if e.get('hours') is not None and e['hours'] < 48:
                fresh[e['channel_id']] = fresh.get(e['channel_id'], 0) + 1
        items = []
        for s in subs:
            li = xbmcgui.ListItem(s['name'])
            n = fresh.get(s['channel_id'], 0)
            li.setLabel2(L(30237) % n if n else '')
            li.setArt({'thumb': s.get('thumb') or ''})
            li.setProperty('kind', 'channel')
            li.setProperty('ytid', s['channel_id'])
            li.setProperty('initials', initials(s['name']))
            li.setProperty('color', color_for(s['name']))
            items.append(li)
        self._fill(SUBS_GRID, items)
        self._prop('subs.empty', '' if subs else L(30233))
        self.subtitles['subs'] = L(30224) % len(subs) if subs else ''
        if self.screen == 'subs':
            self._set_header()

    def _load_history(self, page):
        size = self.page_size * 2
        db = self._db()
        try:
            hist = db.list_history((page - 1) * size, size)
        finally:
            db.close()
        self.history_page = page
        resume = self._resume_map(hist)
        self._fill(HISTORY_GRID, [self._history_item(e, resume.get(e['id'])) for e in hist], append=page > 1,
                   more=len(hist) >= size)
        if page == 1:
            self._prop('history.empty', '' if hist else L(30227))

    def _remove_history(self, video_id):
        db = self._db()
        try:
            db.remove_history(video_id)
        finally:
            db.close()
        self._run(self._load_recent)

    # --- chaîne -----------------------------------------------------------
    def open_channel(self, channel, name='', avatar=''):
        if not channel:
            return
        self._push()
        self.ch = {'key': channel, 'id': channel if channel.startswith('UC') else '',
                   'name': name or '', 'avatar': avatar or '', 'banner': '', 'subs': None,
                   'tab': 'videos', 'page': 1}
        self._set_channel_header()
        self._fill(CH_TABS, chip_items([(k, L(s)) for k, s in TABS], 'videos'))
        self._fill(CH_GRID, [])
        self.show('channel', focus=False)
        self._focus(CH_SUB)
        self._run(self._load_channel, 1)

    def _set_channel_header(self):
        ch = self.ch
        name = ch.get('name') or ''
        info = []
        if ch.get('subs'):
            info.append(L(30220) % fmt_count(ch['subs']))
        if ch.get('count'):
            info.append(L(30219) % ch['count'])
        self._prop('ch.name', name)
        self._prop('ch.info', '  ·  '.join(info))
        self._prop('ch.avatar', ch.get('avatar'))
        self._prop('ch.banner', ch.get('banner'))
        self._prop('ch.initials', initials(name) if name else '')
        self._prop('ch.color', color_for(name or ch.get('key')))
        self._prop('ch.sub_label', self._sub_label(ch['id']) if ch.get('id') else '')

    def _load_channel(self, page):
        gen = self._next_gen('channel')
        key, tab = self.ch.get('key'), self.ch.get('tab', 'videos')
        meta, entries = self.ex.channel_page(key, page, self.page_size, tab)
        if self._stale('channel', gen) or self.ch.get('key') != key:
            return
        self.ch.update({k: v for k, v in (('id', meta.get('channel_id')), ('name', meta.get('name')),
                                          ('avatar', meta.get('avatar')), ('banner', meta.get('banner')),
                                          ('subs', meta.get('subs'))) if v})
        self.ch['page'] = page
        self.loaded['channel'] = (key, tab)
        self._set_channel_header()
        if page == 1 and self.ch.get('id') and self.ch.get('avatar'):
            db = self._db()
            try:
                if self.ch['id'] in db.sub_ids():  # avatar pour la grille des abonnements
                    db.add_sub(self.ch['id'], self.ch.get('name') or self.ch['id'], self.ch['avatar'])
            finally:
                db.close()
        if tab == 'playlists':
            items = []
            for e in entries:
                li = playlist_item(e)
                li.setLabel2(L(30219) % e['count'] if e.get('count') else L(30234))
                items.append(li)
        else:
            items = [video_item(e, label2=views_text(e.get('views'))) for e in entries]
        self._fill(CH_GRID, items, append=page > 1, more=len(entries) >= self.page_size)
        if page == 1 and self.screen == 'channel' and self._focus_id() == CH_SUB and items:
            self._focus(CH_GRID, delay=False)

    # --- playlist ---------------------------------------------------------
    def open_playlist(self, playlist_id, title=''):
        if not playlist_id:
            return
        self._push()
        self.pl = {'id': playlist_id, 'title': title or L(30234), 'page': 1}
        self._prop('pl.info', '')
        self._fill(PL_GRID, [])
        self.show('list', focus=False)
        self._focus(PL_BACK)
        self._run(self._load_playlist, 1)

    def _load_playlist(self, page):
        gen = self._next_gen('list')
        pid = self.pl.get('id')
        title, entries = self.ex.playlist_page(pid, page, self.page_size)
        if self._stale('list', gen) or self.pl.get('id') != pid:
            return
        if title:
            self.pl['title'] = title
        self.pl['page'] = page
        self.loaded['list'] = pid
        items = [video_item(e, label2='  ·  '.join(x for x in (e.get('channel'), fmt_duration(e.get('duration')))
                                                    if x))
                 for e in entries]
        self._fill(PL_GRID, items, append=page > 1, more=len(entries) >= self.page_size)
        if self.screen == 'list':
            self._set_header()
        if page == 1 and self.screen == 'list' and self._focus_id() == PL_BACK and items:
            self._focus(PL_GRID, delay=False)


def open_main():
    """Ouvre l'interface (une seule à la fois, sans boucle de relance)."""
    home = xbmcgui.Window(10000)
    # Garde anti-doublon : une seule interface à la fois. Si la fenêtre n'est
    # plus visible (Kodi planté pendant qu'elle était ouverte), la garde est
    # périmée et se nettoie d'elle-même.
    if home.getProperty(GUARD_PROP):
        if xbmc.getCondVisibility('Window.IsVisible(%s)' % XML_FILE):
            log('interface déjà ouverte, relance ignorée')
            return
        home.clearProperty(GUARD_PROP)
    try:
        closed_at = float(home.getProperty(CLOSED_PROP) or 0)
    except ValueError:
        closed_at = 0
    if closed_at and time.time() - closed_at < REOPEN_GRACE:
        log('relance juste après fermeture, ignorée')
        return
    home.setProperty(GUARD_PROP, 'pending')
    try:
        win = MainWindow(XML_FILE, ADDON_PATH, 'Default', '1080i')
        home.setProperty(GUARD_PROP, '1')
        win.doModal()
        del win
    finally:
        home.clearProperty(GUARD_PROP)
        home.setProperty(CLOSED_PROP, '%f' % time.time())
        # Ouverte depuis Vidéos > Extensions, Kodi reviendrait sur la page
        # vide du plugin, qui relancerait l'interface : on rentre à l'accueil.
        if xbmc.getCondVisibility('String.StartsWith(Container.FolderPath,plugin://%s)' % ADDON_ID):
            xbmc.executebuiltin('ReplaceWindow(home)')
