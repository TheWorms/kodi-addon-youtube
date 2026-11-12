# -*- coding: utf-8 -*-
"""Service de fond : relais HLS local (SponsorBlock sans coupure) et
rafraîchissement régulier du flux des abonnements (widgets instantanés)."""
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import feed, launcher, widgets
from resources.lib.hlsproxy import PROPERTY, Relay
from resources.lib.storage import Storage

FEED_PROPERTY = 'wtube.feed_updated'  # utilisable par le skin pour recharger les widgets


def refresh_feed(profile):
    addon = xbmcaddon.Addon()
    db = Storage(profile)
    try:
        ids = [s['channel_id'] for s in db.list_subs()]
    finally:
        db.close()
    if ids:
        feed.fetch_all_cached(ids, profile, widgets.feed_ttl(addon), force=True,
                              fallback=_channel_fallback(profile, addon))
        xbmcgui.Window(10000).setProperty(FEED_PROPERTY, str(int(time.time())))
    return widgets.feed_ttl(addon)


def _channel_fallback(profile, addon):
    """Secours pour les chaînes dont le RSS est refusé : page de la chaîne."""
    state = {}

    def fallback(channel_id):
        if 'ex' not in state:
            from resources.lib.extractor import DEFAULT_CLIENTS, Extractor, find_js_runtime
            state['ex'] = Extractor(profile, js_runtime=find_js_runtime(profile, addon.getSetting('js_runtime')),
                                    clients=addon.getSetting('yt_clients') or DEFAULT_CLIENTS)
        entries = state['ex'].channel_feed(channel_id)
        xbmc.log('[wtube] nouveautés : %s lue sur la page de la chaîne (%d vidéos)' % (channel_id, len(entries)),
                 xbmc.LOGINFO)
        return entries
    return fallback


def _subs_changed(profile):
    """Abonnement ajouté ou retiré depuis le dernier rafraîchissement : les
    widgets lisent le cache, il faut donc le refaire tout de suite."""
    try:
        db = Storage(profile)
        try:
            ids = [s['channel_id'] for s in db.list_subs()]
        finally:
            db.close()
    except Exception:
        return False
    return bool(ids) and not feed.cache_matches(profile, ids)


def _safe_refresh(profile):
    try:
        refresh_feed(profile)
    except Exception as exc:  # le relais ne doit jamais tomber à cause du flux
        xbmc.log('[wtube] rafraîchissement du flux : %s' % exc, xbmc.LOGWARNING)


class SplashHost:
    """Affiche l'écran de chargement à la demande de l'entrée plugin ou
    script. Le service est déjà en mémoire : l'écran apparaît dès le clic,
    avant même que l'interpréteur de l'interface ait démarré."""
    MAX_SECONDS = 25  # filet : jamais d'écran de chargement bloqué à l'écran

    def __init__(self):
        self.home = xbmcgui.Window(10000)
        self.path = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('path'))
        self.window = None
        self.since = 0.0
        self.home.clearProperty(launcher.PROP_SPLASH)
        self.home.setProperty(launcher.PROP_ALIVE, '1')

    def _close(self):
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None

    def poll(self):
        try:
            order = self.home.getProperty(launcher.PROP_SPLASH)
            if order:
                self.home.clearProperty(launcher.PROP_SPLASH)
            if order == 'show' and self.window is None:
                self.window = xbmcgui.WindowXMLDialog(launcher.SPLASH_XML, self.path, 'Default',
                                                      launcher.SPLASH_RES)
                self.window.show()
                self.since = time.time()
            elif order == 'hide':
                self._close()
            elif self.window is not None and time.time() - self.since > self.MAX_SECONDS:
                xbmc.log('[wtube] écran de chargement fermé d\'office', xbmc.LOGWARNING)
                self._close()
        except Exception as exc:  # ne doit jamais faire tomber le relais
            xbmc.log('[wtube] écran de chargement : %s' % exc, xbmc.LOGWARNING)
            self.window = None

    def stop(self):
        self.home.clearProperty(launcher.PROP_ALIVE)
        self.home.clearProperty(launcher.PROP_SPLASH)
        self._close()


def run():
    relay = Relay()
    port = relay.start()
    home = xbmcgui.Window(10000)
    if port:
        home.setProperty(PROPERTY, str(port))
        xbmc.log('[wtube] relais HLS démarré sur 127.0.0.1:%d' % port, xbmc.LOGINFO)
    else:
        xbmc.log('[wtube] relais HLS : aucun port libre, SponsorBlock passera par le saut', xbmc.LOGWARNING)
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    monitor = xbmc.Monitor()
    next_refresh = time.time() + 20  # laisse Kodi finir de démarrer
    worker = None
    splash = SplashHost()
    last_tick = time.time()
    # Boucle à 20 Hz pour l'écran de chargement (une lecture de propriété) ;
    # le reste garde son rythme de 15 s.
    while not monitor.waitForAbort(0.05):
        splash.poll()
        if time.time() - last_tick < 15:
            continue
        last_tick = time.time()
        # Rafraîchissement dans un fil à part : la boucle reste réactive à
        # l'arrêt de Kodi même si YouTube répond lentement (429, VPN).
        due = time.time() >= next_refresh or _subs_changed(profile)
        if due and (worker is None or not worker.is_alive()):
            worker = threading.Thread(target=_safe_refresh, args=(profile,), daemon=True)
            worker.start()
            next_refresh = time.time() + widgets.feed_ttl(xbmcaddon.Addon())
    # N'efface la propriété que si elle désigne encore ce relais (une nouvelle
    # instance du service, après une mise à jour, a pu en publier une autre).
    if port and home.getProperty(PROPERTY) == str(port):
        home.clearProperty(PROPERTY)
    splash.stop()
    relay.stop()


if __name__ == '__main__':
    run()
