"""Replacing a snapshot keeps stable listing ownership and fails atomically."""
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SourceConfig
from app.parsers import extract
from app.store import Store


HEADERS = {'X-Requested-With': 'MicList'}


def rows():
    return [
        {'id': 'stable-one', 'name': 'First Example Mic', 'venue': 'First Example Room',
         'borough': 'Manhattan', 'weekday': 'Monday', 'start_time': '19:00', 'cost': '5'},
        {'id': 'stable-two', 'name': 'Second Example Mic', 'venue': 'Second Example Room',
         'borough': 'Queens', 'weekday': 'Tuesday', 'start_time': '18:00', 'cost': 'Free'},
    ]


def config(kind='upload', url=''):
    return SourceConfig(name='Example source', kind=kind, url=url, permission_confirmed=True)


def preview(store, source_config, data=None, skipped=0):
    content = json.dumps(data if data is not None else rows()).encode()
    parsed = extract(content, source_config, 'application/json')
    parsed['skipped'] += skipped
    return store.preview(source_config.model_dump(), parsed, content=content,
                         validators={'etag': 'example-validator', 'hash': 'example-content-hash'})


def persisted_state(store):
    with store.connect() as c:
        return {
            table: [dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1')]
            for table in ('sources', 'observations', 'runs', 'mic_registry', 'ownership', 'overlays')
        }


def test_upload_replacement_preserves_claim_id_and_owner_edits(database, monkeypatch):
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    app = create_app(database)
    store = app.state.store
    with TestClient(app, headers=HEADERS) as admin, TestClient(app, headers=HEADERS) as host:
        assert admin.post('/api/login', json={'password': password}).status_code == 200
        source_id = store.commit_preview(preview(store, config()))
        created = store.get_source(source_id)['created']
        before = host.get('/api/public').json()['listings']
        mic = next(r for r in before if r['external_id'] == 'stable-one')
        before_ids = {r['external_id']: r['id'] for r in before}
        assert host.post(f"/api/mics/{mic['id']}/submissions", json={
            'kind': 'claim', 'name': 'Example Host', 'email': 'host@example.com',
            'message': 'I organize this mic and can verify with the venue.'
        }).status_code == 201
        claim_id = admin.get('/api/admin/community').json()['submissions'][0]['id']
        invitation = admin.post('/api/admin/submissions/' + claim_id, json={'action': 'approve'})
        invite_token = invitation.json()['invite_path'].split('#')[1]
        assert host.post('/api/owner/redeem', json={
            'token': invite_token, 'password': secrets.token_urlsafe(24)
        }).status_code == 200
        assert host.patch('/api/owner/mics/' + mic['id'], json={
            'fields': {'start_time': '21:00', 'notes': 'The host confirmed this later start.'}
        }).status_code == 200
        changed = rows()
        changed[0].update(name='Renamed First Example Mic', start_time='20:00', cost='10')
        live = config('json', 'https://example.org/live-mics.json')

        async def fetch_live(*args, **kwargs):
            return {'status': 200, 'headers': {'Content-Type': 'application/json'},
                    'content': json.dumps(changed).encode(), 'url': live.url}

        app.state.fetcher.fetch = fetch_live
        prepared = admin.post('/api/sources/preview', json=live.model_dump())
        assert prepared.status_code == 200, prepared.text
        result = admin.post('/api/sources', json={
            'preview_id': prepared.json()['preview_id'], 'replacement_source_id': source_id
        })
        assert result.status_code == 200, result.text
        assert result.json()['id'] == source_id
        assert len(store.list_sources()) == 1
        current = store.get_source(source_id)
        assert current['config']['url'] == live.url
        assert current['config']['kind'] == 'json'
        assert current['enabled'] == 1 and current['state'] == 'ready'
        assert current['upload'] is None and current['created'] == created
        after = host.get('/api/public').json()['listings']
        assert {r['external_id']: r['id'] for r in after} == before_ids
        updated = next(r for r in after if r['id'] == mic['id'])
        assert updated['name'] == changed[0]['name']
        assert updated['cost'] == 10
        assert updated['start_time'] == '21:00'
        assert updated['notes'] == 'The host confirmed this later start.'
        assert updated['claimed'] and updated['host_confirmed_at']
        assert host.patch('/api/owner/mics/' + mic['id'], json={'fields': {'notes': 'Still editable'}}).status_code == 200


@pytest.mark.parametrize('reason', ['absent', 'busy', 'skipped'])
def test_replacement_rejections_leave_all_data_and_preview_intact(database, reason):
    store = Store(database)
    source_id = store.commit_preview(preview(store, config()))
    store.public_data()  # Register IDs before verifying transactional preservation.
    if reason == 'busy':
        assert store.acquire(source_id)
    live = config('json', 'https://example.org/live-mics.json')
    preview_id = preview(store, live, skipped=int(reason == 'skipped'))
    before = persisted_state(store)
    with pytest.raises(ValueError):
        store.commit_preview(preview_id, 'missing-source' if reason == 'absent' else source_id)
    assert persisted_state(store) == before
    with store.connect() as c:
        assert c.execute('SELECT id FROM previews WHERE id=?', (preview_id,)).fetchone()


def test_replacement_rejects_url_connected_to_another_source(database):
    store = Store(database)
    original_id = store.commit_preview(preview(store, config()))
    live = config('json', 'https://example.org/live-mics.json')
    connected_id = store.commit_preview(preview(store, live))
    assert connected_id != original_id
    preview_id = preview(store, live)
    before = persisted_state(store)
    with pytest.raises(ValueError, match='already connected'):
        store.commit_preview(preview_id, original_id)
    assert persisted_state(store) == before


def test_replacement_marks_missing_rows_without_deleting_them(database):
    store = Store(database)
    source_id = store.commit_preview(preview(store, config()))
    before = {r['external_id']: r for r in store.public_data()['listings']}
    live = config('json', 'https://example.org/live-mics.json')
    store.commit_preview(preview(store, live, data=rows()[:1]), source_id)
    with store.connect() as c:
        observations = c.execute('SELECT payload,missing_count FROM observations WHERE source_id=?', (source_id,)).fetchall()
    assert len(observations) == 2
    assert {json.loads(o['payload'])['external_id']: o['missing_count'] for o in observations} == {
        'stable-one': 0, 'stable-two': 1
    }
    after = {r['external_id']: r for r in store.public_data()['listings']}
    assert after['stable-two']['id'] == before['stable-two']['id']
    assert after['stable-two']['stale']
    assert after['stable-two']['sources'][0]['missing_count'] == 1
    assert not after['stable-one']['stale']
    # Reappearance clears the missing state without assigning a new public ID.
    store.commit_preview(preview(store, live), source_id)
    restored = {r['external_id']: r for r in store.public_data()['listings']}
    assert restored['stable-two']['id'] == before['stable-two']['id']
    assert not restored['stable-two']['stale']
