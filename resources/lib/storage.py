# -*- coding: utf-8 -*-
"""Stockage local (SQLite) : abonnements, historique, recherches récentes.

Aucun compte, aucune synchro : tout reste dans addon_data/plugin.video.wtube/.
"""
import os
import shutil
import sqlite3
import time

HISTORY_MAX = 500
SEARCHES_MAX = 30


LEGACY_ID, LEGACY_DB = 'plugin.video.ytlocal', 'ytlocal.db'  # ancien identifiant (0.1 à 0.4)


def _migrate_legacy(profile_dir, path):
    """Reprend abonnements, historique, favoris et reprises de l'ancien addon."""
    legacy = os.path.join(os.path.dirname(os.path.normpath(profile_dir)), LEGACY_ID, LEGACY_DB)
    if os.path.isfile(legacy):
        try:
            shutil.copy2(legacy, path)
        except OSError:
            pass


class Storage:
    def __init__(self, profile_dir):
        os.makedirs(profile_dir, exist_ok=True)
        path = os.path.join(profile_dir, 'wtube.db')
        if not os.path.exists(path):
            _migrate_legacy(profile_dir, path)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS subs (
                channel_id TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                thumb      TEXT DEFAULT '',
                added      INTEGER
            );
            CREATE TABLE IF NOT EXISTS history (
                video_id   TEXT PRIMARY KEY,
                title      TEXT,
                channel    TEXT,
                channel_id TEXT,
                thumb      TEXT,
                duration   INTEGER,
                watched_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS favorites (
                video_id   TEXT PRIMARY KEY,
                title      TEXT,
                channel    TEXT,
                channel_id TEXT,
                thumb      TEXT,
                duration   INTEGER,
                added      INTEGER
            );
            CREATE TABLE IF NOT EXISTS resume (
                video_id TEXT PRIMARY KEY,
                position REAL,
                total    REAL,
                updated  INTEGER
            );
            CREATE TABLE IF NOT EXISTS searches (
                query TEXT PRIMARY KEY,
                ts    INTEGER
            );
            """
        )
        self._db.commit()

    def close(self):
        self._db.close()

    # --- abonnements -----------------------------------------------------
    def add_sub(self, channel_id, name, thumb=''):
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO subs (channel_id, name, thumb, added) VALUES (?, ?, ?, ?)',
                (channel_id, name, thumb, int(time.time())),
            )

    def remove_sub(self, channel_id):
        with self._db:
            self._db.execute('DELETE FROM subs WHERE channel_id = ?', (channel_id,))

    def sub_ids(self):
        return {r['channel_id'] for r in self._db.execute('SELECT channel_id FROM subs')}

    def list_subs(self):
        rows = self._db.execute('SELECT * FROM subs ORDER BY name COLLATE NOCASE')
        return [dict(r) for r in rows]

    # --- historique ------------------------------------------------------
    def add_history(self, video_id, title, channel='', channel_id='', thumb='', duration=0):
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO history '
                '(video_id, title, channel, channel_id, thumb, duration, watched_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (video_id, title, channel, channel_id, thumb, int(duration or 0), int(time.time())),
            )
            self._db.execute(
                'DELETE FROM history WHERE video_id NOT IN '
                '(SELECT video_id FROM history ORDER BY watched_at DESC LIMIT ?)',
                (HISTORY_MAX,),
            )

    def list_history(self, offset=0, limit=20):
        rows = self._db.execute(
            'SELECT * FROM history ORDER BY watched_at DESC LIMIT ? OFFSET ?', (limit, offset)
        )
        return [
            {
                'kind': 'video',
                'id': r['video_id'],
                'title': r['title'],
                'channel': r['channel'],
                'channel_id': r['channel_id'],
                'thumb': r['thumb'],
                'duration': r['duration'],
                'watched_at': r['watched_at'],
                'url': '',
            }
            for r in rows
        ]

    def remove_history(self, video_id):
        with self._db:
            self._db.execute('DELETE FROM history WHERE video_id = ?', (video_id,))
            self._db.execute('DELETE FROM resume WHERE video_id = ?', (video_id,))

    def clear_history(self):
        with self._db:
            self._db.execute('DELETE FROM history')
            self._db.execute('DELETE FROM resume')

    # --- favoris ----------------------------------------------------------
    def add_favorite(self, video_id, title, channel='', channel_id='', thumb='', duration=0):
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO favorites (video_id, title, channel, channel_id, thumb, duration, added) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (video_id, title, channel, channel_id, thumb, int(duration or 0), int(time.time())),
            )

    def remove_favorite(self, video_id):
        with self._db:
            self._db.execute('DELETE FROM favorites WHERE video_id = ?', (video_id,))

    def favorite_ids(self):
        return {r['video_id'] for r in self._db.execute('SELECT video_id FROM favorites')}

    def list_favorites(self, offset=0, limit=200):
        rows = self._db.execute('SELECT * FROM favorites ORDER BY added DESC LIMIT ? OFFSET ?', (limit, offset))
        return [{'kind': 'video', 'id': r['video_id'], 'title': r['title'], 'channel': r['channel'],
                 'channel_id': r['channel_id'], 'thumb': r['thumb'], 'duration': r['duration'], 'url': ''}
                for r in rows]

    # --- reprise de lecture -----------------------------------------------
    def set_resume(self, video_id, position, total):
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO resume (video_id, position, total, updated) VALUES (?, ?, ?, ?)',
                (video_id, float(position), float(total or 0), int(time.time())),
            )

    def get_resume(self, video_id):
        row = self._db.execute('SELECT position, total FROM resume WHERE video_id = ?', (video_id,)).fetchone()
        return (row['position'], row['total']) if row else None

    def clear_resume(self, video_id):
        with self._db:
            self._db.execute('DELETE FROM resume WHERE video_id = ?', (video_id,))

    def resume_map(self, video_ids):
        ids = list(video_ids)
        if not ids:
            return {}
        rows = self._db.execute('SELECT video_id, position, total FROM resume WHERE video_id IN (%s)'
                                % ','.join('?' * len(ids)), ids)
        return {r['video_id']: (r['position'], r['total']) for r in rows}

    # --- recherches récentes ---------------------------------------------
    def add_search(self, query):
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO searches (query, ts) VALUES (?, ?)', (query, int(time.time()))
            )
            self._db.execute(
                'DELETE FROM searches WHERE query NOT IN '
                '(SELECT query FROM searches ORDER BY ts DESC LIMIT ?)',
                (SEARCHES_MAX,),
            )

    def list_searches(self):
        return [r['query'] for r in self._db.execute('SELECT query FROM searches ORDER BY ts DESC')]

    def clear_searches(self):
        with self._db:
            self._db.execute('DELETE FROM searches')
