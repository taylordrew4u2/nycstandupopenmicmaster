/* Count one visible public-page load, never directory auto-refreshes. */
'use strict';
(()=>{
  const publicPage=['/','/about','/nyc-comedy-open-mics'].includes(location.pathname)||/^\/(?:open-mics|mics)\/[^/]+$/.test(location.pathname);
  if(window.MICLIST_PREVIEW||!publicPage)return;
  const counter=document.querySelector('#visit-count');
  const showCount=async()=>{
    if(!counter)return;
    try{
      const response=await fetch('/api/visits',{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)return;
      const data=await response.json();
      if(!Number.isSafeInteger(data.total)||data.total<0)return;
      counter.textContent=data.total.toLocaleString()+' '+(data.total===1?'visit':'visits');
      counter.title=(data.starting_offset?'Counter starts at '+data.starting_offset.toLocaleString()+'; '+data.tracked_total.toLocaleString()+' recorded visits. ':'')+'Public page visits'+(data.started_at?' tracked since '+new Date(data.started_at*1000).toLocaleDateString('en-US',{timeZone:'America/New_York'}):' — tracking begins with the first visit')+'. Repeat visits count; automatic refreshes do not.';
      counter.ariaLabel=counter.textContent+'. '+counter.title;
      counter.hidden=false;
    }catch{}
  };
  let sent=false;
  const count=async()=>{
    if(sent||document.visibilityState!=='visible')return;
    sent=true;
    if(navigator.globalPrivacyControl!==true&&navigator.doNotTrack!=='1'){
      try{await fetch('/api/visit',{method:'POST',headers:{'X-Requested-With':'MicList'},credentials:'same-origin',keepalive:true});}catch{}
    }
    await showCount();
  };
  document.addEventListener('visibilitychange',count);count();
})();
