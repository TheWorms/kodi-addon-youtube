# -*- coding: utf-8 -*-
"""Enchaînement : liste des vidéos qui suivent celle en cours de lecture.

La liste est figée au lancement d'une vidéo (les éléments qui la suivent dans
l'écran ou le widget d'où elle a été lancée) et enregistrée dans le profil.
Chaque vidéo enchaînée consomme la tête de liste.
"""
import json
import os
import time
from urllib.parse import parse_qs, urlparse

import xbmc

ADDON_ID = 'plugin.video.wtube'
FILE = 'upnext.json'
MAX_ITEMS = 50
FRESH = 60  # secondes : liste écrite par l'interface juste avant la lecture


def _path(profile):
    return os.path.join(profile, FILE)


def save(profile, current, following, from_gui=False):
    data = {'current': current, 'next': list(following)[:MAX_ITEMS], 'ts': time.time(), 'gui': from_gui}
    try:
        with open(_path(profile), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except OSError:
        pass


def load(profile):
    try:
        with open(_path(profile), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_fresh_for(data, video_id):
    """Liste écrite par l'interface plein écran juste avant cette lecture."""
    return (bool(data) and data.get('gui') and data.get('current') == video_id
            and time.time() - data.get('ts', 0) < FRESH)


def _video_id(path):
    """id YouTube si `path` est une URL de lecture Wtube, sinon None."""
    if not path or not path.startswith('plugin://%s/' % ADDON_ID):
        return None
    q = parse_qs(urlparse(path).query)
    if q.get('action', [''])[0] != 'play':
        return None
    return q.get('id', [None])[0]


def capture_from_container(current):
    """Vidéos qui suivent l'élément sélectionné du conteneur actif (liste
    classique ou widget du skin). Vide si l'élément sélectionné n'est pas
    la vidéo lancée (lecture venue d'ailleurs)."""
    if _video_id(xbmc.getInfoLabel('Container.ListItem.FileNameAndPath')) != current:
        return []
    out = []
    for i in range(1, MAX_ITEMS + 1):
        base = 'Container.ListItemNoWrap(%d)' % i
        path = xbmc.getInfoLabel(base + '.FileNameAndPath')
        if not path:
            break
        vid = _video_id(path)
        if vid and vid != current:
            out.append({'id': vid, 'title': xbmc.getInfoLabel(base + '.Label'),
                        'thumb': xbmc.getInfoLabel(base + '.Art(thumb)')})
    return out
