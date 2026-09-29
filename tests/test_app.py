import asyncio
import io
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

TEST_ADMIN_PASSWORD = secrets.token_urlsafe(24)
os.environ.update(ADMIN_PASSWORD=TEST_ADMIN_PASSWORD, LOCAL_DEV='1', SCHEDULER_ENABLED='0',GEOCODING_ENABLED='0')
from app.main import create_app
from app.fetcher import validate_url, public_ip, FetchError
from app.models import SourceConfig
from app.parsers import extract, parse_time, parse_days

HEADERS={'X-Requested-With':'MicList'}

@pytest.fixture
def clients(database):
    app=create_app(database)
    with TestClient(app,headers=HEADERS) as admin, TestClient(app,headers=HEADERS) as visitor, TestClient(app,headers=HEADERS) as host:
        assert admin.post('/api/login',json={'password':TEST_ADMIN_PASSWORD}).status_code==200
        yield app,admin,visitor,host

def make_mic(admin,name='Example Monday Mic',day=0):
    r=admin.post('/api/admin/mics',json={'fields':{'name':name,'venue':'Example Room','borough':'Manhattan','weekday':day,'start_time':'19:00','address':'1 Broadway','cost':'5','latitude':'40.704','longitude':'-74.011'}})
    assert r.status_code==201,r.text
    return next(x for x in admin.get('/api/public').json()['listings'] if x['name']==name)

def claim(admin,visitor,mic,email='host@example.com'):
    response=visitor.post(f"/api/mics/{mic['id']}/submissions",json={'kind':'claim','name':'Example Host','email':email,'message':'I organize this mic. Verify using the venue contact.'})
    assert response.status_code==201,response.text
    queue=admin.get('/api/admin/community').json()['submissions']
    row=next(s for s in queue if s['mic_id']==mic['id'] and s['email']==email)
    approval=admin.post('/api/admin/submissions/'+row['id'],json={'action':'approve'});assert approval.status_code==200,approval.text
    return approval.json()['invite_path'].split('#')[1]

def test_admin_auth_and_route(clients):
    app,admin,v,h=clients
    assert v.get('/admin').status_code==200
    assert v.get('/api/admin/community').status_code==401
    assert v.get('/api/sources').status_code==401
    assert v.post('/api/login',json={'password':'wrong'}).status_code==401
    assert admin.get('/api/session').json()['weak_password'] is False
    assert TEST_ADMIN_PASSWORD not in v.get('/').text
    assert TEST_ADMIN_PASSWORD not in v.get('/assets/app.js').text
    assert v.get('/assets/../.env').status_code==404

def test_csrf_and_origin(clients):
    app,a,v,h=clients
    assert v.post('/api/login',headers={'X-Requested-With':''},json={'password':TEST_ADMIN_PASSWORD}).status_code==403
    assert v.post('/api/login',headers={'Origin':'https://attacker.invalid'},json={'password':TEST_ADMIN_PASSWORD}).status_code==403

def test_complete_claim_revoke_flow(clients):
    app,a,v,h=clients
    mic=make_mic(a);other=make_mic(a,'Other mic',1)
    assert h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'start_time':'21:00'}}).status_code==401
    token=claim(a,v,mic)
    assert h.post('/api/owner/redeem',json={'token':token,'password':'short'}).status_code==400
    assert h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'}).status_code==200
    assert h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'}).status_code==400
    assert h.patch('/api/owner/mics/'+other['id'],json={'fields':{'start_time':'21:00'}}).status_code==403
    assert h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'start_time':'21:00','host_names':'Taylor','host_socials':'https://instagram.com/example'}}).status_code==200
    row=next(r for r in v.get('/api/public').json()['listings'] if r['id']==mic['id'])
    assert row['start_time']=='21:00' and row['claimed'] and row['host_confirmed_at']
    assert row['signup_time'] is None
    assert row['host_names']=='Taylor' and row['host_socials']=='https://instagram.com/example'
    assert h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'host_socials':'javascript:alert(1)'}}).status_code==400
    assert a.delete('/api/admin/mics/'+mic['id']+'/owner').status_code==200
    assert h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'start_time':'22:00'}}).status_code==401
    assert h.post('/api/owner/login',json={'email':'host@example.com','password':'a long correct horse battery'}).status_code==401

def test_fix_does_not_publish_contacts_or_edit(clients):
    app,a,v,h=clients;mic=make_mic(a)
    r=v.post('/api/mics/'+mic['id']+'/submissions',json={'kind':'fix','name':'Private Name','email':'private@example.com','message':'This starts at 8 PM, not 7 PM. Here is the source.'})
    assert r.status_code==201
    assert 'private@example.com' not in v.get('/api/public').text
    assert v.get('/api/public').json()['listings'][0]['start_time']=='19:00'
    fix=a.get('/api/admin/community').json()['submissions'][0]
    assert a.post('/api/admin/submissions/'+fix['id'],json={'action':'resolve'}).status_code==200

