# -*- coding: utf-8 -*-
import sys

from resources.lib.router import Router

if __name__ == '__main__':
    Router(sys.argv).dispatch()
