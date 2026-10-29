"""Transactionally published, compressed directory snapshots using existing storage.

Readers never materialize listings. Writers replay observation changes against a
compressed copy, so checking a source doesn't download every historical payload.
Only the public projection is eligible for the bundled outage fallback.
"""
import base64
import copy
import gzip
import json
import re
import sqlite3
import time
import uuid
import zlib
from contextlib import closing
from pathlib import Path

FALLBACK_PATH = Path(__file__).with_name('public_snapshot.json.gz')
SNAPSHOT_KEY = 'public_snapshot'
REVISION_KEY = 'public_snapshot_revision'
INPUT_KEY = 'snapshot_observations'
REGISTRY_KEY = 'snapshot_registry'
PUBLISHED = {'sources', 'observations', 'overlays', 'mic_flags', 'ownership', 'locations', 'mic_registry'}


def pack(value):
    return base64.b64encode(zlib.compress(json.dumps(value, separators=(',', ':')).encode(), 9)).decode()


def unpack(value):
    return json.loads(zlib.decompress(base64.b64decode(value)))


def put(c, key, value):
    c.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


class PublishingConnection:
    """Track published writes, serialize them, and keep publication in their transaction."""
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres
        self.dirty = False
        self.locked = False
        self.observation_writes = []
        self.registry_writes = []

    def _before(self, sql):
        normalized = ' '.join(sql.lower().split())
        if normalized == 'begin immediate':
            if self.locked:
                return False
            self.locked = True
            return True
        match = re.match(r'(?:insert\s+(?:or\s+\w+\s+)?into|update|delete\s+from)\s+(\w+)', normalized)
        table = match[1] if match else None
        if table not in PUBLISHED:
            return True
        # Lease acquisition is operational state, not a directory change.
        if table == 'sources' and normalized.startswith('update sources set '):
            assigned = normalized.split(' set ', 1)[1].split(' where ', 1)[0]
            if not re.search(r'\b(config|enabled|state|last_success|error)\s*=', assigned):
                return True
        if not self.locked:
            if self.postgres or not self.connection.in_transaction:
                self.connection.execute('BEGIN IMMEDIATE')
            self.locked = True
        self.dirty = True
        return True

    def execute(self, sql, args=None):
        if not self._before(sql):
            return self.connection.execute('SELECT 1')
        result = self.connection.execute(sql, args or ())
        normalized = sql.lstrip().lower()
        if re.match(r'(insert\s+into|update|delete\s+from)\s+observations\b', normalized):
            self.observation_writes.append((sql, args or ()))
        if re.match(r'(insert\s+into|update|delete\s+from)\s+mic_registry\b', normalized):
            self.registry_writes.append((sql, args or ()))
        return result

    def executemany(self, sql, args):
        for row in args:
            self.execute(sql, row)

    def __getattr__(self, name):
        return getattr(self.connection, name)


OBSERVATION_SCHEMA = '''CREATE TABLE observations (
 source_id TEXT NOT NULL, remote_key TEXT NOT NULL, payload TEXT NOT NULL,
 payload_hash TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
 updated REAL NOT NULL, missing_count INTEGER NOT NULL DEFAULT 0,
 hidden INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source_id,remote_key))'''


def observations(c, writes=(), cached=None):
    if cached is None:
        saved = c.execute('SELECT value FROM meta WHERE key=?', (INPUT_KEY,)).fetchone()
        if not saved:
            # One bootstrap, performed by a published write, never by a public visit.
            return [dict(row) for row in c.execute('SELECT * FROM observations')]
        rows = unpack(saved[0])
    else:
        rows = copy.deepcopy(cached)
    if writes:
        # The application uses a shared SQLite/PostgreSQL SQL subset. Replaying
        # the exact successful writes retains hidden/missing/history semantics.
        with sqlite3.connect(':memory:') as mirror:
            mirror.row_factory = sqlite3.Row
            mirror.execute(OBSERVATION_SCHEMA)
            columns = ('source_id', 'remote_key', 'payload', 'payload_hash', 'first_seen', 'last_seen', 'updated', 'missing_count', 'hidden')
            mirror.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                               [tuple(row[k] for k in columns) for row in rows])
            for sql, args in writes:
                mirror.execute(sql, args)
            rows = [dict(row) for row in mirror.execute('SELECT * FROM observations')]
    # Deleting a source cascades on the authoritative database, not the mirror.
    sources = {row[0] for row in c.execute('SELECT id FROM sources')}
    return [row for row in rows if row['source_id'] in sources]


