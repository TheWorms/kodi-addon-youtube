# -*- coding: utf-8 -*-
"""Contournements propres à l'interpréteur Python embarqué de Kodi."""
import datetime
import time

import _strptime  # chargé ici, dans l'interpréteur de l'addon


class _KodiSafeDatetime(datetime.datetime):
    """datetime.strptime casse dans Kodi après le premier lancement
    (« 'NoneType' object is not callable ») : le module _strptime mis en
    cache par la partie C vient d'un interpréteur précédent. On passe par
    notre propre import de _strptime."""

    @classmethod
    def strptime(cls, date_string, fmt):
        func = getattr(_strptime, '_strptime_datetime', None)
        if func is not None:
            return func(cls, date_string, fmt)
        return cls(*time.strptime(date_string, fmt)[:6])


def patch_strptime():
    """À appeler avant d'importer yt-dlp."""
    if datetime.datetime is not _KodiSafeDatetime:
        datetime.datetime = _KodiSafeDatetime
