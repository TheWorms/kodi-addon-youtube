# -*- coding: utf-8 -*-
"""Stockage local (SQLite) : abonnements, historique, recherches récentes.

Aucun compte, aucune synchro : tout reste dans addon_data/plugin.video.ytlocal/.
"""
import os
import sqlite3
import time

HISTORY_MAX = 500
SEARCHES_MAX = 30


class Storage:
    def __init__(self, profile_dir):
        os.makedirs(profile_dir, exist_ok=True)
        self._db = sqlite3.connect(os.path.join(profile_dir, 'ytlocal.db'))
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
                'url': '',
            }
            for r in rows
        ]

    def remove_history(self, video_id):
        with self._db:
            self._db.execute('DELETE FROM history WHERE video_id = ?', (video_id,))

    def clear_history(self):
        with self._db:
            self._db.execute('DELETE FROM history')

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
