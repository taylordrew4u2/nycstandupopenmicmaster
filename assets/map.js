/* Small, dependency-free Web Mercator map. Only visible OSM tiles are fetched.
   No bulk downloads, offline prefetch, or hidden attribution. */
'use strict';
let micPinSequence=0;
class MicMap {
  constructor(root, onSelect) {
    this.root = root; this.onSelect = onSelect; this.zoom = 10;
    this.center = [40.705, -73.975]; this.initialFit = false; this.rows = []; this.tiles = new Map();
    this.plane = root.querySelector('.tile-plane'); this.pins = root.querySelector('.pin-plane');
    this.popup = root.querySelector('.map-popup'); this.loaded = 0; this.failed = 0;
    root.querySelectorAll('[data-map]').forEach(b => b.addEventListener('click', () => {
      this.zoom = Math.max(8, Math.min(19, this.zoom + (b.dataset.map === 'in' ? 1 : -1))); this.hide(); this.draw();
    }));
    root.addEventListener('dblclick', e => {
      if (e.target.closest('button,a,.map-popup') || e.button !== 0) return;
      e.preventDefault();
      if (this.zoom >= 19) return;
      if(performance.now()<(this.suppressDoubleUntil||0))return;
      this.zoomAt(this.zoom+1,e.clientX,e.clientY);this.haptic();
    });
    this.pointers = new Map(); this.gesture = null; this.suppressClickUntil = 0;
    // A drag/pinch ending over a pin must not accidentally select that mic.
    root.addEventListener('click', e => {
      if (performance.now() < this.suppressClickUntil) { e.preventDefault(); e.stopImmediatePropagation(); }
      else if (e.target.closest('button')) this.haptic();
    }, true);
    const blocked = e => e.target.closest('a,.map-popup,.map-controls');
    root.addEventListener('pointerdown', e => {
      if (blocked(e) || e.button !== 0) return;
      this.pointers.set(e.pointerId, {x:e.clientX,y:e.clientY});
      if (!e.target.closest('.mic-pin')) root.setPointerCapture(e.pointerId);
      this.beginGesture();
      if (this.pointers.size > 1) {
        this.lastTap = null; this.suppressClickUntil = performance.now()+500;
        for (const id of this.pointers.keys()) { try { root.setPointerCapture(id); } catch {} }
      }
    });
    root.addEventListener('pointermove', e => {
      if (!this.pointers.has(e.pointerId) || !this.gesture) return;
      this.pointers.set(e.pointerId, {x:e.clientX,y:e.clientY});
      const points=[...this.pointers.values()].slice(0,2), g=this.gesture;
      const mid=this.midpoint(points), distance=points.length===2?Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y):0;
      if (!g.moved && Math.hypot(mid.x-g.mid.x,mid.y-g.mid.y)<4 && Math.abs(distance-g.distance)<4) return;
      g.moved=true; this.lastTap=null; this.suppressClickUntil=performance.now()+500;
      try { root.setPointerCapture(e.pointerId); } catch {}
      if (points.length===2 && g.distance>0) this.zoom=Math.max(8,Math.min(19,g.zoom+Math.log2(Math.max(1,distance)/g.distance)));
      const bounds=root.getBoundingClientRect(),[x,y]=this.project(...g.anchor);
      this.center=this.unproject(x-(mid.x-bounds.left-root.clientLeft-root.clientWidth/2),y-(mid.y-bounds.top-root.clientTop-root.clientHeight/2));
      this.hide(); this.scheduleDraw();
    });
    const finish = e => {
      if (!this.pointers.has(e.pointerId)) return;
      const g=this.gesture, multi=this.pointers.size>1;
      if(e.type!=='pointerup')this.lastTap=null;
      this.pointers.delete(e.pointerId);
      if (g?.moved || multi) {
        this.suppressClickUntil=performance.now()+500;
        if (multi && e.type==='pointerup' && Math.abs(this.zoom-g.zoom)>.1) this.haptic();
      } else if (e.type==='pointerup' && e.pointerType==='touch' && performance.now()>=this.suppressClickUntil && !e.target.closest('button')) {
        const now=performance.now(), previous=this.lastTap;
        if (previous && now-previous.time<320 && Math.hypot(e.clientX-previous.x,e.clientY-previous.y)<24) {
          this.zoomAt(this.zoom+1,e.clientX,e.clientY);this.haptic();this.lastTap=null;
          this.suppressDoubleUntil=now+500;
        } else this.lastTap={time:now,x:e.clientX,y:e.clientY};
      }
      this.beginGesture();
      if (!this.pointers.size && this.frame) { cancelAnimationFrame(this.frame);this.frame=null;this.draw(); }
    };
    root.addEventListener('pointerup',finish);
    root.addEventListener('pointercancel',finish);
    root.addEventListener('lostpointercapture',e=>{if(e.target===root)finish(e);});
    this.observer=new ResizeObserver(()=>{this.hide();this.draw();}); this.observer.observe(root);
    this.draw();
  }
  haptic(){
    if(!matchMedia('(pointer: coarse)').matches || matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    if(typeof navigator.vibrate==='function'){try{navigator.vibrate(10);}catch{}}
  }
  midpoint(points){return points.length>1?{x:(points[0].x+points[1].x)/2,y:(points[0].y+points[1].y)/2}:points[0];}
  beginGesture(){
    const points=[...this.pointers.values()].slice(0,2);
    if(!points.length){this.gesture=null;return;}
    const mid=this.midpoint(points),bounds=this.root.getBoundingClientRect(),[cx,cy]=this.project(...this.center);
    this.gesture={mid,zoom:this.zoom,moved:false,distance:points.length===2?Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y):0,
      anchor:this.unproject(cx+mid.x-bounds.left-this.root.clientLeft-this.root.clientWidth/2,cy+mid.y-bounds.top-this.root.clientTop-this.root.clientHeight/2)};
  }
  scheduleDraw(){if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});}
  zoomAt(zoom,clientX,clientY){
    const bounds=this.root.getBoundingClientRect(),dx=clientX-bounds.left-this.root.clientLeft-this.root.clientWidth/2,dy=clientY-bounds.top-this.root.clientTop-this.root.clientHeight/2;
    const [cx,cy]=this.project(...this.center),anchor=this.unproject(cx+dx,cy+dy);
    this.zoom=Math.max(8,Math.min(19,zoom));const [x,y]=this.project(...anchor);
    this.center=this.unproject(x-dx,y-dy);this.hide();this.draw();
  }
  project(lat,lng) {const size=256*2**this.zoom,s=Math.sin(Math.max(-85,Math.min(85,lat))*Math.PI/180);return [(lng+180)/360*size,(.5-Math.log((1+s)/(1-s))/(4*Math.PI))*size];}
  unproject(x,y) {const size=256*2**this.zoom;return [180/Math.PI*Math.atan(Math.sinh(Math.PI*(1-2*y/size))),x/size*360-180];}
  fitCity(){
    this.zoom=0; const a=this.project(40.49,-74.26),b=this.project(40.92,-73.69);
    this.zoom=Math.max(8,Math.min(12,Math.floor(Math.log2(Math.min((this.root.clientWidth-35)/Math.abs(b[0]-a[0]),(this.root.clientHeight-40)/Math.abs(b[1]-a[1]))))));
    this.center=[40.705,-73.975];this.initialFit=true;
  }
  reset(){this.fitCity();this.hide();this.draw();}
  hide(){this.popup.hidden=true;}
  setRows(rows){this.rows=rows;this.hide();this.draw();}
  focus(id){
    const row=this.rows.find(r=>r.id===id);
    if(!row||!Number.isFinite(row.latitude)||!Number.isFinite(row.longitude))return false;
    this.center=[row.latitude,row.longitude];this.zoom=Math.max(this.zoom,17);this.initialFit=true;
    this.hide();this.draw();
    this.show([row],this.root.clientWidth/2,this.root.clientHeight/2);
    return true;
  }
  draw(){
    const w=this.root.clientWidth,h=this.root.clientHeight;if(!w||!h)return;
    if(!this.initialFit)this.fitCity();
    const [cx,cy]=this.project(...this.center),left=cx-w/2,top=cy-h/2;
    const tileZoom=Math.floor(this.zoom),scale=2**(this.zoom-tileZoom),tileSize=256*scale;
    const minX=Math.floor(left/tileSize),minY=Math.floor(top/tileSize),maxX=Math.floor((left+w)/tileSize),maxY=Math.floor((top+h)/tileSize),keep=new Set();
    for(let x=minX;x<=maxX;x++)for(let y=minY;y<=maxY;y++){
      if(y<0||y>=2**tileZoom)continue;
      const id=`${tileZoom}/${((x % 2**tileZoom)+2**tileZoom)%2**tileZoom}/${y}`;keep.add(id);let tile=this.tiles.get(id);
      if(!tile){tile=new Image();tile.alt='';tile.draggable=false;tile.referrerPolicy='strict-origin-when-cross-origin';tile.className='map-tile';tile.onload=()=>{tile.style.visibility='visible';this.loaded++;this.root.querySelector('.map-offline').hidden=true;};tile.onerror=()=>{tile.style.visibility='hidden';this.failed++;if(this.loaded===0)this.root.querySelector('.map-offline').hidden=false;};tile.src=`https://tile.openstreetmap.org/${id}.png`;this.tiles.set(id,tile);this.plane.append(tile);}
      tile.style.transformOrigin='0 0';tile.style.transform=`translate(${x*tileSize-left}px,${y*tileSize-top}px) scale(${scale})`;
    }
    for(const [id,tile]of this.tiles)if(!keep.has(id)){tile.remove();this.tiles.delete(id);}
    this.pins.replaceChildren();
    if(this.zoom<=11)for(const [name,lat,lng]of [['Manhattan',40.795,-73.988],['Brooklyn',40.635,-73.95],['Queens',40.735,-73.78],['Bronx',40.87,-73.86],['Staten Island',40.555,-74.16]]){const [x,y]=this.project(lat,lng);const label=document.createElement('span');label.className='map-borough';label.textContent=name;label.style.left=`${x-left}px`;label.style.top=`${y-top}px`;this.pins.append(label);}
    // One marker per mic. Never average or offset its supplied coordinates.
    for(const row of this.rows){
      if(!Number.isFinite(row.latitude)||!Number.isFinite(row.longitude))continue;
      const [px,py]=this.project(row.latitude,row.longitude),x=px-left,y=py-top;
      if(x<-44||y<-44||x>w+44||y>h+44)continue;
      const button=document.createElement('button');button.type='button';button.className='mic-pin';
      button.classList.toggle('confirmed-pin',!!(row.claimed||row.venue_confirmed));
      button.classList.toggle('biweekly-pin',typeof isBiweekly==='function'&&isBiweekly(row));
      button.dataset.micId=row.id;
      const pinId='pushpin-'+(++micPinSequence);
      button.innerHTML=`<svg viewBox="0 0 44 44" aria-hidden="true" focusable="false"><defs><linearGradient id="${pinId}-steel"><stop stop-color="#666"/><stop offset=".4" stop-color="#f4f4f4"/><stop offset=".7" stop-color="#aaa"/><stop offset="1" stop-color="#555"/></linearGradient><radialGradient id="${pinId}-head" cx="65%" cy="25%" r="80%"><stop stop-color="#fff"/><stop offset=".16" stop-color="currentColor"/><stop offset=".63" stop-color="currentColor"/><stop offset="1" stop-color="#15202b"/></radialGradient></defs><path d="M21 26H23L22.6 39Q22.4 43 22 44Q21.6 43 21.4 39Z" fill="url(#${pinId}-steel)"/><circle class="pushpin-head" cx="22" cy="21" r="6" style="fill:url(#${pinId}-head)"/><ellipse class="pushpin-glint" cx="24.5" cy="18" rx="1.1" ry=".8" transform="rotate(35 24.5 18)"/></svg>`;
      button.style.left=`${x}px`;button.style.top=`${y}px`;
      const status=button.classList.contains('biweekly-pin')?'Biweekly':row.claimed?'Host claimed':row.venue_confirmed?'Venue confirmed':'Unclaimed';
      button.setAttribute('aria-label',`${row.name} at ${row.venue} · ${status}`);
      button.title=`${row.name} · ${row.venue} · ${status}`;
      button.addEventListener('click',()=>this.onSelect(row.id));
      this.pins.append(button);
    }
  }
  show(rows,x,y,page=0){
    this.popup.replaceChildren();const close=document.createElement('button');close.className='popup-close';close.textContent='×';close.setAttribute('aria-label','Close map popup');close.onclick=()=>this.hide();this.popup.append(close);
    const title=document.createElement('strong');title.textContent=new Set(rows.map(r=>r.venue)).size>1?'Mics in this area':rows[0].venue;this.popup.append(title);
    const size=Math.max(1,Math.min(3,Math.floor((this.root.clientHeight-90)/44))),pages=Math.ceil(rows.length/size);
    page=Math.max(0,Math.min(page,pages-1));
    for(const row of rows.slice(page*size,(page+1)*size)){const item=document.createElement('button');item.className='popup-mic';const time=document.createElement('small');time.textContent=`${row.start_time} · ${row.venue}`;const name=document.createElement('span');name.textContent=row.name;const claim=document.createElement('span');claim.className='claim-status'+(row.claimed||row.venue_confirmed?' is-claimed':'');claim.textContent=claimLabel(row);{const badge=document.createElement('span');badge.className=isBiweekly(row)?'biweekly-badge':'schedule-badge';badge.textContent=scheduleLabel(row);name.append(badge);}name.append(claim);item.append(time,name);item.onclick=()=>this.onSelect(row.id);this.popup.append(item);}
    if(pages>1){const pager=document.createElement('div');pager.className='popup-pages';const prev=document.createElement('button'),next=document.createElement('button'),label=document.createElement('span');prev.className=next.className='compact-button';prev.textContent='‹';next.textContent='›';prev.setAttribute('aria-label','Previous nearby mics');next.setAttribute('aria-label','Next nearby mics');prev.disabled=page===0;next.disabled=page===pages-1;prev.onclick=()=>this.show(rows,x,y,page-1);next.onclick=()=>this.show(rows,x,y,page+1);label.textContent=`${page+1}/${pages}`;pager.append(prev,label,next);this.popup.append(pager);}
    this.popup.hidden=false;
    this.popup.style.left=`${Math.max(6,Math.min(this.root.clientWidth-this.popup.offsetWidth-6,x-124))}px`;
    this.popup.style.top=`${Math.max(6,Math.min(this.root.clientHeight-this.popup.offsetHeight-6,y-this.popup.offsetHeight-8))}px`;
  }
}
window.MicMap=MicMap;
