"""Visitor estimates are deduplicated, private, persistent and time-bounded."""
import secrets
import time
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.analytics import COOKIE

H={'X-Requested-With':'MicList','User-Agent':'Mozilla/5.0'}

@pytest.fixture
def setup(database,monkeypatch):
    password=secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD',password)
    monkeypatch.setenv('LOCAL_DEV','1')
    monkeypatch.setenv('SCHEDULER_ENABLED','0')
    monkeypatch.setenv('GEOCODING_ENABLED','0')
    monkeypatch.delenv('PUBLIC_ORIGIN',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    app=create_app(database)
    with TestClient(app,headers=H) as admin,TestClient(app,headers=H) as visitor:
        assert admin.post('/api/login',json={'password':password}).status_code==200
        yield app,admin,visitor


def test_count_repeat_browser_once_and_never_expose_analytics(setup):
    app,admin,v=setup
    assert v.get('/api/admin/visitors').status_code==401
    assert admin.get('/api/admin/visitors').json()['total']==0
    first=v.post('/api/visit')
    assert first.status_code==204 and 'HttpOnly' in first.headers['set-cookie']
    token=v.cookies.get(COOKIE)
    assert len(token)==43
    for _ in range(3):assert v.post('/api/visit').status_code==204
    stats=admin.get('/api/admin/visitors').json()
    assert all(stats[k]==1 for k in ['today','week','month','total'])
    assert token not in str(stats)
    with app.state.store.connect() as c:
        rows=c.execute('SELECT * FROM visitors').fetchall()
        assert len(rows)==1 and token not in str(dict(rows[0]))
    v.cookies.clear()
    assert v.post('/api/visit').status_code==204
    assert admin.get('/api/admin/visitors').json()['total']==2


def test_ignore_admin_hosts_bots_and_reject_cross_origin(setup):
    _,admin,v=setup
    assert admin.post('/api/visit').status_code==204
    assert v.post('/api/visit',headers={'User-Agent':'Googlebot'}).status_code==204
    v.cookies.set('miclist_owner','signed-in-browser')
    assert v.post('/api/visit').status_code==204
    v.cookies.clear()
    assert v.post('/api/visit',headers={'DNT':'1'}).status_code==204
    assert v.post('/api/visit',headers={'Sec-GPC':'1'}).status_code==204
    assert v.post('/api/visit',headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert v.post('/api/visit',headers={'X-Requested-With':''}).status_code==403
    assert admin.get('/api/admin/visitors').json()['total']==0


def test_time_windows_and_persistence(setup):
    app,admin,v=setup
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo('America/New_York')).replace(hour=0,minute=0,second=0,microsecond=0)
    with app.state.store.connect() as c:
        for i,days in enumerate([0,2,10,40]):
            stamp=(today-timedelta(days=days)).timestamp()+1
            c.execute('INSERT INTO visitors VALUES (?,?,?)',(str(i),stamp,stamp))
    stats=admin.get('/api/admin/visitors').json()
    assert [stats[k] for k in ['today','week','month','total']]==[1,2,3,4]
    from app.store import Store
    with Store(app.state.store.path).connect() as c:
        assert c.execute('SELECT COUNT(*) FROM visitors').fetchone()[0]==4
