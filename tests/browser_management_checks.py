"""End-to-end moderation and public controls against an isolated backend."""
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app.main import create_app

H={'X-Requested-With':'MicList'}

def run():
    password=secrets.token_urlsafe(24)
    os.environ.update(ADMIN_PASSWORD=password,LOCAL_DEV='1',SCHEDULER_ENABLED='0',GEOCODING_ENABLED='0')
    os.environ.pop('PUBLIC_ORIGIN',None)
    os.environ.pop('VERCEL',None)
    with tempfile.TemporaryDirectory() as tmp:
        app=create_app(str(Path(tmp)/'test.db'))
        with TestClient(app,headers=H,base_url='https://mic.test') as admin, TestClient(app,base_url='https://mic.test') as visitor, sync_playwright() as pw:
            assert admin.post('/api/login',json={'password':password}).status_code==200
            with TestClient(app,headers={**H,'User-Agent':'Mozilla/5.0'}) as reader:
                assert reader.post('/api/visit').status_code==204
            browser=pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/tmp/mic-chromium'),headless=True,args=['--no-sandbox','--disable-gpu'])
            page=browser.new_page(viewport={'width':390,'height':844})
            page.set_default_timeout(6000)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def transport(route):
                req=route.request; u=urlsplit(req.url)
                if u.netloc!='mic.test':
                    route.abort();return
                response=visitor.request(req.method,u.path+('?' + u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in ('host','content-length','cookie')})
                route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
            page.route('**/*',transport)
            page.goto('https://mic.test/submit')
            page.wait_for_selector('#mic-proposal-form')
            for width,height in [(320,568),(390,844),(1440,900),(844,390)]:
                page.set_viewport_size({'width':width,'height':height})
                for tab in ['Mic','When','You']:
                    page.get_by_role('button',name=tab,exact=True).click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('#view-sources').evaluate('(e)=>e.scrollHeight<=e.clientHeight+1'),(width,height,tab)
                    box=page.get_by_role('button',name='Submit for review').bounding_box()
                    assert box['y']+box['height']<=height,(width,height,tab,box)
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('button',name='Mic',exact=True).click()
            page.get_by_label('Mic name',exact=True).fill('Submitted Browser Mic')
            page.get_by_label('Venue',exact=True).fill('Example Room')
            page.get_by_label('Street address',exact=True).fill('1 Broadway')
            page.get_by_role('button',name='When',exact=True).click()
            page.get_by_label('Start time',exact=True).fill('19:00')
            page.get_by_role('button',name='You',exact=True).click()
            page.get_by_label('Your name',exact=True).fill('Example Host')
            page.get_by_label('Your host email',exact=True).fill('host@example.com')
            page.get_by_label('How can we verify',exact=False).fill('The example venue manager can verify this test mic.')
            page.get_by_role('button',name='Submit for review').click()
            page.wait_for_selector('#proposal-status-link')
            status_url=page.url
            assert '/submit#' in status_url
            assert len(admin.get('/api/public').json()['listings'])==0
            page.goto('https://mic.test/admin')
            page.get_by_label('Admin password').fill(password)
            page.get_by_role('button',name='Sign in',exact=False).click()
            page.wait_for_selector('[data-admin-tab="proposals"]')
            assert page.locator('[data-admin-tab]').count()==9
            page.wait_for_selector('#visitor-stats .visitor-counts')
            assert page.locator('.visitor-counts strong').all_text_contents()==['1','1','1','1']
            page.locator('[data-admin-tab="proposals"]').click()
            page.on('dialog',lambda d:d.accept('Verified with the venue.') if d.type=='prompt' else d.accept())
            page.get_by_role('button',name='Approve mic & host setup').click()
            expect(page.locator('#modal')).to_be_visible()
            page.goto(status_url)
            page.get_by_role('link',name='Set up host password').click()
            host_password=secrets.token_urlsafe(24)
            page.get_by_label('Choose a password (15+ characters)',exact=True).fill(host_password)
            page.get_by_label('Confirm password',exact=True).fill(host_password)
            page.get_by_role('button',name='Create account & edit my mic',exact=False).click()
            page.wait_for_selector('#host-edit-form')
            mic=admin.get('/api/public').json()['listings'][0]
            assert mic['claimed']
            page.goto('https://mic.test/')
            page.wait_for_selector('#day-filter')
            page.locator('#day-filter').select_option('all')
            page.locator('[data-action="filters"]').click()
            assert page.locator('#claim-filter').count()==1
            page.locator('#claim-filter').select_option('unclaimed')
            assert page.locator('.mic-card').count()==0
            page.locator('#claim-filter').select_option('claimed')
            assert page.locator('.mic-card').count()==1
            page.locator('#sort').select_option('price')
            page.get_by_role('button',name='Reset',exact=True).click()
            assert page.locator('#sort').input_value()=='time'
            assert page.locator('#claim-filter').input_value()=='all'
            page.get_by_role('button',name='Done',exact=True).click()
            page.locator('.mic-title').click()
            page.get_by_role('button',name='Report inactive',exact=True).click()
            page.get_by_label('Why does this mic',exact=False).fill('Example report requiring administrator review.')
            page.get_by_role('button',name='Send report',exact=True).click()
            expect(page.locator('#modal')).not_to_be_visible()
            assert len(admin.get('/api/public').json()['listings'])==1
            page.goto('https://mic.test/admin')
            page.locator('[data-admin-tab="reports"]').click()
            expect(page.get_by_role('button',name='Hide mic & resolve')).to_be_visible()
            page.locator('[data-admin-tab="about"]').click()
            page.get_by_label('Page heading').fill('About Taylor and this list')
            page.locator('[data-admin-tab="about"]').click()
            assert page.get_by_label('Page heading').input_value()=='About Taylor and this list'
            page.get_by_role('button',name='Why',exact=True).click()
            page.get_by_label('Why you built it',exact=False).fill('A test introduction from the site administrator.')
            page.get_by_role('button',name='SEO',exact=True).click()
            page.get_by_label('Search description',exact=False).fill('Find NYC open mics with this test description.')
            page.get_by_role('button',name='Save About page',exact=True).click()
            expect(page.locator('#toast')).to_have_text('About page updated.')
            page.goto('https://mic.test/about')
            expect(page.locator('#about-title')).to_have_text('About Taylor and this list')
            assert page.locator('meta[name="description"]').get_attribute('content')=='Find NYC open mics with this test description.'
            assert not errors,errors
            browser.close()
    print('Management browser checks passed: compact submission layouts, admin approval, private status, host setup, claim filters/reset, moderated report, About editor and SEO.')

if __name__=='__main__':run()
