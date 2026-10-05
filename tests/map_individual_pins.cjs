const fs=require('fs'),vm=require('vm'),assert=require('assert');
function element(){const classes=new Set();return {style:{},dataset:{},children:[],classList:{toggle(k,v){if(v)classes.add(k);else classes.delete(k);},contains:k=>classes.has(k)},setAttribute(k,v){this[k]=v;},addEventListener(k,fn){this[k]=fn;},append(x){this.children.push(x);},replaceChildren(){this.children=[];},remove(){}};}
const context=vm.createContext({window:{},document:{createElement:element},Image:element,isBiweekly:r=>r.frequency==='biweekly'});
vm.runInContext(fs.readFileSync('assets/map.js','utf8'),context);
const rows=[{id:'a',name:'A',venue:'Room',latitude:1,longitude:1,claimed:true},{id:'b',name:'B',venue:'Room',latitude:1,longitude:1,frequency:'biweekly'},{id:'c',name:'C',venue:'Next door',latitude:2,longitude:2}];
const selected=[],map={root:{clientWidth:400,clientHeight:400},initialFit:true,zoom:16,center:[0,0],project:(a,b)=>[a,b],tiles:new Map(),plane:element(),pins:element(),rows,onSelect:id=>selected.push(id)};
context.map=map;vm.runInContext('MicMap.prototype.draw.call(map)',context);
assert.equal(map.pins.children.length,3);
map.pins.children.forEach((pin,i)=>{assert.equal(pin.dataset.micId,rows[i].id);assert(!pin.innerHTML.includes('<text'));assert(!pin.className.includes('cluster'));assert.equal(pin.style.left,`${200+rows[i].latitude}px`);assert.equal(pin.style.top,`${200+rows[i].longitude}px`);pin.click();});
assert.deepEqual(selected,['a','b','c']);
assert(map.pins.children[0].classList.contains('confirmed-pin'));
assert(map.pins.children[1].classList.contains('biweekly-pin'));
console.log('Individual pins preserve exact coordinates, colors, and per-mic clicks; no numbers or clusters');
