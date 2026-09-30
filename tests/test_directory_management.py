"""Public submissions, moderated reports, editable copy and truthful sync status."""
import json
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SourceConfig
from app.parsers import extract

HEADERS = {'X-Requested-With': 'MicList'}

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
    with TestClient(app, headers=HEADERS) as admin, TestClient(app, headers=HEADERS) as visitor:
        assert admin.post('/api/login', json={'password': password}).status_code == 200
        yield app, admin, visitor


def proposal(visitor, name='New test mic'):
    data = {'name': 'Private Host', 'email': 'private@example.com',
            'message': 'I run this mic; verify with the venue manager.',
            'fields': {'name': name, 'venue': 'Example Venue', 'address': '1 Broadway',
                       'borough': 'Manhattan', 'weekday': 'Wednesday', 'start_time': '19:00', 'cost': 'Free'}}
    result = visitor.post('/api/mic-proposals', json=data)
    assert result.status_code == 201, result.text
    return result.json()['status_path'].split('#')[1], data


def test_submission_approval_setup_and_single_use(clients):
    app, admin, visitor = clients
    token, data = proposal(visitor)
    assert visitor.get('/api/public').json()['listings'] == []
    assert visitor.post('/api/owner/redeem', json={'token': token, 'password': secrets.token_urlsafe(24)}).status_code == 400
    status = visitor.post('/api/mic-proposals/status', json={'token': token}).json()
    assert status['state'] == 'pending' and not status['can_setup']
    assert 'email' not in status and 'token' not in status
    queue = admin.get('/api/admin/community').json()['proposals']
    assert len(queue) == 1 and 'token_hash' not in queue[0]
    pid = queue[0]['id']
    assert visitor.post('/api/admin/mic-proposals/' + pid, json={'action': 'approve'}).status_code == 401
    result = admin.post('/api/admin/mic-proposals/' + pid, json={'action': 'approve', 'reply': 'Verified with the venue.'})
    assert result.status_code == 200, result.text
    assert admin.post('/api/admin/mic-proposals/' + pid, json={'action': 'approve'}).status_code == 409
    status = visitor.post('/api/mic-proposals/status', json={'token': token}).json()
    assert status['state'] == 'approved' and status['can_setup']
    assert status['reply'] == 'Verified with the venue.'
    public = visitor.get('/api/public')
    assert 'private@example.com' not in public.text and 'Private Host' not in public.text
    assert len(public.json()['listings']) == 1
    assert visitor.post('/api/owner/redeem', json={'token': token, 'password': secrets.token_urlsafe(24)}).status_code == 200
    owned = visitor.get('/api/owner/me').json()['listings']
    assert len(owned) == 1 and owned[0]['id'] == result.json()['mic_id']
    assert visitor.patch('/api/owner/mics/' + owned[0]['id'], json={'fields': {'host_names': 'Public host name'}}).status_code == 200
    second_token = result.json()['invite_path'].split('#')[1]
    assert visitor.post('/api/owner/redeem', json={'token': second_token, 'password': secrets.token_urlsafe(24)}).status_code == 400
    assert visitor.post('/api/mic-proposals/status', json={'token': token}).json()['state'] == 'activated'
    admin.delete('/api/admin/mics/' + owned[0]['id'] + '/owner')
    assert visitor.post('/api/mic-proposals/status', json={'token': token}).json()['state'] == 'revoked'


def test_rejection_private_status_and_duplicate_protection(clients):
    app, admin, visitor = clients
    token, data = proposal(visitor)
    assert visitor.post('/api/mic-proposals', json=data).status_code == 409
    assert visitor.post('/api/mic-proposals/status', json={'token': 'x' * 40}).status_code == 404
    pid = admin.get('/api/admin/community').json()['proposals'][0]['id']
    assert admin.post('/api/admin/mic-proposals/' + pid, json={'action': 'reject', 'reply': 'Please confirm the address.'}).status_code == 200
    status = visitor.post('/api/mic-proposals/status', json={'token': token}).json()
    assert status['state'] == 'rejected' and not status['can_setup']
    assert visitor.get('/api/public').json()['listings'] == []
    assert visitor.post('/api/owner/invitation', json={'token': token}).status_code == 400


