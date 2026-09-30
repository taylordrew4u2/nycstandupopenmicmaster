/* Count a visible public-page visit once per load, never directory auto-refreshes. */
'use strict';
(()=>{
  if(navigator.globalPrivacyControl===true||navigator.doNotTrack==='1'||window.MICLIST_PREVIEW||!['/','/about'].includes(location.pathname))return;
  let sent=false;
  const count=()=>{
    if(sent||document.visibilityState!=='visible')return;
    sent=true;
    fetch('/api/visit',{method:'POST',headers:{'X-Requested-With':'MicList'},credentials:'same-origin',keepalive:true}).catch(()=>{});
  };
  document.addEventListener('visibilitychange',count);count();
})();
