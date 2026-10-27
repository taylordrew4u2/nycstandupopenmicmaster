"""Mobile directory regression checks with real Chromium touch input.

Build preview.html with scripts/build_preview.py first. Uses Playwright's
Chromium by default; CHROMIUM_PATH can select a system browser instead.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)


with sync_playwright() as pw:
    browser = pw.chromium.launch(
        executable_path=os.getenv('CHROMIUM_PATH'),
        headless=True,
        args=['--no-sandbox'],
    )
    page = browser.new_page(
        viewport={'width': 390, 'height': 844},
        has_touch=True,
        is_mobile=True,
        timezone_id='Asia/Tokyo',
    )
    page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.clock.set_fixed_time(datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc))
    page.set_content((ROOT / 'preview.html').read_text(), wait_until='domcontentloaded')
    page.locator('#day-filter').select_option('all')

    def settle():
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def ids(selector='#cards .mic-card', attribute='data-mic'):
        return page.locator(selector).evaluate_all(
            '(els, attr) => els.map(el => el.getAttribute(attr)).sort()', attribute
        )

    page.locator('#map-toggle').click()
    settle()
    assert page.locator('#map-toggle').get_attribute('aria-expanded') == 'false'
    assert page.locator('.mic-card').count() == 21
    assert len(set(ids())) == 21
    assert page.locator('.list-pagination').is_hidden()
    assert page.locator('#cards').evaluate('(el) => el.scrollHeight > el.clientHeight')

    # Exercise native touch scrolling, including a swipe beginning in a row.
    # The list must consume the vertical gesture without moving the page/map.
    original_center = page.evaluate('map.center.slice()')
    box = page.locator('#cards').bounding_box()
    x, y = box['x'] + box['width'] * .88, box['y'] + box['height'] * .8
    cdp = page.context.new_cdp_session(page)

    def touch(kind, current_y=None):
        points = [] if current_y is None else [{'x': x, 'y': current_y, 'id': 1}]
        cdp.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': points})

    touch('touchStart', y)
    for step in range(1, 9):
        touch('touchMove', y - step * 25)
        page.wait_for_timeout(20)
    touch('touchEnd')
    page.wait_for_function('document.querySelector("#cards").scrollTop > 50')
    page.evaluate('window.scrollCheck={top:document.querySelector("#cards").scrollTop,stable:0}')
    page.wait_for_function('''() => {
        const top=document.querySelector('#cards').scrollTop;
        scrollCheck.stable=top===scrollCheck.top?scrollCheck.stable+1:0;
        scrollCheck.top=top;return scrollCheck.stable>=4;
    }''', polling=50, timeout=3000)
    assert page.evaluate('window.scrollY') == 0
    assert page.evaluate('map.center') == original_center

    # Every result remains reachable in the one scroll container.
    page.locator('#cards').evaluate('(el) => el.scrollTop = el.scrollHeight')
    settle()
    assert page.locator('.mic-card').last.evaluate('''el => {
        const row=el.getBoundingClientRect(), list=el.parentElement.getBoundingClientRect();
        return row.top>=list.top && row.bottom<=list.bottom+1;
    }''')
    page.screenshot(path=str(OUT / 'mobile-directory-scrolled.png'))
    page.locator('.mic-title').last.click()
    assert page.locator('#modal').is_visible()
    page.get_by_role('button', name='Close dialog').click()

    # Keep a modest offset that fits with the map both shown and hidden.
    page.locator('#cards').evaluate('(el) => el.scrollTop = 100')
    page.locator('#map-toggle').click()
    settle()
    assert page.locator('#map-toggle').get_attribute('aria-expanded') == 'true'
    assert page.locator('#nyc-map').is_visible()
    assert page.locator('#cards').evaluate('(el) => el.scrollTop') == 100
    page.locator('#map-toggle').click()
    settle()
    assert page.locator('#cards').evaluate('(el) => el.scrollTop') == 100

    # Existing filtering resets list position and retains every matching result.
    page.locator('#borough-filter').select_option('Brooklyn')
    assert page.locator('#cards').evaluate('(el) => el.scrollTop') == 0
    assert all('Brooklyn' in text for text in page.locator('.venue-line').all_text_contents())
    page.locator('#search').fill('no such mic')
    assert page.locator('.blank-state').is_visible()
    page.get_by_role('button', name='Reset filters', exact=True).click()
    assert page.locator('.mic-card').count() == 21
    page.locator('#more-filters').click()
    page.locator('#time-from').fill('18:00')
    page.locator('#time-to').fill('20:00')
    page.get_by_role('button', name='Done', exact=True).click()
    assert page.locator('.mic-card').count() == 7
    assert all('7:00' in text for text in page.locator('.mic-time').all_text_contents())
    page.evaluate("Object.assign(state,{timeFrom:'',timeTo:''});renderDirectory()")

    # Phone rotation retains continuous scrolling; desktop still uses pages.
    for width, height in [(844, 390), (568, 320), (390, 844)]:
        page.set_viewport_size({'width': width, 'height': height})
        settle()
        assert page.locator('.mic-card').count() == 21, (width, height)
        assert page.locator('.list-pagination').is_hidden(), (width, height)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 1440, 'height': 900})
    settle()
    page.evaluate('map.reset()')
    assert page.locator('.list-pagination').is_visible()
    assert page.locator('.mic-card').count() < 21
    seen = []
    while True:
        seen += ids()
        if page.locator('[data-action="next-page"]').is_disabled():
            break
        page.locator('[data-action="next-page"]').click()
    assert len(seen) == len(set(seen)) == 21

    # Three nearby pins and one distant pin make map/list membership explicit.
    page.set_viewport_size({'width': 390, 'height': 844})
    settle()
    page.locator('#map-toggle').click()
    settle()
    page.mouse.move(0, 0)
    page.evaluate('''() => {
        const base=state.data.listings[0];
        state.data.listings=[
          {...base,id:'area-a',name:'Area A',venue:'Nearby A',latitude:40.72,longitude:-73.99},
          {...base,id:'area-b',name:'Area B',venue:'Nearby B',latitude:40.721,longitude:-73.991},
          {...base,id:'area-c',name:'Area C',venue:'Nearby C',latitude:40.719,longitude:-73.989},
          {...base,id:'area-d',name:'Area D',venue:'Distant D',latitude:40.85,longitude:-73.8}
        ];
        renderDirectory();map.center=[40.72,-73.99];map.zoom=15;map.initialFit=true;map.draw();
    }''')
    settle()
    assert ids() == ['area-a', 'area-b', 'area-c']
    assert ids('.mic-pin', 'data-mic-id') == ids()
    assert page.locator('#result-count').inner_text() == '3 mics in map area'
    for _ in range(6):
        page.locator('[data-map="out"]').click()
    settle()
    assert ids() == ['area-a', 'area-b', 'area-c', 'area-d']
    assert ids('.mic-pin', 'data-mic-id') == ids()

    page.evaluate('map.center=[40.85,-73.8];map.zoom=15;map.draw()')
    settle()
    assert ids() == ['area-d']
    page.locator('#search').fill('Area A')
    assert page.locator('.blank-state').is_visible()
    page.locator('#map-toggle').click()
    settle()
    assert ids() == ['area-a']
    page.locator('#search').fill('')
    assert len(ids()) == 4
    page.locator('#map-toggle').click()
    settle()
    assert ids() == ['area-d']

    # A real map pan also updates the list, and empty areas stay usable.
    page.evaluate('map.center=[40.72,-73.99];map.zoom=15;map.draw()')
    settle()
    bounds = page.locator('#nyc-map').bounding_box()
    page.mouse.move(bounds['x'] + bounds['width'] * .85, bounds['y'] + bounds['height'] * .65)
    page.mouse.down()
    page.mouse.move(bounds['x'] + bounds['width'] * .1, bounds['y'] + bounds['height'] * .3, steps=8)
    page.mouse.up()
    settle()
    assert ids() == page.evaluate('map.visibleRows().map(row=>row.id).sort()')
    assert not ids()
    assert page.locator('.blank-state').is_visible()
    page.locator('.blank-state [data-action="map-reset"]').click()
    settle()
    assert len(ids()) == 4

    # Browser timezone differs from New York: 22:00Z is 18:00 in NYC and
    # 07:00 the next day in Tokyo. Only earlier starts today in NYC are dimmed.
    page.locator('#map-toggle').click()
    settle()
    page.evaluate('''() => {
        const base=state.data.listings[0];
        state.data.listings=Array.from({length:21},(_,i)=>({...base,
          id:'time-'+i,name:'Time '+i,weekday:1,date:null,frequency:'weekly',
          start_time:i===0?'17:00':i===1?'18:00':'19:00'}));
        renderDirectory();
    }''')
    assert page.locator('[data-mic="time-0"] .card-main').evaluate('(el)=>getComputedStyle(el).opacity') == '0.58'
    assert page.locator('[data-mic="time-1"] .card-main').evaluate('(el)=>getComputedStyle(el).opacity') == '1'
    assert page.locator('[data-mic="time-2"] .card-main').evaluate('(el)=>getComputedStyle(el).opacity') == '1'
    assert page.locator('.mic-card').count() == 21
    page.evaluate("state.date='2026-11-03';renderDirectory()")
    assert page.locator('.past-mic').count() == 0
    page.locator('#day-filter').select_option('2026-10-27')
    assert page.locator('.past-mic').count() == 1
    page.locator('#cards').evaluate('(el)=>el.scrollTop=100')
    page.clock.set_fixed_time(datetime(2026, 10, 27, 22, 1, tzinfo=timezone.utc))
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    assert page.locator('[data-mic="time-1"] .card-main').evaluate('(el)=>getComputedStyle(el).opacity') == '0.58'
    assert page.locator('#cards').evaluate('(el)=>el.scrollTop') == 100

    # Historical shared IDs resolve to the displayed row's current action target.
    page.evaluate("state.data.listings[0].duplicate_ids=['historical-id'];details('historical-id')")
    assert page.locator('.detail-tabs [data-id]').evaluate_all("els=>els.every(el=>el.dataset.id==='time-0')")
    assert page.locator('[data-action="submit-fix"],[data-action="submit-report"],[data-action="submit-claim"]').evaluate_all(
        "els=>els.length===3&&els.every(el=>el.dataset.id==='time-0')"
    )
    page.locator('[data-action="detail-tab"][data-tab="link"]').click()
    assert page.locator('#mic-link').input_value().endswith('/?mic=time-0')
    assert page.locator('[data-action="copy-mic-link"]').get_attribute('data-id') == 'time-0'
    page.get_by_role('button', name='Close dialog').click()
    page.evaluate("state.demo=false;submission('historical-id','fix')")
    assert page.locator('#submission-form').get_attribute('data-id') == 'time-0'
    page.get_by_role('button', name='Close dialog').click()
    page.evaluate('state.demo=true')
    assert not errors, errors
    browser.close()

print(json.dumps({
    'mobile': '21 unique results reachable with native touch scrolling',
    'rotation': 'portrait and landscape scrolling; desktop pagination preserved',
    'filters': 'search, borough, and start-time filters preserved',
    'map': 'list follows zoom, pan, reset, and map visibility',
    'past_mics': 'New York clock, exact start minute, future date, and clock refresh verified',
    'shared_links': 'historical IDs use current details, links, and submission targets',
    'page_errors': errors,
}, indent=2))
