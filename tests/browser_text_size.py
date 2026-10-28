"""Verify readable text sizes, layout fit, and a real saved browser preference.

Build preview.html first. Uses Playwright Chromium unless CHROMIUM_PATH is set.
"""
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == '/':
            self.path = '/preview.html'
        super().do_GET()

    def log_message(self, *_args):
        pass


with ThreadingHTTPServer(('127.0.0.1', 0), PreviewHandler) as server:
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv('CHROMIUM_PATH'), headless=True,
                args=['--no-sandbox'],
            )
            page = browser.new_page(viewport={'width': 390, 'height': 844}, timezone_id='Asia/Tokyo')
            page.set_default_timeout(10000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
            page.clock.set_fixed_time(datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.locator('#day-filter').select_option('all')

            def settle():
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')

            def set_view(view):
                page.locator(f'[data-action="directory-view"][data-view="{view}"]').click()
                settle()

            def open_text_size():
                page.locator('#site-menu summary').click()
                page.get_by_role('button', name='Text size', exact=True).click()
                assert page.locator('#modal').is_visible()
                assert page.locator('#text-size').evaluate('el=>el.labels.length>0')

            def set_size(size):
                open_text_size()
                page.locator('#text-size').select_option(size)
                page.get_by_role('button', name='Done', exact=True).click()
                settle()

            def ordered_ids():
                return page.locator('#cards .mic-card').evaluate_all('els=>els.map(el=>el.dataset.mic)')

            def filter_state():
                return page.evaluate('({date:state.date,day:state.day,borough:state.borough,search:state.search,from:state.timeFrom,to:state.timeTo,sort:state.sort})')

            def font(selector):
                return page.locator(selector).first.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)')

            def fitted_controls():
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight')
                assert page.locator('.directory-controls > *,[data-action="directory-view"]').evaluate_all('''els=>els.every(el=>{
                    const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;
                })''')

            def detail_font():
                page.locator('.mic-title').first.click()
                assert page.locator('#modal').evaluate('''el=>{
                    const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;
                }''')
                result = font('.detail-item strong')
                page.keyboard.press('Escape')
                assert page.locator('#modal').is_hidden()
                return result

            def reachable_mobile_results():
                assert len(ordered_ids()) == 21
                assert page.locator('.list-pagination').is_hidden()
                page.locator('#cards').evaluate('el=>el.scrollTop=el.scrollHeight')
                settle()
                assert page.locator('#cards .mic-card').last.evaluate('''el=>{
                    const r=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();
                    return r.top>=b.top&&r.bottom<=b.bottom+1;
                }''')

            set_view('both')
            page.locator('#cards .venue-line').first.click()
            assert page.locator('.map-popup').is_visible()
            set_view('list')
            expected_order = ordered_ids()
            expected_past = page.locator('#cards .past-mic').evaluate_all('els=>els.map(el=>el.dataset.mic).sort()')
            assert len(expected_order) == 21 and len(expected_past) == 1
            initial_filters = filter_state()
            metrics = {}
            for size in ('small', 'normal', 'large', 'extra-large'):
                set_size(size)
                fitted_controls()
                assert ordered_ids() == expected_order
                assert page.locator('#cards .past-mic').evaluate_all('els=>els.map(el=>el.dataset.mic).sort()') == expected_past
                assert filter_state() == initial_filters
                reachable_mobile_results()
                metrics[size] = {
                    'title': font('#cards .mic-title'),
                    'control': font('#search'),
                    'map': font('.map-legend'),
                    'popup': font('.popup-mic span'),
                    'row': page.locator('.mic-card').first.evaluate('el=>el.getBoundingClientRect().height'),
                    'details': detail_font(),
                }
            for name in ('title', 'map', 'popup', 'row', 'details'):
                values = [metrics[size][name] for size in ('small', 'normal', 'large', 'extra-large')]
                assert all(a < b for a, b in zip(values, values[1:])), (name, metrics)
            for size, factor in (('small', .9), ('normal', 1), ('large', 1.2), ('extra-large', 1.4)):
                assert abs(metrics[size]['title'] / metrics['normal']['title'] - factor) < .01, metrics
            assert len({values['control'] for values in metrics.values()}) == 1, metrics

            # Largest text must leave every layout choice and the result set usable.
            desktop_large_rows = None
            for width, height in ((390, 844), (320, 400), (844, 390), (1440, 900)):
                page.set_viewport_size({'width': width, 'height': height})
                settle()
                for view in ('map', 'both', 'list'):
                    set_view(view)
                    fitted_controls()
                    assert filter_state() == initial_filters
                    assert page.locator('#cards').is_visible() == (view != 'map')
                    assert page.locator('#nyc-map').is_visible() == (view != 'list')
                if width < 1000:
                    reachable_mobile_results()
                    assert ordered_ids() == expected_order
                    if width == 320:
                        open_text_size()
                        assert page.locator('#text-size').is_visible()
                        assert page.get_by_role('button', name='Done', exact=True).evaluate('''el=>{
                            const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;
                        }''')
                        page.get_by_role('button', name='Done', exact=True).click()
                else:
                    desktop_large_rows = page.locator('.mic-card').count()
                    seen = []
                    while True:
                        seen += ordered_ids()
                        assert page.locator('#cards .mic-card').last.evaluate('''el=>{
                            const r=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return r.bottom<=b.bottom+1;
                        }''')
                        if page.locator('[data-action="next-page"]').is_disabled():
                            break
                        page.locator('[data-action="next-page"]').click()
                    assert len(seen) == len(set(seen)) == 21
                detail_font()

            set_size('small')
            assert page.locator('.mic-card').count() > desktop_large_rows
            fitted_controls()

            # Changing text size preserves active filters; reloading preserves size.
            page.locator('#borough-filter').select_option('Brooklyn')
            page.locator('#search').fill('New Material')
            page.locator('#more-filters').click()
            page.locator('#time-from').fill('18:00')
            page.locator('#time-to').fill('20:00')
            page.get_by_role('button', name='Done', exact=True).click()
            active_filters, active_ids = filter_state(), ordered_ids()
            assert active_ids and active_filters['borough'] == 'Brooklyn'
            set_size('large')
            assert filter_state() == active_filters and ordered_ids() == active_ids
            saved_font = font('#cards .mic-title')
            page.reload()
            page.wait_for_selector('.mic-card')
            assert font('#cards .mic-title') == saved_font
            open_text_size()
            assert page.locator('#text-size').input_value() == 'large'
            assert page.locator('#text-size option').evaluate_all('els=>els.map(el=>el.value)') == ['small', 'normal', 'large', 'extra-large']
            page.get_by_role('button', name='Done', exact=True).click()

            # The menu and all size choices are also operable from the keyboard.
            page.locator('#site-menu summary').focus()
            page.keyboard.press('Enter')
            for _ in range(8):
                page.keyboard.press('Tab')
                if page.get_by_role('button', name='Text size', exact=True).evaluate('el=>el===document.activeElement'):
                    break
            assert page.get_by_role('button', name='Text size', exact=True).evaluate('el=>el===document.activeElement')
            page.keyboard.press('Enter')
            page.locator('#text-size').focus()
            # Typeahead works with both macOS and Linux native select controls.
            page.keyboard.press('s')
            assert page.locator('#text-size').input_value() == 'small'
            page.keyboard.press('Tab')
            page.keyboard.press('Shift+Tab')
            page.keyboard.press('e')
            assert page.locator('#text-size').input_value() == 'extra-large'
            page.get_by_role('button', name='Done', exact=True).focus()
            page.keyboard.press('Enter')
            assert page.locator('#modal').is_hidden()

            # A two-line title plus the longest status badges must fit at XL.
            page.set_viewport_size({'width': 320, 'height': 400})
            set_view('list')
            page.evaluate('''()=>{
                const base=state.data.listings[0];
                Object.assign(state,{date:null,day:'all',borough:'all',search:'',
                    timeFrom:'',timeTo:'',sort:'time'});
                state.data.listings=[
                    {...base,id:'long-unconfirmed',name:'A Very Long Stand-Up Comedy Mic Title for Testing',
                     date:null,weekday:null,frequency:'',recurrence_anchor:null,notes:'',
                     claimed:false,venue_confirmed:false},
                    {...base,id:'long-biweekly',name:'Another Very Long Stand-Up Comedy Mic Title for Testing',
                     date:null,weekday:1,frequency:'biweekly',recurrence_anchor:'2026-10-27',
                     claimed:false,venue_confirmed:true}
                ];renderDirectory();
            }''')
            for view in ('both', 'list'):
                set_view(view)
                fitted_controls()
                assert set(ordered_ids()) == {'long-unconfirmed', 'long-biweekly'}
                assert page.locator('[data-mic="long-unconfirmed"] .schedule-badge').inner_text() == 'Schedule unconfirmed'
                assert page.locator('[data-mic="long-unconfirmed"] .claim-status').inner_text() == 'Unclaimed'
                assert page.locator('[data-mic="long-biweekly"] .biweekly-badge').inner_text() == 'Biweekly'
                assert page.locator('[data-mic="long-biweekly"] .claim-status').inner_text() == 'Venue confirmed'
                bounds = page.locator('#cards .mic-card').evaluate_all('''cards=>cards.map(card=>{
                    const box=card.getBoundingClientRect(),title=card.querySelector('.mic-title');
                    const venue=card.querySelector('.venue-line'),main=card.querySelector('.card-main');
                    const outside=[...card.querySelectorAll('*')].filter(el=>{
                        const r=el.getBoundingClientRect();if(!r.width||!r.height)return false;
                        return r.left<box.left-1||r.right>box.right+1||r.top<box.top-1||r.bottom>box.bottom+1;
                    }).map(el=>({className:el.className,rect:el.getBoundingClientRect().toJSON()}));
                    return {id:card.dataset.mic,outside,venueWidth:venue.getBoundingClientRect().width,
                        titleHeight:title.getBoundingClientRect().height,titleLine:parseFloat(getComputedStyle(title).lineHeight),
                        horizontalOverflow:card.scrollWidth>card.clientWidth+1||main.scrollWidth>main.clientWidth+1};
                })''')
                for result in bounds:
                    assert not result['outside'] and not result['horizontalOverflow'], (view, result)
                    assert result['venueWidth'] > 0, (view, result)
                    assert result['titleLine'] * 1.5 < result['titleHeight'] <= result['titleLine'] * 2 + 1, (view, result)
                page.locator('#cards').evaluate('el=>el.scrollTop=el.scrollHeight')
                settle()
                assert page.locator('#cards .mic-card').last.evaluate('''el=>{
                    const r=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return r.top>=b.top&&r.bottom<=b.bottom+1;
                }''')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        thread.join()

print(json.dumps({'sizes': metrics, 'layouts': 'phone, small phone, landscape, desktop; all three modes',
                  'preference': 'saved across real HTTP reload', 'keyboard': 'menu, options, and Done',
                  'long_cards': 'two-line titles, unconfirmed and biweekly venue badges fit at 320px XL',
                  'page_errors': errors}, indent=2))
