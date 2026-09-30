"""Approved host permissions follow exact provider IDs without merging dates."""
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SourceConfig
from app.parsers import normalize_rows


HEADERS = {'X-Requested-With': 'MicList'}
HOST_PASSWORD = 'the approved host has a long password'
PROVIDERS = {
    'badslava': ('https://badslava.com/open-mics-state.php?state=NY&type=Comedy', '12345', '98765'),
    'comediq': ('https://comediq.us/mics.json', '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'),
}


@pytest.fixture
def clients(database, monkeypatch):
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    app = create_app(database)
    with TestClient(app, headers=HEADERS) as admin, TestClient(app, headers=HEADERS) as host:
        assert admin.post('/api/login', json={'password': password}).status_code == 200
        yield app.state.store, admin, host


def rows(provider='badslava', date='2026-09-30', name='Original mic', uid=None):
    uid = uid or PROVIDERS[provider][1]
    raw = {'external_id': f'{provider}:{uid}:{date}', 'name': name,
           'venue': 'Example Room', 'address': '1 Broadway', 'borough': 'Manhattan',
           'date': date, 'start_time': '19:00', 'cost': '5', 'status': 'scheduled'}
    normalized, warnings, skipped = normalize_rows([raw], SourceConfig(name='Fixture', permission_confirmed=True))
    assert not warnings and not skipped
    return normalized


def source(store, provider='badslava', data=None, url=None):
    config = SourceConfig(name='Known provider', url=url or PROVIDERS[provider][0], kind='auto', permission_confirmed=True)
    return store.commit_preview(store.preview(config.model_dump(), {
        'rows': data or rows(provider), 'skipped': 0, 'warnings': [],
    }))


def listing(host, date, name=None):
    return next(r for r in host.get('/api/public').json()['listings']
                if r['date'] == date and (name is None or r['name'] == name))


def approve(admin, host, mic):
    assert host.post(f"/api/mics/{mic['id']}/submissions", json={
        'kind': 'claim', 'name': 'Approved Host', 'email': 'host@example.com',
        'message': 'I run this mic and can provide verification from the venue.',
    }).status_code == 201
    claim = admin.get('/api/admin/community').json()['submissions'][0]
    response = admin.post('/api/admin/submissions/' + claim['id'], json={'action': 'approve'})
    assert response.status_code == 200, response.text
    token = response.json()['invite_path'].split('#')[1]
    response = host.post('/api/owner/redeem', json={'token': token, 'password': HOST_PASSWORD})
    assert response.status_code == 200, response.text
    return claim['id']


@pytest.mark.parametrize('provider', PROVIDERS)
def test_next_occurrence_keeps_claim_edits_and_permissions_without_merging_dates(clients, provider):
    store, admin, host = clients
    source_id = source(store, provider)
    original = listing(host, '2026-09-30')
    claim_id = approve(admin, host, original)
    response = host.patch('/api/owner/mics/' + original['id'], json={'fields': {
        'start_time': '20:30', 'host_names': 'Approved Host', 'host_socials': 'https://instagram.com/example',
        'notes': 'Host-confirmed arrival instructions.', 'cost': 'Free',
        'date': '2026-10-01', 'status': 'cancelled', 'excluded_dates': '2026-10-07',
    }})
    assert response.status_code == 200, response.text
    unrelated = rows(provider, '2026-10-07', 'Unrelated mic', PROVIDERS[provider][2])
    store.success(source_id, rows(provider, '2026-10-07', 'Provider renamed this mic') + unrelated, {})
    # /me itself must refresh membership before selecting the host's assigned IDs.
    dashboard = host.get('/api/owner/me')
    assert dashboard.status_code == 200, dashboard.text
    next_mic = next(r for r in dashboard.json()['listings'] if r['date'] == '2026-10-07')
    assert next_mic['id'] != original['id'] and next_mic['claimed']
    assert next_mic['name'] == 'Provider renamed this mic'
    assert next_mic['start_time'] == '20:30' and next_mic['cost'] == 0
    assert next_mic['host_names'] == 'Approved Host'
    assert next_mic['host_socials'] == 'https://instagram.com/example'
    assert next_mic['notes'] == 'Host-confirmed arrival instructions.'
    assert next_mic['status'] == 'scheduled' and next_mic['excluded_dates'] == []
    assert next_mic['date'] == '2026-10-07' and next_mic['host_confirmed_at']
    assert host.patch('/api/owner/mics/' + next_mic['id'], json={'fields': {'notes': 'Next session update'}}).status_code == 200
    other = listing(host, '2026-10-07', 'Unrelated mic')
    assert not other['claimed']
    assert host.patch('/api/owner/mics/' + other['id'], json={'fields': {'start_time': '22:00'}}).status_code == 403
    with store.connect() as c:
        owned = [dict(r) for r in c.execute('SELECT * FROM ownership')]
    assert len(owned) == 2 and {r['claim_id'] for r in owned} == {claim_id}


