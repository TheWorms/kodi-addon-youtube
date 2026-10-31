# -*- coding: utf-8 -*-
"""Flux de la vidéo suivante, résolu pendant la fin de la vidéo en cours.

Les adresses de YouTube restent valables plusieurs heures : la vidéo
enchaînée démarre alors sans les quelques secondes d'extraction.
"""
import dataclasses
import json
import os
import time

from .extractor import Stream

FILE = 'next_stream.json'
TTL = 1800  # secondes


def _path(profile):
    return os.path.join(profile, FILE)


def save(profile, video_id, stream):
    data = {'id': video_id, 'ts': time.time(), 'stream': dataclasses.asdict(stream)}
    tmp = _path(profile) + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        os.replace(tmp, _path(profile))
    except (OSError, TypeError, ValueError):
        pass


def take(profile, video_id):
    """Flux préchargé pour cette vidéo (consommé), ou None."""
    try:
        with open(_path(profile), encoding='utf-8') as fh:
            data = json.load(fh)
        os.remove(_path(profile))
    except (OSError, ValueError):
        return None
    if data.get('id') != video_id or time.time() - data.get('ts', 0) > TTL:
        return None
    try:
        return Stream(**data['stream'])
    except (KeyError, TypeError):
        return None
