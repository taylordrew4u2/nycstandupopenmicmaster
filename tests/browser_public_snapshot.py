"""Exercise public revision refresh and dated fallback with the live browser assets."""
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
snapshot = {
    'revision': 'first', 'snapshot_at': 1793292000,
    'last_sync_at': 1793291900, 'stale': False, 'source_count': 1, 'mode': 'live',
    'listings': [{
        'id': f'mic-{index}', 'name': f'Public mic {index}', 'venue': 'Test Room',
        'address': '1 Broadway', 'borough': 'Manhattan', 'neighborhood': '',
        'weekday': index % 7, 'date': None, 'frequency': 'weekly',
        'start_time': '19:00', 'signup_time': '', 'cost': 0, 'cost_text': 'Free',
        'set_minutes': 5, 'signup_method': 'In person', 'signup_url': '',
        'notes': '', 'status': 'scheduled', 'excluded_dates': [],
        'latitude': 40.71, 'longitude': -74.0, 'map_status': 'exact',
        'sources': [], 'checked_at': None, 'updated_at': 1793291900,
        'conflicts': [], 'stale': False, 'claimed': True, 'curated': False,
    } for index in range(21)],
}
calls = {'public': 0, 'revision': 0}
offline = False


class AssetsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == '/':
            self.path = '/assets/index.html'
        super().do_GET()

    def log_message(self, *_args):
        pass


def public(route):
    calls['public'] += 1
    route.fulfill(json=snapshot)


def revision(route):
    calls['revision'] += 1
    if offline:
        route.abort()
    else:
        route.fulfill(json={key: snapshot[key] for key in (
            'revision', 'snapshot_at', 'last_sync_at', 'stale',
        )})


with ThreadingHTTPServer(('127.0.0.1', 0), AssetsHandler) as server:
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv('CHROMIUM_PATH'), headless=True,
                args=['--no-sandbox'],
            )
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
            page.route('**/api/visits**', lambda route: route.fulfill(json={'count': 1}))
            page.route('**/api/public', public)
            page.route('**/api/public/revision', revision)
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.locator('#day-filter').select_option('all')
            page.locator('[data-action="directory-view"][data-view="list"]').click()
            assert page.locator('#cards .mic-card').count() == 21
            page.locator('#search').fill('Public')
            page.locator('#cards').evaluate('el=>el.scrollTop=el.scrollHeight')
            page.evaluate('window.firstCard=document.querySelector("#cards .mic-card");window.savedScroll=document.querySelector("#cards").scrollTop')

            page.evaluate('refreshPublicRevision()')
            assert calls == {'public': 1, 'revision': 1}, calls
            assert page.evaluate('firstCard===document.querySelector("#cards .mic-card")')
            assert page.evaluate('savedScroll===document.querySelector("#cards").scrollTop')
            assert page.locator('#search').input_value() == 'Public'

            snapshot['revision'] = 'changed'
            snapshot['listings'].pop()
            snapshot['listings'][0]['claimed'] = False
            page.evaluate('refreshPublicRevision()')
            assert calls == {'public': 2, 'revision': 2}, calls
            assert page.locator('#cards .mic-card').count() == 20
            assert page.locator('[data-mic="mic-0"] .claim-status').inner_text() == 'Unclaimed'
            assert page.locator('#search').input_value() == 'Public'

            snapshot['stale'] = True
            page.evaluate('refreshPublicRevision()')
            assert calls['public'] == 2
            assert page.locator('#preview-banner').is_visible()
            assert 'saved directory from Oct 29, 2026' in page.locator('#preview-banner').inner_text()
            assert 'Updates are temporarily unavailable' in page.locator('#preview-banner').inner_text()
            for width, height in ((320, 400), (390, 844), (844, 390), (1440, 900)):
                page.set_viewport_size({'width': width, 'height': height})
                for view in ('list', 'map', 'both'):
                    page.locator(f'[data-action="directory-view"][data-view="{view}"]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight'), (width, height, view)
                    assert page.locator('.directory-controls > *,[data-action="directory-view"]').evaluate_all('''els=>els.every(el=>{
                        const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;
                    })'''), (width, height, view)
                    if view != 'map':
                        assert page.locator('#cards .mic-card').count() > 0
                    if view != 'list':
                        assert page.locator('#nyc-map').is_visible()

            offline = True
            page.evaluate('refreshPublicRevision()')
            assert page.evaluate('state.data.listings.length') == 20
            assert page.locator('#preview-banner').is_visible()
            snapshot['stale'] = False
            offline = False
            page.evaluate('refreshPublicRevision()')
            assert page.locator('#preview-banner').is_hidden()
            assert calls['public'] == 2
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        thread.join()

print(json.dumps({'requests': calls, 'unchanged_revision': 'no full fetch or render',
                  'changed_revision': 'hidden row removed and revoked claim refreshed',
                  'fallback': 'dated notice, usable phone/desktop/map/list layouts, recovery',
                  'page_errors': errors}, indent=2))
