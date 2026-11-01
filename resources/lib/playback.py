# -*- coding: utf-8 -*-
"""Lecture d'une vidéo (action « play » du plugin) et tout ce qui l'entoure.

Le processus Kodi de l'action « play » continue après setResolvedUrl :
il suit la lecture (reprise, ancien saut SponsorBlock, retour à 0:00),
précharge la vidéo suivante, affiche l'écran « À suivre » avec un fondu et
lance la suite. PlaybackMixin est mélangé à Router (router.py) : ses
méthodes utilisent self.addon, self.db, self.ex, self.params, self.url…
"""
import sqlite3
import threading

import xbmc
import xbmcgui
import xbmcplugin

from . import direct, hlsproxy, streamcache, ui, upnext
from .ui import L

RESUME_MIN = 15          # en dessous, on repart du début
RESUME_END_MARGIN = 30   # dans les 30 dernières secondes : vidéo considérée comme vue
NEXT_ASK_TIMEOUT = 15    # secondes avant que la question « vidéo suivante » se ferme (= arrêt)
PRELOAD_BEFORE = 90      # secondes avant la fin : résolution de la vidéo suivante
PLAY_FAILED = 'wtube.play_failed'  # propriété Home : vidéo dont la lecture a échoué
NEXT_START_TIMEOUT = 25  # secondes d'attente maximum du démarrage de la vidéo enchaînée
REWIND_FROM = 3.0        # démarrage HLS au-delà : premier segment sauté, on revient à 0:00
EARLY_END = 15           # secondes : fin de lecture plus tôt que ça avant la fin = coupure, pas fin
MAX_RECOVER = 2          # reprises automatiques après une coupure, par vidéo
FADE_BEFORE = 0.6        # secondes avant la fin : début du fondu au noir (court : ne pas manger la fin)


class _NextHelper:
    """Préchargement de la vidéo suivante et fondu de fin, pendant la lecture."""

    def __init__(self, router, video_id, mode):
        self.router, self.video_id, self.mode = router, video_id, mode
        data = upnext.load(router._profile)
        self.next = (data['next'][0] if data and data.get('current') == video_id and data.get('next')
                     else None)
        self.preload_on = router.addon.getSetting('preload_next') != 'false'
        self.fade_on = router.addon.getSetting('fade_transition') != 'false'
        self.preload = None
        self.fade = None

    def tick(self, position, total):
        if not self.next or not total:
            return
        remaining = total - position
        if self.preload_on and self.preload is None and remaining <= PRELOAD_BEFORE:
            self.preload = threading.Thread(target=self._preload, daemon=True)
            self.preload.start()
        if self.fade is not None and remaining > FADE_BEFORE + 1:
            self.cancel()  # retour en arrière pendant le fondu : image et son rétablis
        if self.fade_on and self.mode == 2 and self.fade is None and 0 < remaining <= FADE_BEFORE:
            self.start_fade()

    def _preload(self):
        vid = self.next['id']
        try:
            streamcache.save(self.router._profile, vid, self.router.ex.resolve(vid))
            log('vidéo suivante préchargée : %s' % vid)
        except Exception as exc:  # la lecture suivante refera l'extraction elle-même
            log('préchargement de %s impossible : %s' % (vid, str(exc)[:120]), xbmc.LOGINFO)

    def wait_preload(self, seconds=15):
        if self.preload is not None:
            self.preload.join(seconds)

    def start_fade(self):
        if self.fade_on and self.fade is None:
            from .transition import Fade
            self.fade = Fade()
            self.fade.out(FADE_BEFORE, self.next)

    def cancel(self):
        if self.fade is not None:
            self.fade.cancel()
            self.fade = None


class _EndWatcher(xbmc.Player):
    """Distingue la fin naturelle d'une vidéo d'un arrêt par l'utilisateur."""
    def __init__(self):
        super().__init__()
        self.ended = False

    def onPlayBackEnded(self):
        self.ended = True


def fmt_time(seconds):
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[wtube] %s' % msg, level)