def test_hidden_mic_stays_hidden_after_owner_edit(clients):
    app,a,v,h=clients;mic=make_mic(a);token=claim(a,v,mic)
    h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'})
    a.patch('/api/admin/mics/'+mic['id']+'/visibility',json={'hidden':True})
    assert v.get('/api/public').json()['listings']==[]
    assert h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'notes':'Host update'}}).status_code==200
    assert v.get('/api/public').json()['listings']==[]

def test_invalid_invitations_and_unauthorized_fields(clients):
    app,a,v,h=clients;mic=make_mic(a)
    assert h.post('/api/owner/redeem',json={'token':'x'*40,'password':'a long correct horse battery'}).status_code==400
    assert a.patch('/api/admin/mics/'+mic['id'],json={'fields':{'owner_id':'attacker'}}).status_code==400
    assert a.patch('/api/admin/mics/'+mic['id'],json={'fields':{'latitude':'45'}}).status_code==400

def test_address_change_clears_old_pin(clients):
    app,a,v,h=clients;mic=make_mic(a)
    # API clients changing addresses must clear old coordinates, just like the UI.
    assert a.patch('/api/admin/mics/'+mic['id'],json={'fields':{'address':'2 Broadway'}}).status_code==200
    r=v.get('/api/public').json()['listings'][0]
    assert r['latitude'] is None and r['longitude'] is None

def test_import_preview_snapshot_and_export(clients):
    app,a,v,h=clients
    config={'name':'Sample file','kind':'upload','permission_confirmed':True}
    data=b'name,venue,borough,weekday,start time,cost\nExample mic,Example room,Queens,Monday,7 PM,Free\n'
    r=a.post('/api/sources/upload-preview',data={'config':json.dumps(config)},files={'file':('mics.csv',data,'text/csv')});assert r.status_code==200,r.text
    assert v.get('/api/public').json()['listings']==[]
    result=a.post('/api/sources',json={'preview_id':r.json()['preview_id']});assert result.status_code==200
    assert a.post('/api/sources/'+result.json()['id']+'/sync').status_code==400
    row=v.get('/api/public').json()['listings'][0];assert row['cost']==0 and row['weekday']==0
    assert 'Example mic' in v.get('/api/export.csv').text

def test_changes_and_failure_keep_owner_override(clients):
    app,a,v,h=clients
    config=SourceConfig(name='Online source',url='https://example.org/mics.csv',kind='csv',permission_confirmed=True)
    data=b'id,name,venue,borough,weekday,start_time,cost\n1,Example mic,Example Room,Manhattan,Monday,19:00,5\n'
    parsed=extract(data,config)
    source_id=app.state.store.commit_preview(app.state.store.preview(config.model_dump(),parsed))
    mic=v.get('/api/public').json()['listings'][0];token=claim(a,v,mic)
    h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'})
    h.patch('/api/owner/mics/'+mic['id'],json={'fields':{'start_time':'21:00'}})
    async def fake(*args,**kwargs):
        return {'status':200,'headers':{'Content-Type':'text/csv'},'content':data.replace(b'19:00,5',b'20:00,10'),'url':config.url}
    app.state.fetcher.fetch=fake
    response=a.post('/api/sources/'+source_id+'/sync');assert response.json()['state']=='updated',response.text
    r=v.get('/api/public').json()['listings'][0];assert r['start_time']=='21:00' and r['cost']==10
    async def failed(*args,**kwargs):raise FetchError('Unavailable')
    app.state.fetcher.fetch=failed
    assert a.post('/api/sources/'+source_id+'/sync').json()['state']=='error'
    assert len(v.get('/api/public').json()['listings'])==1
    a.post('/api/admin/mics/'+mic['id']+'/restore')
    assert v.get('/api/public').json()['listings'][0]['start_time']=='20:00'

def test_ssrf():
    for url in ['http://127.0.0.1/x','http://169.254.169.254/latest','http://localhost','file:///etc/passwd','https://user:pass@example.com','https://example.com:8000','http://[::1]/x']:
        with pytest.raises(FetchError):validate_url(url)
    assert not public_ip('10.0.0.1')
    assert validate_url('https://example.org/mics')=='https://example.org/mics'

