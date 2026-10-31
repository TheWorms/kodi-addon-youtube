# -*- coding: utf-8 -*-
import sys

from resources.lib import compat

compat.patch_strptime()

from resources.lib.router import Router  # noqa: E402

if __name__ == '__main__':
    Router(sys.argv).dispatch()
