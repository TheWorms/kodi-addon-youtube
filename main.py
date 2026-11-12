# -*- coding: utf-8 -*-
import sys
import time

import xbmc

STARTED = time.time()
# Ouverture de l'addon (pas un widget ni une lecture) : étapes tracées dans
# kodi.log pour savoir où ça bloque si l'ouverture n'aboutit pas.
OPENING = len(sys.argv) < 3 or sys.argv[2] in ('', '?')
HANDED_OFF = False
if OPENING:
    xbmc.log('[wtube] ouverture : démarrage', xbmc.LOGINFO)
    # Ouverture par l'utilisateur depuis l'explorateur : avant de charger
    # quoi que ce soit de lourd, l'interface est confiée à l'entrée script
    # et l'explorateur est remplacé par l'accueil (voir launcher.py). À la
    # fermeture, Kodi revient ainsi droit à l'accueil, sans repasser par
    # l'explorateur.
    try:
        import xbmcaddon

        from resources.lib import launcher
        if xbmcaddon.Addon().getSetting('ui_fullscreen') != 'false' and launcher.user_open():
            launcher.hand_off(int(sys.argv[1]))
            HANDED_OFF = True
            xbmc.log('[wtube] ouverture : confiée à l\'entrée script, accueil sous l\'interface', xbmc.LOGINFO)
    except Exception as exc:  # en cas de souci, ouverture comme avant
        xbmc.log('[wtube] ouverture : passage direct (%s)' % exc, xbmc.LOGWARNING)

if not HANDED_OFF:
    from resources.lib import compat  # noqa: E402

    compat.patch_strptime()

    from resources.lib.router import Router  # noqa: E402

    if __name__ == '__main__':
        if OPENING:
            xbmc.log('[wtube] ouverture : modules chargés en %.1f s' % (time.time() - STARTED), xbmc.LOGINFO)
        Router(sys.argv).dispatch()
