"""Durable PostgreSQL/SQLite storage, history and conservative deduplication."""
import hashlib
import json
import sqlite3
import time
import threading
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from .parsers import key

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL DEFAULT 'ready', created REAL NOT NULL, next_check REAL NOT NULL,
 last_attempt REAL, last_success REAL, changed_at REAL, etag TEXT, modified TEXT,
 content_hash TEXT, failure_count INTEGER NOT NULL DEFAULT 0, error TEXT,
 lease_until REAL NOT NULL DEFAULT 0, upload BLOB
);
CREATE TABLE IF NOT EXISTS observations (
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 remote_key TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
 first_seen REAL NOT NULL, last_seen REAL NOT NULL, updated REAL NOT NULL,
 missing_count INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source_id, remote_key)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
 created REAL NOT NULL, state TEXT NOT NULL, imported INTEGER NOT NULL DEFAULT 0,
 changed INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS previews (
 id TEXT PRIMARY KEY, created REAL NOT NULL, config TEXT NOT NULL,
 result TEXT NOT NULL, content BLOB, validators TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL, revision TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (ip_hash TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_attempts ON attempts(ip_hash, created);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''

def digest(data):
    if isinstance(data, bytes):
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

class Store:
    def __init__(self, path):
        self.path = str(path)
        self.postgres = self.path.startswith(('postgres://', 'postgresql://'))
        self._ready = False
        self._schema_lock = threading.Lock()

    def _open(self):
        if self.postgres:
            from .database import PostgresConnection
            return PostgresConnection(self.path)
        if not self.path:
            raise RuntimeError('Set DATABASE_URL to a PostgreSQL connection URL before deploying on Vercel.')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _initialize(self):
        # No network calls or database files at module import/build time.
        with self._schema_lock:
            if self._ready:
                return
            conn = self._open()
            try:
                if self.postgres:
                    from .database import SCHEMA_LOCK
                    conn.execute('SELECT pg_advisory_xact_lock(?)', (SCHEMA_LOCK,))
                conn.executescript(SCHEMA)
                from .community import SCHEMA as COMMUNITY_SCHEMA
                conn.executescript(COMMUNITY_SCHEMA)
                conn.commit()
                self._ready = True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def connect(self):
        if not self._ready:
            self._initialize()
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_sources(self):
        with self.connect() as c:
            records = c.execute('''SELECT s.*, (SELECT COUNT(*) FROM observations o WHERE o.source_id=s.id AND o.hidden=0) AS listings
                                  FROM sources s ORDER BY s.created DESC''').fetchall()
            result = []
            for record in records:
                row = dict(record)
                row.pop('upload', None)
                row['config'] = json.loads(row['config'])
                result.append(row)
            return result

    def get_source(self, source_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            if not row:
                return None
            row = dict(row)
            row['config'] = json.loads(row['config'])
            return row

    def preview(self, config, result, content=None, validators=None):
        preview_id = uuid.uuid4().hex
        with self.connect() as c:
            c.execute('DELETE FROM previews WHERE created<?', (time.time() - 1800,))
            c.execute('INSERT INTO previews VALUES (?,?,?,?,?,?)',
                      (preview_id, time.time(), json.dumps(config), json.dumps(result), content, json.dumps(validators or {})))
        return preview_id

    def commit_preview(self, preview_id):
        now = time.time()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM previews WHERE id=? AND created>?', (preview_id, now - 1800)).fetchone()
            if not row:
                raise ValueError('Preview expired. Preview the source again before importing.')
            config, result = json.loads(row['config']), json.loads(row['result'])
            if not result['rows']:
                raise ValueError('No valid rows to import.')
            if config['url']:
                for existing in c.execute('SELECT config FROM sources'):
                    if json.loads(existing['config'])['url'] == config['url']:
                        raise ValueError('This URL is already connected. Use Check now on its existing source.')
            source_id = uuid.uuid4().hex
            validators = json.loads(row['validators'])
            uploaded = config['kind'] == 'upload'
            state = 'snapshot' if uploaded else ('review' if result['skipped'] else 'ready')
            c.execute('''INSERT INTO sources(id,config,enabled,state,created,next_check,last_attempt,last_success,changed_at,etag,modified,content_hash,upload,error)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (source_id, json.dumps(config), int(not uploaded), state, now, now + config['interval_minutes'] * 60,
                       now, now, now, validators.get('etag'), validators.get('modified'), validators.get('hash'),
                       row['content'] if uploaded else None,
                       f"{result['skipped']} row(s) skipped. Automatic checks require an entirely valid parse." if result['skipped'] else None))
            self._apply(c, source_id, result['rows'], now, mark_missing=False)
            self._run(c, source_id, state, len(result['rows']), len(result['rows']), 'Initial preview approved by administrator.')
            c.execute('DELETE FROM previews WHERE id=?', (preview_id,))
            return source_id

    @staticmethod
    def _run(c, source_id, state, imported=0, changed=0, message=''):
        c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',
                  (uuid.uuid4().hex, source_id, time.time(), state, imported, changed, message[:3000]))
        c.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY created DESC LIMIT 2000)')

    @staticmethod
    def _apply(c, source_id, rows, now, mark_missing=True):
        existing = {row['remote_key']: row['payload_hash'] for row in c.execute('SELECT remote_key,payload_hash FROM observations WHERE source_id=?', (source_id,))}
        changed = 0
        if mark_missing:
            c.execute('UPDATE observations SET missing_count=missing_count+1 WHERE source_id=?', (source_id,))
        for row in rows:
            data_hash = digest(row)
            is_changed = existing.get(row['remote_key']) != data_hash
            changed += int(is_changed)
            c.execute('''INSERT INTO observations(source_id,remote_key,payload,payload_hash,first_seen,last_seen,updated)
                         VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_id,remote_key) DO UPDATE SET
                         payload=excluded.payload, payload_hash=excluded.payload_hash,last_seen=excluded.last_seen,
                         updated=CASE WHEN observations.payload_hash!=excluded.payload_hash THEN excluded.updated ELSE observations.updated END,
                         missing_count=0''',
                      (source_id, row['remote_key'], json.dumps(row), data_hash, now, now, now))
        return changed

    def acquire(self, source_id):
        now = time.time()
        with self.connect() as c:
            result = c.execute('UPDATE sources SET lease_until=?,last_attempt=? WHERE id=? AND lease_until<?',
                               (now + 180, now, source_id, now))
            return result.rowcount == 1

    def success(self, source_id, rows, validators, unchanged=False):
        now = time.time()
        source = self.get_source(source_id)
        with self.connect() as c:
            if unchanged:
                changed = 0
                c.execute('UPDATE observations SET last_seen=? WHERE source_id=? AND missing_count=0', (now, source_id))
                count = c.execute('SELECT COUNT(*) FROM observations WHERE source_id=? AND missing_count=0', (source_id,)).fetchone()[0]
            else:
                changed = self._apply(c, source_id, rows, now)
                count = len(rows)
            c.execute('''UPDATE sources SET state='ready',last_success=?,changed_at=CASE WHEN ?>0 THEN ? ELSE changed_at END,
                         next_check=?,failure_count=0,error=NULL,lease_until=0,
                         etag=COALESCE(?,etag),modified=COALESCE(?,modified),content_hash=COALESCE(?,content_hash) WHERE id=?''',
                      (now, changed, now, now + source['config']['interval_minutes']*60, validators.get('etag'),
                       validators.get('modified'), validators.get('hash'), source_id))
            self._run(c, source_id, 'unchanged' if unchanged else 'updated', count, changed)
            return {'state': 'unchanged' if unchanged else 'updated', 'imported': count, 'changed': changed}

    def failure(self, source_id, message, review=False):
        now = time.time()
        source = self.get_source(source_id)
        if not source:
            return
        delay = min(86400, source['config']['interval_minutes'] * 60 * 2 ** min(source['failure_count'], 4))
        with self.connect() as c:
            c.execute('''UPDATE sources SET state=?,error=?,failure_count=failure_count+1,lease_until=0,next_check=? WHERE id=?''',
                      ('review' if review else 'error', message[:2000], now + delay, source_id))
            self._run(c, source_id, 'review' if review else 'error', message=message)

    def public_data(self, include_hidden=False):
        now = time.time()
        sources = {s['id']: s for s in self.list_sources()}
        with self.connect() as c:
            observations = c.execute('SELECT * FROM observations WHERE hidden=0').fetchall()
        grouped = defaultdict(list)
        for o in observations:
            source = sources.get(o['source_id'])
            if not source:
                continue
            payload = json.loads(o['payload'])
            schedule = payload['date'] or f"weekly:{payload['weekday']}"
            identity = f"{key(payload['name'])}|{key(payload['venue'])}|{key(payload['borough'])}|{schedule}"
            grouped[identity].append((dict(o), payload, source))
        final_groups = {}
        for identity, items in grouped.items():
            # Multiple sessions in the same source are not collapsed together.
            repeated_source = any(n > 1 for n in Counter(o['source_id'] for o, _, _ in items).values())
            if repeated_source:
                for item in items:
                    o, payload, _ = item
                    final_groups.setdefault(identity + '|' + payload['start_time'], []).append(item)
            else:
                final_groups[identity] = items
        with self.connect() as c:
            overlays = {r['mic_id']: dict(r) for r in c.execute('SELECT * FROM overlays')}
            flags = {r['mic_id']: r['hidden'] for r in c.execute('SELECT * FROM mic_flags')}
            owners = {r['mic_id']: r['owner_id'] for r in c.execute('SELECT * FROM ownership')}
            registry = {(r['source_id'],r['remote_key']):r['mic_id'] for r in c.execute('SELECT * FROM mic_registry')}
            locations = {r['address_key']:dict(r) for r in c.execute('SELECT * FROM locations')}
        # Reunite source observations that already share a stable mic ID, even
        # when one source renames its listing. This avoids duplicate public IDs.
        stable_groups = {}
        for identity, items in final_groups.items():
            known = [registry[(o['source_id'],o['remote_key'])] for o,_,_ in items if (o['source_id'],o['remote_key']) in registry]
            group_key = ('known:' + known[0]) if known else identity
            stable_groups.setdefault(group_key, []).extend(items)
        final_groups = stable_groups
        listings = []
        registry_new = []
        conflict_fields = ['start_time', 'signup_time', 'cost', 'purchase_minimum', 'set_minutes', 'status', 'address']
        for identity, items in final_groups.items():
            # Published value comes from the configured source priority, never silent recency guessing.
            items.sort(key=lambda x: (-x[2]['config']['priority'], -x[0]['last_seen'], x[0]['source_id']))
            observation, chosen, source = items[0]
            conflicts = []
            for field in conflict_fields:
                values = {json.dumps(payload.get(field), sort_keys=True) for _, payload, _ in items if payload.get(field) not in (None, '')}
                if len(values) > 1:
                    conflicts.append(field)
            all_sources = []
            for o, payload, s in items:
                all_sources.append({'id': s['id'], 'name': s['config']['name'], 'url': s['config']['url'],
                                    'kind': s['config']['kind'], 'checked_at': o['last_seen'],
                                    'state': s['state'] if s['enabled'] or s['config']['kind']=='upload' else 'paused',
                                    'missing_count': o['missing_count'], 'priority': s['config']['priority'],
                                    'values': {f: payload.get(f) for f in conflicts}})
            stale = now - observation['last_seen'] > max(86400, source['config']['interval_minutes'] * 240)
            stale = stale or observation['missing_count'] > 0 or (not source['enabled'] and source['config']['kind'] != 'upload')
            # A stable ID keeps owner permissions attached when the source edits its title.
            known = [registry[(o['source_id'],o['remote_key'])] for o,_,_ in items if (o['source_id'],o['remote_key']) in registry]
            mic_id = next((x for x in known if x in owners), known[0] if known else digest(identity)[:24])
            for o,_,_ in items:
                registry_new.append((o['source_id'],o['remote_key'],mic_id))
            hidden = bool(flags.get(mic_id,False))
            if hidden and not include_hidden:
                continue
            curated = overlays.get(mic_id)
            values = {**chosen, **(json.loads(curated['payload']) if curated else {})}
            patch = json.loads(curated['payload']) if curated else {}
            # Keep imported mic names and public host links when another source only
            # supplies a generic event label. Explicit owner edits take precedence.
            for field in ('host_names', 'host_socials'):
                if field not in patch and not values.get(field):
                    values[field] = next((p.get(field) for _, p, _ in items if p.get(field)), '')
            if 'name' not in patch:
                values['name'] = next((p['display_name'] for _, p, _ in items if p.get('display_name')), values['name'])
            from .geocoding import address_key
            geo = locations.get(address_key(values))
            map_status = 'source' if values.get('latitude') is not None else 'unmapped'
            if values.get('latitude') is None and geo and geo['state']=='geocoded':
                values['latitude'],values['longitude'] = geo['lat'],geo['lng']
                map_status = 'geocoded'
            if curated and any(k in json.loads(curated['payload']) for k in ('latitude','longitude')) and values.get('latitude') is not None and map_status!='geocoded':
                map_status = 'reviewed'
            host_confirmed = curated['updated'] if curated and curated['actor']==owners.get(mic_id) else None
            listings.append({**values, 'id': mic_id, 'sources': all_sources,
                             'map_status':map_status,'hidden':hidden,'claimed':mic_id in owners,
                             'curated':bool(curated),'curated_at':curated['updated'] if curated else None,
                             'overridden_fields':list(json.loads(curated['payload'])) if curated else [],
                             'checked_at': observation['last_seen'], 'updated_at': observation['updated'],
                             'conflicts': conflicts, 'stale': bool(stale), 'host_confirmed_at': host_confirmed})
        with self.connect() as c:
            c.executemany('INSERT INTO mic_registry VALUES (?,?,?) ON CONFLICT(source_id,remote_key) DO UPDATE SET mic_id=excluded.mic_id', registry_new)
        return {'listings': listings, 'source_count': len(sources), 'last_check': max((s['last_success'] or 0 for s in sources.values()), default=None),
                'timezone': 'America/New_York', 'mode': 'live' if sources else 'empty'}

    def review_rows(self):
        with self.connect() as c:
            rows = c.execute('SELECT * FROM observations WHERE missing_count>0 OR hidden=1 ORDER BY missing_count DESC LIMIT 200').fetchall()
            return [{**{k: r[k] for k in ('source_id', 'remote_key', 'missing_count', 'hidden', 'last_seen')}, 'listing': json.loads(r['payload'])} for r in rows]

    def activity(self):
        with self.connect() as c:
            rows = c.execute('SELECT * FROM runs ORDER BY created DESC LIMIT 100').fetchall()
            return [dict(row) for row in rows]
