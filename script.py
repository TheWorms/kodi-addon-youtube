# -*- coding: utf-8 -*-
"""Lancement direct de l'interface (RunScript, favori, menu du skin, Programmes)."""
import time

import xbmc

STARTED = time.time()
xbmc.log('[wtube] ouverture (raccourci) : démarrage', xbmc.LOGINFO)

from resources.lib import compat  # noqa: E402

compat.patch_strptime()

from resources.lib import gui  # noqa: E402

xbmc.log('[wtube] ouverture : modules chargés en %.1f s' % (time.time() - STARTED), xbmc.LOGINFO)
gui.open_main()
