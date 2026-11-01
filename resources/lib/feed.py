# -*- coding: utf-8 -*-
"""Flux de nouveautés des abonnements via les flux RSS publics de YouTube.

Rapide, sans yt-dlp, sans compte (même principe que NewPipe / PipePipe).
"""
import json
import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from . import direct
from .utils import atomic_write_json

FEED_URL = 'https://www.youtube.com/feeds/videos.xml?channel_id={}'
NS = {
    'a': 'http://www.w3.org/2005/Atom',
    'yt': 'http://www.youtube.com/xml/schemas/2015',
    'm': 'http://search.yahoo.com/mrss/',
}


def fetch_channel(channel_id, timeout=10):
    """Retourne les ~15 dernières vidéos d'une chaîne, ou None en cas d'erreur
    (YouTube limite les requêtes, surtout derrière un VPN : HTTP 429)."""
    root = None
    for attempt in range(2):
        try:
            resp = requests.get(FEED_URL.format(channel_id), timeout=timeout)
            if resp.status_code == 429 and attempt == 0:
                time.sleep(2)  # un seul nouvel essai, un peu plus tard
                continue
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            break
        except (requests.RequestException, ET.ParseError):
            return None
    if root is None:
        return None

    author = root.findtext('a:author/a:name', default='', namespaces=NS)
    out = []
    for ent in root.findall('a:entry', NS):
        vid = ent.findtext('yt:videoId', default='', namespaces=NS)
        if not vid:
            continue
        link = ent.find('a:link', NS)
        href = link.get('href', '') if link is not None else ''
        stats = ent.find('m:group/m:community/m:statistics', NS)
        views = 0
        if stats is not None:
            try:
                views = int(stats.get('views', 0))
            except ValueError:
                views = 0
        out.append({
            'kind': 'video',
            'id': vid,
            'title': ent.findtext('a:title', default='', namespaces=NS),
            'channel': author,
            'channel_id': channel_id,
            'published': ent.findtext('a:published', default='', namespaces=NS),
            'views': views,
            'duration': 0,  # absent du RSS ; renseigné à la lecture
            'description': (ent.findtext('m:group/m:description', default='', namespaces=NS) or '')[:300],
            'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid,
            'url': '',
            'short': '/shorts/' in href,
        })
    return out


def fetch_all(channel_ids, max_workers=4, previous=None):
    """Fusionne les flux de toutes les chaînes, du plus récent au plus ancien.

    previous : entrées déjà connues ; une chaîne dont le flux est refusé garde
    ses anciennes vidéos au lieu de disparaître des nouveautés.
    Retourne (entrées, nombre de chaînes en échec).
    """
    if not channel_ids:
        return [], 0
    direct.ensure_host('www.youtube.com')  # option « YouTube hors du VPN »
    with ThreadPoolExecutor(max_workers=max_workers) as pool:  # 4 : moins de refus 429
        results = list(pool.map(fetch_channel, channel_ids))
    failed = 0
    old = {}
    for e in previous or []:
        old.setdefault(e.get('channel_id'), []).append(e)
    for i, (cid, entries) in enumerate(zip(channel_ids, results)):
        if entries is None:
            failed += 1
            results[i] = old.get(cid, [])
    seen, merged = set(), []
    for entries in results:
        for e in entries:
            if e['id'] not in seen:
                seen.add(e['id'])
                merged.append(e)
    merged.sort(key=lambda e: e['published'], reverse=True)
    return merged, failed


CACHE_FILE = 'feed_cache.json'


LOCK_FILE = 'feed_cache.lock'
LOCK_STALE = 180     # secondes : verrou abandonné (processus tué) au-delà
RETRY_AFTER = 300    # secondes : après un refus général, nouvel essai dans 5 min


def _log(msg):
    try:
        import xbmc
        xbmc.log('[wtube] nouveautés : %s' % msg, xbmc.LOGWARNING)
    except ImportError:
        pass


def _lock(cache_dir):
    """Un seul processus interroge YouTube à la fois (service, interface et
    chaque widget sont des processus distincts) ; les autres servent le cache."""
    path = os.path.join(cache_dir, LOCK_FILE)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        if time.time() - os.path.getmtime(path) > LOCK_STALE:
            os.remove(path)
    except OSError:
        pass
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return path
    except OSError:
        return None


def _read_cache(cache_dir):
    try:
        with open(os.path.join(cache_dir, CACHE_FILE), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def cached_entries(cache_dir, channel_ids):
    """Entrées du cache pour ces chaînes, quel que soit leur âge ; None s'il
    n'y a pas encore de cache (les widgets ne doivent pas attendre YouTube)."""
    data = _read_cache(cache_dir)
    if data is None:
        return None
    wanted = set(channel_ids)
    return [e for e in data.get('entries') or [] if e.get('channel_id') in wanted]


def cache_matches(cache_dir, channel_ids):
    """Le cache couvre-t-il exactement ces chaînes ? (abonnement ajouté/retiré)"""
    data = _read_cache(cache_dir)
    return data is not None and data.get('ids') == sorted(set(channel_ids))


def fetch_all_cached(channel_ids, cache_dir, ttl=900, force=False):
    """fetch_all avec un cache disque partagé (interface, widgets, service).

    Le service le rafraîchit en arrière-plan : les widgets s'affichent sans
    interroger YouTube à chaque passage sur l'accueil du skin. Si YouTube
    refuse (HTTP 429, VPN), les vidéos déjà connues sont conservées.
    """
    ids = sorted(set(channel_ids))
    wanted = set(ids)
    path = os.path.join(cache_dir, CACHE_FILE)
    previous, fresh = [], False
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        previous = data.get('entries') or []
        fresh = data.get('ids') == ids and time.time() - data.get('ts', 0) < ttl
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    if fresh and not force:
        return previous
    lock = _lock(cache_dir)
    if lock is None:  # un autre processus rafraîchit déjà : cache actuel
        return [e for e in previous if e.get('channel_id') in wanted]
    try:
        entries, failed = fetch_all(ids, previous=previous)
        if failed:
            _log('%d chaîne(s) sur %d refusée(s) par YouTube, anciennes vidéos conservées' % (failed, len(ids)))
        if ids and failed == len(ids):
            # Tout refusé : anciennes vidéos gardées, nouvel essai dans 5 min
            # seulement (sinon chaque widget relancerait toutes les requêtes).
            entries = [e for e in previous if e.get('channel_id') in wanted]
            stamp = time.time() - max(0, ttl - RETRY_AFTER)
        else:
            stamp = time.time()
        atomic_write_json(path, {'ts': stamp, 'ids': ids, 'entries': entries})
        return entries
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def without_shorts(entries, hide=True):
    return [e for e in entries if not (hide and e.get('short'))]
