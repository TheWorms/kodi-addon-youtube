# -*- coding: utf-8 -*-
"""Segments SponsorBlock (API publique, requête anonymisée par préfixe de hash)."""
import hashlib
import json

import requests

API = 'https://sponsor.ajay.app/api/skipSegments'
DEFAULT_CATEGORIES = ('sponsor', 'selfpromo', 'interaction')


def get_segments(video_id, categories=DEFAULT_CATEGORIES, timeout=5):
    """Retourne une liste triée de tuples (début_s, fin_s) à ignorer."""
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

    for item in data:
        if item.get('videoID') == video_id:
            return sorted(
                (float(s['segment'][0]), float(s['segment'][1]))
                for s in item.get('segments', [])
            )
    return []
