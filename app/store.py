"""Durable PostgreSQL/SQLite storage, history and conservative deduplication."""
import hashlib
import json
import re
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

def venue_slot(payload):
    """Exact street/date/time key for matching generic calendar entries only."""
    address = str(payload.get('address', '')).lower().split(',')[0].strip()
    for word, short in [('street','st'),('avenue','ave'),('road','rd'),('boulevard','blvd'),
                        ('west','w'),('east','e'),('north','n'),('south','s')]:
        address = re.sub(r'\b'+word+r'\b', short, address)
    address = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', address)
    address = re.sub(r'[^a-z0-9]', '', address)
    if not address or not payload.get('date'):
        return None
    return address, key(payload.get('borough','')), payload['date'], payload['start_time']


RECURRING_OWNER_FIELDS = {
    'name', 'venue', 'address', 'borough', 'neighborhood', 'start_time', 'signup_time',
    'signup_url', 'signup_method', 'cost', 'cost_text', 'purchase_minimum', 'set_minutes',
    'host_names', 'host_socials', 'notes', 'latitude', 'longitude',
}


def recurring_source_key(source, payload):
    """Only provider-issued mic IDs establish a series, never listing text."""
    from .badslava import supports as badslava_source
    from .comediq import supports as comediq_source
    config = source['config']
    if config.get('kind') == 'upload':
        return None
    external_id = str(payload.get('external_id') or '')
    if badslava_source(config.get('url', '')):
        matched = re.fullmatch(r'badslava:(\d+):(\d{4}-\d{2}-\d{2})', external_id)
        provider = 'badslava'
    elif comediq_source(config.get('url', '')):
        matched = re.fullmatch(r'comediq:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}):(\d{4}-\d{2}-\d{2})', external_id)
        provider = 'comediq'
    else:
        return None
    if not matched or matched[2] != payload.get('date'):
        return None
    # A newly connected source is a separate authority even if its URL matches.
    return source['id'], provider, matched[1]

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
                from .analytics import SCHEMA as ANALYTICS_SCHEMA
                conn.executescript(ANALYTICS_SCHEMA)
                conn.execute("INSERT INTO meta(key,value) VALUES ('visitor_tracking_started',?) ON CONFLICT(key) DO NOTHING", (str(time.time()),))
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

    def commit_preview(self, preview_id, replacement_source_id=None):
        now = time.time()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM previews WHERE id=? AND created>?', (preview_id, now - 1800)).fetchone()
            if not row:
                raise ValueError('Preview expired. Preview the source again before importing.')
            config, result = json.loads(row['config']), json.loads(row['result'])
            if not result['rows']:
                raise ValueError('No valid rows to import.')
            replacement = c.execute('SELECT * FROM sources WHERE id=?' + (' FOR UPDATE' if self.postgres else ''),
                                    (replacement_source_id,)).fetchone() if replacement_source_id else None
            if replacement_source_id and not replacement:
                raise ValueError('Source to replace was not found.')
            if replacement and (replacement['lease_until'] > now or result['skipped']):
                raise ValueError('Wait for any active check and resolve skipped rows before replacing a source.')
            if config['url']:
                for existing in c.execute('SELECT id,config FROM sources'):
                    if existing['id'] != replacement_source_id and json.loads(existing['config'])['url'] == config['url']:
                        raise ValueError('This URL is already connected. Use Check now on its existing source.')
            source_id = replacement_source_id or uuid.uuid4().hex
            validators = json.loads(row['validators'])
            uploaded = config['kind'] == 'upload'
            state = 'snapshot' if uploaded else ('review' if result['skipped'] else 'ready')
            if replacement:
                c.execute('''UPDATE sources SET config=?,enabled=?,state=?,next_check=?,last_attempt=?,last_success=?,changed_at=?,
                             etag=?,modified=?,content_hash=?,upload=?,error=NULL,failure_count=0,lease_until=0 WHERE id=?''',
                          (json.dumps(config),int(not uploaded),state,now+config['interval_minutes']*60,now,now,now,
                           validators.get('etag'),validators.get('modified'),validators.get('hash'),row['content'] if uploaded else None,source_id))
            else:
                c.execute('''INSERT INTO sources(id,config,enabled,state,created,next_check,last_attempt,last_success,changed_at,etag,modified,content_hash,upload,error)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (source_id, json.dumps(config), int(not uploaded), state, now, now + config['interval_minutes'] * 60,
                       now, now, now, validators.get('etag'), validators.get('modified'), validators.get('hash'),
                       row['content'] if uploaded else None,
                       f"{result['skipped']} row(s) skipped. Automatic checks require an entirely valid parse." if result['skipped'] else None))
            changed = self._apply(c, source_id, result['rows'], now, mark_missing=bool(replacement))
            self._run(c, source_id, state, len(result['rows']), changed,
                      'Replacement preview approved by administrator.' if replacement else 'Initial preview approved by administrator.')
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

    def last_live_sync(self, sources=None):
        sources = self.list_sources() if sources is None else sources
        return max((s['last_success'] for s in sources if s['config'].get('url')
                    and s['config']['kind'] != 'upload' and s['last_success']), default=None)

    def public_data(self, include_hidden=False):
        now = time.time()
        sources = {s['id']: s for s in self.list_sources()}
        with self.connect() as c:
            observations = c.execute('SELECT * FROM observations WHERE hidden=0').fetchall()
        from .badslava import supports as badslava_source
        from .comediq import supports as comediq_source
        from .bushwick import supports as bushwick_source
        calendar_slots = defaultdict(set)
        named_slots = defaultdict(set)
        def named_slot(p):
            # Venue titles sometimes append the already-recorded time. No fuzzy names.
            title = re.sub(r"\s*\(\d{1,2}:\d{2}\s*[AP]M\)\s*$", "", p["name"], flags=re.I)
            return venue_slot(p), key(p["venue"]), key(title)
        for o in observations:
            source = sources.get(o['source_id'])
            if source and comediq_source(source['config']['url']):
                p = json.loads(o['payload'])
                if venue_slot(p):
                    named_slots[named_slot(p)].add((p['name'], p['venue']))
            if source and badslava_source(source['config']['url']) and not o['missing_count']:
                p = json.loads(o['payload'])
                slot = venue_slot(p)
                if slot and key(p['name']) == key('Open Mic'):
                    calendar_slots[slot].add((p['name'], p['venue']))
        grouped = defaultdict(list)
        for o in observations:
            source = sources.get(o['source_id'])
            if not source:
                continue
            payload = json.loads(o['payload'])
            schedule = payload['date'] or f"weekly:{payload['weekday']}"
            name, venue = payload['name'], payload['venue']
            unmatched_slot = None
            venue_calendar = bushwick_source(source['config']['url'])
            namespace = 'bushwick' if venue_calendar else 'comediq'
            if comediq_source(source['config']['url']) or venue_calendar:
                matches = calendar_slots.get(venue_slot(payload), set())
                if len(matches) == 1:
                    name, venue = next(iter(matches))
                else:
                    unmatched_slot = digest(venue_slot(payload))[:16]
                    exact_names = named_slots.get(named_slot(payload), set()) if venue_calendar else set()
                    if len(exact_names) == 1:
                        name, venue = next(iter(exact_names))
                        namespace = 'comediq'
            identity = f"{key(name)}|{key(venue)}|{key(payload['borough'])}|{schedule}"
            if unmatched_slot:
                identity += '|' + namespace + ':' + unmatched_slot
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
        # Register occurrence IDs before following an approved provider mic into
        # later dates. Dates remain independent public records and edit targets.
        occurrence_ids = {}
        recurring_members = defaultdict(set)
        registry_new = []
        for identity, items in final_groups.items():
            known = [registry[(o['source_id'], o['remote_key'])] for o, _, _ in items
                     if (o['source_id'], o['remote_key']) in registry]
            mic_id = next((x for x in known if x in owners), known[0] if known else digest(identity)[:24])
            occurrence_ids[identity] = mic_id
            series_keys = {series for _, payload, source in items
                           if (series := recurring_source_key(source, payload))}
            ambiguous_sources = {source_id for source_id, count in
                                 Counter(series[0] for series in series_keys).items() if count > 1}
            for o, payload, source in items:
                registry_new.append((o['source_id'], o['remote_key'], mic_id))
                series = recurring_source_key(source, payload)
                if series and source['id'] not in ambiguous_sources:
                    recurring_members[series].add(mic_id)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.executemany('INSERT INTO mic_registry VALUES (?,?,?) ON CONFLICT(source_id,remote_key) DO UPDATE SET mic_id=excluded.mic_id', registry_new)
            self._inherit_recurring_ownership(c, recurring_members)
            # Read after reconciliation and under the same lock as revocation.
            owners = {r['mic_id']: r['owner_id'] for r in c.execute('SELECT * FROM ownership')}
            overlays = {r['mic_id']: dict(r) for r in c.execute('SELECT * FROM overlays')}
        from .venue_confirmation import official_venue_source
        listings = []
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
            mic_id = occurrence_ids[identity]
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
                             'venue_confirmed':any(s['config']['kind'] != 'upload' and not o['missing_count'] and official_venue_source(s['config']['url']) for o, _, s in items),
                             'curated':bool(curated),'curated_at':curated['updated'] if curated else None,
                             'overridden_fields':list(json.loads(curated['payload'])) if curated else [],
                             'checked_at': observation['last_seen'], 'updated_at': observation['updated'],
                             'conflicts': conflicts, 'stale': bool(stale), 'host_confirmed_at': host_confirmed})
        return {'listings': listings, 'source_count': len(sources), 'last_check': max((s['last_success'] or 0 for s in sources.values()), default=None),
                'last_sync_at': self.last_live_sync(sources.values()),
                'timezone': 'America/New_York', 'mode': 'live' if sources else 'empty'}

    def _inherit_recurring_ownership(self, c, members):
        """Extend active grants; deleting a claim's grants prevents resurrection.

        Call only while holding the shared write lock. Source observations are
        retained after a rollover, which keeps the original approved connection
        available without trusting names, venues, or cross-source similarities.
        """
        ownership = {r['mic_id']: dict(r) for r in c.execute('SELECT * FROM ownership')}
        if not ownership or not members:
            return
        candidates = defaultdict(set)
        blocked = set()
        for ids in members.values():
            grants = {(ownership[m]['owner_id'], ownership[m]['claim_id']) for m in ids if m in ownership}
            if len(grants) > 1:
                blocked.update(ids)
            elif grants:
                for mic_id in ids - ownership.keys():
                    candidates[mic_id].update(grants)
        overlays = [dict(r) for r in c.execute('SELECT * FROM overlays ORDER BY updated')]
        family_edits = {}
        for overlay in overlays:
            grant = ownership.get(overlay['mic_id'])
            if not grant or overlay['actor'] != grant['owner_id']:
                continue
            family = (grant['owner_id'], grant['claim_id'])
            patch, updated = family_edits.setdefault(family, ({}, 0))
            persistent = {k: v for k, v in json.loads(overlay['payload']).items() if k in RECURRING_OWNER_FIELDS}
            if persistent:
                patch.update(persistent)
                family_edits[family] = (patch, max(updated, overlay['updated']))
        existing_overlays = {r['mic_id'] for r in overlays}
        for mic_id, grants in candidates.items():
            if mic_id in blocked or len(grants) != 1:
                continue
            owner_id, claim_id = next(iter(grants))
            approved = min(r['approved'] for r in ownership.values()
                           if r['owner_id'] == owner_id and r['claim_id'] == claim_id)
            c.execute('INSERT INTO ownership VALUES (?,?,?,?)', (mic_id, owner_id, approved, claim_id))
            # Existing occurrence edits (especially administrator corrections)
            # are never replaced by inherited host defaults.
            patch, updated = family_edits.get((owner_id, claim_id), ({}, 0))
            if patch and mic_id not in existing_overlays:
                c.execute('INSERT INTO overlays VALUES (?,?,?,?)', (mic_id, json.dumps(patch), updated, owner_id))

    def review_rows(self):
        with self.connect() as c:
            rows = c.execute('SELECT * FROM observations WHERE missing_count>0 OR hidden=1 ORDER BY missing_count DESC LIMIT 200').fetchall()
            return [{**{k: r[k] for k in ('source_id', 'remote_key', 'missing_count', 'hidden', 'last_seen')}, 'listing': json.loads(r['payload'])} for r in rows]

    def activity(self):
        with self.connect() as c:
            rows = c.execute('SELECT * FROM runs ORDER BY created DESC LIMIT 100').fetchall()
            return [dict(row) for row in rows]
