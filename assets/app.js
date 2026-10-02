/* MIC LIST — public directory, source dashboard, moderated ownership. */
'use strict';
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const BOROUGHS=['Manhattan','Brooklyn','Queens','Bronx','Staten Island'];
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=s=>{try{const u=new URL(s);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}};
const external=(url,label,cls='')=>safeUrl(url)?`<a class="${cls}" href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:esc(label);
const nyToday=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const weekday=date=>(new Date(date+'T12:00:00Z').getUTCDay()+6)%7;
const timeText=t=>{if(!t)return 'Not listed';const [h,m]=t.split(':').map(Number);return `${h%12||12}:${String(m).padStart(2,'0')} ${h>=12?'PM':'AM'}`;};
const ago=n=>{if(!n)return 'Never';const d=Math.max(0,Date.now()/1000-n);return d<60?'Just now':d<3600?`${Math.floor(d/60)}m ago`:d<86400?`${Math.floor(d/3600)}h ago`:`${Math.floor(d/86400)}d ago`;};
const dateText=d=>new Date(d+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
try{localStorage.removeItem('miclist-saved');}catch{}
const state={data:{listings:[],source_count:0,mode:'empty'},demo:!!window.MICLIST_PREVIEW,date:nyToday(),day:'all',borough:'all',search:'',cost:'all',signup:'all',claimStatus:'all',hideOffWeek:false,timeFrom:'',timeTo:'',sort:'time',adminTab:'sources',admin:null,community:null,owner:null,preview:null};
let map;
state.page=0;state.pageSize=1;state.resultKey='';
async function api(path,options={}){
  if(window.MICLIST_PREVIEW)throw Error('This file is a read-only interface preview. Run the included website to use the backend.');
  const opt={credentials:'same-origin',...options,headers:{'X-Requested-With':'MicList',...(options.headers||{})}};
  if(opt.body&&!(opt.body instanceof FormData)){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body);}
  const response=await fetch(path,opt);let data;try{data=await response.json();}catch{throw Error('The server returned an unreadable response.');}
  if(!response.ok){const msg=typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(r=>r.msg).join('; '):'Request failed';throw Object.assign(Error(msg),{status:response.status});}return data;
}
let toastTimer;
function toast(message){const t=$('#toast');t.textContent=message;t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,6500);}
function modal(title,body,foot='',eyebrow='NYC Open Mic Master List'){
  $('#modal').removeAttribute('data-view');
  $('#modal-content').innerHTML=`<div class="modal-head"><div><div class="eyebrow muted">${esc(eyebrow)}</div><h2 id="modal-title">${esc(title)}</h2></div><button class="close-button" data-action="close" aria-label="Close dialog">×</button></div><div class="modal-body">${body}</div>${foot?`<div class="modal-foot">${foot}</div>`:''}`;
  if(!$('#modal').open)$('#modal').showModal();
}
function errorAt(form,err){let el=$('.form-error',form);if(!el){el=document.createElement('div');el.className='form-error';el.setAttribute('role','alert');form.prepend(el);}el.textContent=err.message;el.tabIndex=-1;el.focus({preventScroll:true});}
function button(text,action,id='',cls='quiet'){return `<button class="button ${cls}" data-action="${action}" data-id="${esc(id)}">${text}</button>`;}
function blank(title,description,actions=''){return `<div class="blank-state"><h3>${esc(title)}</h3><p>${description}</p>${actions}</div>`;}
function field(label,name,value='',type='text',required=false,helper=''){
  return `<div class="field"><label for="f-${name}">${esc(label)}</label><input id="f-${name}" name="${name}" type="${type}" value="${esc(value)}" ${required?'required':''} ${type==='password'?'autocomplete="current-password"':''} ${type==='number'?'step="any"':''}><div class="form-helper">${helper}</div></div>`;
}
function selectField(label,name,values,chosen=''){
  return `<div class="field"><label for="f-${name}">${esc(label)}</label><select name="${name}" id="f-${name}">${values.map(v=>{const [value,text]=Array.isArray(v)?v:[v,v];return `<option value="${esc(value)}" ${String(value)===String(chosen)?'selected':''}>${esc(text)}</option>`;}).join('')}</select></div>`;
}
function textField(label,name,value='',required=false){return `<div class="field"><label for="f-${name}">${esc(label)}</label><textarea name="${name}" id="f-${name}" maxlength="3000" ${required?'required minlength="10"':''}>${esc(value)}</textarea></div>`;}
function demoData(){
  const rooms=[['Manhattan','Example Midtown Room',40.754,-73.989],['Brooklyn','Example Bushwick Room',40.696,-73.928],['Queens','Example Ridgewood Room',40.704,-73.904],['Bronx','Example Bronx Room',40.828,-73.921],['Staten Island','Example North Shore Room',40.640,-74.079]];
  const rows=[];DAYS.forEach((day,i)=>{for(let j=0;j<3;j++){const [borough,venue,latitude,longitude]=rooms[(i+j)%5];rows.push({id:`demo-${i}-${j}`,name:[`The ${day} Five`,`New Material ${day}`,`Late Set ${day}`][j],venue,address:'Illustrative location — not a real mic listing',borough,neighborhood:'',weekday:i,date:null,start_time:['17:00','19:00','21:30'][j],signup_time:['16:45','18:30','21:00'][j],cost:j===0?0:5,cost_text:j===0?'Free':'$5',purchase_minimum:j===1?'One item':'Not listed',set_minutes:5,signup_method:j===1?'Online signup':'In person',signup_url:'',notes:'Fictional sample data for testing the interface. This mic is not a real event.',status:'scheduled',excluded_dates:[],latitude,longitude,map_status:'demo',sources:[{name:'Interface example',url:'',kind:'upload',checked_at:null}],checked_at:null,updated_at:null,conflicts:[],stale:false,claimed:false,curated:false});}});
  return {listings:rows,source_count:0,mode:'demo'};
}
function showLastSync(value){
  const label=$('#last-sync');if(!label)return;
  const stamp=value?new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZone:'America/New_York',timeZoneName:'short'}).format(new Date(value*1000)):'—';
  label.textContent='LAST SYNC AT '+stamp;label.title=value?'Latest successful online source check':'No successful online source check recorded';
}
async function loadSyncStatus(){try{const status=await api('/api/sync-status');showLastSync(status.last_sync_at);}catch{if($('#last-sync'))$('#last-sync').textContent='LAST SYNC AT unavailable';}}
async function loadPublic(){
  if(!map)map=new MicMap($('#nyc-map'),id=>details(id));
  if(!state.data.listings.length&&!state.demo)$('#cards').innerHTML='<p class="directory-loading" role="status">Loading mics…</p>';
  if(state.demo){state.data=demoData();renderDirectory();return;}
  try{state.data=await api('/api/public');renderDirectory();}catch(err){$('#directory-status').textContent='Directory unavailable';$('#cards').innerHTML=blank('Could not load the directory.',esc(err.message),button('Try again','refresh'));}
}
const validDate=d=>typeof d==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(d)&&!Number.isNaN(Date.parse(d+'T12:00:00Z'))&&new Date(d+'T12:00:00Z').toISOString().slice(0,10)===d;
const generatedSourceDate=row=>row.frequency!=='one-time'&&(row.sources||[]).some(s=>s.url==='https://comediq.us/mics.json')&&!row.overridden_fields?.includes('date');
function occursOnDate(row,date){
  if(!validDate(date))return false;
  if((row.excluded_dates||[]).includes(date))return false;
  const generatedDate=generatedSourceDate(row);
  if(row.date&&!generatedDate)return row.date===date;
  if(!Number.isInteger(row.weekday)||row.weekday!==weekday(date))return false;
  if(isBiweekly(row)&&row.recurrence_anchor){const diff=Math.round((new Date(date+'T12:00:00Z')-new Date(row.recurrence_anchor+'T12:00:00Z'))/86400000);return diff>=0&&diff%14===0;}
  // Unknown alternate-week anchors stay visible rather than silently dropping a mic.
  return true;
}
let calendarMonth='';
function renderCalendar(offset=0){
  const month=new Date((calendarMonth||state.date||nyToday()).slice(0,7)+'-01T12:00:00Z');month.setUTCMonth(month.getUTCMonth()+offset);calendarMonth=month.toISOString().slice(0,10);
  const year=month.getUTCFullYear(),m=month.getUTCMonth(),count=new Date(Date.UTC(year,m+1,0)).getUTCDate(),start=(month.getUTCDay()+6)%7;
  let cells=DAYS.map(d=>`<span class="calendar-weekday">${d.slice(0,3)}</span>`).join('')+'<span></span>'.repeat(start);
  for(let day=1;day<=count;day++){const date=`${year}-${String(m+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;cells+=`<button class="calendar-day ${date===state.date?'selected':''}" data-action="calendar-date" data-id="${date}" aria-label="View mics for ${date}" ${date===nyToday()?'aria-current="date"':''}>${day}</button>`;}
  modal('Calendar',`<div class="calendar-heading">${button('←','calendar-prev')}<strong>${month.toLocaleDateString('en-US',{month:'long',year:'numeric',timeZone:'UTC'})}</strong>${button('→','calendar-next')}</div><div class="calendar-grid">${cells}</div><p class="form-helper">Choose a date to view mics. Biweekly mics with unconfirmed dates remain visible; check their details.</p>`,button('Today','calendar-date',nyToday())+button('All dates','calendar-clear'));
}

