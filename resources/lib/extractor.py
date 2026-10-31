# -*- coding: utf-8 -*-
"""Extraction via yt-dlp : aucune clé API, aucun compte Google.

C'est la seule couche qui parle à YouTube (avec feed.py pour le RSS).
Quand YouTube casse quelque chose, on met à jour yt-dlp (resources/lib/vendor) ;
le reste de l'addon ne bouge pas.
"""
import os
import shutil
import sys
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

import xbmc

# yt-dlp est embarqué dans resources/lib/vendor (absent du dépôt officiel Kodi).
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except ImportError:  # dossier vendor/ absent
    YoutubeDL = None
    DownloadError = Exception


RUNTIMES = (('deno', 'deno'), ('node', 'node'), ('qjs', 'quickjs'))
# mweb est la seule identité qui passe le contrôle anti-robot sur Cormoran
# (test du 31/10/2026, avec moteur JavaScript) ; réglable dans les réglages.
DEFAULT_CLIENTS = 'mweb'


def find_js_runtime(profile_dir, configured=''):
    """Moteur JavaScript pour les défis YouTube : (nom yt-dlp, chemin) ou None.

    Ordre : chemin indiqué dans les réglages, dossier bin/ du profil de
    l'addon (où le script d'installation dépose Deno ou Node), puis PATH.
    """
    try:  # addon séparé « Moteur JavaScript » (script.module.jsruntime)
        import jsruntime
        found = jsruntime.find()
        if found and not configured:
            return found
    except ImportError:
        pass
    candidates = []
    if configured:
        candidates.append(configured)
    bindir = os.path.join(profile_dir, 'bin')
    candidates += [os.path.join(bindir, exe) for exe, _ in RUNTIMES]
    candidates += [shutil.which(exe) or '' for exe, _ in RUNTIMES]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            base = os.path.basename(path).lower()
            for exe, key in RUNTIMES:
                if base.startswith(exe):
                    return key, path
    return None


class ExtractorError(Exception):
    """Erreur d'extraction, message affichable/loggable."""


class _KodiLogger:
    """Redirige les logs yt-dlp vers kodi.log (sans le bruit de debug)."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        xbmc.log('[wtube] yt-dlp WARNING: %s' % msg, xbmc.LOGWARNING)

    def error(self, msg):
        xbmc.log('[wtube] yt-dlp ERROR: %s' % msg, xbmc.LOGERROR)


@dataclass
class Stream:
    url: str
    title: str = ''
    thumb: str = ''
    fanart: str = ''
    duration: int = 0
    channel: str = ''
    channel_id: str = ''
    mime: str = ''
    manifest: str = ''  # '' = flux direct | 'hls' = via inputstream.adaptive
    headers: dict = field(default_factory=dict)
    subtitles: list = field(default_factory=list)
    is_live: bool = False

    def header_string(self):
        return urlencode(self.headers, quote_via=quote) if self.headers else ''

    def kodi_url(self):
        """URL avec en-têtes à la syntaxe Kodi (url|Header=valeur&...)."""
        hdr = self.header_string()
        return '%s|%s' % (self.url, hdr) if hdr else self.url


def _normalize(entry):
    """Transforme une entrée yt-dlp (flat) en dict simple pour l'UI."""
    vid = entry.get('id') or ''
    url = entry.get('url') or entry.get('webpage_url') or ''
    if entry.get('ie_key') == 'YoutubeTab' or entry.get('_type') == 'playlist':
        kind = 'playlist' if ('list=' in url or '/playlist' in url) else 'channel'
    else:
        kind = 'video'

    thumb = ''
    if kind == 'video' and vid:
        thumb = 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid
    else:
        thumbs = entry.get('thumbnails') or []
        if thumbs:
            thumb = thumbs[-1].get('url', '')
            if thumb.startswith('//'):
                thumb = 'https:' + thumb

    channel_id = entry.get('channel_id') or ''
    if kind == 'channel' and vid.startswith('UC'):
        channel_id = vid

    return {
        'kind': kind,
        'id': vid,
        'url': url,
        'title': entry.get('title') or entry.get('channel') or vid,
        'channel': entry.get('channel') or entry.get('uploader') or '',
        'channel_id': channel_id,
        'duration': entry.get('duration') or 0,
        'views': entry.get('view_count'),
        'subs': entry.get('channel_follower_count'),
        'count': entry.get('playlist_count'),
        'description': (entry.get('description') or '')[:300],
        'thumb': thumb,
    }


def _https(url):
    return 'https:' + url if url and url.startswith('//') else (url or '')


