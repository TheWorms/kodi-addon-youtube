# -*- coding: utf-8 -*-
"""Lancement direct de l'interface (RunScript, favori, menu du skin, Programmes),
et ouverture confiée par l'entrée plugin (main.py)."""
import sys
import time

import xbmc
import xbmcaddon
import xbmcvfs

STARTED = time.time()
xbmc.log('[wtube] ouverture (raccourci) : démarrage', xbmc.LOGINFO)

from resources.lib import launcher  # noqa: E402

# Écran de chargement d'abord : les modules mettent du temps à se charger.
launcher.splash_show(xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('path')))
try:
    # L'accueil de Kodi sous l'interface : à la fermeture, pas d'explorateur.
    launcher.go_home(launcher.HANDOFF_ARG in sys.argv[1:])

    from resources.lib import compat  # noqa: E402

    compat.patch_strptime()

    from resources.lib import gui  # noqa: E402

    xbmc.log('[wtube] ouverture : modules chargés en %.1f s' % (time.time() - STARTED), xbmc.LOGINFO)
    gui.open_main()  # l'écran de chargement est fermé par la fenêtre dès qu'elle s'affiche
finally:
    launcher.splash_hide()
