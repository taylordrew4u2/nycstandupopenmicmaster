"""Real Chromium touch input; vibration calls are mocked, not physical hardware."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/tmp/mic-chromium'),headless=True,args=['--no-sandbox','--disable-gpu'])
    page=browser.new_page(viewport={'width':390,'height':844},has_touch=True,is_mobile=True)
    page.route('https://tile.openstreetmap.org/**',lambda r:r.abort())
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content((ROOT/'preview.html').read_text(),wait_until='domcontentloaded')
    page.evaluate("map.rows=[];map.zoom=12;map.initialFit=true;map.center=[40.705,-73.975];map.draw();window.buzz=[];Object.defineProperty(navigator,'vibrate',{configurable:true,value:(n)=>{buzz.push(n);return true}})")
    cdp=page.context.new_cdp_session(page)
    b=page.locator('#nyc-map').bounding_box(); x=b['x']+b['width']*.55;y=b['y']+b['height']*.55
    def touch(kind,points):
        cdp.send('Input.dispatchTouchEvent',{'type':kind,'touchPoints':[{'x':px,'y':py,'id':i,'radiusX':2,'radiusY':2} for i,px,py in points]})
        page.evaluate('()=>new Promise(requestAnimationFrame)')
    def geo(px,py):
        return page.evaluate('''([x,y])=>{const b=map.root.getBoundingClientRect(),[cx,cy]=map.project(...map.center);return map.unproject(cx+x-b.left-map.root.clientLeft-map.root.clientWidth/2,cy+y-b.top-map.root.clientTop-map.root.clientHeight/2)}''',[px,py])
    def close(a,b,tolerance=1e-8):assert max(abs(x-y) for x,y in zip(a,b))<tolerance,(a,b)
    anchor=geo(x,y)
    touch('touchStart',[(1,x-35,y),(2,x+35,y)])
    touch('touchMove',[(1,x-52.5+10,y+15),(2,x+52.5+10,y+15)])
    assert abs(page.evaluate('map.zoom')-(12+__import__('math').log2(1.5)))<1e-6
    close(anchor,geo(x+10,y+15))
    assert page.locator('.map-tile').evaluate_all("els=>els.every(e=>/\\/\\d+\\/\\d+\\/\\d+\\.png$/.test(e.src))")
    touch('touchMove',[(1,x-70,y),(2,x+70,y)])
    assert abs(page.evaluate('map.zoom')-13)<1e-6
    close(anchor,geo(x,y))
    touch('touchEnd',[(2,x+70,y)])
    before=geo(x-70,y)
    touch('touchMove',[(1,x-50,y+20)])
    close(before,geo(x-50,y+20))
    touch('touchEnd',[])
    assert page.evaluate('map.pointers.size')==0
    assert page.evaluate('buzz')==[10]
    # Pin taps still work; dragging from a pin must not select it.
    page.evaluate("map.suppressClickUntil=0;map.rows=[{id:'touch-pin',name:'Test mic',venue:'Test venue',latitude:map.center[0],longitude:map.center[1],start_time:'19:00'}];map.draw();buzz=[]")
    page.locator('.mic-pin').tap()
    assert page.locator('.map-popup').is_visible() and page.evaluate('buzz')==[10]
    page.locator('.popup-close').tap()
    assert page.locator('.map-popup').is_hidden()
    pin=page.locator('.mic-pin').bounding_box();px=pin['x']+pin['width']/2;py=pin['y']+pin['height']/2
    touch('touchStart',[(1,px,py)])
    touch('touchMove',[(1,px+35,py+20)])
    touch('touchEnd',[])
    assert page.locator('.map-popup').is_hidden()
    page.evaluate('map.rows=[];map.draw()')
    # Zoom out and clamp both bounds, with no stale pointers after cancellation.
    page.evaluate('map.zoom=8.2;map.draw()')
    touch('touchStart',[(1,x-70,y),(2,x+70,y)])
    touch('touchMove',[(1,x-10,y),(2,x+10,y)])
    assert page.evaluate('map.zoom')==8
    touch('touchCancel',[])
    assert page.evaluate('map.pointers.size')==0
    page.evaluate('map.zoom=18.8;map.draw()')
    touch('touchStart',[(1,x-10,y),(2,x+10,y)])
    touch('touchMove',[(1,x-70,y),(2,x+70,y)])
    assert page.evaluate('map.zoom')==19
    touch('touchEnd',[])
    # A new drag works after cancellation, and release stops movement.
    page.evaluate('map.zoom=12;map.suppressClickUntil=0;map.draw()')
    anchor=geo(x,y)
    touch('touchStart',[(1,x,y)])
    touch('touchMove',[(1,x+30,y+20)])
    touch('touchEnd',[])
    close(anchor,geo(x+30,y+20))
    # Double tap increases zoom once, not once for pointerup and again for dblclick.
    page.evaluate('map.suppressClickUntil=0;map.lastTap=null')
    touch('touchStart',[(1,x,y)]);touch('touchEnd',[])
    touch('touchStart',[(1,x,y)]);touch('touchEnd',[])
    assert page.evaluate('map.zoom')==13
    # Touch button feedback and safe unsupported/disabled fallbacks.
    page.evaluate('map.suppressClickUntil=0;buzz=[]')
    page.locator('[data-map="in"]').tap()
    assert page.evaluate('map.zoom')==14 and page.evaluate('buzz')==[10]
    page.emulate_media(reduced_motion='reduce')
    page.locator('[data-map="out"]').tap()
    assert page.evaluate('buzz')==[10]
    page.emulate_media(reduced_motion='no-preference')
    for definition in ["undefined","()=>{throw Error('unavailable')} "]:
        page.evaluate("Object.defineProperty(navigator,'vibrate',{configurable:true,value:"+definition+"})")
        page.locator('[data-map="in"]').tap()
    assert page.evaluate('map.zoom')==15
    assert not errors,errors
    browser.close()
print('Mobile map passed: real two-finger pinch, moving anchor, fractional tile zoom, pan after finger lift, cancellation, zoom limits, double tap, haptic dispatch and unsupported/reduced-motion fallbacks. Physical vibration requires device verification.')
