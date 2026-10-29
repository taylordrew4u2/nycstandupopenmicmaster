const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('assets/app.js','utf8').split("document.addEventListener(")[0];
const nodes=Object.fromEntries(['#preview-banner','#last-sync','#cards','#directory-status'].map(id=>[id,{hidden:true,textContent:'',innerHTML:''}]));
const calls=[],responses=[];
const ctx=vm.createContext({
  window:{},document:{hidden:false,querySelector:id=>nodes[id]},
  location:{pathname:'/'},localStorage:{removeItem(){}},Intl,Date,URL,console,
  fetch:async(path,options)=>{
    calls.push({path,options});const result=responses.shift();
    if(result instanceof Error)throw result;
    return {ok:true,json:async()=>result};
  },
});
vm.runInContext(source,ctx);
const run=s=>vm.runInContext(s,ctx);
run('map={setRows(){}};let renders=0;renderDirectory=()=>{renders++;showPublicStatus();}');
const first={revision:'one',snapshot_at:1793292000,last_sync_at:1793291900,stale:false,
  listings:[{id:'visible',name:'A public mic',claimed:true},{id:'to-hide',name:'Another public mic'}]};
const meta=payload=>({revision:payload.revision,snapshot_at:payload.snapshot_at,last_sync_at:payload.last_sync_at,stale:payload.stale});
const clone=value=>JSON.parse(JSON.stringify(value));

(async()=>{
  responses.push(clone(first));await run('loadPublic()');
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/public');
  assert.equal(calls[0].options.cache,'no-store');
  assert.equal(run('state.publicLoaded'),true);assert.equal(run('renders'),1);
  assert.equal(nodes['#preview-banner'].hidden,true);

  run("Object.assign(state,{search:'public',borough:'Queens',date:'2026-11-01',page:3})");
  responses.push(meta(first));await run('refreshPublicRevision()');
  assert.equal(calls.at(-1).path,'/api/public/revision');assert.equal(calls.length,2);
  assert.equal(run('renders'),1,'An unchanged revision must preserve the rendered map/list');
  assert.equal(run('state.page'),3);assert.equal(run('state.search'),'public');
  assert.equal(run('state.borough'),'Queens');assert.equal(run('state.date'),'2026-11-01');

  // A new public snapshot removes a hidden row and removes a revoked claim badge.
  const changed={...first,revision:'two',snapshot_at:first.snapshot_at+60,listings:[{...first.listings[0],claimed:false}]};
  responses.push(meta(changed),clone(changed));await run('refreshPublicRevision()');
  assert.deepEqual(calls.slice(-2).map(call=>call.path),['/api/public/revision','/api/public']);
  assert.equal(run('state.data.listings.length'),1);assert.equal(run('state.data.listings[0].claimed'),false);
  assert.equal(run('renders'),2);assert.equal(run('state.search'),'public');

  responses.push({...meta(changed),stale:true});await run('refreshPublicRevision()');
  assert.equal(calls.at(-1).path,'/api/public/revision');assert.equal(run('renders'),2);
  assert.equal(nodes['#preview-banner'].hidden,false);
  assert.match(nodes['#preview-banner'].textContent,/saved directory from Oct 29, 2026/);
  assert.match(nodes['#preview-banner'].textContent,/Updates are temporarily unavailable/);

  responses.push(new Error('Network unavailable'));await run('refreshPublicRevision()');
  assert.equal(run('state.data.listings[0].id'),'visible');assert.equal(run('renders'),2);
  assert.equal(nodes['#preview-banner'].hidden,false);

  responses.push(meta(changed));await run('refreshPublicRevision()');
  assert.equal(nodes['#preview-banner'].hidden,true,'A successful current revision clears the failure notice');

  responses.push(new Error('Snapshot request unavailable'));await run('loadPublic()');
  assert.equal(run('state.data.listings[0].id'),'visible');assert.equal(run('renders'),2);
  assert.equal(nodes['#preview-banner'].hidden,false,'A failed reload retains the last successful public directory');

  // Empty is a valid saved directory; it must not turn into an error screen.
  responses.push({...changed,revision:'empty',listings:[]});await run('loadPublic()');
  responses.push(new Error('Offline'));await run('refreshPublicRevision()');
  assert.equal(run('state.data.listings.length'),0);assert.equal(run('renders'),3);
  assert.equal(nodes['#preview-banner'].hidden,false);

  let resolve;responses.push(new Promise(done=>resolve=done));
  const before=calls.length,pending=run('refreshPublicRevision()');
  await run('refreshPublicRevision()');assert.equal(calls.length,before+1,'Overlapping revision polls are coalesced');
  resolve({...meta(changed),revision:'empty'});await pending;

  for(const condition of ['document.hidden=true',"location.pathname='/owner'",'state.demo=true']){
    run(condition);const count=calls.length;await run('refreshPublicRevision()');assert.equal(calls.length,count);
    run("document.hidden=false;location.pathname='/';state.demo=false");
  }

  // Private API calls use no-store as well; no data is saved to browser storage.
  responses.push({authenticated:true});await run("api('/api/owner/me')");
  assert.equal(calls.at(-1).options.cache,'no-store');
  assert.equal(calls.at(-1).options.credentials,'same-origin');

  // Switching into the demo while a public fetch is in flight cannot relabel
  // real listings as fictional, or publish fictional rows after leaving it.
  let finishLoad;responses.push(new Promise(done=>finishLoad=done));
  const loading=run('loadPublic()');
  run('state.demo=true');await run('loadPublic()');
  assert.equal(run('state.data.mode'),'demo');
  finishLoad(clone(first));await loading;
  assert.equal(run('state.data.mode'),'demo');
  run('state.demo=false');responses.push(new Error('Offline'));await run('loadPublic()');
  assert.equal(run('state.data.mode'),'empty');
  assert.equal(run('state.data.listings.length'),0);
  assert.equal(nodes['#directory-status'].textContent,'Directory unavailable');
  assert.equal(nodes['#preview-banner'].hidden,true);
  console.log('Public revision polling, changed/hidden/revoked rows, dated fallback, empty snapshots, recovery and private no-store passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