def registry(c, writes=(), cached=None):
    if cached is None:
        saved = c.execute('SELECT value FROM meta WHERE key=?', (REGISTRY_KEY,)).fetchone()
        if not saved:
            return {(r['source_id'], r['remote_key']): r['mic_id'] for r in c.execute('SELECT * FROM mic_registry')}
        rows = unpack(saved[0])
    else:
        rows = [[sid, remote, mic] for (sid, remote), mic in cached.items()]
    if writes:
        with sqlite3.connect(':memory:') as mirror:
            mirror.execute('CREATE TABLE mic_registry (source_id TEXT, remote_key TEXT, mic_id TEXT, PRIMARY KEY(source_id,remote_key))')
            mirror.executemany('INSERT INTO mic_registry VALUES (?,?,?)', rows)
            for sql, args in writes:
                mirror.execute(sql, args)
            rows = mirror.execute('SELECT * FROM mic_registry').fetchall()
    sources = {r[0] for r in c.execute('SELECT id FROM sources')}
    return {(sid, remote): mic for sid, remote, mic in rows if sid in sources}


def inputs(store, c, observation_writes=(), registry_writes=()):
    current = c.execute('SELECT value FROM meta WHERE key=?', (REVISION_KEY,)).fetchone()
    revision = json.loads(current[0])['revision'] if current else None
    cache = store._materialized
    cached_rows, cached_ids = (cache[1], cache[2]) if cache and cache[0] == revision else (None, None)
    return (observations(c, observation_writes, cached_rows),
            registry(c, registry_writes, cached_ids))


def publish(store, transaction):
    c = transaction.connection
    rows, ids = inputs(store, c, transaction.observation_writes, transaction.registry_writes)
    data = store._build_public_data(c, rows, ids)
    now = time.time()
    data.update(revision=uuid.uuid4().hex, snapshot_at=now, stale=False)
    put(c, INPUT_KEY, pack(rows))
    put(c, REGISTRY_KEY, pack([[sid, remote, mic] for (sid, remote), mic in ids.items()]))
    put(c, SNAPSHOT_KEY, pack(data))
    put(c, REVISION_KEY, json.dumps(metadata(data), separators=(',', ':')))
    transaction.materialized = (data['revision'], rows, ids)
    return data


def metadata(data):
    return {k: data.get(k) for k in ('revision', 'snapshot_at', 'stale', 'last_sync_at')}


def validate_public(data):
    allowed = {'listings', 'source_count', 'last_check', 'last_sync_at', 'timezone', 'mode',
               'revision', 'snapshot_at', 'stale'}
    private = {'owners', 'ownership', 'submissions', 'invitations', 'sessions',
               'password_hash', 'token_hash', 'email', 'owner_id', 'actor'}

    def contains_private(value):
        if isinstance(value, dict):
            return bool(private.intersection(value)) or any(contains_private(v) for v in value.values())
        if isinstance(value, list):
            return any(contains_private(v) for v in value)
        return False

    if (not isinstance(data, dict) or set(data) - allowed
            or not isinstance(data.get('listings'), list)
            or not isinstance(data.get('snapshot_at'), (int, float))
            or not data.get('revision') or contains_private(data)
            or any(not isinstance(row, dict) or row.get('hidden') for row in data['listings'])):
        raise ValueError('Invalid public fallback snapshot')
    return data


def empty():
    return {'listings': [], 'source_count': 0, 'last_check': None, 'last_sync_at': None,
            'timezone': 'America/New_York', 'mode': 'empty',
            'revision': 'empty', 'snapshot_at': 0, 'stale': False}


def fallback(store):
    cached = store._public_snapshot
    if cached is not None:
        data = copy.deepcopy(cached)
    elif store.postgres:
        # This file is created from the public endpoint, never a database backup.
        with gzip.open(FALLBACK_PATH, 'rt') as source:
            data = json.load(source)
        validate_public(data)
    else:
        return empty()
    data['stale'] = True
    return data


def read(store, revision_only=False):
    try:
        # Public reads don't run schema initialization or any write hooks.
        with closing(store._open()) as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', (REVISION_KEY,)).fetchone()
            if not row:
                # A healthy new installation is empty; a legacy populated
                # database waits for its first authorized publication.
                if not c.execute('SELECT 1 FROM sources LIMIT 1').fetchone():
                    return metadata(empty()) if revision_only else empty()
                return metadata(fallback(store)) if revision_only else fallback(store)
            current = json.loads(row[0])
            if revision_only:
                return current
            cached = store._public_snapshot
            if cached is None or cached.get('revision') != current['revision']:
                saved = c.execute('SELECT value FROM meta WHERE key=?', (SNAPSHOT_KEY,)).fetchone()
                if not saved:
                    raise ValueError('Missing published directory snapshot')
                data = unpack(saved[0])
                # Both values are read within one transaction, but a PostgreSQL
                # READ COMMITTED reader may observe a later committed payload.
                cached = data
                store._public_snapshot = cached
            return copy.deepcopy(cached)
    except (OSError, sqlite3.Error, ValueError, KeyError, zlib.error):
        data = fallback(store)
        return metadata(data) if revision_only else data
    except Exception as exc:
        # psycopg is an optional dependency in local SQLite installations.
        if exc.__class__.__module__.startswith('psycopg'):
            data = fallback(store)
            return metadata(data) if revision_only else data
        raise
