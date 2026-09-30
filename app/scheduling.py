"""Authenticated, bounded source checks for short-lived web processes."""
import asyncio
import hmac
import os
import secrets
import time

from fastapi import HTTPException
import jwt

ISSUER = 'https://token.actions.githubusercontent.com'
AUDIENCE = 'nycstandupopenmicmaster:source-sync'
REPOSITORY = 'taylordrew4u2/nycstandupopenmicmaster'
KEYS = jwt.PyJWKClient(ISSUER + '/.well-known/jwks', timeout=10, cache_keys=True)


def authorize(header):
    if not header.startswith('Bearer '):
        raise HTTPException(401, 'Scheduled check authorization required.')
    token = header[7:]
    secret = os.getenv('CRON_SECRET', '')
    if len(secret) >= 32 and hmac.compare_digest(token.encode(), secret.encode()):
        return
    if os.getenv('GITHUB_SYNC_ENABLED') == '1' and len(token) <= 16000 and token.count('.') == 2:
        try:
            key = KEYS.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=AUDIENCE,
                                issuer=ISSUER, options={'strict_aud': True, 'require': ['exp', 'iat', 'nbf', 'sub', 'jti']})
            expected = {'repository': REPOSITORY, 'repository_id': '1394360235',
                        'repository_owner_id': '225953221', 'ref': 'refs/heads/main',
                        'workflow_ref': REPOSITORY + '/.github/workflows/sync.yml@refs/heads/main'}
            subjects = {f'repo:{REPOSITORY}:ref:refs/heads/main',
                        'repo:taylordrew4u2@225953221/nycstandupopenmicmaster@1394360235:ref:refs/heads/main'}
            if (all(claims.get(k) == v for k, v in expected.items())
                    and claims['sub'] in subjects
                    and claims.get('event_name') in ('schedule', 'workflow_dispatch')
                    and time.time() - claims['iat'] <= 600):
                return
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            pass
    raise HTTPException(401, 'Invalid scheduled check authorization.')


async def run_due(store, sync_one, budget=220):
    def summary():
        sources = store.list_sources()
        enabled = [s for s in sources if s['enabled'] and s['config']['kind'] != 'upload']
        return {'enabled_sources': len(enabled),
                'snapshot_sources': sum(s['config']['kind'] == 'upload' for s in sources),
                'unhealthy_sources': sum(s['state'] in ('error', 'review') for s in enabled),
                'due_remaining': sum(s['next_check'] <= time.time() for s in enabled)}

    outcomes = {'checked': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'review': 0, 'busy': 0}
    results = []
    lease = secrets.token_hex(16)
    now = time.time()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute("SELECT value FROM meta WHERE key='sync_lease'").fetchone()
        if current and float(current[0].split(':', 1)[0]) > now:
            return {'state': 'busy', **outcomes, **summary(), 'results': results}
        c.execute("INSERT INTO meta(key,value) VALUES('sync_lease',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (f'{now + 330}:{lease}',))
    deadline = time.monotonic() + budget
    try:
        due = sorted((s for s in store.list_sources() if s['enabled'] and s['config']['kind'] != 'upload' and s['next_check'] <= now
                      and s['lease_until'] < now), key=lambda s: s['next_check'])
        for source in due:
            if deadline - time.monotonic() < 65:
                break
            current = store.get_source(source['id'])
            if not current or not current['enabled'] or current['next_check'] > time.time():
                continue
            try:
                result = await asyncio.wait_for(sync_one(source['id']), timeout=60)
            except asyncio.TimeoutError:
                result = {'state': 'error', 'message': 'Source check timed out. Existing listings kept.'}
                store.failure(source['id'], result['message'])
            if not isinstance(result, dict) or result.get('state') not in ('updated', 'unchanged', 'error', 'review', 'busy'):
                result = {'state': 'error', 'message': 'Source check returned an invalid outcome. Existing listings kept.'}
                store.failure(source['id'], result['message'])
            state = result['state']
            outcomes['checked'] += 1
            outcomes['failed' if state == 'error' else state] += 1
            results.append({'source_id': source['id'], **{k: result[k] for k in ('state', 'message', 'imported', 'changed') if k in result}})
        from .geocoding import geocode_pending
        await geocode_pending(store, deadline=deadline)
        with store.connect() as c:
            c.execute("INSERT INTO meta(key,value) VALUES('worker_heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(time.time()),))
        return {'state': 'complete', **outcomes, **summary(), 'results': results}
    finally:
        with store.connect() as c:
            c.execute("DELETE FROM meta WHERE key='sync_lease' AND value=?", (f'{now + 330}:{lease}',))
