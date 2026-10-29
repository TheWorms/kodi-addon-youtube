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
    """1234 -> '1.2k', 1500000 -> '1.5M'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ''
    if n >= 1_000_000_000:
        return '%.1fB' % (n / 1_000_000_000)
    if n >= 1_000_000:
        return '%.1fM' % (n / 1_000_000)
    if n >= 1_000:
        return '%.1fk' % (n / 1_000)
    return str(n)
