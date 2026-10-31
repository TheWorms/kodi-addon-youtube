# -*- coding: utf-8 -*-
"""Widgets pour l'accueil du skin (chemins plugin://plugin.video.wtube/?action=w_...).

Contraintes d'un widget : réponse rapide (le skin les recharge souvent),
aucune boîte de dialogue, éléments complets (vignette, date, chaîne,
progression). Les nouveautés viennent du cache du flux, rafraîchi par le
service ; les recherches ont leur propre cache d'une heure.
"""
import datetime
import hashlib
import json
import os
import time

import xbmcaddon
import xbmcgui

from . import feed
from .utils import background_art

ADDON_FANART = os.path.join(xbmcaddon.Addon('plugin.video.wtube').getAddonInfo('path'), 'resources', 'fanart.jpg')

SEARCH_TTL = 3600


def feed_ttl(addon):
    try:
        return max(5, int(addon.getSetting('feed_ttl') or 15)) * 60
    except ValueError:
        return 900


def hide_shorts(addon):
    return addon.getSetting('hide_shorts') != 'false'


def _date(iso):
    try:
        return datetime.datetime.fromisoformat(iso).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return ''


def video_li(e, url_fn, L, resume=None, favorite=False, label_channel=True):
    """Élément jouable complet pour un widget."""
    vid = e['id']
    title = e.get('title') or vid
    li = xbmcgui.ListItem(title)
    if label_channel and e.get('channel'):
        li.setLabel2(e['channel'])
    thumb = e.get('thumb') or 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid
    li.setArt(dict(background_art(vid), thumb=thumb, icon=thumb, poster=thumb))
    tag = li.getVideoInfoTag()
    tag.setMediaType('video')
    tag.setTitle(title)
    if e.get('channel'):
        tag.setStudios([e['channel']])
        tag.setDirectors([e['channel']])
    if e.get('description'):
        tag.setPlot(e['description'])
    if e.get('duration'):
        tag.setDuration(int(e['duration']))
    day = _date(e.get('published'))
    if day:
        tag.setPremiered(day)
        tag.setFirstAired(day)
        tag.setYear(int(day[:4]))
    params = {'action': 'play', 'id': vid}
    if resume and resume[1]:
        # Kodi affiche sa propre barre de progression et sa question de reprise :
        # la lecture ne doit pas la reposer.
        tag.setResumePoint(resume[0], resume[1])
        params['kodiresume'] = '1'
    li.setProperty('IsPlayable', 'true')
    ctx = []
    if e.get('channel_id'):
        ctx.append((L(30022),
                    'ActivateWindow(Videos,%s,return)' % url_fn(action='channel', channel=e['channel_id'])))
    ctx.append((L(30254) if favorite else L(30253),
                'RunPlugin(%s)' % url_fn(action='fav_toggle', id=vid, title=title,
                                         channel=e.get('channel') or '', channel_id=e.get('channel_id') or '',
                                         duration=str(int(e.get('duration') or 0)))))
    if resume:
        ctx.append((L(30255), 'RunPlugin(%s)' % url_fn(action='mark_watched', id=vid)))
    li.addContextMenuItems(ctx)
    return url_fn(**params), li, False


def channel_li(sub, url_fn):
    li = xbmcgui.ListItem(sub['name'])
    thumb = sub.get('thumb') or ''
    li.setArt({'thumb': thumb, 'icon': thumb, 'poster': thumb, 'fanart': ADDON_FANART})
    return url_fn(action='channel', channel=sub['channel_id']), li, True


def latest_per_channel(entries):
    seen, out = set(), []
    for e in entries:  # entrées déjà triées de la plus récente à la plus ancienne
        if e['channel_id'] not in seen:
            seen.add(e['channel_id'])
            out.append(e)
    return out


# --- cache des recherches ------------------------------------------------------
def _search_cache_path(cache_dir, query):
    key = hashlib.md5(query.encode('utf-8')).hexdigest()[:16]
    return os.path.join(cache_dir, 'widget_search_%s.json' % key)


def cached_search(ex, cache_dir, query, size):
    path = _search_cache_path(cache_dir, query)
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        if time.time() - data['ts'] < SEARCH_TTL:
            return data['entries']
    except (OSError, ValueError, KeyError):
        pass
    entries = [e for e in ex.search(query, 1, size, 'video') if e.get('id')]
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'ts': time.time(), 'entries': entries}, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return entries
