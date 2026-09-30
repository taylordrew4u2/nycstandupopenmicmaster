"""Run the approved-host UI against an isolated real backend, with no external writes."""
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {'X-Requested-With': 'MicList'}

def run():
    password = secrets.token_urlsafe(24)
    host_password, replacement = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    os.environ.update(ADMIN_PASSWORD=password, LOCAL_DEV='1', SCHEDULER_ENABLED='0', GEOCODING_ENABLED='0')
    os.environ.pop('PUBLIC_ORIGIN', None)
    os.environ.pop('VERCEL', None)
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(str(Path(tmp) / 'host.sqlite3'))
        with TestClient(app, headers=HEADERS, base_url='https://host.test') as admin, TestClient(app, base_url='https://host.test') as host, sync_playwright() as pw:
            assert admin.post('/api/login', json={'password': password}).status_code == 200
            for name in ('Approved mic', 'Someone else’s mic'):
                assert admin.post('/api/admin/mics', json={'fields': {'name': name, 'venue': 'Example Room', 'address': '1 Broadway', 'borough': 'Manhattan', 'weekday': 2, 'start_time': '19:00'}}).status_code == 201
            mic = next(r for r in admin.get('/api/public').json()['listings'] if r['name'] == 'Approved mic')
            assert admin.post('/api/mics/' + mic['id'] + '/submissions', json={'kind': 'claim', 'name': 'Approved Host', 'email': 'host@example.com', 'message': 'Verified with the venue for this isolated test.'}).status_code == 201
            claim = admin.get('/api/admin/community').json()['submissions'][0]
            invite = admin.post('/api/admin/submissions/' + claim['id'], json={'action': 'approve'}).json()['invite_path']
            browser = pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH', '/tmp/mic-chromium'), headless=True, args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.set_default_timeout(6000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def transport(route):
                req = route.request
                parsed = urlsplit(req.url)
                if parsed.netloc != 'host.test':
                    route.abort()
                    return
                response = host.request(req.method, parsed.path + ('?' + parsed.query if parsed.query else ''), content=req.post_data_buffer, headers={k:v for k,v in req.headers.items() if k not in ('host', 'content-length', 'cookie')})
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
            page.route('**/*', transport)
            page.goto('https://host.test' + invite)
            page.get_by_label('Choose a password (15+ characters)', exact=True).fill(host_password)
            page.get_by_label('Confirm password', exact=True).fill(host_password)
            page.get_by_role('button', name='Create account & edit my mic', exact=False).click()
            page.wait_for_selector('#host-edit-form')
            assert page.locator('.host-mic-summary').inner_text().startswith('Approved mic')
            assert 'Someone else' not in page.locator('#view-sources').inner_text()
            page.get_by_label('Mic name', exact=True).fill('Updated approved mic')
            page.get_by_role('button', name='Hosts', exact=True).click()
            page.get_by_label('Host name(s), public', exact=True).fill('Updated Host')
            page.get_by_label('Social links', exact=False).fill('https://www.instagram.com/example/')
            page.get_by_role('button', name='Mic', exact=True).click()
            assert page.get_by_label('Mic name', exact=True).input_value() == 'Updated approved mic'
            page.get_by_role('button', name='Save changes', exact=True).click()
            expect(page.locator('#host-save-state')).to_have_text('Your edits are protected from imports.')
            public = admin.get('/api/public').json()['listings']
            changed = next(r for r in public if r['id'] == mic['id'])
            assert changed['name'] == 'Updated approved mic' and changed['host_names'] == 'Updated Host' and changed['claimed']
            out = ROOT / 'test-results'
            out.mkdir(exist_ok=True)
            for width, height in ((390,844), (320,568), (1440,900), (844,390)):
                page.set_viewport_size({'width':width,'height':height})
                for tab in ('Mic', 'When', 'Where', 'Hosts'):
                    page.get_by_role('button', name=tab, exact=True).click()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
                    assert page.locator('#view-sources').evaluate('(e)=>e.scrollHeight <= e.clientHeight + 1')
                    metrics = page.locator('[data-host-panel]:visible').evaluate('(e)=>({h:e.clientHeight,s:e.scrollHeight})')
                    assert metrics['s'] <= metrics['h'] + 1, (width,height,tab,metrics)
                    button_box = page.get_by_role('button',name='Save changes').bounding_box()
                    assert button_box and button_box['y'] + button_box['height'] <= height
                if width in (390,1440):
                    page.screenshot(path=str(out / ('host-mobile.png' if width==390 else 'host-desktop.png')))
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('button', name='Password', exact=True).click()
            page.get_by_label('Current password', exact=True).fill(host_password)
            page.get_by_label('New password (15+ characters)', exact=True).fill(replacement)
            page.get_by_label('Confirm new password', exact=True).fill(replacement)
            page.get_by_role('button',name='Change password',exact=True).click()
            page.wait_for_selector('#owner-login')
            page.get_by_label('Email',exact=True).fill('host@example.com')
            page.get_by_label('Password',exact=True).fill(replacement)
            page.get_by_role('button',name='Sign in',exact=True).click()
            page.wait_for_selector('#host-edit-form')
            assert admin.delete('/api/admin/mics/' + mic['id'] + '/owner').status_code == 200
            page.reload()
            page.wait_for_selector('#owner-login')
            assert not errors, errors
            browser.close()
    print('Host browser checks passed: invitation setup, scoped dashboard, draft retention, published edits, four viewport layouts, password change/sign-in, and revoked access.')

if __name__ == '__main__':
    run()