function occurs(row){
  if(state.date)return occursOnDate(row,state.date);
  const dated=row.date&&!generatedSourceDate(row);
  if(dated&&row.date<nyToday())return false;
  if(state.day==='all')return true;
  return dated?weekday(row.date)===Number(state.day):row.weekday===Number(state.day);
}
function nextDate(row){
  if(state.date)return state.date;
  if(row.date&&!generatedSourceDate(row))return row.date;
  if(!Number.isInteger(row.weekday))return '9999-12-31';
  const today=nyToday(),d=new Date(today+'T12:00:00Z');
  d.setUTCDate(d.getUTCDate()+(Number(row.weekday)-weekday(today)+7)%7);
  for(let i=0;i<53;i++){const date=d.toISOString().slice(0,10);if(occursOnDate(row,date))return date;d.setUTCDate(d.getUTCDate()+7);}
  return '9999-12-31';
}
const claimLabel=r=>r.claimed?'Host claimed':r.venue_confirmed?'Venue confirmed':'Unclaimed';
const claimBadge=r=>`<span class="claim-status ${r.claimed||r.venue_confirmed?'is-claimed':''}" title="${r.claimed?'An approved mic owner can update this listing.':r.venue_confirmed?'Listed on an official venue website. A host can still claim this mic.':'No approved host has claimed this mic.'}">${claimLabel(r)}</span>`;
function scheduleLabel(r){if(r.frequency==='one-time')return 'Pop-up mic (not recurring)';if(isBiweekly(r))return 'Biweekly';if(r.frequency==='weekly'||generatedSourceDate(r)||/\b(?:weekly|every week)\b/i.test(r.notes||'')||(!r.date&&Number.isInteger(r.weekday)))return 'Weekly';return 'Schedule unconfirmed';}
const scheduleBadge=r=>`<span class="${isBiweekly(r)?'biweekly-badge':'schedule-badge'}">${scheduleLabel(r)}</span>`;
const micTitle=r=>r.name;
const updatedAt=r=>Math.max(r.updated_at||0,r.curated_at||0);
const updatedText=r=>updatedAt(r)?new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZone:'America/New_York'}).format(new Date(updatedAt(r)*1000)):'not recorded';
const micLink=id=>new URL('/?mic='+encodeURIComponent(id),location.origin==='null'?'https://nycopenmicmasterlist.com':location.origin).href;
function withinTime(time,from,to){if(!from&&!to)return true;if(!time)return false;return from&&to&&from>to?time>=from||time<=to:(!from||time>=from)&&(!to||time<=to);}
function isBiweekly(r){
  return r.frequency==='biweekly'||(!r.frequency&&/\b(?:bi[ -]?weekly|every other (?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b/i.test([r.name,r.notes].join(' ')));
}
function biweeklyThisWeek(r){
  const today=nyToday(),monday=new Date(today+'T12:00:00Z');
  monday.setUTCDate(monday.getUTCDate()-weekday(today));
  const start=monday.toISOString().slice(0,10);
  monday.setUTCDate(monday.getUTCDate()+7);
  const end=monday.toISOString().slice(0,10);
  // Comediq weekly dates are generated, not confirmation of alternate-week dates.
  const dated=r.date&&(!(r.sources||[]).some(s=>s.url==='https://comediq.us/mics.json')||r.overridden_fields?.includes('date'));
  if(dated)return r.date>=start&&r.date<end&&!(r.excluded_dates||[]).includes(r.date);
  if(r.recurrence_anchor){
    const anchor=new Date(r.recurrence_anchor+'T12:00:00Z');
    const target=new Date(start+'T12:00:00Z');
    // Convert Sunday-based JS weekday to Monday-based calendar position.
    target.setTime(new Date(start+'T12:00:00Z').getTime()+((anchor.getUTCDay()+6)%7)*86400000);
    const days=Math.round((target-anchor)/86400000);
    return days>=0&&days%14===0&&!(r.excluded_dates||[]).includes(target.toISOString().slice(0,10));
  }
  return null;
}
function recurrenceFields(r){
  return '<div class="form-row">'+selectField('Schedule','frequency',[['weekly','Weekly'],['biweekly','Biweekly (every other week)'],['one-time','Pop-up mic (not recurring)']],r.frequency||(isBiweekly(r)?'biweekly':r.date&&!generatedSourceDate(r)?'one-time':'weekly'))+field('Confirmed biweekly date','recurrence_anchor',r.recurrence_anchor||'','date',false,'A date this mic runs, to establish alternate weeks.')+'</div>';
}
function syncScheduleForm(form,changed=false){
  if(!form)return;const frequency=$('[name=frequency]',form)?.value;if(!frequency)return;
  const date=$('[name=date]',form),day=$('[name=weekday]',form),anchor=$('[name=recurrence_anchor]',form);
  if(date){date.closest('.field').hidden=frequency!=='one-time';date.required=frequency==='one-time';if(changed&&frequency!=='one-time')date.value='';}
  if(day)day.closest('.field').hidden=frequency==='one-time';
  if(anchor){anchor.closest('.field').hidden=frequency!=='biweekly';anchor.required=form.id==='mic-proposal-form'&&frequency==='biweekly';if(changed&&frequency!=='biweekly')anchor.value='';}
}
function filtered(){return state.data.listings.filter(r=>{
  if(!state.date&&state.hideOffWeek&&isBiweekly(r)&&biweeklyThisWeek(r)===false)return false;
  if(state.claimStatus==='venue'&&!r.venue_confirmed)return false;if(state.claimStatus==='claimed'&&!r.claimed)return false;if(state.claimStatus==='unclaimed'&&r.claimed)return false;
  if(!occurs(r))return false;if(state.borough!=='all'&&r.borough!==state.borough)return false;
  if(!withinTime(r.start_time,state.timeFrom,state.timeTo))return false;
  if(state.search&&!`${r.name} ${r.venue} ${r.address} ${r.borough} ${r.neighborhood} ${DAYS[r.weekday]||''} ${r.date||''} ${r.date?dateText(r.date):''}`.toLowerCase().includes(state.search))return false;
  if(state.cost==='free'&&r.cost!==0)return false;if(['5','10'].includes(state.cost)&&(r.cost===null||r.cost>Number(state.cost)))return false;
  if(state.signup!=='all'&&!String(r.signup_method).toLowerCase().includes(state.signup))return false;return true;
}).sort((a,b)=>state.sort==='name'?micTitle(a).localeCompare(micTitle(b)):state.sort==='price'?(a.cost??999)-(b.cost??999):(nextDate(a)+a.start_time).localeCompare(nextDate(b)+b.start_time));}
function renderDirectory(){
  showLastSync(state.demo?null:state.data.last_sync_at);
  $('#preview-banner').hidden=!state.demo;
  $('#preview-banner').innerHTML=`Demo · Fictional mics${window.MICLIST_PREVIEW?'':' <button data-action="exit-demo">Exit demo</button>'}`;
  $('#directory-status').textContent=state.demo?'Fictional examples':`${state.data.listings.length} listings`;
  const dateOptions=Array.from({length:7},(_,i)=>{const d=new Date(nyToday()+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+i);const date=d.toISOString().slice(0,10);return [date,d.toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric',timeZone:'UTC'})+(i===0?' · Today':'')];});
  if(state.date&&!dateOptions.some(([v])=>v===state.date))dateOptions.unshift([state.date,new Date(state.date+'T12:00:00Z').toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric',timeZone:'UTC'})]);
  $('#day-filter').innerHTML=[['all','All days / dates'],...dateOptions,...DAYS.map((d,i)=>[i,'Every '+d]),['calendar','Pick a date…']].map(([v,d])=>`<option value="${v}" ${String(state.date||state.day)===String(v)?'selected':''}>${esc(d)}</option>`).join('');
  $('#borough-filter').value=state.borough;
  const active=state.cost!=='all'||state.signup!=='all'||state.claimStatus!=='all'||state.timeFrom||state.timeTo||state.hideOffWeek;
  $('#more-filters').classList.toggle('active',active);
  $('#more-filters').textContent=active?'Filters •':'Filters';
  renderCards();
  if(!map)map=new MicMap($('#nyc-map'),id=>details(id));
  map.setRows(filtered().filter(r=>r.status==='scheduled'&&Number.isFinite(r.latitude)&&Number.isFinite(r.longitude)));
}
function renderCards(){
  const rows=filtered(),box=$('#cards');
  const key=JSON.stringify([state.date,state.day,state.borough,state.search,state.cost,state.signup,state.claimStatus,state.timeFrom,state.timeTo,state.sort,state.hideOffWeek]);
  if(key!==state.resultKey){state.page=0;state.resultKey=key;}
  const rowHeight=parseFloat(getComputedStyle(box).getPropertyValue('--row-height'))||52;
  const size=Math.max(1,Math.floor(box.clientHeight/rowHeight));
  if(size!==state.pageSize){state.page=Math.floor(state.page*state.pageSize/size);state.pageSize=size;}
  const pages=Math.max(1,Math.ceil(rows.length/size));state.page=Math.max(0,Math.min(state.page,pages-1));
  const start=state.page*size;
  $('#result-count').textContent=`${rows.length} mic${rows.length===1?'':'s'}`;
  $('#page-count').textContent=`${state.page+1}/${pages}`;
  $('[data-action="previous-page"]').disabled=state.page===0;
  $('[data-action="next-page"]').disabled=state.page>=pages-1;
  $('.list-pagination').hidden=pages===1;
  box.innerHTML=rows.length?rows.slice(start,start+size).map(card).join(''):state.data.listings.length?blank('No matching mics.','',button('Reset filters','reset')):blank('No mics yet.','',`<button class="demo-toggle" data-action="demo">View demo</button>`);
}
function card(r){
  const cost=r.cost===0?'Free':r.cost!=null?`$${r.cost}`:r.cost_text?'See fee':'—';
  const located=r.status==='scheduled'&&Number.isFinite(r.latitude)&&Number.isFinite(r.longitude);
  const title=micTitle(r),venue=`${r.venue} · ${r.borough}`;
  return `<article class="mic-card ${isBiweekly(r)?'biweekly-mic':''}" data-mic="${esc(r.id)}"><div class="card-main"><div class="mic-time"><strong>${esc(timeText(r.start_time))}</strong><span class="mic-day">${r.status!=='scheduled'?esc(r.status):esc(isBiweekly(r)&&!r.date&&!r.recurrence_anchor?'Dates unconfirmed':new Date(nextDate(r)+'T12:00:00Z').toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',timeZone:'UTC'}))}</span></div><div class="mic-info"><div class="mic-heading"><button class="mic-title" title="${esc(title)}" data-action="details" data-id="${esc(r.id)}">${esc(title)}</button></div><div class="mic-meta">${located?`<button class="venue-line" title="${esc(venue)}" data-action="locate" data-id="${esc(r.id)}" aria-label="Show ${esc(venue)} on map">${esc(venue)}</button>`:`<div class="venue-line" title="${esc(venue)}">${esc(venue)}</div>`}${scheduleBadge(r)}${claimBadge(r)}</div><small class="row-updated" title="${updatedAt(r)?esc(new Date(updatedAt(r)*1000).toLocaleString()):'Not recorded'}">Updated ${esc(updatedText(r))}</small></div><div class="mic-cost" title="${esc(r.cost_text||'Entry fee not listed')}" aria-label="Entry fee: ${esc(r.cost_text||cost)}">${cost}</div></div></article>`;
}
function filtersDialog(){
  const option=(v,t,current)=>`<option value="${v}" ${current===v?'selected':''}>${t}</option>`;
  modal('Filters',`<div class="filter-options"><label>Entry fee<select id="cost">${[['all','Any price'],['free','Free'],['5','$5 or less'],['10','$10 or less']].map(([v,t])=>option(v,t,state.cost)).join('')}</select></label><label>Sort<select id="sort">${[['time','Date & time'],['price','Lowest fee'],['name','Mic name']].map(([v,t])=>option(v,t,state.sort)).join('')}</select></label><label>Host status<select id="claim-filter">${[['all','All mics'],['claimed','Host claimed'],['venue','Venue confirmed'],['unclaimed','Unclaimed (including venue confirmed)']].map(([v,t])=>option(v,t,state.claimStatus)).join('')}</select></label><label>Biweekly mics<select id="biweekly-filter">${option('all','Show all',state.hideOffWeek?'current':'all')}${option('current','Hide mics off this week',state.hideOffWeek?'current':'all')}</select><small>Purple = biweekly. Unconfirmed dates stay visible.</small></label><label>Start time from<input id="time-from" type="time" value="${esc(state.timeFrom)}"></label><label>Start time until<input id="time-to" type="time" value="${esc(state.timeTo)}"></label></div>`,button('Reset','reset')+button('Done','close','','primary'));
}
function findMic(id){return state.data.listings.find(r=>r.id===id)||state.community?.listings?.find(r=>r.id===id)||state.owner?.listings?.find(r=>r.id===id);}
function details(id,tab='mic',page=0){
  const r=findMic(id);if(!r)return;
  const item=(label,value)=>`<div class="detail-item"><small>${label}</small><strong>${esc(value)}</strong></div>`;
  const tabs=['mic','hosts','notes','sources','link'].map(t=>`<button class="compact-button ${tab===t?'active':''}" data-action="detail-tab" data-id="${esc(id)}" data-tab="${t}" aria-pressed="${tab===t}">${t[0].toUpperCase()+t.slice(1)}</button>`).join('');
  let body='';
  if(tab==='mic'){
    body=`<p class="detail-location">${esc(r.venue)} · ${esc(r.borough)}<br>${esc(r.address||'Address not listed')}</p><div class="detail-grid">${item('REPEATS',scheduleLabel(r)+(isBiweekly(r)?' · '+(biweeklyThisWeek(r)===null?'dates unconfirmed':biweeklyThisWeek(r)?'on this week':'off this week'):''))}${item('WHEN',`${isBiweekly(r)&&!r.date&&!r.recurrence_anchor?DAYS[r.weekday]+' · dates unconfirmed':new Date(nextDate(r)+'T12:00:00Z').toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric',timeZone:'UTC'})} · ${timeText(r.start_time)}`)}${item('SIGNUP',timeText(r.signup_time))}${item('ENTRY FEE',r.cost_text||'Not listed')}${item('STAGE TIME',r.set_minutes?`${r.set_minutes} minutes`:'Not listed')}${item('STATUS',r.status+' · '+claimLabel(r))}</div><div class="detail-actions">${r.signup_url?external(r.signup_url,/^https?:\/\/(www\.)?(badslava\.com|comediq\.us)\//i.test(r.signup_url)?'Source details':'Signup','button primary'):''}${r.address?external('https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(r.address+', '+r.borough+', NY'),'Directions','button quiet'):''}</div>`;
  }else if(tab==='hosts'){
    const links=String(r.host_socials||'').split(/\s+/).filter(Boolean);
    body=`<p>${esc(r.host_names||'Host not listed')}</p><div class="host-links">${links.map(u=>external(u,new URL(safeUrl(u)||'https://example.com').hostname.replace('www.',''),'button quiet')).join('')}</div><p class="form-helper">${r.claimed?'An approved host can update these details.':'Host details are from the source unless a host claims this mic.'}</p>`;
  }else if(tab==='link'){
    body=`<label for="mic-link">Link to this mic</label><input class="input-full" id="mic-link" readonly value="${esc(micLink(id))}"><div class="detail-actions">${button('Copy link','copy-mic-link',id)}</div>`;
  }else{
    const text=tab==='notes' ?[r.notes,r.purchase_minimum&&`Purchase minimum: ${r.purchase_minimum}`,r.excluded_dates?.length&&`Not running: ${r.excluded_dates.map(dateText).join(', ')}`,r.conflicts?.length&&`Sources disagree: ${r.conflicts.join(', ')}`,r.stale&&'Source needs a recheck.',r.curated&&'Edited by an administrator or approved host.'].filter(Boolean).join('\n\n')||'No additional notes.':null;
    const texts=tab==='notes'?[text]:(r.sources||[]).map(source=>[source.name,source.kind==='upload'?'Uploaded snapshot':`Checked ${ago(source.checked_at)}`,source.missing_count?'Missing from latest source.':'',source.values?Object.entries(source.values).map(([k,v])=>`${k}: ${v}`).join('\n'):''].filter(Boolean).join('\n'));
    const size=innerHeight<450?180:innerWidth<650?290:440,parts=[];
    (texts.length?texts:['No source listed.']).forEach((text,i)=>{
      let rest=text;while(rest.length){let n=rest.length<=size?rest.length:rest.lastIndexOf(' ',size);if(n<size/2)n=Math.min(size,rest.length);parts.push({text:rest.slice(0,n),source:tab==='sources'?r.sources[i]:null});rest=rest.slice(n).trimStart();}
    });
    page=Math.max(0,Math.min(page,parts.length-1));const part=parts[page];
    body=`<p class="text-page">${esc(part.text)}</p>${part.source?.url?external(part.source.url,'Open source','text-link'):''}${tab==='sources'?'<p class="form-helper">Check the original listing before going.</p>':''}${parts.length>1?`<div class="detail-pages"><button class="compact-button" data-action="detail-tab" data-id="${esc(id)}" data-tab="${tab}" data-page="${page-1}" ${page===0?'disabled':''}>‹ Previous</button><span>${page+1}/${parts.length}</span><button class="compact-button" data-action="detail-tab" data-id="${esc(id)}" data-tab="${tab}" data-page="${page+1}" ${page===parts.length-1?'disabled':''}>Next ›</button></div>`:''}`;
  }
  modal(micTitle(r),`<div class="detail-tabs">${tabs}</div><div class="detail-panel">${body}</div>`,`<small class="detail-updated">Updated ${esc(updatedText(r))}</small>`+button('Submit a fix','submit-fix',id)+button('Report inactive','submit-report',id)+button(r.claimed?'Host / claim help':'Claim this mic','submit-claim',id));
  $('#modal').dataset.view='mic';$('#modal .modal-head h2').title=micTitle(r);
}
function submission(id,kind){
  if(state.demo){toast('These are fictional examples. Use a real listing to submit a report or claim.');return;}
  const r=findMic(id);if(!r)return;
  if(kind==='claim'&&r.claimed){modal('This mic has a host.',`<p>The host can sign in to edit this listing. For ownership disputes or corrections, submit a fix for the administrator.</p><a class="button primary" href="/owner">Host login</a>`,button('Submit a fix','submit-fix',id));return;}
  const report=kind==='report',claim=kind==='claim';
  modal(claim?'Claim this mic':report?'Report an inactive mic':'Submit a correction',`<p>${esc(r.name)} · ${esc(r.venue)}</p><form id="submission-form" data-id="${esc(id)}" data-kind="${kind}">${field(report?'Your name (optional)':'Your name','name','','text',!report)}${field(claim?'Email for your host account':'Email (optional, for follow-up)','email','','email',claim)}${textField(claim?'How can we verify you run this mic?':report?'Why does this mic no longer run? Add a source or the last date it ran.':'What needs fixing? Include the correct details and a source link.','message','',true)}<div class="honeypot" aria-hidden="true"><label>Leave this empty<input name="website" tabindex="-1" autocomplete="off"></label></div><p class="form-helper">${claim?'You will receive a private status link. Save it and check for approval; password setup appears there when approved.':report?'The administrator reviews reports before hiding a mic. Your report will not remove it automatically.':'The administrator reviews corrections before updating listings.'} Your contact details are not published.</p><button class="button primary" type="submit">${claim?'Submit claim':report?'Send report':'Send correction'}</button></form>`);
}
function sourceForm(){state.preview=null;modal('Add your source.',`<p>Paste a public source URL or upload a spreadsheet. Preview the extracted rows before anything is published.</p><form id="source-form">${field('Source name','name','','text',true)}${selectField('Source type','kind',[['auto','Website / automatic detection'],['csv','Online CSV / published Google Sheet'],['xlsx','Online Excel (.xlsx)'],['json','JSON events feed'],['table','Website table'],['jsonld','Structured event page (JSON-LD)'],['cards','Website cards (configure selectors)'],['upload','Upload Excel or CSV']],'auto')}<div id="source-url-field">${field('Public URL','url','','url',false,'For Google Sheets, use its published CSV export URL. Private/login-only pages are not supported.')}</div><div id="source-file-field" hidden><div class="field"><label for="source-file">Excel or CSV file (maximum 5 MB)</label><input type="file" id="source-file" name="file" accept=".csv,.xlsx"><p class="form-helper">An upload is a snapshot. Use an online file URL for scheduled updates.</p></div></div><div class="form-row">${selectField('Check for updates','interval_minutes',[[15,'Every 15 minutes'],[30,'Every 30 minutes'],[60,'Every hour'],[180,'Every 3 hours'],[360,'Every 6 hours'],[720,'Every 12 hours'],[1440,'Daily']],60)}${selectField('Source priority','priority',[[100,'High — official host / venue'],[50,'Normal — directory'],[10,'Low — secondary source']],50)}</div><details><summary>Field mapping and source-specific settings</summary>${field('Worksheet (optional)','sheet')}${field('Table or repeating card CSS selector (optional)','row_selector','','text',false,'For cards, enter a repeating container such as .event. Field mappings below then become CSS selectors inside that card.')}${textField('Column mapping (JSON, optional)','mapping','{}')}${textField('Default fields (JSON, optional)','defaults','{}')}<p class="form-helper">Mapping example: {"name":"Event title","venue":"Place","weekday":"Day"}. Defaults example: {"borough":"Queens"}. Only set a borough default when it is true for every row. Pin fields: latitude and longitude.</p></details><label class="checkbox-label"><input type="checkbox" name="permission_confirmed" required> I have permission to access and reuse this source. Its source URL will be visible on listings.</label><button type="submit" class="button primary">Preview import ↗</button></form>`);}
function previewTable(result){return `<div class="preview-summary"><span class="tag">${result.rows.length} valid listings</span><span class="tag ${result.skipped?'stale-badge':''}">${result.skipped} skipped rows</span><span class="tag">${esc(result.detected_kind)}</span></div><div class="preview-table-wrapper"><table class="preview-table"><thead><tr><th>Mic</th><th>Venue</th><th>Day / date</th><th>Time</th><th>Borough</th></tr></thead><tbody>${result.rows.slice(0,100).map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.venue)}</td><td>${esc(r.date||DAYS[r.weekday])}</td><td>${esc(timeText(r.start_time))}</td><td>${esc(r.borough)}</td></tr>`).join('')}</tbody></table></div>${result.rows.length>100?'<p class="form-helper">Showing the first 100 rows.</p>':''}<div class="warning-list">${result.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}</div>${result.skipped?'<p class="security-warning">Skipped rows will not be published. Future automatic checks with skipped rows will be held for review.</p>':''}`;}
async function renderAdmin(){
  $('#view-directory').hidden=true;$('#view-sources').hidden=false;
  const root=$('#view-sources');
  if(window.MICLIST_PREVIEW){root.innerHTML=blank('Backend preview only.','This standalone preview does not simulate account security or imports. Run the included server to access /admin.');return;}
  try{
    const session=await api('/api/session');
    if(!session.authenticated){root.innerHTML=`<div class="login-panel"><div class="eyebrow">PRIVATE / ADMINISTRATOR</div><h2>One place to run it all.</h2><p>Manage sources, corrections, claims, host access and every listing.</p>${!session.configured?'<p class="security-warning">Set ADMIN_PASSWORD on the server to enable this panel.</p>':''}<form id="admin-login">${field('Admin password','password','','password',true)}<button class="button primary" type="submit">Sign in ↗</button></form><p class="community-link">Run a mic? <a href="/owner">Host login</a></p></div>`;return;}
    const [admin,community]=await Promise.all([api('/api/sources'),api('/api/admin/community')]);state.admin=admin;state.community=community;
    const claims=community.submissions.filter(s=>s.kind==='claim'&&s.state==='pending').length,fixes=community.submissions.filter(s=>s.kind==='fix'&&s.state==='pending').length,newMics=(community.proposals||[]).filter(s=>s.state==='pending').length,reports=community.submissions.filter(s=>s.kind==='report'&&s.state==='pending').length;
    root.innerHTML=`<div class="admin-heading"><div><div class="eyebrow">YOUR DIRECTORY / YOUR CONTROL</div><h1>The control room<span class="red-period">.</span></h1><p>Good information in. More stage time out.</p></div><div class="admin-buttons">${button('Add source ↗','add-source','','primary')}${button('Password','admin-password')}${button('Sign out','admin-logout')}</div></div>${session.weak_password?'<p class="security-warning">Your administrator password is short and easy to guess. Use Password to change it.</p>':''}<div class="stats-grid"><div class="stat"><div class="eyebrow">CONNECTED SOURCES</div><strong>${admin.sources.length}</strong><p>${admin.sources.filter(s=>s.enabled).length} scheduled</p></div><div class="stat"><div class="eyebrow">PUBLISHED MICS</div><strong>${community.listings.filter(r=>!r.hidden).length}</strong><p>${community.listings.filter(r=>!Number.isFinite(r.latitude)).length} need a map pin</p></div><div class="stat"><div class="eyebrow">AWAITING REVIEW</div><strong>${claims+fixes+newMics+reports}</strong><p>${newMics} new mics · ${claims} claims · ${fixes} fixes · ${reports} reports</p></div></div><div id="visitor-stats" class="visitor-stats" aria-label="Visitor statistics">Loading visitor counts…</div><div class="admin-tabs">${[['sources','Sources'],['proposals',`New mics (${newMics})`],['reports',`Reports (${reports})`],['fixes',`Corrections (${fixes})`],['claims',`Claims (${claims})`],['owners','Host access'],['mics','All mics'],['activity','Activity'],['about','About page']].map(([v,t])=>`<button data-admin-tab="${v}" class="${state.adminTab===v?'active':''}">${t}</button>`).join('')}</div><div id="admin-body"></div>`;
    renderAdminBody();loadVisitorStats();
  }catch(err){root.innerHTML=blank('Admin could not load.',esc(err.message),button('Retry','admin-refresh'));}
}
async function loadVisitorStats(){
  const box=$('#visitor-stats');if(!box)return;
  try{const v=await api('/api/admin/visitors');if(!box.isConnected)return;
    box.innerHTML=`<div class="visitor-heading"><strong>Visitors</strong><button class="compact-button" data-action="visitors-refresh">Refresh</button></div><div class="visitor-counts">${[['today','Today'],['week','Last 7 days'],['month','Last 30 days'],['total','Since tracking began']].map(([key,label])=>`<div><strong>${Number(v[key]).toLocaleString()}</strong><span>${label}</span></div>`).join('')}</div><small>Estimated unique browsers · New York time · Tracking since ${esc(new Date(v.started_at*1000).toLocaleDateString('en-US',{timeZone:'America/New_York'}))}. Repeat visits count once per period. No IP addresses stored.</small>`;
  }catch{box.innerHTML='Visitor counts unavailable. '+button('Retry','visitors-refresh');}
}
async function renderAdminBody(){const root=$('#admin-body'),a=state.admin,c=state.community;if(!root)return;
  if(state.adminTab==='about'){
    root.innerHTML='<p>Loading About page…</p>';
    try{const content=await api('/api/admin/about');if(state.adminTab!=='about')return;
      root.innerHTML=`<div class="admin-list-heading"><h2>Edit About page</h2><a href="/about" target="_blank" rel="noopener" class="text-link">View page</a></div><form id="about-form" class="about-editor"><div class="host-tabs" aria-label="About sections">${[['intro','Intro'],['why','Why'],['how','How'],['seo','SEO']].map(([id,label])=>`<button type="button" data-about-tab="${id}" aria-pressed="${id==='intro'}" class="${id==='intro'?'active':''}">${label}</button>`).join('')}</div><div data-about-panel="intro">${field('Page heading','title',content.title,'text',true)}${textField('Introduction','intro',content.intro)}</div><div data-about-panel="why" hidden>${field('Section heading','why_heading',content.why_heading)}${textField('Why you built it / about you','why',content.why)}</div><div data-about-panel="how" hidden>${field('Section heading','how_heading',content.how_heading)}${textField('How listings work','how',content.how)}${textField('Host information','hosts',content.hosts)}</div><div data-about-panel="seo" hidden>${textField('Search description (up to 300 characters)','seo_description',content.seo_description)}<p class="form-helper">Used in search and link previews. Plain text only.</p></div><button type="submit" class="button primary">Save About page</button></form>`;
      for(const [name,max]of Object.entries({title:120,intro:1200,why_heading:120,why:2400,how_heading:120,how:2400,hosts:1200,seo_description:300})){$('#about-form [name='+name+']').maxLength=max;}
      $('#about-form [name=seo_description]').required=true;
      $('#about-form').dataset.original=JSON.stringify(Object.fromEntries(new FormData($('#about-form'))));
    }catch(err){root.innerHTML=blank('About editor unavailable.',esc(err.message));}
    return;
  }
  if(state.adminTab==='sources'){
    root.innerHTML=`<p class="notice-inline">${a.scheduler_enabled?`Scheduled worker: ${a.heartbeat&&Date.now()/1000-a.heartbeat<(a.heartbeat_max_age||240)?'running':'awaiting heartbeat'}.`:'Scheduled checks are disabled on the server.'} Sources are polled at their configured intervals, not instantly. File uploads do not auto-sync.</p><div class="admin-list-heading"><h2>Your sources</h2><a class="text-link" href="/api/export">Export source backup ↓</a></div>${a.sources.length?a.sources.map(s=>`<article class="source-card"><div class="source-title-row"><div class="source-icon">${s.config.kind==='upload'?'↓':'↻'}</div><div class="source-title"><h3>${esc(s.config.name)}</h3><p>${s.config.url?external(s.config.url,new URL(s.config.url).hostname):'Uploaded or manually added snapshot'}</p></div><span class="tag ${s.state==='error'||s.state==='review'?'stale-badge':''}">${s.enabled?esc(s.state):s.config.kind==='upload'?'snapshot':'paused'}</span><div class="source-controls">${s.config.kind!=='upload'?button('Check now','source-sync',s.id)+button(s.enabled?'Pause':'Resume','source-toggle',s.id):''}${button('Remove','source-delete',s.id)}</div></div><div class="source-meta"><span>${s.listings} listings</span><span>Checked ${ago(s.last_success)}</span><span>Changed ${ago(s.changed_at)}</span>${s.config.kind!=='upload'?`<label>Interval <select data-source-interval="${esc(s.id)}">${[15,30,60,180,360,720,1440].map(n=>`<option value="${n}" ${n===s.config.interval_minutes?'selected':''}>${n<60?n+' minutes':n/60+' hours'}</option>`).join('')}</select></label>`:''}<label>Priority <select data-source-priority="${esc(s.id)}">${[[100,'High'],[50,'Normal'],[10,'Low']].map(([n,t])=>`<option value="${n}" ${n===s.config.priority?'selected':''}>${t}</option>`).join('')}</select></label></div>${s.error?`<p class="security-warning">${esc(s.error)}</p>`:''}</article>`).join(''):blank('Add the places you trust.','Connect a listings page, published spreadsheet, or uploaded Excel file.',button('Add your first source ↗','add-source','','primary'))}${a.review.length?`<h3>Source changes for your review</h3><p class="notice-inline">Missing entries stay published. Only you can hide a mic after reviewing it.</p>${a.review.map(r=>`<div class="review-item"><div><strong>${esc(r.listing.name)}</strong><small>${esc(r.listing.venue)} · ${esc(r.listing.date||DAYS[r.listing.weekday])} ${esc(timeText(r.listing.start_time))}</small><p>${r.hidden?'Hidden':'Missing from '+r.missing_count+' successful source checks'}</p></div>${button(r.hidden?'Show':'Hide','observation-toggle',r.source_id+'|'+r.remote_key+'|'+(r.hidden?'0':'1'))}</div>`).join('')}`:''}`;
    const connected=new Set(a.sources.map(s=>s.config.url.replace('www.','').replace(/\/$/,'')));
    const candidates=(a.club_candidates||[]).filter(s=>!connected.has(s.url.replace('www.','').replace(/\/$/,'')));
    if(candidates.length)root.insertAdjacentHTML('beforeend',`<details class="club-review"><summary>Club sources to review (${candidates.length})</summary><p class="notice-inline">These are research leads, not syncing sources. No listings are added until a schedule is verified.</p>${candidates.map(s=>`<article class="queue-item"><h3>${external(s.url,s.name)}</h3><p>${esc(s.note)}</p><small>Reviewed ${esc(s.reviewed_at)}</small></article>`).join('')}</details>`);
  }else if(['claims','fixes','reports'].includes(state.adminTab)){
    const kind=state.adminTab==='claims'?'claim':state.adminTab==='reports'?'report':'fix';const rows=c.submissions.filter(s=>s.kind===kind);
    root.innerHTML=`<p class="notice-inline">${kind==='claim'?'Verify the person actually runs the mic before approving. Approval unlocks password setup on the host’s saved status link. No message needs to be sent.':kind==='report'?'Verify whether the mic no longer runs. Reports alone do not hide a listing; you can hide a confirmed inactive mic below.':'Corrections do not edit the public directory automatically. Open the mic, apply the correction, then mark the submission resolved.'}</p>${rows.length?rows.map(s=>`<article class="queue-item"><div class="queue-heading"><div><h3>${esc(s.mic_name)}</h3><small>${esc(s.name)}${s.email?' · '+esc(s.email):''} · ${ago(s.created)}</small></div><span class="tag ${s.state==='pending'?'stale-badge':''}">${esc(s.state)}</span></div><p>${esc(s.message)}</p>${s.note?`<p>Review note: ${esc(s.note)}</p>`:''}<div class="queue-buttons">${button('View / edit mic','edit-admin',s.mic_id)}${['pending','approved'].includes(s.state)?kind==='claim'?button(s.state==='approved'?'Create replacement signup link':'Approve','claim-approve',s.id,'primary')+button('Reject','submission-reject',s.id):kind==='report'?button('Hide mic & resolve','report-hide',s.id,'primary')+button('Dismiss','submission-reject',s.id):button('Mark resolved','fix-resolve',s.id,'primary')+button('Reject','submission-reject',s.id):''}</div></article>`).join(''):blank('Nothing awaiting your judgment.','New '+(kind==='claim'?'ownership claims':kind==='report'?'inactive mic reports':'corrections')+' will appear here.')}`;
  }else if(state.adminTab==='proposals'){
    const rows=(c.proposals||[]).slice().sort((a,b)=>Number(b.state==='pending')-Number(a.state==='pending')||b.created-a.created);
    root.innerHTML=`<p class="notice-inline">Approval publishes the listing. Only submissions marked Host access requested also receive password setup; verify those submitters run the mic. Nothing is automatically emailed.</p>${rows.length?rows.map(p=>`<article class="queue-item"><div class="queue-heading"><div><h3>${esc(p.listing.name)}</h3><small>${esc(p.listing.venue)} · ${esc(p.listing.borough)} · ${esc(p.listing.date||DAYS[p.listing.weekday])} ${esc(timeText(p.listing.start_time))}</small></div><span class="tag">${esc(p.state)}</span></div><p>${esc(p.listing.address)}<br>Fee: ${esc(p.listing.cost_text||'Not provided')}</p><p><strong>${esc(p.name)}</strong> · ${esc(p.email)}</p><p><strong>${p.request_host_access?'Host access requested':'Listing only — no host access'}</strong></p><p>${esc(p.message)}</p>${p.reply?`<p>Reply to submitter: ${esc(p.reply)}</p>`:''}<div class="queue-buttons">${p.state==='pending'?button(p.request_host_access?'Approve mic + host':'Approve listing','proposal-approve',p.id,'primary')+button('Reject','proposal-reject',p.id):p.mic_id?button('View / edit mic','edit-admin',p.mic_id):''}</div></article>`).join(''):blank('No new mic submissions.','Submissions from the menu appear here for your review.')}`;
  }else if(state.adminTab==='owners'){
    root.innerHTML=c.ownership.length?c.ownership.map(o=>`<article class="queue-item"><h3>${esc(findMic(o.mic_id)?.name||o.mic_id)}</h3><p>${esc(o.name)} · ${esc(o.email)}</p><small>Approved ${ago(o.approved)}</small><div class="queue-buttons">${button('Edit mic','edit-admin',o.mic_id)}${button('Revoke host access','owner-revoke',o.mic_id)}</div></article>`).join(''):blank('No approved hosts yet.','Approve a claim and share its signup link. The host appears here after creating an account.');
  }else if(state.adminTab==='mics'){
    root.innerHTML=`<div class="admin-list-heading"><h2>Every mic, one place.</h2>${button('Add a mic ↗','mic-new','','primary')}</div><input type="search" id="admin-search" class="admin-search input-full" placeholder="Filter by mic name, venue or borough" aria-label="Search all mics"><div id="admin-mic-rows"></div>`;renderAdminMics('');
  }else{
    root.innerHTML=`<h2>Edits & access</h2>${c.audit.length?c.audit.map(r=>`<div class="activity-row"><span class="activity-dot"></span><div>${esc(r.action)}<small>${esc(r.actor==='admin'?'Administrator':'Approved host')} · ${esc(findMic(r.mic_id)?.name||'Directory')}</small>${r.detail?`<details><summary>Change record</summary><pre class="audit-detail">${esc(r.detail)}</pre></details>`:''}</div><time>${ago(r.created)}</time></div>`).join(''):'<p class="read-only-note">No edits or ownership actions yet.</p>'}<h2 class="field-divider">SOURCE CHECKS</h2>${a.activity.map(r=>`<div class="activity-row"><span class="activity-dot ${r.state==='error'?'error':''}"></span><div>${esc(a.sources.find(s=>s.id===r.source_id)?.config.name||'Source')} · ${esc(r.state)}<small>${r.imported} imported · ${r.changed} changed${r.message?' · '+esc(r.message):''}</small></div><time>${ago(r.created)}</time></div>`).join('')}`;
  }
}
function renderAdminMics(q){const rows=state.community.listings.filter(r=>`${r.name} ${r.venue} ${r.borough}`.toLowerCase().includes(q));$('#admin-mic-rows').innerHTML=rows.length?rows.map(r=>`<article class="queue-item"><div class="queue-heading"><div><h3>${esc(r.name)}</h3><small>${esc(r.venue)} · ${esc(r.borough)} · ${esc(r.date||DAYS[r.weekday])} ${esc(timeText(r.start_time))}</small></div><span class="tag">${r.hidden?'Hidden':'Published'}</span></div><p>${r.claimed?'Host claimed · ':''}${r.curated?'Edited values protected from scraping · ':''}${!Number.isFinite(r.latitude)?'Location needs a map pin':'Location mapped'}</p><div class="queue-buttons">${button('Edit mic / pin','edit-admin',r.id,'primary')}${button(r.hidden?'Publish':'Hide from directory','mic-hide',r.id)}${r.curated?button('Restore source values','mic-restore',r.id):''}</div></article>`).join(''):blank('No listings yet.','Import a source or add a mic manually.');}
function editForm(id,mode){const r=id?findMic(id):{};if(id&&!r){toast('This mic is no longer available.');return;}
  modal(id?'Edit the mic.':'Add a mic.',`<p>${mode==='owner'?'You can edit only mics approved for your account. Your edits take priority over imports.':'Changes are saved over the source data. Unchanged fields continue syncing.'}</p><form id="edit-form" data-id="${esc(id||'')}" data-mode="${mode}"><div class="form-row">${field('Mic name','name',r.name||'','text',true)}${field('Venue','venue',r.venue||'','text',true)}</div>${field('Street address','address',r.address||'','text',true)}<div class="form-row">${selectField('Borough','borough',BOROUGHS,r.borough||'Manhattan')}${field('Neighborhood (optional)','neighborhood',r.neighborhood||'')}</div><div class="form-row">${selectField('Weekly day','weekday',DAYS.map((d,i)=>[i,d]),r.weekday??0)}${field('One-time date (overrides weekly day)','date',r.date||'','date')}</div><div class="form-row">${field('Start time','start_time',r.start_time||'19:00','time',true)}${field('Signup time (optional)','signup_time',r.signup_time||'','time')}</div><div class="form-row">${field('Entry fee (Free, $5, or descriptive)','cost',r.cost_text||'')}${field('Purchase minimum (optional)','purchase_minimum',r.purchase_minimum||'')}</div><div class="form-row">${field('Minutes per comic','set_minutes',r.set_minutes||'','number')}${field('Signup method','signup_method',r.signup_method||'')}</div>${field('Host name(s), public','host_names',r.host_names||'')}${textField('Host social links (optional, up to 5 URLs)','host_socials',r.host_socials||'')}${field('Signup link','signup_url',r.signup_url||'','url')}${selectField('Status','status',['scheduled','cancelled','postponed'],r.status||'scheduled')}${recurrenceFields(r)}${field('Excluded dates, comma-separated YYYY-MM-DD','excluded_dates',(r.excluded_dates||[]).join(', '))}${textField('Notes','notes',r.notes||'')}<details><summary>Map pin coordinates</summary><p class="form-helper">Use accurate coordinates for this venue. Leave both blank to request address geocoding. Approximate geocoder matches are labeled on the directory.</p><div class="form-row">${field('Latitude','latitude',r.latitude??'','number')}${field('Longitude','longitude',r.longitude??'','number')}</div></details><button type="submit" class="button primary">Save mic ↗</button></form>`);
  syncScheduleForm($('#edit-form'));$('#edit-form').dataset.original=JSON.stringify(Object.fromEntries(new FormData($('#edit-form'))));
}
function submissionTab(tab){
  $$('[data-submission-panel]').forEach(p=>p.hidden=p.dataset.submissionPanel!==tab);
  $$('[data-submission-tab]').forEach(b=>{const active=b.dataset.submissionTab===tab;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
}
async function renderSubmit(){
  $('#view-directory').hidden=true;$('#view-sources').hidden=false;const root=$('#view-sources');root.classList.add('host-page');document.title='Submit a mic | NYC Open Mic Master List';
  if(location.hash.length>1){await renderProposalStatus();return;}
  root.innerHTML=`<div class="host-heading"><h1>Submit a mic</h1><a class="text-link" href="/">Back to map</a></div><p class="host-help">Know a missing mic? Anyone can submit it. Host access is optional and requires approval.</p>
    <div class="host-tabs submit-tabs" aria-label="Submission sections">${[['mic','Mic'],['when','When'],['you','You']].map(([id,label])=>`<button type="button" data-submission-tab="${id}" aria-pressed="false">${label}</button>`).join('')}</div>
    <form id="mic-proposal-form" class="proposal-form">
      <div class="host-edit-panel" data-submission-panel="mic">
        ${field('Mic name','mic_name','','text',true)}${field('Venue','venue','','text',true)}${field('Street address','address','','text',true)}${selectField('Borough','borough',BOROUGHS,'Manhattan')}
      </div>
      <div class="host-edit-panel" data-submission-panel="when" hidden>
        ${recurrenceFields({})}<div class="form-row">${selectField('Weekly day','weekday',DAYS.map((d,i)=>[i,d]),0)}${field('One-time date (optional)','date','','date')}</div>
        ${field('Start time','start_time','','time',true)}${field('Entry fee (optional)','cost')}
        <p class="host-help">Choose a schedule. For biweekly mics, enter a confirmed date so we know which weeks they run.</p>
      </div>
      <div class="host-edit-panel" data-submission-panel="you" hidden>
        ${field('Your name','name','','text',true)}${field('Your email','email','','email',true)}<label class="field">Are you requesting host access?<select name="request_host_access" required><option value="">Choose one</option><option value="no">No — just submitting a mic</option><option value="yes">Yes — I run this mic and want to edit it</option></select></label>${textField('Source link / verification details','message','',true)}
        <div class="honeypot" aria-hidden="true"><label>Leave this empty<input name="website" tabindex="-1" autocomplete="off"></label></div>
        <p class="host-help">Link a source confirming the mic. Requesting host access? Include proof you run it. Your contact details stay private.</p>
      </div>
      <div class="host-save-row"><span>Nothing is published until approved.</span><button type="submit" class="button primary">Submit for review</button></div>
    </form>`;
  syncScheduleForm($('#mic-proposal-form'));submissionTab('mic');
}
async function renderProposalStatus(){
  const root=$('#view-sources'),token=location.hash.slice(1);if(!token)return;
  try{
    const result=await api(location.pathname==='/claim-status'?'/api/claims/status':'/api/mic-proposals/status',{method:'POST',body:{token}});state.proposalState=result.state;
    const states={pending:['Submitted for review','Save this link—don’t lose it. Keep checking here for approval. This page checks automatically while open.'],approved:['Your mic is approved',!result.request_host_access&&location.pathname!=='/claim-status'?'The mic is published. No host access was requested.':result.can_setup?'Your mic is published. Set up your host password to claim and edit it.':'Your mic is published. The setup link expired or was replaced; ask the administrator for a new link.'],rejected:['Submission not approved','The administrator has reviewed your submission.'],activated:['Your host access is ready','Sign in to update your mic whenever you need.'],revoked:['Host access is unavailable','Contact the administrator about this submission.']};
    const [title,message]=states[result.state]||['Submission status','Check with the administrator.'];
    root.innerHTML=`<div class="host-heading"><h1>${esc(title)}</h1><a class="text-link" href="/">Back to map</a></div><div class="proposal-status"><h2>${esc(result.mic_name)}</h2><p role="status">${esc(message)}</p>${result.reply?`<p class="proposal-reply">${esc(result.reply)}</p>`:''}${result.can_setup?`<a class="button primary" href="/claim#${esc(token)}">Set up host password</a><p class="host-help">Setup access expires ${esc(new Date(result.expires_at*1000).toLocaleDateString('en-US',{timeZone:'America/New_York'}))}.</p>`:''}${result.state==='activated'?'<a class="button primary" href="/owner">Host sign in</a>':''}<label for="proposal-status-link">Your private status link</label><input id="proposal-status-link" readonly value="${esc(location.href)}"><div class="queue-buttons">${button('Copy status link','copy-proposal-link')}${button('Check status','proposal-refresh')}</div><p class="host-help">${result.request_host_access||location.pathname==='/claim-status'?'Keep this link private: approved host requests can use it to set up access.':'This link tracks your listing submission. It does not grant host access.'} No email has been sent.</p></div>`;
  }catch(err){state.proposalState=null;root.innerHTML=blank('Status could not load.',esc(err.message),button('Try again','proposal-refresh'));}
}
function hostTab(tab){
  state.ownerTab=tab;
  $$('[data-host-panel]').forEach(p=>p.hidden=p.dataset.hostPanel!==tab);
  $$('[data-host-tab]').forEach(b=>{const active=b.dataset.hostTab===tab;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
}
function aboutDirty(){const f=$('#about-form');return !!f&&JSON.stringify(Object.fromEntries(new FormData(f)))!==f.dataset.original;}
function hostDirty(){const f=$('#host-edit-form');if(!f)return false;return JSON.stringify(Object.fromEntries(new FormData(f)))!==f.dataset.original;}
function hostMic(){return state.owner?.listings.find(r=>r.id===state.ownerMic);}
function renderHostEditor(){
  const r=hostMic(),box=$('#host-editor');if(!box||!r)return;
  box.innerHTML=`<div class="host-mic-summary"><div><strong title="${esc(r.name)}">${esc(r.name)}</strong><small>Updated ${esc(updatedText(r))} · ${r.hidden?'Hidden by site admin':esc(r.status)}</small></div>${r.hidden?'':`<a class="text-link" href="${esc(micLink(r.id))}" target="_blank" rel="noopener">View listing</a>`}</div>
  ${r.hidden?'<p class="host-notice">This mic is hidden by the site administrator. You can edit it here.</p>':''}
  <div class="host-tabs" aria-label="Mic settings">${[['mic','Mic'],['when','When'],['where','Where'],['hosts','Hosts']].map(([id,label])=>`<button type="button" data-host-tab="${id}" aria-pressed="false">${label}</button>`).join('')}</div>
  <form id="host-edit-form" data-id="${esc(r.id)}">
    <div class="host-edit-panel" data-host-panel="mic">
      ${field('Mic name','name',r.name||'','text',true)}
      <div class="form-row">${field('Entry fee','cost',r.cost_text||'')}${field('Minutes per comic','set_minutes',r.set_minutes??'','number')}</div>
      ${field('Purchase minimum (optional)','purchase_minimum',r.purchase_minimum||'')}
      ${textField('Notes','notes',r.notes||'')}
    </div>
    <div class="host-edit-panel" data-host-panel="when" hidden>
      <div class="form-row">${field('One-time date','date',r.date||'','date')}${selectField('Weekly day','weekday',DAYS.map((d,i)=>[i,d]),r.weekday??0)}</div>
      ${recurrenceFields(r)}<div class="form-row">${field('Start time','start_time',r.start_time||'19:00','time',true)}${field('Signup time (optional)','signup_time',r.signup_time||'','time')}</div>
      ${selectField('Status for this listing','status',[['scheduled','Scheduled'],['cancelled','Cancelled'],['postponed','Postponed']],r.status||'scheduled')}
      ${field('Skip dates (YYYY-MM-DD, comma separated)','excluded_dates',(r.excluded_dates||[]).join(', '))}
      <p class="host-help">A date overrides the weekly day. Date and cancellation changes apply to this occurrence.</p>
    </div>
    <div class="host-edit-panel" data-host-panel="where" hidden>
      ${field('Venue','venue',r.venue||'','text',true)}${field('Street address','address',r.address||'','text',true)}
      <div class="form-row">${selectField('Borough','borough',BOROUGHS,r.borough||'Manhattan')}${field('Neighborhood','neighborhood',r.neighborhood||'')}</div>
      <div class="form-row">${field('Latitude (optional)','latitude',r.latitude??'','number')}${field('Longitude (optional)','longitude',r.longitude??'','number')}</div>
      <p class="host-help">Leave both coordinates blank to locate the street address automatically.</p>
    </div>
    <div class="host-edit-panel" data-host-panel="hosts" hidden>
      ${field('Host name(s), public','host_names',r.host_names||'')}
      ${textField('Social links (optional, one URL per line, up to 5)','host_socials',r.host_socials||'')}
      ${field('Signup link (optional)','signup_url',r.signup_url||'','url')}
      <p class="host-help">These details appear when someone opens your mic.</p>
    </div>
    <div class="host-save-row"><span id="host-save-state" role="status">Your edits are protected from imports.</span><button type="submit" class="button primary">Save changes</button></div>
  </form>`;
  const f=$('#host-edit-form');syncScheduleForm(f);f.dataset.original=JSON.stringify(Object.fromEntries(new FormData(f)));hostTab(state.ownerTab||'mic');
}
async function renderOwner(){
  $('#view-directory').hidden=true;$('#view-sources').hidden=false;const root=$('#view-sources');root.classList.add('host-page');document.title='Host dashboard | NYC Open Mic Master List';
  try{
    state.owner=await api('/api/owner/me');
    const rows=state.owner.listings.slice().sort((a,b)=>Number(!!(a.date&&a.date<nyToday()))-Number(!!(b.date&&b.date<nyToday()))||(nextDate(a)+a.start_time).localeCompare(nextDate(b)+b.start_time));
    if(!rows.some(r=>r.id===state.ownerMic))state.ownerMic=rows[0]?.id;
    root.innerHTML=`<div class="host-heading"><div><h1>Manage your mic</h1><small title="${esc(state.owner.name)}">${esc(state.owner.name)} · Approved host</small></div><div class="host-buttons">${button('Password','owner-password')}${button('Sign out','owner-logout')}</div></div>
      ${rows.length>1?`<label class="host-chooser">Your approved listings<select id="host-mic-select">${rows.map(r=>`<option value="${esc(r.id)}" ${r.id===state.ownerMic?'selected':''}>${esc(r.name)} · ${esc(r.date||DAYS[r.weekday])} · ${esc(timeText(r.start_time))}</option>`).join('')}</select></label>`:''}
      ${rows.length?'<div id="host-editor"></div>':blank('No available listings.','Your approved listing is not currently available. Ask the site administrator to check it.')}`;
    renderHostEditor();
  }catch(err){
    state.owner=null;
    if(err.status!==401&&!window.MICLIST_PREVIEW){root.innerHTML=blank('Host sign-in unavailable.',esc(err.message),button('Try again','owner-refresh'));return;}
    root.innerHTML=`<div class="login-panel"><h1>Host sign in</h1><p>Use the account created from your approved claim.</p><form id="owner-login">${field('Email','email','','email',true)}${field('Password','password','','password',true)}<button class="button primary" type="submit">Sign in</button></form><p class="community-link">First time? Check your saved private status link. Password setup appears there after approval.</p><a href="/" class="text-link">Find and claim your mic</a></div>`;
  }
}
async function renderClaim(){
  $('#view-directory').hidden=true;$('#view-sources').hidden=false;const root=$('#view-sources');const token=location.hash.slice(1);root.classList.add('host-page');document.title='Set up host access | NYC Open Mic Master List';
  if(!token){root.innerHTML=blank('An invitation is required.','Use the one-use signup link shared by the administrator after your mic claim is approved.');return;}
  try{const info=await api('/api/owner/invitation',{method:'POST',body:{token}});root.innerHTML=`<div class="login-panel"><div class="eyebrow">YOUR CLAIM WAS APPROVED</div><h2>${info.existing_account?'Connect this mic.':'Create your host login.'}</h2><p>${esc(info.mic_name)}</p><p class="notice-inline">Account email: ${esc(info.email)}</p><form id="redeem-form">${field(info.existing_account?'Your existing account password':'Choose a password (15+ characters)','password','','password',true)}${info.existing_account?'':field('Confirm password','confirm_password','','password',true)}<button type="submit" class="button primary">${info.existing_account?'Add mic to my account':'Create account & edit my mic'} ↗</button></form></div>`;$('#redeem-form').dataset.token=token;$('#redeem-form').dataset.newAccount=String(!info.existing_account);if(!info.existing_account)$$('input[type=password]',$('#redeem-form')).forEach(i=>{i.autocomplete='new-password';i.minLength=15;});
  }catch(err){root.innerHTML=blank('This invitation cannot be used.',esc(err.message));}
}
function how(){modal('One list. Traceable information.',`<div class="how-step"><span class="how-number">01</span><div><h3>Your sources, connected.</h3><p>An administrator adds permitted websites and spreadsheets, then reviews the first import. A pasted URL must contain supported data or have configured extraction rules.</p></div></div><div class="how-step"><span class="how-number">02</span><div><h3>Changes, not guesses.</h3><p>Scheduled checks update supported sources. Missing rows, parser failures and conflicts are flagged rather than treated as cancellations. Uploads are snapshots.</p></div></div><div class="how-step"><span class="how-number">03</span><div><h3>Real hosts have a say.</h3><p>Anyone may submit corrections or claim a mic. Only administrator-approved owners can edit their mic. Host edits are protected from imports.</p></div></div><div class="how-step"><span class="how-number">04</span><div><h3>Check before heading out.</h3><p>Click a day to filter the map and list. Pins can represent multiple sessions. Unlocated mics remain in the list; approximate address matches are labeled. This directory does not guarantee a mic will run.</p></div></div>`);}
// Form submissions are same-origin authenticated requests; no credentials are stored in browser storage.
document.addEventListener('submit',async e=>{
  const f=e.target;if(!['admin-login','owner-login','submission-form','source-form','edit-form','redeem-form','admin-password-form','host-edit-form','owner-password-form','mic-proposal-form','about-form'].includes(f.id))return;e.preventDefault();const b=$('button[type=submit]',f),old=b.textContent;b.disabled=true;b.textContent='Working…';$('.form-error',f)?.remove();
  try{const raw=Object.fromEntries(new FormData(f));
    if(f.id==='about-form'){await api('/api/admin/about',{method:'PUT',body:raw});f.dataset.original=JSON.stringify(raw);toast('About page updated.');}
    if(f.id==='mic-proposal-form'){
      const fields={name:raw.mic_name};for(const key of ['venue','address','borough','weekday','date','start_time','cost','frequency','recurrence_anchor'])fields[key]=raw[key];
      const result=await api('/api/mic-proposals',{method:'POST',body:{name:raw.name,email:raw.email,message:raw.message,website:raw.website,request_host_access:raw.request_host_access==='yes',fields}});
      f.reset();history.replaceState(null,'',result.status_path);await renderSubmit();
    }
    if(f.id==='owner-password-form'){
      if(raw.new_password!==raw.confirm_password)throw Error('New passwords do not match.');
      await api('/api/owner/password',{method:'POST',body:{current_password:raw.current_password,new_password:raw.new_password}});
      f.reset();$('#modal').close();state.owner=null;await renderOwner();toast('Password changed. Sign in again.');
    }
    if(f.id==='host-edit-form'){
      const original=JSON.parse(f.dataset.original),fields={};for(const [key,value]of Object.entries(raw))if(value!==original[key])fields[key]=value;
      if(!Object.keys(fields).length){toast('No changes to save.');return;}
      if('date' in fields||'weekday' in fields||'frequency' in fields){fields.date=raw.date;fields.weekday=raw.weekday;if('frequency' in fields)fields.recurrence_anchor=raw.recurrence_anchor||'';}
      if('address' in fields||'borough' in fields){if(!('latitude' in fields))fields.latitude='';if(!('longitude' in fields))fields.longitude='';}
      await api(`/api/owner/mics/${f.dataset.id}`,{method:'PATCH',body:{fields}});
      await renderOwner();toast('Changes saved.');
    }
    if(f.id==='admin-password-form'){if(raw.new_password!==raw.confirm_password)throw Error('New passwords do not match.');await api('/api/admin/password',{method:'POST',body:{current_password:raw.current_password,new_password:raw.new_password}});f.reset();$('#modal').close();await renderAdmin();toast('Password changed. Sign in with your new password.');}
    if(f.id==='admin-login'){await api('/api/login',{method:'POST',body:{password:raw.password}});await renderAdmin();}
    if(f.id==='owner-login'){await api('/api/owner/login',{method:'POST',body:raw});await renderOwner();}
    if(f.id==='redeem-form'){if(f.dataset.newAccount==='true'&&raw.password!==raw.confirm_password)throw Error('Passwords do not match.');await api('/api/owner/redeem',{method:'POST',body:{token:f.dataset.token,password:raw.password}});history.replaceState(null,'','/owner');await renderOwner();}
    if(f.id==='submission-form'){if(f.dataset.kind==='report'&&!raw.name.trim())raw.name='Anonymous';const result=await api(`/api/mics/${f.dataset.id}/submissions`,{method:'POST',body:{kind:f.dataset.kind,...raw}});$('#modal').close();if(result.status_path){history.replaceState(null,'',result.status_path);await renderSubmit();}else toast(result.message);}
    if(f.id==='source-form'){
      const config={name:raw.name,url:raw.kind==='upload'?'':raw.url,kind:raw.kind,interval_minutes:Number(raw.interval_minutes),priority:Number(raw.priority),permission_confirmed:raw.permission_confirmed==='on',sheet:raw.sheet,row_selector:raw.row_selector,mapping:JSON.parse(raw.mapping||'{}'),defaults:JSON.parse(raw.defaults||'{}')};let result;
      if(config.kind==='upload'){const file=$('#source-file').files[0];if(!file)throw Error('Choose an Excel or CSV file.');const body=new FormData();body.append('file',file);body.append('config',JSON.stringify(config));result=await api('/api/sources/upload-preview',{method:'POST',body});}
      else{result=await api('/api/sources/preview',{method:'POST',body:config});}
      state.preview=result;modal('Review before publishing.',previewTable(result),button('Cancel','close')+(result.rows.length?button('Approve & connect source','source-commit','','primary'):''));
    }
    if(f.id==='edit-form'){
      const original=JSON.parse(f.dataset.original||'{}'),fields={};for(const [k,v]of Object.entries(raw))if(!f.dataset.id||v!==original[k])fields[k]=v;
      if(!Object.keys(fields).length){$('#modal').close();toast('No changes to save.');return;}
      if('date' in fields||'weekday' in fields||'frequency' in fields){fields.date=raw.date;fields.weekday=raw.weekday;if('frequency' in fields)fields.recurrence_anchor=raw.recurrence_anchor||'';}
      if('address' in fields||'borough' in fields){if(!('latitude' in fields))fields.latitude='';if(!('longitude' in fields))fields.longitude='';}
      const id=f.dataset.id,mode=f.dataset.mode;const path=id?`/api/${mode==='owner'?'owner':'admin'}/mics/${id}`:'/api/admin/mics';
      await api(path,{method:id?'PATCH':'POST',body:{fields}});$('#modal').close();toast('Mic saved.');await loadPublic();if(mode==='owner')await renderOwner();else await renderAdmin();
    }
  }catch(err){errorAt(f,err);}finally{b.disabled=false;b.textContent=old;}
});
document.addEventListener('click',async e=>{
  const menu=$('#site-menu');if(menu&&(!menu.contains(e.target)||e.target.closest('nav')))menu.open=false;
  if(window.MICLIST_PREVIEW){const link=e.target.closest('a[href]');if(link&&['/admin','/owner','/'].includes(link.getAttribute('href'))){e.preventDefault();if(link.getAttribute('href')==='/admin'){await renderAdmin();}else if(link.getAttribute('href')==='/owner'){await renderOwner();}else{$('#view-directory').hidden=false;$('#view-sources').hidden=true;renderDirectory();}return;}}
  const day=e.target.closest('[data-day]');if(day){state.date=null;state.day=day.dataset.day==='all'?'all':Number(day.dataset.day);renderDirectory();return;}
  const borough=e.target.closest('[data-borough]');if(borough){state.borough=borough.dataset.borough;renderDirectory();return;}
  const aboutButton=e.target.closest('[data-about-tab]');if(aboutButton){$$('[data-about-panel]').forEach(p=>p.hidden=p.dataset.aboutPanel!==aboutButton.dataset.aboutTab);$$('[data-about-tab]').forEach(b=>{const active=b===aboutButton;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});return;}
  const submissionButton=e.target.closest('[data-submission-tab]');if(submissionButton){submissionTab(submissionButton.dataset.submissionTab);return;}
  const hostButton=e.target.closest('[data-host-tab]');if(hostButton){hostTab(hostButton.dataset.hostTab);return;}
  const tab=e.target.closest('[data-admin-tab]');if(tab){if(tab.dataset.adminTab===state.adminTab)return;if(aboutDirty()&&!confirm('Discard unsaved About page changes?'))return;state.adminTab=tab.dataset.adminTab;$$('[data-admin-tab]').forEach(t=>t.classList.toggle('active',t===tab));renderAdminBody();return;}
  const el=e.target.closest('[data-action]');if(!el)return;const action=el.dataset.action,id=el.dataset.id;e.preventDefault();
  try{
    if(action==='calendar'){calendarMonth='';renderCalendar();}
    if(action==='calendar-prev')renderCalendar(-1);
    if(action==='calendar-next')renderCalendar(1);
    if(action==='calendar-date'||action==='calendar-clear'){if(location.pathname!=='/'){location.href=action==='calendar-date'?'/?date='+encodeURIComponent(id):'/';return;}state.date=action==='calendar-date'?id:null;state.day='all';$('#modal').close();renderDirectory();}
    if(action==='visitors-refresh')await loadVisitorStats();
    if(action==='proposal-refresh')await renderProposalStatus();
    if(action==='copy-proposal-link'){try{await navigator.clipboard.writeText(location.href);toast('Private status link copied.');}catch{$('#proposal-status-link').focus();$('#proposal-status-link').select();toast('Select and copy your private link.');}}
    if(action==='close')$('#modal').close();
    if(action==='how')how();
    if(action==='donate'){modal('Donate','<p>Support NYC Open Mic Master List.</p><div class="donation-options"><button class="button quiet" disabled>Cash App</button><button class="button quiet" disabled>Venmo</button></div><p class="form-helper">Donation links coming soon.</p>');}

    if(action==='refresh'){await loadPublic();toast('Loaded the latest saved listings.');}
    if(action==='reset'){if($('#biweekly-filter'))$('#biweekly-filter').value='all';Object.assign(state,{date:null,day:'all',borough:'all',search:'',cost:'all',signup:'all',claimStatus:'all',hideOffWeek:false,timeFrom:'',timeTo:'',sort:'time'});if($('#sort'))$('#sort').value='time';$('#search').value='';if($('#claim-filter'))$('#claim-filter').value='all';if($('#cost'))$('#cost').value='all';if($('#signup-filter'))$('#signup-filter').value='all';if($('#time-from'))$('#time-from').value='';if($('#time-to'))$('#time-to').value='';renderDirectory();}
    if(action==='filters')filtersDialog();
    if(action==='previous-page'||action==='next-page'){state.page+=action==='next-page'?1:-1;renderCards();}
    if(action==='detail-tab')details(id,el.dataset.tab,Number(el.dataset.page||0));
    if(action==='map-toggle'){
      const collapsed=$('.directory-layout').classList.toggle('map-collapsed');
      el.textContent=collapsed?'Show map':'Hide map';
      el.setAttribute('aria-expanded',String(!collapsed));
      map?.hide();
    }
    if(action==='map-reset')map?.reset();
    if(action==='details')details(id);
    if(action==='copy-mic-link'){
      try{await navigator.clipboard.writeText(micLink(id));toast('Link copied.');}
      catch{$('#mic-link').focus();$('#mic-link').select();toast('Select and copy this link.');}
    }

    if(action==='locate'&&!map?.focus(id))toast('This mic has no map location yet.');
    if(action==='demo'){state.demo=true;state.data=demoData();renderDirectory();}
    if(action==='exit-demo'){state.demo=false;await loadPublic();}
    if(action==='export'){if(state.demo){toast('Demo data is not a real schedule. Export is available after importing real sources.');return;}location.href='/api/export.csv';}
    if(action==='submit-report')submission(id,'report');
    if(action==='submit-fix')submission(id,'fix');
    if(action==='submit-claim')submission(id,'claim');
    if(action==='add-source')sourceForm();
    if(action==='source-commit'){el.disabled=true;await api('/api/sources',{method:'POST',body:{preview_id:state.preview.preview_id}});state.preview=null;$('#modal').close();toast('Source connected.');await loadPublic();await renderAdmin();}
    if(action==='source-sync'){el.disabled=true;el.textContent='Checking…';const result=await api(`/api/sources/${id}/sync`,{method:'POST'});toast(result.message||`Source check: ${result.state}`);await loadPublic();await renderAdmin();}
    if(action==='source-toggle'){const source=state.admin.sources.find(s=>s.id===id);await api(`/api/sources/${id}`,{method:'PATCH',body:{enabled:!source.enabled}});await renderAdmin();}
    if(action==='source-delete'){if(!confirm('Remove this source and its imported observations? Claims linked only to this source may no longer have a published mic. Export a backup first.'))return;await api(`/api/sources/${id}`,{method:'DELETE'});await loadPublic();await renderAdmin();}
    if(action==='observation-toggle'){const [s,r,h]=id.split('|');await api(`/api/listings/${s}/${r}/visibility`,{method:'PATCH',body:{hidden:h==='1'}});await loadPublic();await renderAdmin();}
    if(action==='admin-refresh')await renderAdmin();
    if(action==='admin-password'){modal('Change admin password',`<form id="admin-password-form">${field('Current password','current_password','','password',true)}${field('New password','new_password','','password',true)}${field('Confirm new password','confirm_password','','password',true)}<p class="form-helper">A long, unique password protects the directory. Changing it signs out all admin sessions.</p><button type="submit" class="button primary">Change password</button></form>`);}
    if(action==='admin-logout'){if(aboutDirty()&&!confirm('Discard unsaved About page changes and sign out?'))return;await api('/api/logout',{method:'POST'});state.admin=null;state.community=null;await renderAdmin();}
    if(action==='owner-refresh')await renderOwner();
    if(action==='owner-password'){if(hostDirty()){toast('Save your mic changes before changing your password.');return;}modal('Change your password',`<form id="owner-password-form">${field('Current password','current_password','','password',true)}${field('New password (15+ characters)','new_password','','password',true)}${field('Confirm new password','confirm_password','','password',true)}<p class="form-helper">This signs out all your host sessions.</p><button type="submit" class="button primary">Change password</button></form>`);$$('input[name=new_password],input[name=confirm_password]',$('#owner-password-form')).forEach(i=>{i.minLength=15;i.autocomplete='new-password';});}
    if(action==='owner-logout'){if(hostDirty()&&!confirm('Discard unsaved changes and sign out?'))return;await api('/api/owner/logout',{method:'POST'});state.owner=null;await renderOwner();}
    if(action==='edit-admin')editForm(id,'admin');
    if(action==='edit-owner'){state.ownerMic=id;await renderOwner();}
    if(action==='mic-new')editForm(null,'admin');
    if(action==='mic-hide'){const r=findMic(id);await api(`/api/admin/mics/${id}/visibility`,{method:'PATCH',body:{hidden:!r.hidden}});await loadPublic();await renderAdmin();}
    if(action==='mic-restore'){if(!confirm('Remove manual/host overrides and use the imported source values again?'))return;await api(`/api/admin/mics/${id}/restore`,{method:'POST'});await loadPublic();await renderAdmin();}
    if(action==='owner-revoke'){if(!confirm('Revoke this host’s editing access to this mic? Existing edits remain until you change or restore them.'))return;await api(`/api/admin/mics/${id}/owner`,{method:'DELETE'});await loadPublic();await renderAdmin();}
    if(['proposal-approve','proposal-reject'].includes(action)){
      const approve=action==='proposal-approve';
      const reply='';
      const result=await api(`/api/admin/mic-proposals/${id}`,{method:'POST',body:{action:approve?'approve':'reject',reply}});await renderAdmin();
      toast(result.message);
    }
    if(['claim-approve','submission-reject','fix-resolve','report-hide'].includes(action)){
      if(action==='report-hide'&&!confirm('Hide this mic from the public map and list? It can be restored in All mics.'))return;
      const note='';
      const result=await api(`/api/admin/submissions/${id}`,{method:'POST',body:{action:action==='claim-approve'?'approve':action==='fix-resolve'?'resolve':action==='report-hide'?'hide':'reject',note}});await renderAdmin();
      if(result.status_link_ready){toast(result.message);}else if(result.invite_path){const link=location.origin+result.invite_path;modal('Claim approved.',`<p>Send this signup link directly to <strong>${esc(result.email)}</strong> after verifying their identity.</p><div class="invite-link">${esc(link)}</div><p class="security-warning">This link grants access to the approved mic. It expires in seven days and works once. It has <strong>not</strong> been emailed.</p>`,button('Copy signup link','copy-invite',link,'primary'));}else toast('Review saved.');
    }
    if(action==='copy-invite'){await navigator.clipboard.writeText(id);toast('Signup link copied.');}
  }catch(err){toast(err.message);el.disabled=false;}
});
document.addEventListener('change',async e=>{const t=e.target;try{
  if(t.name==='frequency'){syncScheduleForm(t.form,true);if(t.form?.id==='host-edit-form')$('#host-save-state').textContent='Unsaved changes';}
  if(t.id==='host-mic-select'){if(hostDirty()&&!confirm('Discard unsaved changes and switch listings?')){t.value=state.ownerMic;return;}state.ownerMic=t.value;renderHostEditor();}
  if(t.id==='day-filter'){if(t.value==='calendar'){t.value=String(state.date||state.day);calendarMonth='';renderCalendar();return;}state.date=/^\d{4}-\d{2}-\d{2}$/.test(t.value)?t.value:null;state.day=state.date||t.value==='all'?'all':Number(t.value);renderDirectory();}
  if(t.id==='borough-filter'){state.borough=t.value;renderDirectory();}
  if(t.id==='time-from'){state.timeFrom=t.value;renderDirectory();}
  if(t.id==='time-to'){state.timeTo=t.value;renderDirectory();}
  if(t.id==='cost'){state.cost=t.value;renderDirectory();}
  if(t.id==='claim-filter'){state.claimStatus=t.value;renderDirectory();}
  if(t.id==='signup-filter'){state.signup=t.value;renderDirectory();}
  if(t.id==='biweekly-filter'){state.hideOffWeek=t.value==='current';renderDirectory();}
  if(t.id==='sort'){state.sort=t.value;renderDirectory();}
  if(t.name==='kind'&&t.closest('#source-form')){$('#source-url-field').hidden=t.value==='upload';$('#source-file-field').hidden=t.value!=='upload';}
  if(t.dataset.sourceInterval){await api(`/api/sources/${t.dataset.sourceInterval}`,{method:'PATCH',body:{interval_minutes:Number(t.value)}});toast('Refresh interval updated.');await renderAdmin();}
  if(t.dataset.sourcePriority){await api(`/api/sources/${t.dataset.sourcePriority}`,{method:'PATCH',body:{priority:Number(t.value)}});toast('Source priority updated.');await loadPublic();await renderAdmin();}
}catch(err){toast(err.message);}});
document.addEventListener('invalid',e=>{const panel=e.target.closest('[data-host-panel]');if(panel)hostTab(panel.dataset.hostPanel);const submission=e.target.closest('[data-submission-panel]');if(submission)submissionTab(submission.dataset.submissionPanel);const about=e.target.closest('[data-about-panel]');if(about)$('[data-about-tab='+about.dataset.aboutPanel+']').click();},true);
window.addEventListener('beforeunload',e=>{if(hostDirty()||aboutDirty()){e.preventDefault();e.returnValue='';}});
document.addEventListener('input',e=>{if(e.target.closest('#host-edit-form'))$('#host-save-state').textContent=hostDirty()?'Unsaved changes':'Your edits are protected from imports.';if(e.target.id==='search'){state.search=e.target.value.toLowerCase().trim();renderDirectory();}if(e.target.id==='admin-search')renderAdminMics(e.target.value.toLowerCase().trim());});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#site-menu')?.open){$('#site-menu').open=false;$('#site-menu summary').focus();}if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('#modal').open&&location.pathname==='/'){e.preventDefault();$('#search').focus();}});
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))$('#modal').close();});
async function boot(){
  if(location.pathname!=='/'&&!window.MICLIST_PREVIEW)loadSyncStatus();
  if(location.pathname==='/admin'||location.hash==='#sources'){await renderAdmin();}
  else if(location.pathname==='/owner'){await renderOwner();}
  else if(location.pathname==='/claim'){await renderClaim();}
  else if(['/submit','/claim-status'].includes(location.pathname)){await renderSubmit();}
  else{const selectedDate=new URLSearchParams(location.search).get('date');if(validDate(selectedDate)){state.date=selectedDate;state.day='all';}await loadPublic();const id=new URLSearchParams(location.search).get('mic');if(id){const r=findMic(id);if(r){state.date=null;state.day='all';renderDirectory();details(id);}else toast('This mic is no longer listed.');}}
}
new ResizeObserver(()=>{if(!$('#view-directory').hidden)renderCards();}).observe($('#cards'));
boot();
// Refresh the browser's saved snapshot periodically; source checks run on the server.
setInterval(()=>{if(!document.hidden&&!state.demo&&location.pathname==='/')loadPublic();},60000);



setInterval(()=>{if(!document.hidden&&['/submit','/claim-status'].includes(location.pathname)&&location.hash&&state.proposalState==='pending')renderProposalStatus();},30000);
setInterval(()=>{if(!document.hidden&&!state.demo&&location.pathname!=='/')loadSyncStatus();},60000);
