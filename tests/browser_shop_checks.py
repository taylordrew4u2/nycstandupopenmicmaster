"""Storefront UI and checkout contract, using isolated API fixtures (no orders)."""
import os,json,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from app.main import create_app
os.environ.update(LOCAL_DEV='1',SCHEDULER_ENABLED='0',GEOCODING_ENABLED='0',FOURTHWALL_STOREFRONT_TOKEN='ptkn_test_fixture')

def run():
 with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
  with TestClient(create_app(str(Path(tmp)/'shop.db')),base_url='https://mic.test') as client:
   browser=pw.chromium.launch(executable_path='/tmp/mic-chromium',headless=True,args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':320,'height':568});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   state={'mode':'products','checkout':None}
   product={'id':'p1','name':'Support the list shirt','state':{'type':'AVAILABLE'},'images':[], 'variants':[{'id':'v1','attributes':{'description':'Black, M'},'unitPrice':{'value':25,'currency':'USD'},'stock':{'type':'LIMITED','inStock':2}},{'id':'v2','name':'Black, L','unitPrice':{'value':27,'currency':'USD'},'stock':{'type':'LIMITED','inStock':0}}]}
   def transport(route):
    req=route.request;u=urlsplit(req.url)
    if u.netloc=='storefront-api.fourthwall.com':
     if u.path.endswith('/carts'):
      assert json.loads(req.post_data)=={'currency':'USD','items':[{'variantId':'v1','quantity':1}]}
      route.fulfill(json={'id':'test-cart'});return
     if state['mode']=='error':route.fulfill(status=503,body='');return
     route.fulfill(json={'results':[] if state['mode']=='empty' else [product],'paging':{'hasNextPage':False}});return
    if u.netloc.endswith('.fourthwall.com'):
     state['checkout']=req.url;route.fulfill(body='Hosted checkout');return
    r=client.get(u.path);route.fulfill(status=r.status_code,headers=dict(r.headers),body=r.content)
   page.route('**/*',transport)
   page.goto('https://mic.test/shop');expect(page.get_by_role('button',name='Add to cart')).to_be_enabled()
   assert page.locator('option[value="v2"]').is_disabled()
   for width,height in [(320,568),(390,844),(844,390),(1440,900)]:
    page.set_viewport_size({'width':width,'height':height});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight')
   page.get_by_role('button',name='Add to cart').click();expect(page.locator('#subtotal')).to_have_text('Subtotal: $25.00')
   page.get_by_role('button',name='Remove one').click();expect(page.locator('#checkout')).to_be_disabled()
   page.get_by_role('button',name='Close',exact=True).click();page.get_by_role('button',name='Add to cart').click();page.locator('#checkout').click();page.wait_for_url('**/checkout/**');assert 'cartId=test-cart' in state['checkout']
   state['mode']='empty';page.goto('https://mic.test/shop');expect(page.locator('#status')).to_contain_text('No shirts available')
   state['mode']='error';page.reload();expect(page.locator('#retry')).to_be_visible();state['mode']='products';page.locator('#retry').click();expect(page.get_by_role('button',name='Add to cart')).to_be_enabled()
   assert not errors,errors
   assert 'storefront-api.fourthwall.com' in client.get('/shop').headers['content-security-policy']
   assert 'storefront-api.fourthwall.com' not in client.get('/').headers['content-security-policy']
   browser.close()
 print('PASS: variants, stock, cart, checkout handoff, empty/error/retry, four viewport sizes, scoped CSP')
if __name__=='__main__':run()
