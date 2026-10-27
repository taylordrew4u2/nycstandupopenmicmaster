const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('assets/app.js','utf8').split("document.addEventListener(")[0];
const ctx=vm.createContext({window:{},localStorage:{removeItem(){}},Intl,Date,URL,console});
vm.runInContext(source,ctx);
const run=s=>vm.runInContext(s,ctx);
const base={weekday:1,frequency:'weekly',start_time:'17:59',status:'scheduled'};
const evening='2026-10-27T22:00:30Z'; // Tuesday, 6:00 PM in New York.
function check(label,expected,fields={},now=evening,selectedDate=null){
  ctx.testRow={...base,...fields};ctx.testNow=new Date(now);ctx.selectedDate=selectedDate;
  run('state.date=selectedDate');
  assert.strictEqual(run('hasStartedToday(testRow,testNow)'),expected,label);
}

check('Earlier start today is dimmed',true);
check('The current start minute remains bright',false,{start_time:'18:00'});
check('Upcoming start today remains bright',false,{start_time:'18:01'});
check('Explicit today selection is dimmed',true,{},evening,'2026-10-27');
check('Future date selection stays bright even for a mic that also runs today',false,{},evening,'2026-11-03');
check('Past date selection stays bright',false,{},evening,'2026-10-20');
check('One-time event today is dimmed',true,{date:'2026-10-27',frequency:'one-time',weekday:null});
check('One-time event tomorrow stays bright',false,{date:'2026-10-28',frequency:'one-time',weekday:null});
check('One-time event yesterday stays bright',false,{date:'2026-10-26',frequency:'one-time',weekday:null});
check('A recurring mic on another weekday stays bright',false,{weekday:2});
check('An excluded occurrence stays bright',false,{excluded_dates:['2026-10-27']});
check('Cancelled entries stay bright',false,{status:'cancelled'});
check('Postponed entries stay bright',false,{status:'postponed'});
check('An entry with no status stays bright',false,{status:undefined});

check('Known biweekly on-week is dimmed',true,{frequency:'biweekly',recurrence_anchor:'2026-10-13'});
check('Known biweekly off-week stays bright',false,{frequency:'biweekly',recurrence_anchor:'2026-10-20'});
check('Biweekly dates without a confirmed anchor stay bright',false,{frequency:'biweekly'});
check('Non-weekly entry without a confirmed date stays bright',false,{frequency:'non-weekly'});
check('Non-weekly entry with an exact date today is dimmed',true,{frequency:'non-weekly',date:'2026-10-27'});
const imported={date:'2026-10-20',weekday:null,sources:[{url:'https://comediq.us/mics.json'}]};
check('Comediq generated dates still use the recurring weekday',true,imported);
check('A manually overridden imported date stays exact',false,{...imported,overridden_fields:['date']});
check('A one-time imported event stays exact',false,{...imported,frequency:'one-time'});
check('Unknown non-weekly imported dates stay bright',false,{...imported,frequency:'non-weekly'});
for(const time of [null,undefined,'','not a time','7:00','24:00','12:60','17:59:00']){
  check(`Invalid start time ${JSON.stringify(time)} stays bright`,false,{start_time:time});
}

check('UTC next day still counts as today in New York',true,{start_time:'20:59'},'2026-10-28T01:00:00Z');
check('New York midnight clears yesterday',false,{date:'2026-10-27',frequency:'one-time'},'2026-10-28T04:00:00Z');
check('A midnight start remains bright during its start minute',false,{weekday:2,start_time:'00:00'},'2026-10-28T04:00:30Z');
check('A midnight start dims one minute later',true,{weekday:2,start_time:'00:00'},'2026-10-28T04:01:00Z');
check('Daylight time uses the New York offset',true,{date:'2026-07-07',frequency:'one-time',start_time:'18:59'},'2026-07-07T23:00:00Z');
check('Standard time uses the New York offset',false,{date:'2026-01-06',frequency:'one-time',start_time:'18:01'},'2026-01-06T23:00:00Z');
check('Spring daylight-saving transition uses the local clock',true,{date:'2026-03-08',frequency:'one-time',start_time:'03:00'},'2026-03-08T07:01:00Z');
check('Fall daylight-saving transition uses the local date and clock',true,{date:'2026-11-01',frequency:'one-time',start_time:'02:00'},'2026-11-01T07:01:00Z');

