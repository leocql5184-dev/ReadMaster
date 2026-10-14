/* The reading surface is read-only. Its context menu only copies selected text. */
(() => {
 const pages=document.getElementById('pages');
 const menu=document.createElement('div');menu.className='reader-context-menu';menu.hidden=true;menu.setAttribute('role','menu');menu.setAttribute('aria-label','阅读操作');
 const copy=document.createElement('button');copy.type='button';copy.textContent='复制';copy.setAttribute('role','menuitem');menu.append(copy);document.body.append(menu);
 let selected='',ranges=[];
 const hide=()=>{menu.hidden=true;};
 pages.addEventListener('contextmenu',event=>{
  event.preventDefault();
  const selection=window.getSelection();
  selected=window.ReadMasterWordCount.selectedText(selection,pages);
  ranges=Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange());
  copy.disabled=!selected;menu.hidden=false;
  menu.style.left=Math.max(4,Math.min(event.clientX,innerWidth-menu.offsetWidth-4))+'px';
  menu.style.top=Math.max(4,Math.min(event.clientY,innerHeight-menu.offsetHeight-4))+'px';
 });
 menu.addEventListener('pointerdown',event=>event.preventDefault());
 copy.addEventListener('click',async()=>{
  if(!selected)return;
  const text=selected;hide();
  try{await navigator.clipboard.writeText(text);}
  catch{
   // WebView clipboard permissions vary; the fallback is still a user click.
   const field=document.createElement('textarea');field.value=text;field.readOnly=true;field.style.cssText='position:fixed;left:-9999px;top:0';document.body.append(field);field.select();
   const ok=document.execCommand('copy');field.remove();
   const selection=window.getSelection();selection.removeAllRanges();for(const range of ranges)if(range.startContainer.isConnected&&range.endContainer.isConnected)selection.addRange(range);
   if(!ok){document.getElementById('noticeText').textContent='请按 Ctrl+C 复制选中文字。';document.getElementById('notice').hidden=false;}
  }
 });
 document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target))hide();});
 document.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
 document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){hide();return;}
  if(!menu.hidden&&event.key==='Enter'&&!copy.disabled){event.preventDefault();copy.click();return;}
  if(event.target.closest('input,textarea,select,[contenteditable="true"]'))return;
  if((event.ctrlKey||event.metaKey)&&['x','v','z','y','s','p'].includes(event.key.toLowerCase()))event.preventDefault();
 });
 for(const name of ['cut','paste','beforeinput'])pages.addEventListener(name,event=>event.preventDefault());
})();
