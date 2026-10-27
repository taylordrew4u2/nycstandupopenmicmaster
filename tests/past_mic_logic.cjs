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
