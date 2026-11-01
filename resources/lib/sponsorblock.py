# -*- coding: utf-8 -*-
"""Segments SponsorBlock (API publique, requête anonymisée par préfixe de hash)."""
import hashlib
import json
import math

import requests

API = 'https://sponsor.ajay.app/api/skipSegments'
DEFAULT_CATEGORIES = ('sponsor', 'selfpromo', 'interaction')


MAX_SHARE = 0.9  # un segment qui couvrirait presque toute la vidéo est ignoré


def _valid(seg, duration):
    try:
        a, b = float(seg[0]), float(seg[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)) or a < 0 or b <= a:
        return None
    if duration and (b - a) > duration * MAX_SHARE:
        return None
    return a, b


def get_segments(video_id, categories=DEFAULT_CATEGORIES, timeout=5):
    """Retourne une liste triée de tuples (début_s, fin_s) à ignorer.

    Les données viennent d'une base participative : tout ce qui est mal
    formé ou absurde est écarté, et aucune erreur ne remonte à la lecture.
    """
    prefix = hashlib.sha256(video_id.encode('utf-8')).hexdigest()[:4]
    try:
        resp = requests.get(
            '%s/%s' % (API, prefix),
            params={
                'categories': json.dumps(list(categories)),
                'actionTypes': json.dumps(['skip']),
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    if not isinstance(data, list):
        return []
    for item in data:
        if not isinstance(item, dict) or item.get('videoID') != video_id:
            continue
        out = []
        for s in item.get('segments') or []:
            if not isinstance(s, dict):
                continue
            try:
                duration = float(s.get('videoDuration') or 0)
            except (TypeError, ValueError):
                duration = 0.0
            seg = _valid(s.get('segment'), duration if math.isfinite(duration) else 0)
            if seg:
                out.append(seg)
        return sorted(out)
    return []
