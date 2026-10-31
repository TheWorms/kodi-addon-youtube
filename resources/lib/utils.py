# -*- coding: utf-8 -*-
"""Fonctions pures (sans dépendance Kodi), faciles à tester."""
import re

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
_VID_RE = re.compile(r'(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})')
_LIST_RE = re.compile(r'[?&]list=([A-Za-z0-9_-]+)')
_CHAN_RE = re.compile(r'youtube\.com/(channel/UC[A-Za-z0-9_-]{22}|@[\w.-]+)')


def parse_target(text):
    """Reconnaît ce que l'utilisateur a collé.

    Retourne (kind, value) avec kind dans {'video', 'playlist', 'channel'}
    ou (None, None) si rien n'est reconnu.
    """
    text = (text or '').strip()
    if not text:
        return None, None
    if _ID_RE.match(text):
        return 'video', text
    lst = _LIST_RE.search(text)
    if lst and '/playlist' in text:
        return 'playlist', lst.group(1)
    vid = _VID_RE.search(text)
    if vid:
        return 'video', vid.group(1)
    if lst:
        return 'playlist', lst.group(1)
    chan = _CHAN_RE.search(text)
    if chan:
        return 'channel', 'https://www.youtube.com/' + chan.group(1)
    return None, None


def fmt_count(n):
    """1234 -> '1.2k', 48000 -> '48k', 1500000 -> '1.5M'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ''
    for div, unit in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'k')):
        if n >= div:
            value = '%.1f' % (n / div)
            return (value[:-2] if value.endswith('.0') else value) + unit
    return str(n)


def video_fanart(video_id):
    """Image 16:9 en 1280x720, en JPEG (le WebP n'est pas lu par tous les
    scripts de skin, ex. le flou d'arrière-plan d'Embuary)."""
    return 'https://i.ytimg.com/vi/%s/maxresdefault.jpg' % video_id


def background_art(video_id):
    """Fond (fanart) d'une vidéo dans les widgets et listes, selon le réglage
    « widget_fanart » : 0 image HD de la vidéo, 1 vignette, 2 fanart Wtube,
    3 aucun (fond du skin)."""
    import os
    import xbmcaddon
    addon = xbmcaddon.Addon('plugin.video.wtube')
    try:
        mode = int(addon.getSetting('widget_fanart') or 0)
    except ValueError:
        mode = 0
    art = {'landscape': video_fanart(video_id)}
    if mode == 0:
        art['fanart'] = video_fanart(video_id)
    elif mode == 1:
        art['fanart'] = 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % video_id
    elif mode == 2:
        art['fanart'] = os.path.join(addon.getAddonInfo('path'), 'resources', 'fanart.jpg')
    return art
