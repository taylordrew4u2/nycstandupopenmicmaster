import os,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from app.main import create_app
os.environ.update(LOCAL_DEV='1',SCHEDULER_ENABLED='0',GEOCODING_ENABLED='0')
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
 with TestClient(create_app(str(Path(tmp)/'cal.db'))) as client:
  browser=pw.chromium.launch(executable_path='/tmp/mic-chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':390,'height':844});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  def transport(route):
   u=urlsplit(route.request.url)
   if u.netloc!='mic.test':route.abort();return
   r=client.get(u.path);route.fulfill(status=r.status_code,headers=dict(r.headers),body=r.content)
  page.route('**/*',transport);page.goto('https://mic.test/');page.wait_for_selector('#day-filter option',state='attached')
  page.locator('#day-filter').select_option('calendar');expect(page.locator('#modal-title')).to_have_text('Calendar')
  for w,h in [(320,568),(390,844),(844,390),(1440,900)]:
   page.set_viewport_size({'width':w,'height':h});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  date=page.locator('.calendar-day').first.get_attribute('data-id');page.locator('.calendar-day').first.click();expect(page.locator('#day-filter')).to_have_value(date)
  page.locator('#day-filter').select_option('0');expect(page.locator('#day-filter')).to_have_value('0')
  assert page.evaluate("occursOnDate({weekday:0,frequency:'biweekly',recurrence_anchor:'2026-10-05'},'2026-10-19')")
  assert not page.evaluate("occursOnDate({weekday:0,frequency:'biweekly',recurrence_anchor:'2026-10-05'},'2026-10-12')")
  assert page.evaluate("scheduleLabel({frequency:'one-time'})")=='Pop-up mic (not recurring)'
  assert page.evaluate("claimLabel({venue_confirmed:true,claimed:false})")=='Venue confirmed'
  assert page.evaluate("claimLabel({venue_confirmed:true,claimed:true})")=='Host claimed'
  assert not errors,errors
  browser.close()
print('Calendar, date selector, recurrence and venue labels passed')
