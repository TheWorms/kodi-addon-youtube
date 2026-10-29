# -*- coding: utf-8 -*-
"""Flux de nouveautés des abonnements via les flux RSS publics de YouTube.

Rapide, sans yt-dlp, sans compte (même principe que NewPipe / PipePipe).
"""
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
    """Retourne les ~15 dernières vidéos d'une chaîne (liste vide en cas d'erreur)."""
    try:
        resp = requests.get(FEED_URL.format(channel_id), timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError):
        return []

    author = root.findtext('a:author/a:name', default='', namespaces=NS)
    out = []
    for ent in root.findall('a:entry', NS):
        vid = ent.findtext('yt:videoId', default='', namespaces=NS)
        if not vid:
            continue
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
        })
    return out


def fetch_all(channel_ids, max_workers=8):
    """Fusionne les flux de toutes les chaînes, du plus récent au plus ancien."""
    if not channel_ids:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(fetch_channel, channel_ids))
    seen, merged = set(), []
    for entries in results:
        for e in entries:
            if e['id'] not in seen:
                seen.add(e['id'])
                merged.append(e)
    merged.sort(key=lambda e: e['published'], reverse=True)
    return merged