def _channel_meta(info):
    """Nom, identifiant, abonnés, avatar et bannière d'une page de chaîne."""
    avatar = banner = ''
    for t in info.get('thumbnails') or []:
        tid = t.get('id') or ''
        if tid == 'avatar_uncropped':
            avatar = _https(t.get('url'))
        elif tid == 'banner_uncropped':
            banner = _https(t.get('url'))
    if not avatar:  # repli : la vignette carrée la plus grande
        squares = [t for t in info.get('thumbnails') or []
                   if t.get('width') and t.get('width') == t.get('height')]
        if squares:
            avatar = _https(max(squares, key=lambda t: t['width']).get('url'))
    return {
        'name': info.get('channel') or info.get('uploader') or '',
        'channel_id': info.get('channel_id') or '',
        'subs': info.get('channel_follower_count'),
        'avatar': avatar,
        'banner': banner,
    }


def _pick_progressive(formats, max_height):
    """Meilleur flux HTTP audio+vidéo combiné <= max_height.

    Sur YouTube ces flux combinés s'arrêtent en général à 720p ;
    au-delà, audio et vidéo sont séparés (voir README/TODO : DASH).
    """
    cands = [
        f for f in formats
        if f.get('url')
        and f.get('vcodec') not in (None, 'none')
        and f.get('acodec') not in (None, 'none')
        and (f.get('protocol') or '').startswith('http')
    ]
    ok = [f for f in cands if (f.get('height') or 0) <= max_height] or cands
    return max(ok, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0), default=None)


def _pick_hls(info):
    """URL du manifeste HLS (live, ou VOD si le client YouTube en fournit un)."""
    if info.get('is_live') and info.get('manifest_url'):
        return info['manifest_url']
    for f in info.get('formats') or []:
        if f.get('manifest_url') and 'm3u8' in (f.get('protocol') or ''):
            return f['manifest_url']
    return ''


def _summary(formats):
    """Résumé compact des formats, pour le journal (diagnostic)."""
    if not formats:
        return 'aucun'
    parts = []
    for f in formats:
        parts.append('%s:%s:%s/%s:%sp' % (
            f.get('format_id'), f.get('protocol'),
            (f.get('vcodec') or '-')[:4], (f.get('acodec') or '-')[:4], f.get('height') or 0))
    return '%d → %s' % (len(formats), ' '.join(parts))


def _pick_subtitles(info, langs=('fr', 'en')):
    urls = []
    subs = info.get('subtitles') or {}
    for lang in langs:
        for fmt in subs.get(lang, []):
            if fmt.get('ext') == 'vtt' and fmt.get('url'):
                urls.append(fmt['url'])
                break
    return urls


