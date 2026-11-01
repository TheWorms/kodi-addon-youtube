# -*- coding: utf-8 -*-
import sys
import time

import xbmc

STARTED = time.time()
# Ouverture de l'addon (pas un widget ni une lecture) : étapes tracées dans
# kodi.log pour savoir où ça bloque si l'ouverture n'aboutit pas.
OPENING = len(sys.argv) < 3 or sys.argv[2] in ('', '?')
if OPENING:
    xbmc.log('[wtube] ouverture : démarrage', xbmc.LOGINFO)

from resources.lib import compat  # noqa: E402

compat.patch_strptime()

from resources.lib.router import Router  # noqa: E402

if __name__ == '__main__':
    if OPENING:
        xbmc.log('[wtube] ouverture : modules chargés en %.1f s' % (time.time() - STARTED), xbmc.LOGINFO)
    Router(sys.argv).dispatch()