class PlaybackMixin:
    """Méthodes de lecture de Router (voir l'en-tête du module)."""

    _last_position = 0.0  # où le suivi de lecture s'est arrêté (secondes)
    _last_total = 0.0

    def _resume_choice(self, video_id, ask=True):
        """Position de départ (secondes), 0 = début, None = lecture annulée."""
        try:
            saved = self.db.get_resume(video_id)
        except sqlite3.Error as exc:
            log('reprise illisible : %s' % exc, xbmc.LOGWARNING)
            saved = None
        try:  # lecture texte : getSettingInt peu fiable sur les listes (cf. Radio)
            mode = int(self.addon.getSetting('resume_mode') or 0)  # 0 demander, 1 toujours, 2 jamais
        except ValueError:
            mode = 0
        if not saved or mode == 2:
            return 0
        position, total = saved
        if position < RESUME_MIN or (total and position > total - RESUME_END_MARGIN):
            return 0
        if mode == 1:
            return position
        if not ask:
            return 0  # vidéo enchaînée : on ne bloque pas l'enchaînement sur une question
        choice = xbmcgui.Dialog().contextmenu([L(30242) % fmt_time(position), L(30243)])
        if choice < 0:
            return None
        return position if choice == 0 else 0

    def a_play(self):
        video_id = self.params['id']
        home = xbmcgui.Window(10000)
        home.clearProperty(PLAY_FAILED)
        player = xbmc.Player()
        # Vidéo en cours avant celle-ci : le suivi ne doit pas s'y accrocher
        # (Kodi la laisse tourner jusqu'au démarrage de la nouvelle).
        try:
            previous_file = player.getPlayingFile() if player.isPlayingVideo() else None
        except RuntimeError:
            previous_file = None
        resolved = False
        try:
            prepared = self._prepare(video_id)
            if prepared is None:  # lecture annulée (question de reprise)
                xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
                resolved = True
                return
            stream, item, start, segments, relayed = prepared
            xbmcplugin.setResolvedUrl(self.handle, True, item)
            resolved = True
        except Exception:
            # Toujours répondre à Kodi (sinon la roue d'attente tourne sans fin)
            # et prévenir l'enchaînement en attente que cette vidéo a échoué.
            if not resolved:
                xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            home.setProperty(PLAY_FAILED, video_id)
            raise
        try:
            self.db.add_history(video_id, stream.title, stream.channel, stream.channel_id,
                                stream.thumb, stream.duration)
        except sqlite3.Error as exc:  # base occupée : la lecture continue quand même
            log('historique non enregistré : %s' % exc, xbmc.LOGWARNING)
        if stream.is_live:
            return
        kick = (stream.manifest == 'hls' and not start
                and self.addon.getSetting('hls_audio_kick') == 'true')  # désactivé par défaut depuis 0.6.12
        mode = self._autoplay_mode()
        helper = _NextHelper(self, video_id, mode) if mode else None
        try:
            ended = self._watch(video_id, [] if relayed else segments, kick,
                                on_tick=helper.tick if helper else None,
                                rewind=stream.manifest == 'hls' and not start,
                                previous_file=previous_file)
            if ended and self._cut_short(stream, segments):
                # Flux interrompu avant la fin (serveur coupé, réseau) : Kodi le
                # signale comme une fin normale. On reprend là où ça s'est arrêté
                # au lieu de passer à la vidéo suivante.
                self._recover(video_id)
            elif ended and helper:
                self._play_next(video_id, helper)
        finally:
            if helper:
                helper.cancel()  # image et volume toujours rétablis

    def _cut_short(self, stream, segments):
        position, total = self._last_position, self._last_total
        reference = total
        if stream.duration and not segments:
            # durée annoncée par YouTube (sans SponsorBlock, le flux n'est pas raccourci)
            reference = max(total, stream.duration)
        if not reference or position >= reference - EARLY_END:
            return False
        log('%s : lecture interrompue à %d s sur %d s' % (stream.title[:40] or '?', position, reference),
            xbmc.LOGWARNING)
        return True

    def _recover(self, video_id):
        tries = int(self.params.get('retry') or 0)
        if tries >= MAX_RECOVER:
            log('%s : trop de coupures, abandon' % video_id, xbmc.LOGWARNING)
            return
        resume_at = max(0.0, self._last_position - 3)
        log('%s : reprise automatique à %d s (essai %d)' % (video_id, resume_at, tries + 1))
        xbmc.executebuiltin('PlayMedia(%s)' % self.url(action='play', id=video_id, chain='1',
                                                        resume_at='%.1f' % resume_at, retry=str(tries + 1)))

    def _prepare(self, video_id):
        """Tout ce qui précède setResolvedUrl ; None si l'utilisateur annule."""
        chained = self.params.get('chain') == '1'
        if not chained and not upnext.is_fresh_for(upnext.load(self._profile), video_id):
            # Lancée depuis une liste classique ou un widget : on fige la suite de la liste.
            upnext.save(self._profile, video_id, upnext.capture_from_container(video_id))
        if self.params.get('resume_at'):
            start = float(self.params['resume_at'])  # reprise automatique après une coupure
        elif self.params.get('kodiresume') == '1':
            start = 0  # Kodi a déjà posé la question et applique lui-même la reprise
        else:
            start = self._resume_choice(video_id, ask=not chained)  # avant l'extraction : réponse immédiate
        if start is None:
            return None
        stream = streamcache.take(self._profile, video_id) if chained else None
        if stream is not None:
            log('%s : flux préchargé, démarrage immédiat' % video_id)
        stream = stream or self.ex.resolve(video_id)
        direct.ensure_url(stream.url)  # option « YouTube hors du VPN » (flux préchargé compris)
        segments = []
        if self.addon.getSettingBool('sponsorblock') and not stream.is_live:
            from .sponsorblock import get_segments
            segments = get_segments(video_id, timeout=3)

        relayed = False
        if segments and stream.manifest == 'hls' and self.addon.getSettingInt('sponsorblock_mode') == 0:
            port = xbmcgui.Window(10000).getProperty(hlsproxy.PROPERTY)
            if port:
                stream.url = hlsproxy.build_url(int(port), stream.url, segments,
                                                stream.headers.get('User-Agent', ''))
                relayed = True
            else:
                log('relais HLS indisponible, repli sur le saut', xbmc.LOGWARNING)

        item = ui.playable_item(stream)
        if start:
            item.setProperty('StartOffset', '%.1f' % start)
        return stream, item, start, segments, relayed

    def _autoplay_mode(self):
        try:
            return int(self.addon.getSetting('autoplay_next') or 0)  # 0 non, 1 demander, 2 sans demander
        except ValueError:
            return 0

    def _play_next(self, video_id, helper):
        """Fin naturelle de la vidéo : enchaîne la suivante de la liste."""
        monitor, player = xbmc.Monitor(), xbmc.Player()
        data = upnext.load(self._profile)
        if not data or data.get('current') != video_id or not data.get('next'):
            return
        nxt, rest = data['next'][0], data['next'][1:]
        if helper.mode == 2:
            # Écran « À suivre » tout de suite (si la vidéo s'est terminée avant
            # le fondu prévu), il couvre l'attente jusqu'à la suivante.
            helper.start_fade()
        if monitor.waitForAbort(0.8) or player.isPlaying():
            return  # une file d'attente Kodi a pris le relais
        if helper.mode == 1:
            title = nxt.get('title') or nxt['id']
            if not xbmcgui.Dialog().yesno(L(30293), '[B]%s[/B]' % title, nolabel=L(30295),
                                          yeslabel=L(30294), autoclose=NEXT_ASK_TIMEOUT * 1000):
                return
            helper.start_fade()
        helper.wait_preload()
        upnext.save(self._profile, nxt['id'], rest)
        log('enchaînement : %s' % nxt['id'])
        home = xbmcgui.Window(10000)
        home.clearProperty(PLAY_FAILED)
        xbmc.executebuiltin('PlayMedia(%s)' % self.url(action='play', id=nxt['id'], chain='1'))
        if helper.fade is not None:
            # L'écran « À suivre » reste affiché jusqu'au démarrage effectif de
            # la suivante, ou jusqu'à l'échec de sa lecture (pas d'attente à vide).
            waited = 0.0
            while waited < NEXT_START_TIMEOUT and not monitor.abortRequested():
                if home.getProperty(PLAY_FAILED) == nxt['id']:
                    log('%s : lecture impossible, fin de l\'enchaînement' % nxt['id'])
                    break
                try:
                    if player.isPlayingVideo() and player.getTime() > 0.3:
                        helper.fade.into()
                        helper.fade = None
                        return
                except RuntimeError:
                    pass
                monitor.waitForAbort(0.25)
                waited += 0.25
            helper.cancel()  # échec ou délai dépassé : image et volume rétablis aussitôt

    def _watch(self, video_id, skip_segments, kick_audio, on_tick=None, rewind=False, previous_file=None):
        """Suit la lecture : position de reprise, saut SponsorBlock (ancienne
        méthode) et relance du son au démarrage des flux HLS.

        previous_file : vidéo qui jouait avant celle-ci ; on attend qu'elle ait
        laissé la place, sinon le suivi s'accrocherait à la mauvaise vidéo.
        """
        monitor, player = xbmc.Monitor(), _EndWatcher()
        waited = 0.0
        while True:  # attendre le démarrage de cette lecture
            try:
                if player.isPlayingVideo() and (previous_file is None or player.getPlayingFile() != previous_file):
                    break
            except RuntimeError:
                pass
            if monitor.waitForAbort(0.5) or waited > 30:
                return False
            waited += 0.5
        player.ended = False  # un rappel de fin venu de la vidéo précédente ne compte pas
        try:
            playing_file = player.getPlayingFile()
        except RuntimeError:
            return False
        position = total = 0.0
        saved_at = 0.0
        done = set()
        first = True
        while not monitor.abortRequested() and player.isPlayingVideo():
            try:
                if player.getPlayingFile() != playing_file:
                    break  # une autre vidéo a pris la place
                position = player.getTime()
                total = player.getTotalTime() or total
            except RuntimeError:
                break
            if first:
                first = False
                log('%s : position au démarrage %.1f s' % (video_id, position))
                if rewind and REWIND_FROM <= position < REWIND_FROM + 6:
                    # inputstream.adaptive saute parfois le premier segment HLS
                    # (5 s chez YouTube) en changeant de qualité au démarrage :
                    # retour au tout début, le mode adaptatif reste actif.
                    player.seekTime(0)
                    position = 0.0
                    log('%s : premier segment sauté, retour à 0:00' % video_id)
            if kick_audio and position > 1.0:
                # Le son de certains flux HLS ne démarre qu'après un premier saut :
                # un saut sur place le fait partir dès le début.
                kick_audio = False
                player.seekTime(max(0.0, position - 0.2))
                log('relance du son au démarrage (saut sur place)')
            for i, (seg_start, seg_end) in enumerate(skip_segments):
                if i not in done and seg_start <= position < seg_end - 0.5:
                    done.add(i)
                    player.seekTime(seg_end)  # sans notification (demande de Thib)
            if position - saved_at >= 10 or position < saved_at:
                self._save_resume(video_id, position, total)
                saved_at = position
            if on_tick is not None:
                on_tick(position, total)
            # Suivi plus fin dans les dernières secondes (fondu au bon moment)
            if monitor.waitForAbort(0.1 if total and total - position < 3 else 0.5):
                break
        self._save_resume(video_id, position, total)
        self._last_position, self._last_total = position, total
        if not player.ended and not monitor.abortRequested():
            monitor.waitForAbort(0.5)  # laisse arriver le rappel de fin de lecture
        # Fin naturelle : rappel Kodi, ou position arrivée au bout (repli)
        return player.ended or bool(total and position >= total - 3)

    def _save_resume(self, video_id, position, total):
        try:
            if total and position > total - RESUME_END_MARGIN:
                self.db.clear_resume(video_id)  # vue jusqu'au bout
            elif position >= RESUME_MIN:
                self.db.set_resume(video_id, position, total)
        except sqlite3.Error as exc:  # base occupée : le suivi de lecture continue
            log('position de reprise non enregistrée : %s' % exc, xbmc.LOGWARNING)
