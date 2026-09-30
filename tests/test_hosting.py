import asyncio
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SourceConfig
from app.parsers import extract
from app.store import Store
from app import scheduling


def source(store, url='https://example.org/mics.csv'):
    config = SourceConfig(name='Test feed', url=url, kind='csv', permission_confirmed=True)
    content = b'id,name,venue,borough,weekday,start_time\n1,Test mic,Test venue,Queens,Monday,19:00\n'
    return store.commit_preview(store.preview(config.model_dump(), extract(content, config)))


def test_restarted_store_and_atomic_leases(database):
    first = Store(database)
    sid = source(first)
    other = Store(database)
    assert other.public_data()['listings'][0]['name'] == 'Test mic'
    now = time.time()
    with other.connect() as c:
        c.execute('UPDATE sources SET next_check=? WHERE id=?', (now, sid))
    assert abs(first.get_source(sid)['next_check'] - now) < 0.001
    with ThreadPoolExecutor(max_workers=2) as pool:
        acquired = list(pool.map(lambda store: store.acquire(sid), [first, other]))
    assert sorted(acquired) == [False, True]


def test_transaction_rollback(database):
    store = Store(database)
    with pytest.raises(ValueError):
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO meta VALUES (?,?)', ('rollback', 'private'))
            raise ValueError('cancel write')
    with store.connect() as c:
        assert c.execute('SELECT * FROM meta WHERE key=?', ('rollback',)).fetchone() is None


def test_bounded_due_checks_and_job_lease(database, monkeypatch):
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    store = Store(database)
    sid = source(store)
    calls = []
    async def sync(source_id):
        calls.append(source_id)
        return store.success(source_id, [], {}, unchanged=True)
    assert asyncio.run(scheduling.run_due(store, sync))['checked'] == 0
    with store.connect() as c:
        c.execute('UPDATE sources SET next_check=0 WHERE id=?', (sid,))
    deferred = asyncio.run(scheduling.run_due(store, sync, budget=0))
    assert deferred['checked'] == 0 and deferred['due_remaining'] == 1
    result = asyncio.run(scheduling.run_due(store, sync))
    assert result['checked'] == 1 and result['unchanged'] == 1 and result['due_remaining'] == 0
    assert calls == [sid]
    with store.connect() as c:
        c.execute('UPDATE sources SET enabled=0 WHERE id=?', (sid,))
    assert asyncio.run(scheduling.run_due(store, sync))['checked'] == 0
    with store.connect() as c:
        c.execute('INSERT INTO meta VALUES (?,?)', ('sync_lease', f'{time.time()+300}:other'))
    assert asyncio.run(scheduling.run_due(store, sync))['state'] == 'busy'


def test_source_failures_are_reported_without_losing_listings(database, monkeypatch):
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    store = Store(database)
    failed, review = source(store), source(store, 'https://example.org/other-mics.csv')
    upload = SourceConfig(name='Uploaded archive', kind='upload', permission_confirmed=True)
    content = b'id,name,venue,borough,weekday,start_time\n2,Archive mic,Test venue,Queens,Tuesday,19:00\n'
    store.commit_preview(store.preview(upload.model_dump(), extract(content, upload, filename='archive.csv'), content))
    before = [(r['id'], r['name']) for r in store.public_data()['listings']]
    with store.connect() as c:
        c.execute('UPDATE sources SET next_check=0')

    async def sync(source_id):
        state = 'error' if source_id == failed else 'review'
        store.failure(source_id, 'A source changed its format.', review=state == 'review')
        return {'state': state, 'message': 'A source changed its format.'}

    result = asyncio.run(scheduling.run_due(store, sync))
    assert result['checked'] == 2 and result['failed'] == 1 and result['review'] == 1
    assert result['enabled_sources'] == 2 and result['snapshot_sources'] == 1
    assert result['unhealthy_sources'] == 2 and result['due_remaining'] == 0
    assert {r['source_id'] for r in result['results']} == {failed, review}
    assert [(r['id'], r['name']) for r in store.public_data()['listings']] == before
    # Backoff must not make a broken source appear healthy on the next worker run.
    later = asyncio.run(scheduling.run_due(store, sync))
    assert later['checked'] == 0 and later['unhealthy_sources'] == 2


