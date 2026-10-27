const fs=require('fs'),vm=require('vm'),assert=require('assert');
function element(){
  const classes=new Set();
  return {style:{},dataset:{},children:[],handlers:{},hidden:false,
    classList:{toggle(k,v){if(v)classes.add(k);else classes.delete(k);},contains:k=>classes.has(k)},
    setAttribute(k,v){this[k]=v;},addEventListener(k,fn){this.handlers[k]=fn;},
    append(x){this.children.push(x);},replaceChildren(){this.children=[];},remove(){}};
}
const elements={'.tile-plane':element(),'.pin-plane':element(),'.map-popup':element(),'.map-offline':element()};
const root=Object.assign(element(),{clientWidth:400,clientHeight:300,clientLeft:0,clientTop:0,
  querySelector:key=>elements[key],querySelectorAll:()=>[],
  getBoundingClientRect:()=>({left:0,top:0}),setPointerCapture(){}});
let resize,frame;
const context=vm.createContext({window:{},document:{createElement:element},Image:element,
  ResizeObserver:class{constructor(callback){resize=callback;}observe(){}},
  requestAnimationFrame:callback=>(frame=callback,1),cancelAnimationFrame:()=>{frame=null;},
  performance:{now:()=>1000},matchMedia:()=>({matches:false}),navigator:{},
  claimLabel:()=>'',scheduleLabel:()=>'',isBiweekly:()=>false});
vm.runInContext(fs.readFileSync('assets/map.js','utf8'),context);
const notifications=[],map=new context.window.MicMap(root,()=>{},instance=>notifications.push(instance.visibleRows().map(r=>r.id)));
const rows=[
  {id:'center',name:'Center',venue:'Room',latitude:40.705,longitude:-73.975},
  {id:'north',name:'North',venue:'Room',latitude:40.86,longitude:-73.83},
  {id:'south',name:'South',venue:'Room',latitude:40.555,longitude:-74.16},
  {id:'unmapped',name:'Unmapped',venue:'Room',latitude:null,longitude:null}
];
map.setRows(rows);
assert.deepEqual(Array.from(map.visibleRows(),r=>r.id),['center','north','south']);
assert.equal(notifications.length,0,'Initial data and unchanged viewport must not notify');

map.zoomAt(17,200,150);
assert.deepEqual(Array.from(map.visibleRows(),r=>r.id),['center']);
assert.equal(notifications.length,1);
map.setRows(rows);
map.draw();
assert.equal(notifications.length,1,'Drawing a row change must not create a viewport callback loop');
assert.equal(map.rows.length,4,'Viewport filtering must preserve rows so panning can reveal them');

assert(map.focus('north'));
assert.deepEqual(Array.from(map.visibleRows(),r=>r.id),['north']);
assert.equal(notifications.length,2);
assert.equal(map.popup.hidden,false);
resize();
assert.equal(map.popup.hidden,false,'An already drawn viewport must keep its newly focused popup');
assert.equal(notifications.length,2,'A redundant resize notification must not redraw the viewport');
map.reset();
assert.deepEqual(Array.from(map.visibleRows(),r=>r.id),['center','north','south']);
assert.equal(notifications.length,3);
root.clientWidth=350;
map.popup.hidden=false;
resize();
assert.equal(map.popup.hidden,true,'A real size change must dismiss the popup before redrawing');
assert.equal(notifications.length,4,'Resizing the map must update the list viewport');

// A pointer drag schedules a draw, and the resulting viewport notifies once.
const target={closest:()=>null};
root.handlers.pointerdown({target,button:0,pointerId:1,clientX:100,clientY:100});
root.handlers.pointermove({target,pointerId:1,clientX:200,clientY:100});
assert(frame);
frame();frame=null;
assert.equal(notifications.length,5,'Panning must update the list viewport');
root.handlers.pointerup({target,pointerId:1,type:'pointerup',pointerType:'mouse'});
assert.equal(notifications.length,5);

// Exact geographic edges are included; offscreen and invalid anchors are not.
const boundsMap={root:{clientWidth:100,clientHeight:80},center:[0,0],project:(x,y)=>[x,y]};
const edgeRows=[[-50,0],[50,0],[0,-40],[0,40],[-50.01,0],[0,40.01],[NaN,0]]
  .map(([latitude,longitude],id)=>({id,latitude,longitude}));
assert.deepEqual(Array.from(context.window.MicMap.prototype.visibleRows.call(boundsMap,edgeRows),r=>r.id),[0,1,2,3]);
root.clientHeight=0;
assert.equal(map.visibleRows().length,0);
console.log('Viewport bounds track zoom, focus, reset, resize and pan while row updates stay quiet');
