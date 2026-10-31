# -*- coding: utf-8 -*-
"""Lancement direct de l'interface (RunScript, favori, menu du skin, Programmes)."""
from resources.lib import compat

compat.patch_strptime()

from resources.lib import gui  # noqa: E402

gui.open_main()