class Extractor:
    def __init__(self, profile_dir, cookies='', max_height=1080, hls=False, js_runtime=None,
                 clients=DEFAULT_CLIENTS):
        if YoutubeDL is None:
            raise ExtractorError('yt-dlp introuvable (resources/lib/vendor)')
        self.profile_dir = profile_dir
        self.cookies = cookies
        self.max_height = max_height
        self.hls = hls
        self.js_runtime = js_runtime
        self.clients = [c.strip() for c in (clients or '').split(',') if c.strip()]

    # --- options ---------------------------------------------------------
    def _opts(self, **extra):
        opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 15,
            'logger': _KodiLogger(),
            'cachedir': os.path.join(self.profile_dir, 'ytdlp-cache'),
            # Pour contourner un client YouTube qui casse, essayer par ex. :
        }
        if self.clients:
            # Identités de lecteur présentées à YouTube, dans l'ordre d'essai
            opts['extractor_args'] = {'youtube': {'player_client': self.clients}}
        if self.js_runtime:
            key, path = self.js_runtime
            opts['js_runtimes'] = {key: {'path': path}}
        if self.cookies and os.path.isfile(self.cookies):
            opts['cookiefile'] = self.cookies
        opts.update(extra)
        return opts

    def _extract(self, url, **opts):
        try:
            with YoutubeDL(self._opts(**opts)) as ydl:
                return ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise ExtractorError(str(exc)) from exc

    # --- listes (extraction « flat », rapide) ----------------------------
    def _flat_info(self, url, start, end):
        info = self._extract(
            url,
            extract_flat='in_playlist',
            noplaylist=False,
            playlist_items='%d-%d' % (start, end),
        )
        return info, [_normalize(e) for e in (info.get('entries') or []) if e]

    def _flat(self, url, start, end):
        return self._flat_info(url, start, end)[1]

    def search(self, query, page=1, size=20, kind='video'):
        """kind : 'video' | 'channel' | 'playlist'."""
        end = page * size
        start = end - size + 1
        if kind == 'video':
            url = 'ytsearch%d:%s' % (end, query)
        else:
            sp = {'channel': 'EgIQAg==', 'playlist': 'EgIQAw=='}[kind]
            url = 'https://www.youtube.com/results?search_query=%s&sp=%s' % (quote(query), quote(sp))
        return self._flat(url, start, end)

    def channel_page(self, channel, page=1, size=20, tab='videos'):
        """Page d'une chaîne : (infos de la chaîne, entrées de l'onglet).

        channel : identifiant UC... ou URL (/@handle, /channel/UC...).
        tab : 'videos' ou 'playlists'.
        """
        if channel.startswith('http'):
            url = channel.rstrip('/')
            for suffix in ('/videos', '/playlists', '/featured', '/shorts', '/streams'):
                if url.endswith(suffix):
                    url = url[:-len(suffix)]
        else:
            url = 'https://www.youtube.com/channel/%s' % channel
        end = page * size
        # Une chaîne sans onglet « Vidéos » (que des directs ou des Shorts) :
        # on se rabat sur les autres onglets plutôt que d'échouer.
        tabs = (tab, 'streams', 'shorts') if tab == 'videos' else (tab,)
        for t in tabs:
            try:
                info, entries = self._flat_info('%s/%s' % (url, t), end - size + 1, end)
                return _channel_meta(info), entries
            except ExtractorError as exc:
                if 'does not have a' not in str(exc):
                    raise
        try:  # aucun onglet utilisable : au moins l'en-tête de la chaîne
            info, _ = self._flat_info(url, 1, 1)
            return _channel_meta(info), []
        except ExtractorError:
            return {}, []

    def channel_videos(self, channel, page=1, size=20):
        return self.channel_page(channel, page, size, 'videos')[1]

    def playlist_page(self, playlist_id, page=1, size=20):
        """(titre de la playlist, entrées)."""
        end = page * size
        info, entries = self._flat_info(
            'https://www.youtube.com/playlist?list=%s' % playlist_id, end - size + 1, end
        )
        return info.get('title') or '', entries

    def playlist(self, playlist_id, page=1, size=20):
        return self.playlist_page(playlist_id, page, size)[1]

    # --- lecture ---------------------------------------------------------
    def resolve(self, video_id):
        """Résout une vidéo en flux jouable par Kodi."""
        info = self._extract(
            'https://www.youtube.com/watch?v=%s' % video_id,
            format='best/bestvideo*+bestaudio',
        )
        formats = info.get('formats') or []
        base = dict(
            title=info.get('title') or video_id,
            # JPEG (le WebP de yt-dlp n'est pas lu par tous les scripts de skin) ;
            # hqdefault existe pour toutes les vidéos, l'image HD sert de fond.
            thumb='https://i.ytimg.com/vi/%s/hqdefault.jpg' % video_id,
            fanart='https://i.ytimg.com/vi/%s/maxresdefault.jpg' % video_id,
            duration=int(info.get('duration') or 0),
            channel=info.get('channel') or info.get('uploader') or '',
            channel_id=info.get('channel_id') or '',
            subtitles=_pick_subtitles(info),
            is_live=bool(info.get('is_live')),
        )
        ua = (info.get('http_headers') or {}).get('User-Agent', '')

        # 1) Direct / HLS adaptatif (expérimental, ou obligatoire pour un live)
        if self.hls or info.get('is_live'):
            hls_url = _pick_hls(info)
            if hls_url:
                headers = {'User-Agent': ua} if ua else {}
                return Stream(url=hls_url, manifest='hls', headers=headers,
                              mime='application/vnd.apple.mpegurl', **base)

        # 2) Flux progressif audio+vidéo (fiable, plafonné ~720p)
        fmt = _pick_progressive(formats, self.max_height)
        if fmt is None:
            # 3) Repli automatique sur le HLS si aucun flux combiné n'est servi
            hls_url = _pick_hls(info)
            if hls_url:
                xbmc.log('[wtube] pas de flux progressif pour %s, repli HLS' % video_id, xbmc.LOGINFO)
                headers = {'User-Agent': ua} if ua else {}
                return Stream(url=hls_url, manifest='hls', headers=headers,
                              mime='application/vnd.apple.mpegurl', **base)
            xbmc.log('[wtube] formats reçus pour %s : %s' % (video_id, _summary(formats)), xbmc.LOGWARNING)
            raise ExtractorError('Aucun format lisible pour %s' % video_id)
        headers = dict(fmt.get('http_headers') or info.get('http_headers') or {})
        headers = {k: v for k, v in headers.items() if k in ('User-Agent', 'Referer')}
        ext = fmt.get('ext') or 'mp4'
        return Stream(url=fmt['url'], headers=headers,
                      mime='video/webm' if ext == 'webm' else 'video/mp4', **base)
