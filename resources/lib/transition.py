# -*- coding: utf-8 -*-
"""Fondu au noir (image et son) entre deux vidéos enchaînées.

Un voile noir (fenêtre de dialogue animée) apparaît en fondu pendant que le
volume baisse, reste affiché pendant le changement de vidéo, puis disparaît
en fondu quand la suivante démarre, pendant que le volume remonte.
"""
import json
import threading

import xbmc
import xbmcaddon
import xbmcgui

ADDON_PATH = xbmcaddon.Addon('plugin.video.wtube').getAddonInfo('path')
XML_FILE = 'script-wtube-fade.xml'
HEADING = xbmcaddon.Addon('plugin.video.wtube').getLocalizedString(30309)


def _rpc(method, params=None):
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(req))).get('result')
    except (TypeError, ValueError):
        return None


def _get_volume():
    res = _rpc('Application.GetProperties', {'properties': ['volume', 'muted']}) or {}
    return None if res.get('muted') else res.get('volume')


def _set_volume(value):
    _rpc('Application.SetVolume', {'volume': int(max(0, min(100, value)))})


class _Veil(xbmcgui.WindowXMLDialog):
    def onAction(self, action):  # retour / stop : on laisse la main à l'utilisateur
        if action.getId() in (9, 10, 13, 92, 247):
            self.close()


class Fade:
    """fade = Fade(); fade.out(1.2) ... fade.into(0.8) ; fade.cancel() rétablit tout."""

    def __init__(self):
        self.veil = None
        self.volume = None
        self._thread = None

    def _ramp(self, start, end, seconds):
        steps = 12
        for i in range(1, steps + 1):
            _set_volume(start + (end - start) * i / steps)
            xbmc.sleep(int(seconds * 1000 / steps))

    def out(self, seconds=1.2, nxt=None):
        """Fondu vers l'écran « À suivre » (ou noir), volume vers 0 (ne bloque pas).

        nxt : {'id', 'title', 'channel', 'thumb'} de la vidéo suivante.
        """
        if self.veil is not None:
            return
        try:
            self.veil = _Veil(XML_FILE, ADDON_PATH, 'Default', '1080i')
            if nxt:
                vid = nxt.get('id') or ''
                self.veil.setProperty('next.heading', HEADING)
                self.veil.setProperty('next.title', nxt.get('title') or vid)
                self.veil.setProperty('next.channel', nxt.get('channel') or '')
                self.veil.setProperty('next.thumb', nxt.get('thumb') or 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid)
                self.veil.setProperty('next.fanart', 'https://i.ytimg.com/vi/%s/maxresdefault.jpg' % vid)
            self.veil.show()
        except RuntimeError:
            self.veil = None
        self.volume = _get_volume()
        if self.volume:
            self._thread = threading.Thread(target=self._ramp, args=(self.volume, 0, seconds), daemon=True)
            self._thread.start()

    def into(self, seconds=0.8):
        """Le voile disparaît en fondu, le volume revient à sa valeur."""
        if self._thread is not None:
            self._thread.join(3)
            self._thread = None
        if self.veil is not None:
            self.veil.close()  # animation de fermeture (fondu) définie dans le XML
            self.veil = None
        if self.volume:
            self._ramp(0, self.volume, seconds)
            self.volume = None

    def cancel(self):
        """Rétablit immédiatement l'image et le volume."""
        if self._thread is not None:
            self._thread.join(3)
            self._thread = None
        if self.veil is not None:
            self.veil.close()
            self.veil = None
        if self.volume:
            _set_volume(self.volume)
            self.volume = None
