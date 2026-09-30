"""Credential changes work across workers without leaving old sessions valid."""
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app import admin_auth


HEADERS = {'X-Requested-With': 'MicList'}


@pytest.fixture
def admins(database, monkeypatch):
    original = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', original)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    first, second = create_app(database), create_app(database)
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app, headers=HEADERS)) for app in (first, second)]
        for client in clients:
            assert client.post('/api/login', json={'password': original}).status_code == 200
        yield first, second, clients[0], clients[1], original


def test_password_change_revokes_sessions_and_reaches_existing_workers(admins):
    first, second, a, b, original = admins
    replacement = secrets.token_hex(2)  # Exercise a chosen short password, without a real credential fixture.
    old_cookie = b.cookies.get('miclist_session')
    response = a.post('/api/admin/password', json={'current_password': original, 'new_password': replacement})
    assert response.status_code == 200
    assert response.json() == {'authenticated': False, 'signin_required': True, 'weak_password': True}
    assert a.cookies.get('miclist_session') is None
    assert a.get('/api/sources').status_code == 401
    assert b.get('/api/sources').status_code == 401
    assert b.post('/api/login', json={'password': original}).status_code == 401
    assert b.post('/api/login', json={'password': replacement}).status_code == 200
    assert b.get('/api/session').json()['weak_password'] is True
    assert a.post('/api/login', json={'password': replacement}).status_code == 200
    with first.state.store.connect() as c:
        record = json.loads(c.execute('SELECT value FROM meta WHERE key=?', (admin_auth.META_KEY,)).fetchone()[0])
        assert len(record['salt']) == 48
        assert len(record['password_hash']) == 64
        assert record['password_hash'] != replacement
        assert original not in json.dumps(record)
        assert record['revision'] != admin_auth.hashlib.sha256(replacement.encode()).hexdigest()
    # A cookie saved before the change cannot be replayed on either worker.
    a.cookies.set('miclist_session', old_cookie, domain='testserver.local', path='/')
    assert a.get('/api/sources').status_code == 401


def test_change_requires_session_and_current_password(admins):
    first, second, a, b, original = admins
    replacement = secrets.token_urlsafe(24)
    wrong = secrets.token_urlsafe(24)
    response = a.post('/api/admin/password', json={'current_password': wrong, 'new_password': replacement})
    assert response.status_code == 401
    assert b.get('/api/sources').status_code == 200
    with TestClient(first, headers=HEADERS) as visitor:
        assert visitor.post('/api/admin/password', json={
            'current_password': original, 'new_password': replacement
        }).status_code == 401
    assert a.post('/api/admin/password', json={'current_password': original, 'new_password': ''}).status_code == 422
    assert b.get('/api/session').json()['weak_password'] is False


def test_current_password_attempts_are_rate_limited(admins):
    first, second, a, b, original = admins
    wrong, replacement = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    for _ in range(8):
        assert a.post('/api/admin/password', json={
            'current_password': wrong, 'new_password': replacement
        }).status_code == 401
    assert a.post('/api/admin/password', json={
        'current_password': original, 'new_password': replacement
    }).status_code == 429
    assert b.post('/api/login', json={'password': original}).status_code == 429


def test_environment_rotation_recovers_and_retires_stale_workers(admins, monkeypatch):
    first, second, a, b, original = admins
    replacement, recovery = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    assert a.post('/api/admin/password', json={
        'current_password': original, 'new_password': replacement
    }).status_code == 200
    assert b.post('/api/login', json={'password': replacement}).status_code == 200
    stale_cookie = b.cookies.get('miclist_session')
    monkeypatch.setenv('ADMIN_PASSWORD', recovery)
    recovered = create_app(first.state.store.path)
    with TestClient(recovered, headers=HEADERS) as c:
        assert c.post('/api/login', json={'password': recovery}).status_code == 200
        assert c.post('/api/login', json={'password': replacement}).status_code == 401
        assert b.get('/api/sources').status_code == 401
        assert b.post('/api/login', json={'password': original}).status_code == 503
        assert b.post('/api/login', json={'password': replacement}).status_code == 503
        assert c.get('/api/sources').status_code == 200
        c.cookies.set('miclist_session', stale_cookie, domain='testserver.local', path='/')
        assert c.get('/api/sources').status_code == 401


def test_concurrent_changes_cannot_both_commit(admins, monkeypatch):
    first, second, a, b, original = admins
    replacements = [secrets.token_urlsafe(24), secrets.token_urlsafe(24)]
    barrier = threading.Barrier(2)
    make_record = admin_auth.password_record

    def pause_after_hash(password):
        result = make_record(password)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(admin_auth, 'password_record', pause_after_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, '/api/admin/password', json={
            'current_password': original, 'new_password': password
        }) for client, password in zip((a, b), replacements)]
        responses = [f.result(timeout=20) for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 401]
    winner = next(password for password, response in zip(replacements, responses) if response.status_code == 200)
    loser = next(password for password, response in zip(replacements, responses) if response.status_code == 401)
    assert b.post('/api/login', json={'password': loser}).status_code == 401
    assert b.post('/api/login', json={'password': winner}).status_code == 200