def test_timed_out_source_is_reported_and_keeps_listings(database, monkeypatch):
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    store = Store(database)
    sid = source(store)
    with store.connect() as c:
        c.execute('UPDATE sources SET next_check=0 WHERE id=?', (sid,))

    async def sync(source_id):
        raise asyncio.TimeoutError()

    result = asyncio.run(scheduling.run_due(store, sync))
    assert result['failed'] == 1 and result['unhealthy_sources'] == 1
    assert result['results'][0]['state'] == 'error'
    assert len(store.public_data()['listings']) == 1


def test_scheduled_report_fails_for_source_issues_but_not_snapshots(capsys):
    from scripts.scheduled_sync import report_result
    report_result({'state': 'complete', 'checked': 0, 'snapshot_sources': 1, 'due_remaining': 3})
    output = capsys.readouterr().out
    assert 'still due 3' in output and '1 uploaded snapshot(s)' in output
    for problem in ({'failed': 1}, {'review': 1}, {'unhealthy_sources': 1}):
        with pytest.raises(SystemExit, match='live sources need attention'):
            report_result({'state': 'complete', **problem})
    report_result({'state': 'busy', 'snapshot_sources': 1})
    with pytest.raises(SystemExit, match='Unexpected'):
        report_result({'state': 'unknown'})


def test_cron_requires_authentication(database, monkeypatch):
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('GITHUB_SYNC_ENABLED', raising=False)
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv('CRON_SECRET', secret)
    with TestClient(create_app(database)) as client:
        assert client.get('/api/cron/sync').status_code == 401
        assert client.get('/api/cron/sync', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        result = client.get('/api/cron/sync', headers={'Authorization': 'Bearer '+secret})
        assert result.status_code == 200 and result.json()['state'] == 'complete'
        health = client.get('/api/health').json()
        assert time.time()-health['worker_heartbeat'] < 10
    # A cold/restarted process can read the same durable state.
    with TestClient(create_app(database)) as client:
        assert client.get('/api/health').json()['worker_heartbeat'] == health['worker_heartbeat']


def test_vercel_never_falls_back_to_local_storage(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '1')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('POSTGRES_URL', raising=False)
    app = create_app()
    assert app.state.store.path == ''
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        app.state.store.list_sources()
    with pytest.raises(RuntimeError, match='not durable'):
        create_app('/tmp/unsafe.sqlite3')


def test_github_identity_rejects_forks_branches_replays_and_bad_signatures(monkeypatch):
    monkeypatch.setenv('GITHUB_SYNC_ENABLED', '1')
    monkeypatch.delenv('CRON_SECRET', raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(scheduling.KEYS, 'get_signing_key_from_jwt', lambda _: SimpleNamespace(key=key.public_key()))
    now = int(time.time())
    claims = {'iss': scheduling.ISSUER, 'aud': scheduling.AUDIENCE,
              'sub': 'repo:taylordrew4u2@225953221/nycstandupopenmicmaster@1394360235:ref:refs/heads/main',
              'iat': now, 'nbf': now, 'exp': now+300, 'jti': secrets.token_hex(8),
              'repository': scheduling.REPOSITORY, 'repository_id': '1394360235',
              'repository_owner_id': '225953221', 'ref': 'refs/heads/main',
              'workflow_ref': scheduling.REPOSITORY+'/.github/workflows/sync.yml@refs/heads/main',
              'event_name': 'schedule'}
    def header(values, signing_key=key):
        return 'Bearer '+jwt.encode(values, signing_key, algorithm='RS256')
    scheduling.authorize(header(claims))
    for changed in [{'repository_id': '1'}, {'repository_owner_id': '2'}, {'ref': 'refs/heads/fork'},
                    {'event_name': 'pull_request'}, {'workflow_ref': 'different'}, {'sub': 'other'},
                    {'aud': 'different'}, {'exp': now-5}, {'iat': now-700}, {'iss': 'https://example.org'}]:
        with pytest.raises(HTTPException):
            scheduling.authorize(header(claims | changed))
    with pytest.raises(HTTPException):
        scheduling.authorize(header(claims, rsa.generate_private_key(public_exponent=65537, key_size=2048)))
