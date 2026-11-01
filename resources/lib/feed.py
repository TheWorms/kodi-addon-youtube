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


def fetch_all_cached(channel_ids, cache_dir, ttl=900, force=False):
    """fetch_all avec un cache disque partagé (interface, widgets, service).

    Le service le rafraîchit en arrière-plan : les widgets s'affichent sans
    interroger YouTube à chaque passage sur l'accueil du skin.
    """
    ids = sorted(set(channel_ids))
    path = os.path.join(cache_dir, CACHE_FILE)
    previous = []
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        previous = data.get('entries') or []
        if not force and data.get('ids') == ids and time.time() - data.get('ts', 0) < ttl:
            return previous
    except (OSError, ValueError, KeyError):
        pass
    entries, failed = fetch_all(ids, previous=previous)
    if failed:
        try:
            import xbmc
            xbmc.log('[wtube] nouveautés : %d chaîne(s) sur %d refusée(s) par YouTube, anciennes vidéos conservées'
                     % (failed, len(ids)), xbmc.LOGWARNING)
        except ImportError:
            pass
    if failed == len(ids) and previous:
        # Tout refusé : on garde le cache tel quel ; nouvel essai au prochain passage
        return [e for e in previous if e.get('channel_id') in set(ids)]
    if entries or not ids:
        tmp = '%s.%d.tmp' % (path, os.getpid())
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'ts': time.time(), 'ids': ids, 'entries': entries}, fh)
            os.replace(tmp, path)
        except OSError:
            pass
    return entries


def without_shorts(entries, hide=True):
    return [e for e in entries if not (hide and e.get('short'))]