def test_parsers_do_not_guess():
    assert parse_time('7') is None
    assert parse_time('7 PM')=='19:00'
    assert parse_time('12 AM')=='00:00'
    assert parse_days('Monday, Wednesday')==[0,2]
    with pytest.raises(ValueError):parse_days('first Monday')
    config=SourceConfig(name='Test',permission_confirmed=True,kind='csv')
    result=extract(b'name,venue,borough,day,time\nBad,Room,Albany,Monday,7 PM\nGood,Room,Queens,Tuesday,8 PM\n',config)
    assert result['skipped']==1 and len(result['rows'])==1

def test_session_replay_and_claim_rejection(clients):
    app,a,v,h=clients;mic=make_mic(a)
    token=claim(a,v,mic)
    row=a.get('/api/admin/community').json()['submissions'][0]
    a.post('/api/admin/submissions/'+row['id'],json={'action':'reject'})
    assert h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'}).status_code==400
    a.post('/api/logout')
    assert a.get('/api/admin/community').status_code==401

def test_expired_invitation_cannot_activate(clients):
    app,a,v,h=clients;mic=make_mic(a);token=claim(a,v,mic)
    with app.state.store.connect() as c:c.execute('UPDATE invitations SET expires=?',(time.time()-10,))
    assert h.post('/api/owner/redeem',json={'token':token,'password':'a long correct horse battery'}).status_code==400

def test_owner_account_can_claim_second_mic_without_resetting_password(clients):
    app,a,v,h=clients;first=make_mic(a);second=make_mic(a,'Second example',1)
    token=claim(a,v,first);password='a long correct horse battery'
    assert h.post('/api/owner/redeem',json={'token':token,'password':password}).status_code==200
    second_token=claim(a,v,second)
    assert h.post('/api/owner/redeem',json={'token':second_token,'password':'incorrect account password'}).status_code==401
    assert h.post('/api/owner/redeem',json={'token':second_token,'password':password}).status_code==200
    assert len(h.get('/api/owner/me').json()['listings'])==2
    h.post('/api/owner/logout')
    assert h.post('/api/owner/login',json={'email':'host@example.com','password':password}).status_code==200
    assert a.delete('/api/admin/mics/'+first['id']+'/owner').status_code==200
    assert len(h.get('/api/owner/me').json()['listings'])==1
    assert h.patch('/api/owner/mics/'+second['id'],json={'fields':{'start_time':'20:00'}}).status_code==200

def test_rate_limiting(clients):
    app,a,v,h=clients
    results=[v.post('/api/login',json={'password':'wrong'}).status_code for _ in range(10)]
    assert 429 in results

def test_parser_jsonld_html_cards_and_coordinates():
    config=SourceConfig(name='Event page',kind='jsonld',permission_confirmed=True)
    html=b'''<script type="application/ld+json">{"@type":"Event","name":"Example mic","startDate":"2027-01-03T01:00:00Z","location":{"name":"Example room","address":{"streetAddress":"1 Test St","addressLocality":"Brooklyn"}}}</script>'''
    row=extract(html,config)['rows'][0]
    assert row['date']=='2027-01-02' and row['start_time']=='20:00'
    card_config=SourceConfig(name='Cards',url='https://example.org',kind='cards',permission_confirmed=True,row_selector='.event',mapping={'name':'.name','venue':'.venue','weekday':'.day','start_time':'.time'},defaults={'borough':'Queens'})
    result=extract(b'<div class="event"><b class="name">Test</b><b class="venue">Room</b><span class="day">Monday</span><span class="time">7 PM</span></div>',card_config)
    assert len(result['rows'])==1
    html_table=b'<table><tr><th>name</th><th>venue</th><th>borough</th><th>weekday</th><th>start_time</th></tr><tr><td>Table mic</td><td>Room</td><td>Bronx</td><td>Sunday</td><td>20:00</td></tr></table>'
    assert extract(html_table,SourceConfig(name='Table',permission_confirmed=True))['rows'][0]['weekday']==6

def test_xlsx_import_supports_real_workbook_bytes():
    # A minimal OOXML fixture tests the deployed importer without generating user data.
    import zipfile
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Mics" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        rows=[['name','venue','borough','weekday','start_time'],['Workbook Mic','Example Room','Queens','Monday','19:00']]
        xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for i,row in enumerate(rows,1):
            xml+=f'<row r="{i}">'+''.join(f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{value}</t></is></c>' for j,value in enumerate(row))+'</row>'
        z.writestr('xl/worksheets/sheet1.xml',xml+'</sheetData></worksheet>')
    result=extract(out.getvalue(),SourceConfig(name='Excel',kind='xlsx',permission_confirmed=True))
    assert result['rows'][0]['name']=='Workbook Mic'