const previousTimezone=process.env.TZ;
try{
  for(const timezone of ['UTC','Pacific/Honolulu','Asia/Tokyo']){
    process.env.TZ=timezone;
    check(`Browser timezone ${timezone} does not change New York today`,true,{start_time:'20:59'},'2026-10-28T01:00:00Z');
    check(`Browser timezone ${timezone} does not dim upcoming New York starts`,false,{start_time:'21:01'},'2026-10-28T01:00:00Z');
  }
}finally{
  if(previousTimezone===undefined)delete process.env.TZ;else process.env.TZ=previousTimezone;
}
console.log('Past-today dimming: New York time, dates, recurrence, exclusions and daylight saving passed');

function orderingRow(id,fields={}){
  return {id,name:id,venue:'Test Room',address:'1 Broadway',borough:'Manhattan',neighborhood:'',
    weekday:null,date:'2026-10-27',frequency:'one-time',start_time:'19:00',status:'scheduled',
    excluded_dates:[],cost:0,signup_method:'In person',...fields};
}
function orderedIds(rows,{now=evening,date=null,day='all',sort='time'}={}){
  ctx.orderRows=rows;ctx.orderNow=new Date(now);ctx.orderSelection={date,day,sort};
  run('Object.assign(state,orderSelection)');
  const result=JSON.parse(run('JSON.stringify(sortDirectoryRows(orderRows,orderNow).map(row=>row.id))'));
  assert.deepStrictEqual([...result].sort(),Array.from(rows,row=>row.id).sort(),'Ordering preserves every mic ID');
  return result;
}
const sortRows=[
  orderingRow('past-late',{name:'Alpha Past',start_time:'17:00',cost:0}),
  orderingRow('future-far',{name:'Alpha Future',date:'2026-10-29',start_time:'09:00',cost:2}),
  orderingRow('up-late',{name:'Alpha Upcoming',start_time:'20:00',cost:9}),
  orderingRow('past-early',{name:'Zeta Past',start_time:'16:00',cost:9}),
  orderingRow('future-near',{name:'Zeta Future',date:'2026-10-28',start_time:'12:00',cost:6}),
  orderingRow('up-now',{name:'Zeta Upcoming',start_time:'18:00',cost:1}),
];
assert.deepStrictEqual(orderedIds(sortRows),
  ['up-now','up-late','future-near','future-far','past-early','past-late'],
  'All days: upcoming today leads, other dates stay in the middle, started today goes last');
assert.deepStrictEqual(orderedIds(sortRows,{sort:'name'}),
  ['up-late','up-now','future-far','future-near','past-late','past-early'],
  'Name sorting is preserved within each time-status group');
assert.deepStrictEqual(orderedIds(sortRows,{sort:'price'}),
  ['up-now','up-late','future-far','future-near','past-late','past-early'],
  'Price sorting is preserved within each time-status group');

const futureSelection=[
  orderingRow('weekly-late',{name:'Zeta',date:null,weekday:1,frequency:'weekly',start_time:'20:00'}),
  orderingRow('future-explicit',{name:'Beta',date:'2026-11-03',start_time:'16:00'}),
  orderingRow('weekly-earlier',{name:'Alpha',date:null,weekday:1,frequency:'weekly',start_time:'17:00'}),
];
assert.deepStrictEqual(orderedIds(futureSelection,{date:'2026-11-03',sort:'name'}),
  ['weekly-earlier','future-explicit','weekly-late'],
  'A selected future date does not move recurring earlier-today rows to the bottom');
const weekdayRows=[
  orderingRow('weekly-past',{date:null,weekday:1,frequency:'weekly',start_time:'17:00'}),
  orderingRow('weekly-upcoming',{date:null,weekday:1,frequency:'weekly',start_time:'20:00'}),
];
assert.deepStrictEqual(orderedIds(weekdayRows,{day:1}),['weekly-upcoming','weekly-past'],
  'Selecting the current weekday still promotes its upcoming mics');
