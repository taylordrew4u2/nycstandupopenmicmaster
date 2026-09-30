"""Approved hosts manage only their own credentials and claimed mic families."""
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app import community
from app.main import create_app


HEADERS = {'X-Requested-With': 'MicList'}


@pytest.fixture
def accounts(database, monkeypatch):
    admin_password = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', admin_password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    app = create_app(database)
    with ExitStack() as stack:
        admin, visitor, host, second = [stack.enter_context(TestClient(app, headers=HEADERS)) for _ in range(4)]
        assert admin.post('/api/login', json={'password': admin_password}).status_code == 200

        def approved_mic(client, name, email, password):
            created = admin.post('/api/admin/mics', json={'fields': {
                'name': name, 'venue': 'Example Room', 'borough': 'Manhattan',
                'weekday': 0, 'start_time': '19:00', 'address': '1 Broadway'
            }})
            assert created.status_code == 201, created.text
            mic = next(row for row in visitor.get('/api/public').json()['listings'] if row['name'] == name)
            submitted = visitor.post('/api/mics/' + mic['id'] + '/submissions', json={
                'kind': 'claim', 'name': 'Example Host', 'email': email,
                'message': 'I run this mic; the venue can confirm ownership.'
            })
            assert submitted.status_code == 201, submitted.text
            claim = next(row for row in admin.get('/api/admin/community').json()['submissions'] if row['mic_id'] == mic['id'])
            approval = admin.post('/api/admin/submissions/' + claim['id'], json={'action': 'approve'})
            assert approval.status_code == 200, approval.text
            token = approval.json()['invite_path'].split('#')[1]
            if client:
                activated = client.post('/api/owner/redeem', json={'token': token, 'password': password})
                assert activated.status_code == 200, activated.text
            return mic, claim, token

        password = secrets.token_urlsafe(24)
        mic, claim, token = approved_mic(host, 'Approved first mic', 'host@example.com', password)
        yield app, admin, visitor, host, second, password, mic, claim, approved_mic


def test_password_change_revokes_all_own_sessions_only(accounts):
    app, admin, visitor, host, other, original, mic, claim, approved_mic = accounts
    other_password = secrets.token_urlsafe(24)
    other_mic, _, _ = approved_mic(other, 'Separate host mic', 'other@example.com', other_password)
    with TestClient(app, headers=HEADERS) as another_session:
        assert another_session.post('/api/owner/login', json={'email': 'host@example.com', 'password': original}).status_code == 200
        old_cookie = another_session.cookies.get('miclist_owner')
        replacement = secrets.token_urlsafe(24)
        changed = host.post('/api/owner/password', json={'current_password': original, 'new_password': replacement})
        assert changed.status_code == 200, changed.text
        assert changed.json() == {'ok': True, 'signin_required': True}
        assert host.cookies.get('miclist_owner') is None
        assert host.get('/api/owner/me').status_code == 401
        assert another_session.get('/api/owner/me').status_code == 401
        assert another_session.patch('/api/owner/mics/' + mic['id'], json={'fields': {'notes': 'stale session'}}).status_code == 401
        assert host.post('/api/owner/login', json={'email': 'host@example.com', 'password': original}).status_code == 401
        assert host.post('/api/owner/login', json={'email': 'host@example.com', 'password': replacement}).status_code == 200
        assert host.get('/api/owner/me').status_code == 200
        another_session.cookies.set('miclist_owner', old_cookie, domain='testserver.local', path='/')
        assert another_session.get('/api/owner/me').status_code == 401
    assert other.get('/api/owner/me').status_code == 200
    assert other.patch('/api/owner/mics/' + other_mic['id'], json={'fields': {'notes': 'still permitted'}}).status_code == 200
    assert other.post('/api/owner/login', json={'email': 'other@example.com', 'password': other_password}).status_code == 200
    assert admin.get('/api/admin/community').status_code == 200
    with app.state.store.connect() as c:
        stored = c.execute('SELECT password_hash FROM owners WHERE email=?', ('host@example.com',)).fetchone()[0]
        assert stored != replacement and community.verify_password(replacement, stored)
        audit = list(c.execute("SELECT detail FROM audit WHERE action='password-changed'"))
        assert len(audit) == 1 and audit[0][0] == ''


def test_password_requires_owner_current_password_and_minimum_length(accounts):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    replacement = secrets.token_urlsafe(24)
    with app.state.store.connect() as c:
        before = c.execute('SELECT password_hash FROM owners WHERE email=?', ('host@example.com',)).fetchone()[0]
    assert visitor.post('/api/owner/password', json={'current_password': original, 'new_password': replacement}).status_code == 401
    assert host.post('/api/owner/password', json={'current_password': 'wrong', 'new_password': replacement}).status_code == 401
    for invalid in ('', 'short', 'x' * 129):
        assert host.post('/api/owner/password', json={'current_password': original, 'new_password': invalid}).status_code == 422
    assert host.get('/api/owner/me').status_code == 200
    with app.state.store.connect() as c:
        assert c.execute('SELECT password_hash FROM owners WHERE email=?', ('host@example.com',)).fetchone()[0] == before
        assert c.execute("SELECT COUNT(*) FROM audit WHERE action='password-changed'").fetchone()[0] == 0


def test_password_attempt_budget_is_shared_with_login(accounts):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    for _ in range(8):
        assert host.post('/api/owner/password', json={'current_password': 'wrong', 'new_password': secrets.token_urlsafe(24)}).status_code == 401
    assert host.post('/api/owner/password', json={'current_password': original, 'new_password': secrets.token_urlsafe(24)}).status_code == 429
    assert second.post('/api/owner/login', json={'email': 'host@example.com', 'password': original}).status_code == 429


