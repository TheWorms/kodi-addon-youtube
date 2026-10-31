# -*- coding: utf-8 -*-
"""Construction des ListItem Kodi (aucune logique réseau ici)."""
import xbmc
import xbmcaddon
import xbmcgui

from .utils import background_art, fmt_count


def _kodi_major():
    try:
        return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
    except ValueError:
        return 0


def L(string_id):
    return xbmcaddon.Addon().getLocalizedString(string_id)


def folder_item(label, url, thumb=''):
    li = xbmcgui.ListItem(label=label)
    if thumb:
        li.setArt({'thumb': thumb, 'icon': thumb})
    return url, li, True


def _set_info(li, e):
    lines = [e.get('channel')]
    if e.get('views') is not None:
        lines.append('%s %s' % (fmt_count(e['views']), L(30040)))
    if e.get('published'):
        lines.append(e['published'][:10])
    if e.get('description'):
        lines.append(e['description'])
    plot = '\n'.join(x for x in lines if x)

    if hasattr(li, 'getVideoInfoTag'):  # Kodi 20+
        tag = li.getVideoInfoTag()
        tag.setTitle(e['title'])
        tag.setMediaType('video')
        tag.setPlot(plot)
        if e.get('duration'):
            tag.setDuration(int(e['duration']))
        if e.get('channel'):
            tag.setStudios([e['channel']])
    else:  # Kodi 19
        info = {'title': e['title'], 'mediatype': 'video', 'plot': plot}
        if e.get('duration'):
            info['duration'] = int(e['duration'])
        li.setInfo('video', info)


def make_item(e, url_fn, subscribed=(), in_history=False):
    """Retourne (url, ListItem, is_folder) pour une entrée normalisée."""
    kind = e['kind']
    li = xbmcgui.ListItem(label=e['title'])
    if e.get('thumb'):
        li.setArt({'thumb': e['thumb'], 'icon': e['thumb']})
    if kind == 'video' and e.get('id'):
        li.setArt(background_art(e['id']))

    ctx = []
    cid = e.get('channel_id') or ''

    if kind == 'video':
        _set_info(li, e)
        li.setProperty('IsPlayable', 'true')
        url, folder = url_fn(action='play', id=e['id']), False
        if cid:
            ctx.append((L(30022), 'Container.Update(%s)' % url_fn(action='channel', channel=cid)))
    elif kind == 'playlist':
        url, folder = url_fn(action='playlist', id=e['id']), True
    else:  # channel
        url, folder = url_fn(action='channel', channel=e['id'] or e['url']), True
        cid = e['id'] if (e['id'] or '').startswith('UC') else ''

    if cid:
        name = e.get('channel') or e['title']
        if cid in subscribed:
            ctx.append((L(30021), 'RunPlugin(%s)' % url_fn(action='sub_remove', id=cid)))
        else:
            ctx.append((L(30020), 'RunPlugin(%s)' % url_fn(action='sub_add', id=cid, name=name)))
    if in_history:
        ctx.append((L(30023), 'RunPlugin(%s)' % url_fn(action='history_remove', id=e['id'])))

    if ctx:
        li.addContextMenuItems(ctx)
    return url, li, folder


def playable_item(stream):
    """ListItem final passé à setResolvedUrl."""
    if stream.manifest == 'hls':
        li = xbmcgui.ListItem(label=stream.title, path=stream.url)
        li.setProperty('inputstream', 'inputstream.adaptive')
        if _kodi_major() < 21:  # Kodi 21+ détecte le type de manifeste tout seul
            li.setProperty('inputstream.adaptive.manifest_type', 'hls')
        hdr = stream.header_string()
        if hdr:
            li.setProperty('inputstream.adaptive.stream_headers', hdr)
        if stream.max_height:  # plafond de qualité choisi dans les réglages
            li.setProperty('inputstream.adaptive.chooser_resolution_max', _resolution_label(stream.max_height))
    else:
        li = xbmcgui.ListItem(label=stream.title, path=stream.kodi_url())
    if stream.mime:
        li.setMimeType(stream.mime)
    li.setContentLookup(False)
    if stream.thumb:
        li.setArt({'thumb': stream.thumb, 'icon': stream.thumb, 'fanart': stream.fanart or stream.thumb})
    if stream.subtitles:
        li.setSubtitles(stream.subtitles)
    return li


def _resolution_label(height):
    """Valeurs reconnues par inputstream.adaptive pour chooser_resolution_max."""
    for limit, label in ((480, '480p'), (640, '640p'), (720, '720p'), (1080, '1080p'), (1440, '1440p')):
        if height <= limit:
            return label
    return '4K'
