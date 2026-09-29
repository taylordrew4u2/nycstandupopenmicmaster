"""Verify the compact directory fits viewports and pagination reaches every mic.
Run scripts/build_preview.py first. External map tiles are excluded from this check.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'], headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
    page.set_content((ROOT / 'preview.html').read_text(), wait_until='domcontentloaded')
    page.locator('#day-filter').select_option('all')
    results = []
    for width, height in [(1440,900), (1024,768), (768,1024), (390,844), (360,640), (320,568), (320,400), (844,390), (568,320)]:
        page.set_viewport_size({'width':width,'height':height})
        page.wait_for_timeout(70)
        metrics = page.evaluate('''() => {
            const root=document.documentElement, box=document.querySelector('#cards'), map=document.querySelector('#nyc-map');
            const last=box.lastElementChild?.getBoundingClientRect(), bounds=box.getBoundingClientRect();
            return {width:innerWidth,height:innerHeight,bodyWidth:root.scrollWidth,bodyHeight:root.scrollHeight,
                listHeight:box.clientHeight,listContentHeight:box.scrollHeight,rows:box.children.length,
                lastFits:!last||last.bottom<=bounds.bottom+1,mapHeight:map.clientHeight,mapWidth:map.clientWidth,
                controlsVisible:[...document.querySelectorAll('.directory-controls > *')].every(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;})};
        }''')
        assert metrics['bodyWidth'] <= width, metrics
        assert metrics['bodyHeight'] <= height, metrics
        assert metrics['listContentHeight'] <= metrics['listHeight']+1, metrics
        assert metrics['lastFits'] and metrics['controlsVisible'] and metrics['rows'] >= 2, metrics
        assert metrics['mapHeight'] >= 120 and metrics['mapWidth'] >= 190, metrics
        page.locator('.mic-title').first.click()
        modal = page.locator('#modal')
        assert modal.evaluate('(el)=>el.scrollHeight<=el.clientHeight+1'), (width,height,'mic details scroll')
        page.locator('[data-action=detail-tab][data-tab=link]').click()
        assert modal.evaluate('(el)=>el.scrollHeight<=el.clientHeight+1'), (width,height,'link details scroll')
        assert '?mic=' in page.locator('#mic-link').input_value()
        page.get_by_role('button', name='Close dialog').click()
        page.locator('#more-filters').click()
        assert modal.evaluate('(el)=>el.scrollHeight<=el.clientHeight+1'), (width,height,'filters scroll')
        page.get_by_role('button', name='Done', exact=True).click()
        if width in (1440,390): page.screenshot(path=str(OUT/f'compact-{width}.png'))
        results.append(metrics)
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(70)
    page.evaluate('state.page=0;renderCards()')
    seen=[]
    while True:
        seen.extend(page.locator('.mic-card').evaluate_all('(els)=>els.map(el=>el.dataset.mic)'))
        if page.locator('[data-action="next-page"]').is_disabled():break
        page.locator('[data-action="next-page"]').click()
    assert len(seen)==21 and len(set(seen))==21, seen
    assert page.evaluate('map.rows.length')==21
    page.locator('#borough-filter').select_option('Brooklyn')
    assert page.evaluate('state.page')==0
    assert all('Brooklyn' in text for text in page.locator('.venue-line').all_text_contents())
    page.locator('.save-button').first.click()
    page.locator('#more-filters').click()
    page.locator('#saved-only').check()
    page.get_by_role('button', name='Done', exact=True).click()
    assert page.locator('.mic-card').count()==1
    page.locator('#search').fill('no such mic')
    assert page.locator('.blank-state').is_visible()
    page.get_by_role('button', name='Reset filters', exact=True).click()
    page.locator('.venue-line').first.click()
    assert page.locator('.map-popup').is_visible()
    page.locator('.popup-mic').first.click()
    assert page.locator('#modal').is_visible()
    page.get_by_role('button', name='Close dialog').click()
    # Many mics at one venue must use popup pages instead of overflowing.
    page.evaluate('map.show(state.data.listings,100,100)')
    assert page.locator('.popup-pages').is_visible()
    assert page.locator('.map-popup').evaluate('(el)=>el.scrollHeight<=el.clientHeight+1')
    assert not page.locator('a[href="/admin"]').count()
    dates=page.evaluate("""() => {
      const today=nyToday(),day=weekday(today),next=new Date(today+'T12:00:00Z');next.setUTCDate(next.getUTCDate()+7);
      return [nextDate({weekday:day}),nextDate({weekday:day,excluded_dates:[today]}),today,next.toISOString().slice(0,10)];
    }""")
    assert dates[0]==dates[2] and dates[1]==dates[3], dates
    assert page.evaluate("micTitle({name:'Open Mic',venue:'QED'})")=='QED'
    assert not errors, errors
    browser.close()
    print(json.dumps({'viewports':results,'pagination':'21 unique mics reachable','page_errors':errors},indent=2))
