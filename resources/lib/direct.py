# -*- coding: utf-8 -*-
"""Routage direct (hors VPN) des serveurs YouTube utilisés par Wtube.

Fonctionne avec l'option « YouTube hors du VPN » de l'addon ProtonVPN
0.5.13 à 0.5.17, qui publiait la propriété Home PROP avec le numéro de sa
table de routage directe et y envoyait les plages d'adresses de Google. Les
serveurs vidéo pouvant être des caches hébergés chez le fournisseur
d'accès, hors de ces plages, Wtube ajoutait une règle /32 pour chacun dans
la même table. ProtonVPN 0.6 remplace cette option par une pause du VPN
pendant l'utilisation de Wtube et ne publie plus PROP : ce module ne fait
alors plus rien.
"""
import ipaddress
import socket
import subprocess
from urllib.parse import urlsplit

try:
    import xbmc
    import xbmcgui
except ImportError:  # tests hors Kodi
    xbmc = xbmcgui = None

PROP = 'protonvpn.bypass_table'
# Plages déjà routées en direct par l'addon ProtonVPN (même liste)
GOOGLE_RANGES = tuple(ipaddress.ip_network(c) for c in (
    '64.233.160.0/19', '66.102.0.0/20', '66.249.64.0/19', '72.14.192.0/18',
    '74.125.0.0/16', '108.170.192.0/18', '108.177.0.0/17', '142.250.0.0/15',
    '172.217.0.0/16', '172.253.0.0/16', '173.194.0.0/16', '192.178.0.0/15',
    '207.223.160.0/20', '208.65.152.0/22', '208.117.224.0/19', '209.85.128.0/17',
    '216.58.192.0/19', '216.239.32.0/19',
))

PROBE_IP = '142.250.179.110'  # adresse de youtube.com, dans les plages ci-dessus
TUNNEL_DEVS = ('wg', 'tun', 'pvpn', 'proton')

_done = set()      # adresses déjà traitées par ce processus
_disabled = False  # « ip » indisponible ou refusé : on n'insiste pas
_checked = False   # route effective vérifiée (une fois par processus)


def _log(msg, warning=False):
    if xbmc is not None:
        xbmc.log('[wtube] hors VPN : %s' % msg, xbmc.LOGWARNING if warning else xbmc.LOGINFO)


def check_route():
    """Une fois par processus : YouTube sort-il vraiment hors du tunnel ?

    Les règles de la route directe peuvent exister sans s'appliquer : ProtonVPN
    0.5.14 et avant laissait wg-quick placer les siennes devant après une
    reconnexion. Tout repartait alors dans le VPN (HTTP 429, vidéos coupées)
    sans aucune trace. Retourne le nom de l'interface de sortie ('' si inconnu).
    """
    global _checked
    if _checked or not _table():
        return ''
    _checked = True
    ok, out = _ip(['route', 'get', PROBE_IP])
    parts = out.split()
    dev = parts[parts.index('dev') + 1] if ok and 'dev' in parts[:-1] else ''
    if dev.startswith(TUNNEL_DEVS):
        _log('YouTube sort encore par le VPN (%s) malgré la route directe : '
             'mettre à jour ProtonVPN (0.6 ou plus récent)' % dev, warning=True)
    return dev


def _table():
    if xbmcgui is None:
        return ''
    return xbmcgui.Window(10000).getProperty(PROP)


def _ip(args):
    for prefix in ([], ['sudo', '-n']):  # root sur CoreELEC/LibreELEC ; sudo ailleurs
        try:
            p = subprocess.run(prefix + ['ip', '-4'] + args, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return True, p.stdout.decode('utf-8', 'ignore')
    return False, ''


def _existing(table):
    ok, out = _ip(['rule', 'show'])
    found = set()
    for line in out.splitlines() if ok else ():
        parts = line.split()
        if parts and parts[0] == '%s:' % table and 'to' in parts:
            found.add(parts[parts.index('to') + 1].split('/')[0])
    return found


def active():
    """Wtube est-il en route directe dans les exceptions de ProtonVPN ?"""
    return bool(_table()) and not _disabled


def ensure_host(host):
    """Route directe pour toutes les adresses IPv4 de `host` (si Wtube est
    en route directe dans ProtonVPN). Ne lève jamais d'exception."""
    global _disabled
    table = _table()
    if not table or not host or _disabled:
        return
    check_route()
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return
    todo = []
    for info in infos:
        ip = info[4][0]
        if ip in _done:
            continue
        _done.add(ip)
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not any(addr in net for net in GOOGLE_RANGES):
            todo.append(ip)
    if not todo:
        return
    existing = _existing(table)
    for ip in todo:
        if ip in existing:
            continue
        ok, _ = _ip(['rule', 'add', 'to', ip + '/32', 'lookup', table, 'pref', table])
        if not ok:
            _disabled = True
            _log('impossible d\'ajouter une route (droits ?), route directe ignorée')
            return
        _log('%s (%s) en direct' % (host, ip))


def ensure_url(url):
    try:
        ensure_host(urlsplit(url).hostname)
    except Exception:  # jamais bloquant pour la lecture
        pass