def test_existing_mic_is_not_republished_by_submission(clients):
    app, admin, visitor = clients
    token, data = proposal(visitor)
    assert admin.post('/api/admin/mics', json={'fields': data['fields']}).status_code == 201
    pid = admin.get('/api/admin/community').json()['proposals'][0]['id']
    result = admin.post('/api/admin/mic-proposals/' + pid, json={'action': 'approve'})
    assert result.status_code == 409
    assert len(visitor.get('/api/public').json()['listings']) == 1
    assert visitor.post('/api/mic-proposals/status', json={'token': token}).json()['state'] == 'pending'
    with app.state.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM owners').fetchone()[0] == 0


def test_inactive_reports_require_admin_review_and_can_be_reversed(clients):
    app, admin, visitor = clients
    token, data = proposal(visitor)
    admin.post('/api/admin/mics', json={'fields': data['fields']})
    mic = visitor.get('/api/public').json()['listings'][0]
    body = {'kind': 'report', 'name': 'Anonymous', 'message': 'The venue confirmed this mic stopped last month.'}
    assert visitor.post('/api/mics/' + mic['id'] + '/submissions', json=body).status_code == 201
    assert len(visitor.get('/api/public').json()['listings']) == 1
    report = next(r for r in admin.get('/api/admin/community').json()['submissions'] if r['kind'] == 'report')
    assert visitor.post('/api/admin/submissions/' + report['id'], json={'action': 'hide'}).status_code == 401
    assert admin.post('/api/admin/submissions/' + report['id'], json={'action': 'hide'}).status_code == 200
    assert visitor.get('/api/public').json()['listings'] == []
    assert admin.get('/api/admin/community').json()['listings'][0]['hidden']
    assert admin.patch('/api/admin/mics/' + mic['id'] + '/visibility', json={'hidden': False}).status_code == 200
    assert len(visitor.get('/api/public').json()['listings']) == 1


def test_about_edits_persist_escape_markup_and_require_admin(clients):
    app, admin, visitor = clients
    assert visitor.get('/api/admin/about').status_code == 401
    body = admin.get('/api/admin/about').json()
    body.update(title='About Taylor’s mic list', why='<script>alert("x")</script>', seo_description='NYC mic finder </script><img src=x>')
    assert visitor.put('/api/admin/about', json=body).status_code == 401
    assert admin.put('/api/admin/about', json=body).status_code == 200
    page = visitor.get('/about')
    assert page.status_code == 200 and 'About Taylor' in page.text
    assert '<script>alert("x")</script>' not in page.text
    assert '&lt;script&gt;' in page.text
    assert '</script><img src=x>' not in page.text
    assert admin.get('/api/admin/about').json() == body
    from app.about import read_content
    from app.store import Store
    assert read_content(Store(app.state.store.path)).model_dump() == body


def test_last_sync_ignores_manual_approvals_and_failed_checks(clients):
    app, admin, visitor = clients
    assert visitor.get('/api/sync-status').json()['last_sync_at'] is None
    token, data = proposal(visitor)
    admin.post('/api/admin/mics', json={'fields': data['fields']})
    assert visitor.get('/api/sync-status').json()['last_sync_at'] is None
    config = SourceConfig(name='Live source', kind='json', url='https://example.com/events.json', permission_confirmed=True)
    content = json.dumps([data['fields']]).encode()
    store = app.state.store
    sid = store.commit_preview(store.preview(config.model_dump(), extract(content, config, 'application/json')))
    stamp = visitor.get('/api/sync-status').json()['last_sync_at']
    assert stamp and visitor.get('/api/public').json()['last_sync_at'] == stamp
    store.failure(sid, 'Temporary source failure')
    assert visitor.get('/api/sync-status').json()['last_sync_at'] == stamp


def test_missing_source_rows_stay_published_until_admin_hides(clients):
    app, admin, visitor = clients
    config = SourceConfig(name='Example calendar',kind='json',url='https://example.org/events',permission_confirmed=True)
    data = [{'id':str(i),'name':'Mic '+str(i),'venue':'Example Room','borough':'Brooklyn','weekday':'Monday','start_time':'19:00'} for i in (1,2)]
    parsed = extract(json.dumps(data).encode(),config)
    store=app.state.store
    sid=store.commit_preview(store.preview(config.model_dump(),parsed))
    original={r['id'] for r in visitor.get('/api/public').json()['listings']}
    for _ in range(5):
        store.success(sid,parsed['rows'][:1],{})
    after=visitor.get('/api/public').json()['listings']
    assert {r['id'] for r in after}==original
    assert store.review_rows()[0]['missing_count']==5
    missing=next(r for r in after if r['stale'])
    assert admin.patch('/api/admin/mics/'+missing['id']+'/visibility',json={'hidden':True}).status_code==200
    store.success(sid,parsed['rows'],{})
    assert missing['id'] not in {r['id'] for r in visitor.get('/api/public').json()['listings']}


