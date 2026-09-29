"""Browser checks using in-process API transport (no network or deployment needed).

The restricted build browser cannot navigate localhost. This test renders our own
HTML directly and transports API calls to FastAPI TestClient. Map tiles are not
verified; they require internet access from the deployed site's visitor browser.
"""
import json
import os
import secrets
from pathlib import Path
TEST_ADMIN_PASSWORD = secrets.token_urlsafe(24)
os.environ.update(ADMIN_PASSWORD=TEST_ADMIN_PASSWORD,LOCAL_DEV='1',SCHEDULER_ENABLED='0',GEOCODING_ENABLED='0')
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from app.main import create_app

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'test-results'
OUT.mkdir(exist_ok=True)

def inline_html(preview=False):
    html=(ROOT/'assets/index.html').read_text().replace('<link rel="stylesheet" href="/assets/styles.css">','<style>'+(ROOT/'assets/styles.css').read_text()+'</style>')
    html=html.replace('<script src="/assets/map.js" defer></script>','').replace('<script src="/assets/app.js" defer></script>','')
    code="window.MICLIST_PREVIEW=true;" if preview else '''window.fetch=async (url,options={})=>{const r=await window.serverRequest(url,{method:options.method||'GET',body:options.body||null,headers:options.headers||{}});return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>r.body};};'''
    return html.replace('</body>','<script>'+code+'</script><script>'+(ROOT/'assets/map.js').read_text()+'</script><script>'+(ROOT/'assets/app.js').read_text()+'</script></body>')


def run():
    db=OUT/'browser-ui.sqlite3'
    if db.exists():db.unlink()
    app=create_app(str(db))
    with TestClient(app) as client,sync_playwright() as p:
        def transport(url,options):
            response=client.request(options['method'],url,headers=options['headers'],content=options['body'])
            return {'status':response.status_code,'body':response.json()}
        b=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        page=b.new_page(viewport={'width':1440,'height':1050});page.set_default_timeout(6000)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.expose_function('serverRequest',transport)
        page.route('https://tile.openstreetmap.org/**',lambda r:r.abort())
        page.set_content(inline_html(),wait_until='domcontentloaded')
        page.wait_for_selector('[data-action="demo"]')
        page.get_by_role('button',name='View demo').click()
        for d in ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']:
            page.locator('#day-filter').select_option(str(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].index(d)))
            assert page.locator('.mic-card').count()==3
            assert page.locator('.mic-pin').count()>=1
            assert page.locator('.mic-pin').evaluate_all('(els)=>els.reduce((s,e)=>s+Number(e.dataset.count),0)')==3
            assert all(d in x for x in page.locator('.mic-title').all_text_contents())
        page.locator('.mic-pin').first.click();assert page.locator('.map-popup').is_visible()
        page.locator('.popup-mic').first.click();assert page.locator('#modal').is_visible()
        page.get_by_role('button',name='Close dialog').click()
        page.locator('#day-filter').select_option('0')
        page.screenshot(path=str(OUT/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.evaluate('document.activeElement.blur()');page.wait_for_timeout(100)
        assert page.evaluate('document.documentElement.scrollWidth')<=390
        page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1050})
        page.evaluate('state.demo=false;renderAdmin()')
        page.get_by_label('Admin password').fill(TEST_ADMIN_PASSWORD);page.get_by_role('button',name='Sign in',exact=False).click()
        page.wait_for_selector('.admin-tabs');assert page.locator('[data-admin-tab]').count()==6
        page.get_by_role('button',name='All mics',exact=True).click()
        page.get_by_role('button',name='Add a mic',exact=False).click()
        page.get_by_label('Mic name',exact=True).fill('Browser Test Mic')
        page.get_by_label('Venue',exact=True).fill('Example Room')
        page.get_by_label('Street address',exact=True).fill('1 Broadway')
        page.get_by_role('button',name='Save mic',exact=False).click()
        page.wait_for_selector('#admin-mic-rows .queue-item')
        assert 'Browser Test Mic' in page.locator('#admin-mic-rows').inner_text()
        page.screenshot(path=str(OUT/'admin.png'),full_page=True)
        page.get_by_role('button',name='Add source',exact=False).click()
        page.get_by_label('Source type',exact=True).select_option('upload')
        assert page.locator('#source-file').is_visible()
        page.get_by_role('button',name='Close dialog').click()
        page.get_by_role('button',name='Claims (0)',exact=True).click()
        page.get_by_role('button',name='Corrections (0)',exact=True).click()
        page.get_by_role('button',name='Host access',exact=True).click()
        page.get_by_role('button',name='Sign out',exact=True).click()
        page.wait_for_selector('#admin-login')
        assert not errors,errors
        b.close()
    print('Browser checks passed: 7 weekday filters, pins/popups, 390px layout, admin login/logout, tabs, manual mic creation, upload form. External tiles not verified.')

if __name__=='__main__':run()
