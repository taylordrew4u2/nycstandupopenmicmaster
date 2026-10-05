const fs=require('fs'),vm=require('vm'),assert=require('assert');
async function check(privacy,hidden=false){
 const counter={hidden:true},calls=[],events={};
 const document={visibilityState:hidden?'hidden':'visible',querySelector:()=>counter,addEventListener:(event,fn)=>events[event]=fn};
 vm.runInNewContext(fs.readFileSync('assets/visitors.js','utf8'),{window:{},location:{pathname:'/'},document,navigator:{doNotTrack:privacy?'1':'0'},fetch:async(path)=>{calls.push(path);return {ok:true,json:async()=>({total:1234,started_at:1791216000})};}});
 await new Promise(setImmediate);
 if(hidden){assert.deepEqual(calls,[]);document.visibilityState='visible';await events.visibilitychange();}
 await events.visibilitychange();
 assert.deepEqual(calls,privacy?['/api/visits']:['/api/visit','/api/visits']);
 assert.equal(counter.textContent,'1,234 visits');assert.equal(counter.hidden,false);
}
(async()=>{await check(false);await check(true);await check(false,true);console.log('Counter loads after counting, respects privacy, and counts once per page load');})().catch(e=>{console.error(e);process.exit(1)});