def test_custom_domain_origin_migration_stays_strict(clients, monkeypatch):
    app, admin, visitor = clients
    monkeypatch.setenv('PUBLIC_ORIGIN','https://nycstandupopenmicmaster.vercel.app')
    body={'token':'x'*40}
    for origin in ['https://nycopenmicmasterlist.com','https://nycstandupopenmicmaster.vercel.app']:
        assert visitor.post('/api/mic-proposals/status',json=body,headers={'Origin':origin}).status_code==404
    for origin in ['https://attacker.invalid','http://nycopenmicmasterlist.com','https://nycopenmicmasterlist.com.attacker.invalid']:
        assert visitor.post('/api/mic-proposals/status',json=body,headers={'Origin':origin}).status_code==403


def test_custom_domain_search_metadata(clients):
    from bs4 import BeautifulSoup
    _, _, visitor=clients
    for path in ['/','/about']:
        doc=BeautifulSoup(visitor.get(path).text,'html.parser')
        assert doc.find('link',rel='canonical')['href']=='https://nycopenmicmasterlist.com'+path
        assert doc.find('meta',property='og:url')['content']=='https://nycopenmicmasterlist.com'+path
    assert 'https://nycopenmicmasterlist.com/sitemap.xml' in visitor.get('/robots.txt').text
    assert 'vercel.app' not in visitor.get('/sitemap.xml').text


def test_claim_private_status_unlocks_without_manual_link(clients):
    app, admin, visitor = clients
    fields = dict(name='Claimable mic',venue='Room',address='1 Broadway',borough='Manhattan',
                  weekday='Wednesday',start_time='19:00')
    added=admin.post('/api/admin/mics',json={'fields':fields})
    assert added.status_code==201, added.text
    mic=visitor.get('/api/public').json()['listings'][0]
    claim=visitor.post('/api/mics/'+mic['id']+'/submissions',json={
        'kind':'claim','name':'Host Person','email':'claimant@example.com',
        'message':'I run this open mic. The venue manager can verify me.'})
    assert claim.status_code==201
    token=claim.json()['status_path'].split('#')[1]
    status=lambda:visitor.post('/api/claims/status',json={'token':token}).json()
    assert status()['state']=='pending' and not status()['can_setup']
    assert visitor.post('/api/owner/redeem',json={'token':token,'password':secrets.token_urlsafe(24)}).status_code==400
    queue=admin.get('/api/admin/community').json()['submissions']
    assert token not in json.dumps(queue)
    approved=admin.post('/api/admin/submissions/'+queue[0]['id'],json={'action':'approve'})
    assert approved.status_code==200 and approved.json()['status_link_ready']
    assert status()['can_setup']
    assert 'email' not in status()
    assert visitor.post('/api/owner/redeem',json={'token':token,'password':secrets.token_urlsafe(24)}).status_code==200
    assert status()['state']=='activated' and not status()['can_setup']
    admin.delete('/api/admin/mics/'+mic['id']+'/owner')
    assert status()['state']=='revoked'
    assert visitor.post('/api/claims/status',json={'token':'x'*40}).status_code==404


@pytest.mark.parametrize('frequency,date,anchor',[
    ('weekly','',''),('biweekly','','2026-09-30'),('one-time','2026-10-07','')
])
def test_submission_schedule_choices(clients,frequency,date,anchor):
    app,admin,visitor=clients
    r=visitor.post('/api/mic-proposals',json={
        'name':'Host Person','email':'host@example.com','message':'Venue manager can confirm I host this mic.',
        'fields':dict(name='Scheduled mic',venue='Room',address='1 Broadway',borough='Manhattan',
                      weekday='Wednesday',start_time='19:00',frequency=frequency,date=date,recurrence_anchor=anchor)})
    assert r.status_code==201,r.text
    item=admin.get('/api/admin/community').json()['proposals'][0]
    assert item['listing']['frequency']==frequency
    assert admin.post('/api/admin/mic-proposals/'+item['id'],json={'action':'approve'}).status_code==200
    row=visitor.get('/api/public').json()['listings'][0]
    assert row['frequency']==frequency
    assert row['date']==(date or None)