const otherWeekday=weekdayRows.map(row=>({...row,weekday:2}));
assert.deepStrictEqual(orderedIds(otherWeekday,{day:2}),['weekly-past','weekly-upcoming'],
  'Selecting another weekday preserves ordinary time order');

const transitionRows=[
  orderingRow('at-start',{start_time:'18:00'}),
  orderingRow('next-minute',{start_time:'18:01'}),
  orderingRow('already-started',{start_time:'17:00'}),
];
assert.deepStrictEqual(orderedIds(transitionRows),['at-start','next-minute','already-started'],
  'The current start minute remains in the upcoming group');
assert.deepStrictEqual(orderedIds(transitionRows,{now:'2026-10-27T22:01:00Z'}),
  ['next-minute','already-started','at-start'],
  'A mic moves below upcoming entries after its start minute passes');

const orderingTimezone=process.env.TZ;
try{
  process.env.TZ='Asia/Tokyo';
  const nearMidnight=[
    orderingRow('ny-started',{start_time:'20:59'}),
    orderingRow('ny-tomorrow',{date:'2026-10-28',start_time:'00:05'}),
    orderingRow('ny-upcoming',{start_time:'21:01'}),
    orderingRow('ny-same-minute',{start_time:'21:00'}),
  ];
  assert.deepStrictEqual(orderedIds(nearMidnight,{now:'2026-10-28T01:00:00Z'}),
    ['ny-same-minute','ny-upcoming','ny-tomorrow','ny-started'],
    'Tokyo and UTC next-day dates do not change the New York ordering');
  assert.deepStrictEqual(orderedIds([
    orderingRow('yesterday',{start_time:'23:59'}),
    orderingRow('new-day-upcoming',{date:'2026-10-28',start_time:'00:01'}),
    orderingRow('new-day-current',{date:'2026-10-28',start_time:'00:00'}),
  ],{now:'2026-10-28T04:00:00Z'}),['new-day-current','new-day-upcoming','yesterday'],
  'New York midnight promotes the new day and keeps yesterday outside the started-today group');
}finally{
  if(orderingTimezone===undefined)delete process.env.TZ;else process.env.TZ=orderingTimezone;
}

ctx.filterRows=[
  orderingRow('past-match',{venue:'Focus Room',borough:'Queens',start_time:'17:00',cost:5}),
  orderingRow('up-match',{venue:'Focus Room',borough:'Queens',start_time:'19:00'}),
  orderingRow('other-borough',{venue:'Focus Room',borough:'Brooklyn',start_time:'18:00'}),
  orderingRow('other-search',{venue:'Other Room',borough:'Queens',start_time:'18:00'}),
  orderingRow('other-time',{venue:'Focus Room',borough:'Queens',start_time:'21:00'}),
  orderingRow('other-price',{venue:'Focus Room',borough:'Queens',start_time:'18:00',cost:10}),
];
run(`Object.assign(state,{data:{listings:filterRows},date:'2026-10-27',day:'all',borough:'Queens',
  search:'focus',cost:'5',signup:'all',claimStatus:'all',hideOffWeek:false,timeFrom:'16:00',timeTo:'20:00',sort:'time'})`);
ctx.filterNow=new Date(evening);
const matchingRows=run('filtered(filterNow)');
assert.deepStrictEqual(Array.from(matchingRows,row=>row.id).sort(),['past-match','up-match'],
  'Search, borough, time and price filters keep the same matching IDs');
assert.deepStrictEqual(orderedIds(matchingRows,{date:'2026-10-27'}),['up-match','past-match'],
  'Ordering promotes upcoming filtered mics without hiding already-started matches');
assert.deepStrictEqual(Array.from(matchingRows,row=>row.id),['up-match','past-match'],
  'The directory filtering path applies the ordering with the supplied New York clock');
console.log('Directory ordering: New York time groups, selected sorts, dates, weekdays and filters passed');
