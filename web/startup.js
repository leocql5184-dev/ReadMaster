/* One brand reveal per loading session; errors can always leave or retry. */
(() => {
 const splash=document.getElementById('startupSplash');
 const tagline=splash.querySelector('.startup-tagline');
 const recovery=splash.querySelector('.startup-recovery');
 const copy='reading tool for amateur';
 let session=null, started=false;
 const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 function show(){
  if(session||started)return;
  started=true;
  const current={reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,inert:[]};
  session=current;
  splash.hidden=false;splash.removeAttribute('data-leaving');splash.removeAttribute('data-written');
  recovery.hidden=true;
  // Keep the writing's left edge still as each letter appears.
  tagline.style.width='';tagline.textContent=copy;
  tagline.style.width=tagline.getBoundingClientRect().width+'px';
  tagline.textContent=current.reduced?copy:'';
  document.documentElement.setAttribute('aria-busy','true');
  for(const node of document.body.children){if(node===splash||node.tagName==='SCRIPT')continue;current.inert.push([node,node.inert]);node.inert=true;}
  // This reveal runs only once, when the application page starts.
  const mark=splash.querySelector('.startup-r');mark.style.animation='none';void mark.offsetWidth;mark.style.animation='';
  current.recovery=setTimeout(()=>{if(session===current)recovery.hidden=false;},10000);
  current.written=(async()=>{
   if(!current.reduced){await wait(720);for(let count=1;count<=copy.length;count++){if(session!==current)return;tagline.textContent=copy.slice(0,count);await wait(34);}}
   splash.setAttribute('data-written','');
  })();
 }
 async function finish(){
  const current=session;if(!current)return;
  await current.written;
  if(session!==current)return;
  clearTimeout(current.recovery);
  splash.setAttribute('data-leaving','');
  await wait(current.reduced?80:180);
  if(session!==current)return;
  splash.hidden=true;for(const [node,inert] of current.inert)node.inert=inert;
  document.documentElement.removeAttribute('aria-busy');session=null;
 }
 document.getElementById('startupRetry').onclick=()=>location.reload();
 window.ReadMasterLoading={show,finish};
 show();
 // If another script fails, reveal the usable page rather than trapping it.
 window.addEventListener('error',()=>{if(session)finish();});
})();