def test_hosts_cannot_use_site_administrator_endpoints(accounts):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    assert host.get('/api/admin/community').status_code == 401
    assert host.get('/api/sources').status_code == 401
    assert host.patch('/api/admin/mics/' + mic['id'], json={'fields': {'notes': 'no'}}).status_code == 401
    assert host.delete('/api/admin/mics/' + mic['id'] + '/owner').status_code == 401
    assert host.post('/api/admin/password', json={'current_password': original, 'new_password': secrets.token_urlsafe(24)}).status_code == 401


def test_invitation_lookup_needs_only_token(accounts):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    _, _, token = approved_mic(None, 'Pending activation mic', 'new@example.com', '')
    response = visitor.post('/api/owner/invitation', json={'token': token})
    assert response.status_code == 200
    assert response.json()['email'] == 'new@example.com'
    assert response.json()['existing_account'] is False
    assert visitor.post('/api/owner/invitation', json={'token': token, 'password': 'legacy-dummy'}).status_code == 200
    assert visitor.post('/api/owner/invitation', json={'token': 'x' * 40}).status_code == 400


def test_revocation_removes_claim_family_but_keeps_separate_approval(accounts):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    other_mic, other_claim, _ = approved_mic(host, 'Separately approved mic', 'host@example.com', original)
    owner_id = host.get('/api/owner/me').json()['id']
    # A subsequent weekly date receives this same claim_id during reconciliation.
    family_mic = 'future-occurrence'
    with app.state.store.connect() as c:
        c.execute('INSERT INTO ownership VALUES (?,?,?,?)', (family_mic, owner_id, time.time(), claim['id']))
    assert admin.delete('/api/admin/mics/' + family_mic + '/owner').status_code == 200
    with app.state.store.connect() as c:
        remaining = list(c.execute('SELECT mic_id,claim_id FROM ownership WHERE owner_id=?', (owner_id,)))
        assert len(remaining) == 1
        assert remaining[0]['mic_id'] == other_mic['id'] and remaining[0]['claim_id'] == other_claim['id']
        assert c.execute('SELECT state FROM submissions WHERE id=?', (claim['id'],)).fetchone()[0] == 'revoked'
        assert c.execute('SELECT 1 FROM invitations WHERE submission_id=?', (claim['id'],)).fetchone() is None
    assert host.patch('/api/owner/mics/' + mic['id'], json={'fields': {'notes': 'revoked'}}).status_code == 403
    assert host.patch('/api/owner/mics/' + other_mic['id'], json={'fields': {'notes': 'allowed'}}).status_code == 200


def test_owner_me_includes_permissions_reconciled_during_public_read(accounts, monkeypatch):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    owner_id = host.get('/api/owner/me').json()['id']
    public_data = app.state.store.public_data

    def reconciled(**kwargs):
        data = public_data(**kwargs)
        with app.state.store.connect() as c:
            c.execute('INSERT INTO ownership VALUES (?,?,?,?) ON CONFLICT(mic_id) DO NOTHING', ('next-week', owner_id, time.time(), claim['id']))
        data['listings'].append({**data['listings'][0], 'id': 'next-week'})
        return data

    monkeypatch.setattr(app.state.store, 'public_data', reconciled)
    ids = {row['id'] for row in host.get('/api/owner/me').json()['listings']}
    assert ids == {mic['id'], 'next-week'}


def test_concurrent_password_changes_have_one_winner(accounts, monkeypatch):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    assert second.post('/api/owner/login', json={'email': 'host@example.com', 'password': original}).status_code == 200
    passwords = [secrets.token_urlsafe(24), secrets.token_urlsafe(24)]
    barrier = threading.Barrier(2)
    make_hash = community.hash_password

    def pause_new_hash(password, salt=None):
        value = make_hash(password, salt)
        if password in passwords and salt is None:
            barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(community, 'hash_password', pause_new_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(client.post, '/api/owner/password', json={'current_password': original, 'new_password': password}) for client, password in zip((host, second), passwords)]
        responses = [task.result(timeout=20) for task in tasks]
    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = next(password for password, response in zip(passwords, responses) if response.status_code == 200)
    assert second.post('/api/owner/login', json={'email': 'host@example.com', 'password': winner}).status_code == 200


def test_login_verified_before_password_change_cannot_issue_session_after_it(accounts, monkeypatch):
    app, admin, visitor, host, second, original, mic, claim, approved_mic = accounts
    verified = threading.Event()
    proceed = threading.Event()
    verify = community.verify_password

    def pause_first_verification(password, encoded):
        result = verify(password, encoded)
        if not verified.is_set():
            verified.set()
            assert proceed.wait(timeout=10)
        return result

    monkeypatch.setattr(community, 'verify_password', pause_first_verification)
    with ThreadPoolExecutor(max_workers=1) as pool:
        login = pool.submit(second.post, '/api/owner/login', json={'email': 'host@example.com', 'password': original})
        assert verified.wait(timeout=10)
        try:
            response = host.post('/api/owner/password', json={'current_password': original, 'new_password': secrets.token_urlsafe(24)})
            assert response.status_code == 200
        finally:
            proceed.set()
        assert login.result(timeout=10).status_code == 401
    assert second.get('/api/owner/me').status_code == 401
