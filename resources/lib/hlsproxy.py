# -*- coding: utf-8 -*-
"""Relais HLS local pour SponsorBlock sans coupure.

Kodi lit le flux à travers ce relais (http://127.0.0.1:<port>/hls.m3u8).
Le relais télécharge les playlists chez YouTube et retire les segments
qui tombent dans une plage sponsorisée : Kodi enchaîne directement sur la
suite, sans saut ni rechargement. Les segments vidéo eux-mêmes sont lus
directement chez YouTube (seules les playlists, quelques Ko, passent ici).
"""
import base64
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

try:
    import xbmc
except ImportError:  # tests hors Kodi
    xbmc = None

PORT_BASE = 52341
PORT_TRIES = 10
PROPERTY = 'wtube.relay_port'  # propriété de la fenêtre Home (10000)
MIME = 'application/vnd.apple.mpegurl'
# Le relais ne va chercher que des playlists YouTube : ce n'est pas un proxy
# ouvert (une page web locale ne peut pas s'en servir pour lire le réseau).
ALLOWED_HOSTS = ('.googlevideo.com', '.youtube.com')
MAX_PLAYLIST = 8 * 1024 * 1024  # octets ; une playlist de plusieurs heures fait ~1 Mo

_URI_RE = re.compile(r'URI="([^"]+)"')
# Balises qui décrivent un seul segment (elles disparaissent avec lui)
_PER_SEGMENT = {
    '#EXTINF', '#EXT-X-BYTERANGE', '#EXT-X-PROGRAM-DATE-TIME',
    '#EXT-X-DISCONTINUITY', '#EXT-X-GAP', '#EXT-X-BITRATE',
}


def _log(msg, warning=False):
    if xbmc is not None:
        xbmc.log('[wtube] relais : %s' % msg, xbmc.LOGWARNING if warning else xbmc.LOGINFO)


# --- encodage des paramètres -------------------------------------------------
def b64e(text):
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii').rstrip('=')


def b64d(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)).decode('utf-8')


def encode_ranges(ranges):
    return ','.join('%.2f-%.2f' % (a, b) for a, b in ranges)


def decode_ranges(text):
    out = []
    for part in (text or '').split(','):
        a, sep, b = part.partition('-')
        if not sep:
            continue
        try:
            a, b = float(a), float(b)
        except ValueError:
            continue
        if b > a:
            out.append((a, b))
    return sorted(out)


def build_url(port, upstream, ranges, user_agent=''):
    """URL à donner à Kodi à la place du manifeste YouTube."""
    query = {'u': b64e(upstream), 's': encode_ranges(ranges)}
    if user_agent:
        query['ua'] = b64e(user_agent)
    return 'http://127.0.0.1:%d/hls.m3u8?%s' % (port, urlencode(query))


# --- réécriture des playlists -----------------------------------------------
def _absolutize_uris(line, base_url):
    return _URI_RE.sub(lambda m: 'URI="%s"' % urljoin(base_url, m.group(1)), line)


