"""First-party anonymous browser counts. No IP addresses or browsing histories."""
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

COOKIE = 'miclist_visitor'
SCHEMA = '''CREATE TABLE IF NOT EXISTS visitors (
 visitor_hash TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS visitors_last_seen ON visitors(last_seen);
'''


def register(app, store, admin, local_dev):
    from fastapi import Depends, Request, Response

    @app.post('/api/visit', status_code=204)
    async def visit(request: Request):
        response = Response(status_code=204)
        agent = request.headers.get('user-agent', '')
        if (request.headers.get('sec-gpc') == '1' or request.headers.get('dnt') == '1'
                or request.cookies.get('miclist_session') or request.cookies.get('miclist_owner')
                or re.search(r'bot|crawler|spider|headless|preview', agent, re.I)):
            return response
        token = request.cookies.get(COOKIE, '')
        fresh = not re.fullmatch(r'[A-Za-z0-9_-]{43}', token)
        if fresh:
            token = secrets.token_urlsafe(32)
        now = time.time()
        with store.connect() as c:
            c.execute('''INSERT INTO visitors VALUES (?,?,?) ON CONFLICT(visitor_hash)
                         DO UPDATE SET last_seen=excluded.last_seen''',
                      (hashlib.sha256(token.encode()).hexdigest(), now, now))
            c.execute("INSERT INTO meta(key,value) VALUES ('public_visit_total','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(meta.value AS BIGINT)+1 AS TEXT)")
            c.execute("INSERT INTO meta(key,value) VALUES ('public_visits_started',?) ON CONFLICT(key) DO NOTHING", (str(now),))
        if fresh:
            response.set_cookie(COOKIE, token, max_age=365*86400, secure=not local_dev,
                                httponly=True, samesite='lax')
        return response

    @app.get('/api/visits')
    async def public_visits():
        with store.connect() as c:
            rows = c.execute("SELECT key,value FROM meta WHERE key IN ('public_visit_total','public_visits_started')").fetchall()
        values = {row['key']: row['value'] for row in rows}
        return {'total': int(values.get('public_visit_total', 0)),
                'started_at': float(values['public_visits_started']) if 'public_visits_started' in values else None}

    @app.get('/api/admin/visitors', dependencies=[Depends(admin)])
    async def visitors():
        today = datetime.now(ZoneInfo('America/New_York')).replace(hour=0, minute=0, second=0, microsecond=0)
        starts = [today.timestamp(), (today-timedelta(days=6)).timestamp(), (today-timedelta(days=29)).timestamp()]
        with store.connect() as c:
            row = c.execute('''SELECT COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN last_seen>=? THEN 1 ELSE 0 END),0) AS today,
                COALESCE(SUM(CASE WHEN last_seen>=? THEN 1 ELSE 0 END),0) AS week,
                COALESCE(SUM(CASE WHEN last_seen>=? THEN 1 ELSE 0 END),0) AS month
                FROM visitors''', starts).fetchone()
            started = c.execute("SELECT value FROM meta WHERE key='visitor_tracking_started'").fetchone()
        return {**dict(row), 'started_at': float(started['value']), 'timezone': 'America/New_York'}