def test_revoke_one_occurrence_revokes_family_without_future_resurrection(clients):
    store, admin, host = clients
    source_id = source(store)
    original = listing(host, '2026-09-30')
    approve(admin, host, original)
    store.success(source_id, rows(date='2026-10-07'), {})
    second = listing(host, '2026-10-07')
    assert second['claimed']
    assert admin.delete('/api/admin/mics/' + second['id'] + '/owner').status_code == 200
    store.success(source_id, rows(date='2026-10-14'), {})
    assert not any(r['claimed'] for r in host.get('/api/public').json()['listings'])
    assert host.get('/api/owner/me').status_code == 401
    assert host.post('/api/owner/login', json={'email': 'host@example.com', 'password': HOST_PASSWORD}).status_code == 401


def test_administrator_overlay_is_not_replaced_by_inherited_defaults(clients):
    store, admin, host = clients
    source_id = source(store)
    original = listing(host, '2026-09-30')
    approve(admin, host, original)
    assert host.patch('/api/owner/mics/' + original['id'], json={'fields': {'cost': 'Free'}}).status_code == 200
    store.success(source_id, rows(date='2026-10-07'), {})
    second = listing(host, '2026-10-07')
    assert admin.patch('/api/admin/mics/' + second['id'], json={'fields': {'cost': '12'}}).status_code == 200
    assert listing(host, '2026-10-07')['cost'] == 12
    assert admin.post('/api/admin/mics/' + second['id'] + '/restore').status_code == 200
    assert listing(host, '2026-10-07')['cost'] == 5


@pytest.mark.parametrize('url', ['https://example.org/mics.json', PROVIDERS['comediq'][0],
                               'https://badslava.com/open-mics.php?state=NY&type=Comedy'])
def test_provider_looking_ids_in_unrelated_sources_do_not_grant_access(clients, url):
    store, admin, host = clients
    source(store)
    original = listing(host, '2026-09-30')
    approve(admin, host, original)
    source(store, data=rows(date='2026-10-07', name='Another source'), url=url)
    other = listing(host, '2026-10-07')
    assert not other['claimed']
    assert host.patch('/api/owner/mics/' + other['id'], json={'fields': {'notes': 'Not authorized'}}).status_code == 403


def test_competing_claims_on_one_provider_id_do_not_extend_either_grant(clients):
    store, admin, host = clients
    source_id = source(store, data=rows() + rows(date='2026-10-07'))
    original = listing(host, '2026-09-30')
    second = listing(host, '2026-10-07')
    # Reproduce independent historical approvals from before series reconciliation.
    with store.connect() as c:
        for owner_id, mic in [('owner-a', original), ('owner-b', second)]:
            c.execute('INSERT INTO owners VALUES (?,?,?,?,?)', (owner_id, owner_id+'@example.com', owner_id, 'unusable', time.time()))
            c.execute('INSERT INTO ownership VALUES (?,?,?,?)', (mic['id'], owner_id, time.time(), owner_id+'-claim'))
    store.success(source_id, rows(date='2026-10-14'), {})
    assert not listing(host, '2026-10-14')['claimed']
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM ownership').fetchone()[0] == 2


def test_ambiguous_provider_ids_in_one_public_group_do_not_extend_access(clients):
    store, admin, host = clients
    source_id = source(store, data=rows() + rows(uid=PROVIDERS['badslava'][2]))
    original = listing(host, '2026-09-30')
    approve(admin, host, original)
    store.success(source_id, rows(date='2026-10-07', name='First separate mic') +
                  rows(date='2026-10-07', name='Second separate mic', uid=PROVIDERS['badslava'][2]), {})
    future = [r for r in host.get('/api/public').json()['listings'] if r['date'] == '2026-10-07']
    assert len(future) == 2 and not any(r['claimed'] for r in future)
