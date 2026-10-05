"""Public page counts are aggregate-only, persistent and distinct from browsers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from starlette.requests import Request
from app.analytics import register
from app.store import Store


def endpoints(path):
    app = FastAPI()
    store = Store(str(path))
    register(app, store, lambda: None, True)
    routes = {r.path: r.endpoint for r in app.routes}
    return store, routes


def request(headers=None):
    values = {'user-agent': 'Mozilla/5.0', 'cookie': 'miclist_visitor=' + 'a'*43, **(headers or {})}
    return Request({'type':'http', 'headers':[(k.encode(),v.encode()) for k,v in values.items()]})


def test_public_total_counts_repeat_visits_and_persists(tmp_path):
    path = tmp_path/'visits.db'
    store, routes = endpoints(path)
    assert asyncio.run(routes['/api/visits']()) == {'total':1000, 'tracked_total':0, 'starting_offset':1000, 'started_at':None}
    for _ in range(3):
        assert asyncio.run(routes['/api/visit'](request())).status_code == 204
    result = asyncio.run(routes['/api/visits']())
    assert set(result) == {'total', 'started_at', 'tracked_total', 'starting_offset'}
    assert result['total'] == 1003 and result['tracked_total'] == 3 and result['started_at'] > 0
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM visitors').fetchone()[0] == 1
    _, restarted = endpoints(path)
    assert asyncio.run(restarted['/api/visits']()) == result
    for headers in [{'dnt':'1'}, {'sec-gpc':'1'}, {'user-agent':'Googlebot'}, {'cookie':'miclist_owner=test'}, {'cookie':'miclist_session=test'}]:
        asyncio.run(routes['/api/visit'](request(headers)))
    assert asyncio.run(routes['/api/visits']()) == result


def test_concurrent_visits_do_not_lose_increments(tmp_path):
    _, routes = endpoints(tmp_path/'visits.db')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: asyncio.run(routes['/api/visit'](request())), range(12)))
    assert asyncio.run(routes['/api/visits']())['total'] == 1012
