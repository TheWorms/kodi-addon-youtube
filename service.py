# -*- coding: utf-8 -*-
"""Service de fond : relais HLS local (SponsorBlock sans coupure) et
rafraîchissement régulier du flux des abonnements (widgets instantanés)."""
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import direct, feed, widgets
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
    try:
        addon = xbmcaddon.Addon()
        if direct.auto_enable_protonvpn(profile, addon):
            xbmcgui.Dialog().notification('Wtube', addon.getLocalizedString(30311), xbmcgui.NOTIFICATION_INFO, 8000)
    except Exception as exc:  # jamais bloquant pour le service
        xbmc.log('[wtube] ProtonVPN : %s' % exc, xbmc.LOGWARNING)
    next_refresh = time.time() + 20  # laisse Kodi finir de démarrer
    worker = None
    while not monitor.waitForAbort(15):
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
    relay.stop()


if __name__ == '__main__':
    run()
