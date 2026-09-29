/* Small, dependency-free Web Mercator map. Only visible OSM tiles are fetched.
   No bulk downloads, offline prefetch, or hidden attribution. */
'use strict';
class MicMap {
  constructor(root, onSelect) {
    this.root = root; this.onSelect = onSelect; this.zoom = 10;
    this.center = [40.705, -73.975]; this.initialFit = false; this.rows = []; this.tiles = new Map();
    this.plane = root.querySelector('.tile-plane'); this.pins = root.querySelector('.pin-plane');
    this.popup = root.querySelector('.map-popup'); this.loaded = 0; this.failed = 0;
    root.querySelectorAll('[data-map]').forEach(b => b.addEventListener('click', () => {
      this.zoom = Math.max(8, Math.min(17, this.zoom + (b.dataset.map === 'in' ? 1 : -1))); this.hide(); this.draw();
    }));
    let drag = null;
    root.addEventListener('pointerdown', e => {
      if (e.target.closest('button,a,.map-popup') || e.button !== 0) return;
      drag = {x:e.clientX,y:e.clientY,center:this.project(...this.center),moved:false};
      root.setPointerCapture(e.pointerId);
    });
    root.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
      if (Math.abs(dx)+Math.abs(dy)>4) drag.moved=true;
      this.center=this.unproject(drag.center[0]-dx,drag.center[1]-dy); this.hide(); this.draw();
    });
    root.addEventListener('pointerup', () => {drag=null;});
    root.addEventListener('pointercancel', () => {drag=null;});
    this.observer=new ResizeObserver(()=>{this.hide();this.draw();}); this.observer.observe(root);
    this.draw();
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
    this.center=[row.latitude,row.longitude];this.zoom=Math.max(this.zoom,13);this.initialFit=true;
    this.hide();this.draw();
    this.show([row],this.root.clientWidth/2,this.root.clientHeight/2);
    return true;
  }
  draw(){
    const w=this.root.clientWidth,h=this.root.clientHeight;if(!w||!h)return;
    if(!this.initialFit)this.fitCity();
    const [cx,cy]=this.project(...this.center),left=cx-w/2,top=cy-h/2;
    const minX=Math.floor(left/256),minY=Math.floor(top/256),maxX=Math.floor((left+w)/256),maxY=Math.floor((top+h)/256),keep=new Set();
    for(let x=minX;x<=maxX;x++)for(let y=minY;y<=maxY;y++){
      if(y<0||y>=2**this.zoom)continue;
      const id=`${this.zoom}/${x}/${y}`;keep.add(id);let tile=this.tiles.get(id);
      if(!tile){tile=new Image();tile.alt='';tile.draggable=false;tile.referrerPolicy='strict-origin-when-cross-origin';tile.className='map-tile';tile.onload=()=>{tile.style.visibility='visible';this.loaded++;this.root.querySelector('.map-offline').hidden=true;};tile.onerror=()=>{tile.style.visibility='hidden';this.failed++;if(this.loaded===0)this.root.querySelector('.map-offline').hidden=false;};tile.src=`https://tile.openstreetmap.org/${id}.png`;this.tiles.set(id,tile);this.plane.append(tile);}
      tile.style.transform=`translate(${Math.round(x*256-left)}px,${Math.round(y*256-top)}px)`;
    }
    for(const [id,tile]of this.tiles)if(!keep.has(id)){tile.remove();this.tiles.delete(id);}
    this.pins.replaceChildren();
    if(this.zoom<=11)for(const [name,lat,lng]of [['Manhattan',40.795,-73.988],['Brooklyn',40.635,-73.95],['Queens',40.735,-73.78],['Bronx',40.87,-73.86],['Staten Island',40.555,-74.16]]){const [x,y]=this.project(lat,lng);const label=document.createElement('span');label.className='map-borough';label.textContent=name;label.style.left=`${x-left}px`;label.style.top=`${y-top}px`;this.pins.append(label);}
    const groups=new Map();
    for(const row of this.rows){if(!Number.isFinite(row.latitude)||!Number.isFinite(row.longitude))continue;const key=`${row.latitude.toFixed(5)}|${row.longitude.toFixed(5)}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
    const clusters=[];
    for(const rows of groups.values()){
      const row=rows[0],[px,py]=this.project(row.latitude,row.longitude),x=px-left,y=py-top;
      if(x<-40||y<-40||x>w+40||y>h+40)continue;
      const nearby=clusters.find(c=>Math.abs(c.x-x)<42&&Math.abs(c.y-y)<44);
      if(nearby){const total=nearby.rows.length+rows.length;nearby.x=(nearby.x*nearby.rows.length+x*rows.length)/total;nearby.y=(nearby.y*nearby.rows.length+y*rows.length)/total;nearby.rows.push(...rows);}
      else clusters.push({x,y,rows:[...rows]});
    }
    for(const {x,y,rows} of clusters){
      const button=document.createElement('button');button.type='button';button.className='mic-pin';button.dataset.count=rows.length;button.textContent=rows.length>1?rows.length:'•';button.style.left=`${x}px`;button.style.top=`${y}px`;button.setAttribute('aria-label',rows.length>1?`${rows.length} nearby mics`:`Mic at ${rows[0].venue}`);button.title=rows.map(r=>r.name).join(', ');button.addEventListener('click',()=>this.show(rows,x,y));this.pins.append(button);
    }
  }
  show(rows,x,y,page=0){
    this.popup.replaceChildren();const close=document.createElement('button');close.className='popup-close';close.textContent='×';close.setAttribute('aria-label','Close map popup');close.onclick=()=>this.hide();this.popup.append(close);
    const title=document.createElement('strong');title.textContent=new Set(rows.map(r=>r.venue)).size>1?'Mics in this area':rows[0].venue;this.popup.append(title);
    const size=Math.max(1,Math.min(3,Math.floor((this.root.clientHeight-90)/44))),pages=Math.ceil(rows.length/size);
    page=Math.max(0,Math.min(page,pages-1));
    for(const row of rows.slice(page*size,(page+1)*size)){const item=document.createElement('button');item.className='popup-mic';const time=document.createElement('small');time.textContent=`${row.start_time} · ${row.venue}`;const name=document.createElement('span');name.textContent=row.name;item.append(time,name);item.onclick=()=>this.onSelect(row.id);this.popup.append(item);}
    if(pages>1){const pager=document.createElement('div');pager.className='popup-pages';const prev=document.createElement('button'),next=document.createElement('button'),label=document.createElement('span');prev.className=next.className='compact-button';prev.textContent='‹';next.textContent='›';prev.setAttribute('aria-label','Previous nearby mics');next.setAttribute('aria-label','Next nearby mics');prev.disabled=page===0;next.disabled=page===pages-1;prev.onclick=()=>this.show(rows,x,y,page-1);next.onclick=()=>this.show(rows,x,y,page+1);label.textContent=`${page+1}/${pages}`;pager.append(prev,label,next);this.popup.append(pager);}
    this.popup.hidden=false;
    this.popup.style.left=`${Math.max(6,Math.min(this.root.clientWidth-this.popup.offsetWidth-6,x-124))}px`;
    this.popup.style.top=`${Math.max(6,Math.min(this.root.clientHeight-this.popup.offsetHeight-6,y-this.popup.offsetHeight-8))}px`;
  }
}
window.MicMap=MicMap;