def rewrite_master(text, base_url, child_url):
    """Fait passer chaque sous-playlist (variantes, pistes audio) par le relais."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-I-FRAME-STREAM-INF'):
            continue  # pistes d'images clés : inutiles ici, et non filtrées
        if line.startswith('#EXT-X-MEDIA') and 'URI="' in line:
            line = _URI_RE.sub(lambda m: 'URI="%s"' % child_url(urljoin(base_url, m.group(1))), line)
        elif not line.startswith('#'):
            line = child_url(urljoin(base_url, line))
        out.append(line)
    return '\n'.join(out) + '\n'


def _extinf(tags):
    for tag in tags:
        if tag.startswith('#EXTINF:'):
            try:
                return float(tag[8:].split(',', 1)[0])
            except ValueError:
                return 0.0
    return 0.0


def filter_media(text, base_url, ranges):
    """Retire d'une playlist de segments ceux dont le milieu tombe dans une plage.

    Retourne (playlist, nb_segments_retirés, secondes_retirées).
    Même règle pour la vidéo et l'audio : leurs segments étant alignés,
    les deux pistes perdent exactement les mêmes morceaux.
    """
    out, pending = [], []
    t = 0.0
    dropped, removed = 0, 0.0
    kept_any, gap, has_type = False, False, False
    byte_end = {}  # uri -> fin du dernier BYTERANGE (offsets implicites)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        name = line.split(':', 1)[0]
        if line.startswith('#'):
            if name == '#EXT-X-ENDLIST':
                pass  # remise à la fin, une seule fois
            elif name in _PER_SEGMENT:
                pending.append(line)
            else:
                if name == '#EXT-X-PLAYLIST-TYPE':
                    has_type = True
                out.append(_absolutize_uris(line, base_url))
            continue

        uri = urljoin(base_url, line)
        dur = _extinf(pending)
        mid = t + dur / 2.0
        t += dur

        # Offsets BYTERANGE rendus explicites : retirer un segment ne doit
        # pas décaler le suivant.
        tags = []
        for tag in pending:
            if tag.startswith('#EXT-X-BYTERANGE:'):
                length, _, offset = tag[17:].partition('@')
                start = int(offset) if offset else byte_end.get(uri, 0)
                byte_end[uri] = start + int(length)
                tag = '#EXT-X-BYTERANGE:%d@%d' % (int(length), start)
            tags.append(tag)
        pending = []

        if any(a <= mid < b for a, b in ranges):
            dropped += 1
            removed += dur
            gap = True
            continue
        if gap and kept_any and '#EXT-X-DISCONTINUITY' not in tags:
            out.append('#EXT-X-DISCONTINUITY')
        gap = False
        kept_any = True
        out.extend(tags)
        out.append(uri)

    if not has_type and out and out[0] == '#EXTM3U':
        out.insert(1, '#EXT-X-PLAYLIST-TYPE:VOD')
    out.append('#EXT-X-ENDLIST')  # vidéo à la demande : pas de rafraîchissement
    return '\n'.join(out) + '\n', dropped, removed


def allowed_upstream(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and any(host.endswith(s) or host == s[1:] for s in ALLOWED_HOSTS)


def _fetch(url, user_agent):
    """Playlist YouTube (texte, URL finale) ; ValueError si refusée."""
    import requests
    if not allowed_upstream(url):
        raise ValueError('adresse refusée : %s' % urlsplit(url).hostname)
    headers = {'User-Agent': user_agent} if user_agent else None
    with requests.get(url, timeout=15, headers=headers, stream=True) as resp:
        resp.raise_for_status()
        if not allowed_upstream(resp.url):  # redirection hors de YouTube
            raise ValueError('redirection refusée : %s' % urlsplit(resp.url).hostname)
        data = b''
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_PLAYLIST:
                raise ValueError('playlist trop grande')
        return data.decode('utf-8', 'replace'), resp.url


# --- serveur -------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 30  # une connexion inactive ne garde pas un fil indéfiniment

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, body=b'', head_only=False):
        self.send_response(code)
        self.send_header('Content-Type', MIME if code == 200 else 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_GET(self, head_only=False):
        # Seules les requêtes adressées à 127.0.0.1 sont servies (pas de
        # « DNS rebinding » depuis une page web ouverte sur la même machine).
        host = (self.headers.get('Host') or '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            return self._reply(403, head_only=head_only)
        parts = urlsplit(self.path)
        if parts.path != '/hls.m3u8':
            return self._reply(404, head_only=head_only)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        try:
            upstream = b64d(query['u'])
            user_agent = b64d(query['ua']) if query.get('ua') else ''
        except (KeyError, ValueError):
            return self._reply(400, head_only=head_only)
        ranges = decode_ranges(query.get('s'))
        try:
            text, base = _fetch(upstream, user_agent)
            if '#EXT-X-STREAM-INF' in text:
                port = self.server.server_address[1]

                def child(url):
                    return 'http://127.0.0.1:%d/hls.m3u8?%s' % (port, urlencode(dict(query, u=b64e(url))))

                body = rewrite_master(text, base, child)
                _log('manifeste : %d variantes, %d pistes audio séparées' % (
                    text.count('#EXT-X-STREAM-INF'), text.count('TYPE=AUDIO')))
            else:
                body, n, secs = filter_media(text, base, ranges)
                if n:
                    _log('%d segments retirés (%.1f s)' % (n, secs))
        except Exception as exc:  # réseau, adresse refusée, playlist mal formée
            _log('playlist non servie : %s' % exc, warning=True)
            return self._reply(502, head_only=head_only)
        self._reply(200, body.encode('utf-8'), head_only=head_only)


class Relay:
    def __init__(self):
        self.httpd = None

    def start(self):
        """Démarre le relais ; retourne le port, ou 0 si aucun n'est libre."""
        for port in range(PORT_BASE, PORT_BASE + PORT_TRIES):
            try:
                self.httpd = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
            except OSError:
                continue
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            return port
        return 0

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
